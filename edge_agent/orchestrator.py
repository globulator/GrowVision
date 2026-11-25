import base64
import logging
import os
import platform
import threading
import time
from datetime import UTC, datetime
from typing import Any

from edge_agent.client import EdgeAPIClient
from edge_agent.config import EdgeConfig
from edge_agent.hardware import HardwareManager, get_hardware_telemetry
from edge_agent.live_stream import LiveStreamManager
from edge_agent.offline_queue import flush_queue, get_queue_count, save_to_queue
from edge_agent.vision import VisionPipeline, apply_rgb_to_jpeg_bytes
from shared.schemas import (
    CaptureMeta,
    CommandItem,
    CommandResult,
    HeartbeatActiveInstallation,
    HeartbeatRequest,
    HeartbeatSoftware,
)
from shared.version import get_app_version

logger = logging.getLogger("edge_agent.orchestrator")


class EdgeOrchestrator:
    """Главный оркестратор фоновых циклов Edge-агента."""

    def __init__(
        self,
        config: EdgeConfig,
        client: EdgeAPIClient | None = None,
        hardware: HardwareManager | None = None,
        vision: VisionPipeline | None = None,
    ):
        self.config = config
        self.client = client or EdgeAPIClient(
            base_url=config.server_url,
            api_key=config.edge_api_key,
            upload_timeout=config.upload_timeout_sec,
        )
        self.hardware = hardware or HardwareManager(
            mode=config.hardware_mode,
            relay_active_high=config.relay_active_high,
            v4l_by_id_dir=config.v4l_by_id_dir,
            sys_dir=config.sys_dir,
            mock_overlay_aruco=config.mock_overlay_aruco,
            mock_cameras=config.mock_cameras,
        )
        self.vision = vision or VisionPipeline(
            model_path=config.model_path,
            mock_mode=(config.hardware_mode == "mock"),
        )
        self.live_stream = LiveStreamManager(
            mode=config.hardware_mode,
            v4l_by_id_dir=config.v4l_by_id_dir,
            client=self.client,
            mock_cameras=config.mock_cameras,
            vision=self.vision,
        )

        self.running = threading.Event()
        self.is_scheduled_capture_running = threading.Event()
        self._flush_lock = threading.Lock()
        self._stopped = False

        # Динамические настройки, получаемые от сервера
        self.capture_interval_hours = config.capture_interval_hours
        self.debug_mode = config.debug_mode
        self.global_capture_delay_sec: float = 1.5
        self.active_installations: list[HeartbeatActiveInstallation] = []
        self._initialized_installations: set[tuple[int, int, int]] = set()
        self._frame_indices: dict[tuple[int, str], int] = {}

        self._heartbeat_thread: threading.Thread | None = None
        self._command_thread: threading.Thread | None = None
        self._scheduler_thread: threading.Thread | None = None

        self._last_scheduled_hour: tuple[int, int, int, int] | None = None
        self.last_scheduled_debug_log_line: str | None = None
        self._camera_rgb: dict[str, tuple[float, float, float]] = {}
        self._last_server_rgb: dict[str, tuple[float, float, float]] = {}
        self._camera_segmentation_modes: dict[str, str] = {}
        self._last_server_segmentation_modes: dict[str, str] = {}

    def start(self) -> None:
        """Запускает фоновые потоки оркестратора."""
        logger.info("Запуск Edge-агента GrowVision...")
        if self.config.test_capture_interval_sec is not None:
            logger.warning(
                f"ТЕСТОВЫЙ ИНТЕРВАЛ СЪЁМКИ: {self.config.test_capture_interval_sec} с"
            )
        self._stopped = False
        self.running.set()

        # Первичный Heartbeat перед запуском потоков
        self.perform_heartbeat()

        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="HeartbeatLoop",
            daemon=True,
        )
        self._command_thread = threading.Thread(
            target=self._command_poll_loop,
            name="CommandPollLoop",
            daemon=True,
        )
        self._scheduler_thread = threading.Thread(
            target=self._scheduler_loop,
            name="SchedulerLoop",
            daemon=True,
        )

        self._heartbeat_thread.start()
        self._command_thread.start()
        self._scheduler_thread.start()
        logger.info("Все фоновые циклы успешно запущены")

    def stop(self) -> None:
        """Останавливает оркестратор и фоновые потоки (идемпотентно)."""
        if self._stopped:
            return
        self._stopped = True
        logger.info("Остановка Edge-агента...")
        self.running.clear()
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=2.0)
        if self._command_thread and self._command_thread.is_alive():
            self._command_thread.join(timeout=2.0)
        if self._scheduler_thread and self._scheduler_thread.is_alive():
            self._scheduler_thread.join(timeout=2.0)
        self.live_stream.stop()
        self.hardware.cleanup()
        logger.info("Edge-агент остановлен")

    def init_installations_light(self) -> None:
        """Включает основной свет и выключает вспышку для новых установок под аппаратным мьютексом."""
        new_insts = [
            inst
            for inst in self.active_installations
            if (inst.id, inst.light_gpio_pin, inst.flash_gpio_pin)
            not in self._initialized_installations
        ]
        if not new_insts:
            return

        if not self.hardware.acquire_lock(blocking=False):
            logger.debug(
                "Аппаратный мьютекс занят, инициализация света установок отложена до следующего heartbeat"
            )
            return

        try:
            for inst in new_insts:
                self.hardware.set_light(inst.light_gpio_pin, True, source="init")
                if inst.flash_gpio_pin != inst.light_gpio_pin:
                    self.hardware.set_flash(inst.flash_gpio_pin, False, source="init")
                self._initialized_installations.add(
                    (inst.id, inst.light_gpio_pin, inst.flash_gpio_pin)
                )
            logger.info(
                f"Инициализирован свет для {len(new_insts)} новых установок: {[i.id for i in new_insts]}"
            )
        finally:
            self.hardware.release_lock()

    def trigger_async_flush(self) -> None:
        """Запускает выгрузку офлайн-очереди в отдельном потоке под замком (до 10 пакетов)."""

        def _worker():
            if not self._flush_lock.acquire(blocking=False):
                logger.debug("Сброс очереди уже выполняется в другом потоке")
                return
            try:
                sent, skipped, failed = flush_queue(
                    self.config.offline_queue_dir,
                    self.client.upload_capture,
                    max_batch_size=10,
                )
                if sent > 0 or failed > 0:
                    logger.info(
                        f"Сброс офлайн-очереди (поток): отправлено={sent}, оставлено={skipped}, ошибок={failed}"
                    )
            finally:
                self._flush_lock.release()

        threading.Thread(target=_worker, name="QueueFlushWorker", daemon=True).start()

    def perform_heartbeat(self) -> None:
        """Выполняет одну итерацию отправки Heartbeat и применения конфигурации."""
        software_info = HeartbeatSoftware(
            edge_version=get_app_version(),
            python_version=platform.python_version(),
            model_name=self.vision.get_model_name(),
        )
        hardware_info = get_hardware_telemetry(
            gpio_mode=self.config.hardware_mode,
            mock_cameras=self.config.mock_cameras,
        )
        queue_count = get_queue_count(self.config.offline_queue_dir)
        detected_cams = self.hardware.scan_cameras(
            v4l_by_id_dir=self.config.v4l_by_id_dir,
            sys_dir=self.config.sys_dir,
        )

        req = HeartbeatRequest(
            software=software_info,
            hardware=hardware_info,
            offline_queue_count=queue_count,
            detected_cameras=detected_cams,
        )

        resp = self.client.send_heartbeat(req)
        if resp is not None:
            self.capture_interval_hours = resp.capture_interval_hours
            self.debug_mode = resp.debug_mode
            self.active_installations = resp.active_installations
            if getattr(resp, "capture_delay_sec", None) is not None:
                self.global_capture_delay_sec = float(resp.capture_delay_sec)
            logger.debug(
                f"Конфигурация обновлена: интервал={self.capture_interval_hours}ч, "
                f"установок={len(self.active_installations)}, "
                f"задержка_съёмки={self.global_capture_delay_sec}с"
            )

            # Переводим реле активных установок в нормальное состояние (свет ВКЛ)
            self.init_installations_light()
            self._sync_rgb_from_installations(self.active_installations)
            self._sync_segmentation_modes_from_installations(self.active_installations)

            if getattr(self.live_stream, "is_running", False) and getattr(
                self.live_stream, "active_camera_id", None
            ):
                self._update_live_camera_context(self.live_stream.active_camera_id)

            # Если в очереди есть неотправленные пакеты, запускаем асинхронный сброс
            if queue_count > 0:
                self.trigger_async_flush()

    def _get_and_inc_frame_index(self, inst_id: int, camera_hw_id: str) -> int:
        """Возвращает текущий frame_index для пары (installation_id, camera_id) и инкрементирует его."""
        idx = self._frame_indices.get((inst_id, camera_hw_id), 0)
        self._frame_indices[(inst_id, camera_hw_id)] = idx + 1
        return idx

    def _heartbeat_loop(self) -> None:
        """Периодический цикл отправки Heartbeat."""
        while self.running.is_set():
            time.sleep(self.config.heartbeat_interval_sec)
            if not self.running.is_set():
                break
            try:
                self.perform_heartbeat()
            except Exception as exc:  # noqa: BLE001
                logger.error(f"Исключение в цикле Heartbeat: {exc}")

    def _command_poll_loop(self) -> None:
        """Периодический цикл опроса команд."""
        next_delay = self.config.command_poll_interval_sec
        while self.running.is_set():
            time.sleep(next_delay)
            if not self.running.is_set():
                break
            next_delay = self.config.command_poll_interval_sec
            try:
                commands = self.client.claim_command()
                for cmd in commands:
                    self.handle_command(cmd)
                if commands:
                    next_delay = min(0.05, self.config.command_poll_interval_sec)
            except Exception as exc:  # noqa: BLE001
                logger.error(f"Исключение в цикле опроса команд: {exc}")

    def handle_command(self, cmd: CommandItem) -> None:
        """Обрабатывает одну полученную команду."""
        logger.info(f"Получена команда #{cmd.id}: {cmd.command_type}")

        # Захват аппаратного мьютекса (с ожиданием до 10.0 с на случай плановой съёмки)
        acquired = self.hardware.acquire_lock(blocking=True, timeout=10.0)
        if not acquired:
            logger.warning(f"Аппаратный мьютекс занят. Отклонение команды #{cmd.id}")
            self.client.send_command_result(
                cmd.id,
                CommandResult(
                    status="error",
                    result_message="Камера недоступна, попробуйте позже",
                ),
            )
            return

        try:
            cmd_type = cmd.command_type.upper()
            if cmd_type == "LIGHT_MAIN":
                self._handle_light(cmd, is_flash=False)
            elif cmd_type == "LIGHT_FLASH":
                self._handle_light(cmd, is_flash=True)
            elif cmd_type == "RELAY_TEST":
                self._handle_relay_test(cmd)
            elif cmd_type == "SCAN_CAMERAS":
                self._handle_scan_cameras(cmd)
            elif cmd_type == "AUTOCALIBRATE_CAMERA":
                self._handle_autocalibrate_camera(cmd)
            elif cmd_type in ("SNAP", "CAPTURE_TEST"):
                self._handle_snap(cmd)
            elif cmd_type == "START_LIVE":
                self._handle_start_live(cmd)
            elif cmd_type == "STOP_LIVE":
                self._handle_stop_live(cmd)
            elif cmd_type == "GET_CAMERA_CONTROLS":
                self._handle_get_camera_controls(cmd)
            elif cmd_type == "SET_CAMERA_CONTROLS":
                self._handle_set_camera_controls(cmd)
            elif cmd_type == "AUTO_COLOR_CALIBRATE":
                self._handle_auto_color_calibrate(cmd)
            else:
                self.client.send_command_result(
                    cmd.id,
                    CommandResult(
                        status="error",
                        result_message=f"Неизвестная команда: {cmd.command_type}",
                    ),
                )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Ошибка при выполнении команды #{cmd.id}: {exc}")
            self.client.send_command_result(
                cmd.id,
                CommandResult(
                    status="error",
                    result_message=f"Внутренняя ошибка выполнения: {exc}",
                ),
            )
        finally:
            self.hardware.release_lock()

    def _handle_light(self, cmd: CommandItem, is_flash: bool) -> None:
        """Обработка команд LIGHT_MAIN и LIGHT_FLASH."""
        state = True
        if cmd.payload and isinstance(cmd.payload, dict):
            raw_state = cmd.payload.get("state")
            if raw_state is not None:
                state = bool(raw_state)

        targets = []
        if cmd.is_global and cmd.installations:
            targets = cmd.installations
        elif cmd.installation:
            targets = [cmd.installation]

        target_name = "вспышка" if is_flash else "основной свет"
        if not targets:
            self.client.send_command_result(
                cmd.id,
                CommandResult(
                    status="error",
                    result_message=f"Нет установок для управления ({target_name})",
                ),
            )
            return

        for inst in targets:
            pin = inst.flash_gpio_pin if is_flash else inst.light_gpio_pin
            if is_flash:
                self.hardware.set_flash(pin, state, source="manual")
            else:
                self.hardware.set_light(pin, state, source="manual")

        state_str = "включен" if state else "выключен"
        msg = f"{target_name.capitalize()} успешно {state_str} на {len(targets)} установках"
        self.client.send_command_result(
            cmd.id,
            CommandResult(
                status="done",
                result_message=msg,
                debug_log_line=f"[GPIO] {target_name} -> {state} for {[t.id for t in targets]}",
            ),
        )

    def _handle_relay_test(self, cmd: CommandItem, duration: float = 3.0) -> None:
        """Обработка команды RELAY_TEST (длительность параметризована)."""
        targets = []
        if cmd.is_global and cmd.installations:
            targets = cmd.installations
        elif cmd.installation:
            targets = [cmd.installation]

        payload_initial: dict[int, bool] = {}
        if cmd.payload and isinstance(cmd.payload, dict):
            raw_initial = cmd.payload.get("initial_states")
            if isinstance(raw_initial, dict):
                for raw_pin, raw_val in raw_initial.items():
                    try:
                        payload_initial[int(raw_pin)] = bool(raw_val)
                    except (TypeError, ValueError):
                        continue

        pins: list[int] = []
        initial_states: dict[int, bool] = {}
        pin_roles = getattr(self.hardware, "_pin_roles", None)
        for inst in targets:
            light_pin = inst.light_gpio_pin
            flash_pin = inst.flash_gpio_pin
            pins.extend([light_pin, flash_pin])

            if isinstance(pin_roles, dict) and light_pin not in pin_roles:
                pin_roles[light_pin] = "light"
            if (
                isinstance(pin_roles, dict)
                and flash_pin != light_pin
                and flash_pin not in pin_roles
            ):
                pin_roles[flash_pin] = "flash"

            if light_pin in payload_initial:
                initial_states[light_pin] = payload_initial[light_pin]
            else:
                initial_states.setdefault(light_pin, True)

            if flash_pin != light_pin:
                if flash_pin in payload_initial:
                    initial_states[flash_pin] = payload_initial[flash_pin]
                else:
                    initial_states.setdefault(flash_pin, False)

        pins = list(dict.fromkeys(pins))

        if not pins:
            self.client.send_command_result(
                cmd.id,
                CommandResult(
                    status="error",
                    result_message="Нет пинов для тестирования реле",
                ),
            )
            return

        restored = self.hardware.test_relay(
            pins, duration=duration, initial_states=initial_states
        )
        if not isinstance(restored, dict):
            restored = {pin: self.hardware.get_pin_state(pin) for pin in pins}
        self.client.send_command_result(
            cmd.id,
            CommandResult(
                status="done",
                result_message=f"Тест реле успешно завершен для пинов {pins}",
                data={"pin_states": {str(p): bool(v) for p, v in restored.items()}},
                debug_log_line=f"[GPIO] RELAY_TEST done for pins {pins}: {restored}",
            ),
        )

    def _handle_scan_cameras(self, cmd: CommandItem) -> None:
        """Обработка команды SCAN_CAMERAS."""
        scanned = self.hardware.scan_cameras(
            v4l_by_id_dir=self.config.v4l_by_id_dir,
            sys_dir=self.config.sys_dir,
        )
        hw_ids = [c.hardware_id for c in scanned]
        self.client.send_command_result(
            cmd.id,
            CommandResult(
                status="done",
                result_message=f"Обнаружено камер: {len(scanned)}",
                scanned_cameras=scanned,
                debug_log_line=f"[CAM] scan_cameras -> found {len(scanned)}: {hw_ids}",
            ),
        )

    def _handle_autocalibrate_camera(self, cmd: CommandItem) -> None:
        """Обработка команды AUTOCALIBRATE_CAMERA."""
        cam_hw_id = None
        if cmd.payload and isinstance(cmd.payload, dict):
            cam_hw_id = cmd.payload.get("camera_hardware_id")

        if not cam_hw_id and cmd.installation and cmd.installation.camera_hardware_ids:
            cam_hw_id = cmd.installation.camera_hardware_ids[0]

        if not cam_hw_id:
            detected = self.hardware.scan_cameras(
                v4l_by_id_dir=self.config.v4l_by_id_dir,
                sys_dir=self.config.sys_dir,
            )
            if detected:
                cam_hw_id = detected[0].hardware_id

        if not cam_hw_id:
            self.client.send_command_result(
                cmd.id,
                CommandResult(
                    status="error",
                    result_message="Камера для калибровки не найдена или не указана",
                ),
            )
            return

        profile = self.hardware.calibrate_camera(cam_hw_id)
        msg = profile.summary_text()
        self._sync_camera_controls_to_server(
            cam_hw_id,
            current_format=profile.pixel_format,
            current_resolution=f"{profile.max_width}x{profile.max_height}",
        )
        self.client.send_command_result(
            cmd.id,
            CommandResult(
                status="done",
                result_message=msg,
                debug_log_line=f"[CAM] AUTOCALIBRATE_CAMERA {cam_hw_id} -> {profile.summary_text()}",
            ),
        )

    def _resolve_camera_dev_path(self, hw_id: str | None) -> str:
        """Возвращает реальный путь к /dev/videoX для указанного hardware_id."""
        if not hw_id:
            return "/dev/video0"
        resolver = getattr(self.hardware, "resolve_device_path", None)
        if callable(resolver):
            resolved = resolver(hw_id)
            if resolved:
                return resolved
        candidate = self.config.v4l_by_id_dir / hw_id
        if candidate.exists():
            return os.path.realpath(str(candidate))
        return f"/dev/v4l/by-id/{hw_id}"

    @property
    def _use_mock_cameras(self) -> bool:
        return self.config.hardware_mode == "mock" and self.config.mock_cameras

    def _query_and_sync_controls(
        self,
        hw_id: str,
        current_format: str | None = None,
        current_resolution: str | None = None,
        sync_to_server: bool = True,
    ) -> tuple[list[dict], list[dict]]:
        """Опрашивает форматы и V4L2-контролы камеры и при необходимости отправляет их на сервер."""
        from edge_agent.camera_controls import (
            query_camera_controls,
            query_camera_formats_and_sizes,
        )

        is_mock = self._use_mock_cameras
        dev_path = self._resolve_camera_dev_path(hw_id)
        formats = query_camera_formats_and_sizes(dev_path, mock_mode=is_mock)
        controls = query_camera_controls(dev_path, mock_mode=is_mock)
        if sync_to_server and (
            is_mock or formats or controls or current_format or current_resolution
        ):
            self.client.send_camera_controls_state(
                hardware_id=hw_id,
                formats=formats,
                controls=controls,
                current_format=current_format,
                current_resolution=current_resolution,
            )
        return formats, controls

    def _sync_camera_controls_to_server(
        self,
        hw_id: str,
        current_format: str | None = None,
        current_resolution: str | None = None,
    ) -> None:
        """Безопасно синхронизирует форматы и контролы камеры с сервером."""
        try:
            self._query_and_sync_controls(
                hw_id,
                current_format=current_format,
                current_resolution=current_resolution,
                sync_to_server=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"Не удалось синхронизировать контролы {hw_id}: {exc}")

    def _update_live_camera_context(self, hw_id: str, inst: Any = None) -> None:
        """Передаёт в LiveStreamManager данные установки для CV-разметки."""
        setter = getattr(self.live_stream, "set_camera_context", None)
        if not callable(setter):
            return
        target_inst = inst
        if target_inst is None:
            for item in self.active_installations:
                if hw_id in (getattr(item, "camera_hardware_ids", None) or []):
                    target_inst = item
                    break
        if target_inst is not None:
            setter(
                inst_name=getattr(target_inst, "name", None),
                culture_name=getattr(target_inst, "culture_name", None),
                aruco_size_mm=getattr(target_inst, "aruco_size_mm", None),
            )
        else:
            setter()

    def _capture_via_live_snapshot(
        self, inst: Any, cam_hw_id: str, inst_delay: float
    ) -> bytes | None:
        """Пробует захватить кадр через /snapshot работающего ustreamer без паузы стрима."""
        snapshot_fn = getattr(self.live_stream, "capture_ustreamer_snapshot", None)
        if not callable(snapshot_fn):
            return None

        # Быстрая предварительная проверка доступности ustreamer /snapshot до переключения реле
        if getattr(self.live_stream, "process", None) is None:
            return None

        light_pin = inst.light_gpio_pin
        flash_pin = inst.flash_gpio_pin
        same_pins = light_pin == flash_pin
        try:
            if not same_pins:
                self.hardware.set_light(light_pin, False, source="capture_warmup")
                self.hardware.set_flash(flash_pin, True, source="capture_warmup")
            else:
                self.hardware.set_light(light_pin, True, source="capture_warmup")

            if self.config.hardware_mode != "mock":
                switch_pause = (
                    max(0.5, self.config.relay_switch_delay_ms / 1000.0)
                    if not same_pins
                    else 0.5
                )
                total_delay = switch_pause + max(0.0, float(inst_delay))
                if total_delay > 0:
                    time.sleep(total_delay)

            snap = snapshot_fn(cam_hw_id)
            if isinstance(snap, (bytes, bytearray)) and len(snap) > 0:
                return bytes(snap)
            return None
        finally:
            if not same_pins:
                self.hardware.set_flash(flash_pin, False, source="capture_warmup")
            self.hardware.set_light(light_pin, True, source="capture_warmup")

    def _capture_frame_for_camera(
        self, inst: Any, cam_hw_id: str, inst_delay: float, frame_idx: int
    ) -> bytes:
        """Захватывает кадр для камеры: через /snapshot без паузы Live или с паузой в fallback."""
        is_live_for_cam = bool(
            getattr(self.live_stream, "is_running", False)
            and getattr(self.live_stream, "active_camera_id", None) == cam_hw_id
        )
        if is_live_for_cam:
            snap_bytes = self._capture_via_live_snapshot(inst, cam_hw_id, inst_delay)
            if snap_bytes is not None:
                logger.info(
                    f"[SNAP] Кадр {cam_hw_id} получен через ustreamer /snapshot без паузы Live"
                )
                return snap_bytes

            self.live_stream.set_paused(True)
            try:
                return self.hardware.capture_frame_with_sequence(
                    camera_hardware_id=cam_hw_id,
                    light_pin=inst.light_gpio_pin,
                    flash_pin=inst.flash_gpio_pin,
                    warmup_frames=self.config.camera_warmup_frames,
                    switch_delay_ms=self.config.relay_switch_delay_ms,
                    capture_delay_sec=inst_delay,
                    frame_index=frame_idx,
                )
            finally:
                self.live_stream.set_paused(False)

        return self.hardware.capture_frame_with_sequence(
            camera_hardware_id=cam_hw_id,
            light_pin=inst.light_gpio_pin,
            flash_pin=inst.flash_gpio_pin,
            warmup_frames=self.config.camera_warmup_frames,
            switch_delay_ms=self.config.relay_switch_delay_ms,
            capture_delay_sec=inst_delay,
            frame_index=frame_idx,
        )

    @staticmethod
    def _normalize_rgb_tuple(raw_rgb: Any) -> tuple[float, float, float]:
        """Преобразует словарь/кортеж RGB в нормализованный кортеж (r, g, b) в [0.0, 1.0]."""
        if isinstance(raw_rgb, dict):
            vals = (raw_rgb.get("r", 1.0), raw_rgb.get("g", 1.0), raw_rgb.get("b", 1.0))
        elif isinstance(raw_rgb, (tuple, list)) and len(raw_rgb) == 3:
            vals = (raw_rgb[0], raw_rgb[1], raw_rgb[2])
        else:
            return (1.0, 1.0, 1.0)
        res = []
        for v in vals:
            try:
                num = float(v)
            except (TypeError, ValueError):
                num = 1.0
            res.append(round(max(0.0, min(1.0, num)), 4))
        return (res[0], res[1], res[2])

    def _sync_rgb_from_installations(self, installations: list[Any]) -> None:
        """Синхронизирует сохранённые RGB-коэффициенты камер из ответа сервера."""
        for inst in installations:
            cam_rgb_map = getattr(inst, "camera_rgb", None)
            if not isinstance(cam_rgb_map, dict):
                continue
            for hw_id, raw_rgb in cam_rgb_map.items():
                norm = self._normalize_rgb_tuple(raw_rgb)
                if self._last_server_rgb.get(hw_id) != norm:
                    self._last_server_rgb[hw_id] = norm
                    self._camera_rgb[hw_id] = norm
                    if (
                        getattr(self.live_stream, "is_running", False)
                        and getattr(self.live_stream, "active_camera_id", None) == hw_id
                    ):
                        self.live_stream.set_rgb_gains(*norm)

    def _get_camera_rgb(self, inst: Any, cam_hw_id: str) -> tuple[float, float, float]:
        """Возвращает актуальные RGB-коэффициенты для указанной камеры."""
        if (
            getattr(self.live_stream, "is_running", False)
            and getattr(self.live_stream, "active_camera_id", None) == cam_hw_id
        ):
            live_gains = getattr(self.live_stream, "rgb_gains", None)
            if isinstance(live_gains, (tuple, list)) and len(live_gains) == 3:
                return self._normalize_rgb_tuple(live_gains)
        if cam_hw_id in self._camera_rgb:
            return self._camera_rgb[cam_hw_id]
        cam_rgb_map = getattr(inst, "camera_rgb", None)
        if isinstance(cam_rgb_map, dict) and cam_hw_id in cam_rgb_map:
            norm = self._normalize_rgb_tuple(cam_rgb_map[cam_hw_id])
            self._camera_rgb[cam_hw_id] = norm
            return norm
        return (1.0, 1.0, 1.0)

    @staticmethod
    def _normalize_segmentation_mode(raw_mode: Any) -> str:
        """Нормализует режим сегментации к ('auto', 'onnx', 'hsv')."""
        raw = str(raw_mode or "auto").strip().lower()
        return raw if raw in ("auto", "onnx", "hsv") else "auto"

    def _sync_segmentation_modes_from_installations(
        self, installations: list[Any]
    ) -> None:
        """Синхронизирует сохранённые режимы сегментации камер из ответа сервера."""
        for inst in installations:
            modes_map = getattr(inst, "camera_segmentation_modes", None)
            if not isinstance(modes_map, dict):
                continue
            for hw_id, raw_mode in modes_map.items():
                norm = self._normalize_segmentation_mode(raw_mode)
                if self._last_server_segmentation_modes.get(hw_id) != norm:
                    self._last_server_segmentation_modes[hw_id] = norm
                    self._camera_segmentation_modes[hw_id] = norm
                    if (
                        getattr(self.live_stream, "is_running", False)
                        and getattr(self.live_stream, "active_camera_id", None) == hw_id
                        and hasattr(self.live_stream, "set_segmentation_mode")
                    ):
                        self.live_stream.set_segmentation_mode(norm)

    def _get_camera_segmentation_mode(self, inst: Any, cam_hw_id: str) -> str:
        """Возвращает актуальный режим сегментации для указанной камеры."""
        if (
            getattr(self.live_stream, "is_running", False)
            and getattr(self.live_stream, "active_camera_id", None) == cam_hw_id
        ):
            live_mode = getattr(self.live_stream, "segmentation_mode", None)
            if isinstance(live_mode, str) and live_mode in ("auto", "onnx", "hsv"):
                return live_mode
        if cam_hw_id in self._camera_segmentation_modes:
            return self._camera_segmentation_modes[cam_hw_id]
        modes_map = getattr(inst, "camera_segmentation_modes", None)
        if isinstance(modes_map, dict) and cam_hw_id in modes_map:
            norm = self._normalize_segmentation_mode(modes_map[cam_hw_id])
            self._camera_segmentation_modes[cam_hw_id] = norm
            return norm
        return "auto"

    def _prepare_capture_rgb(
        self, inst: Any, cam_hw_id: str, raw_bytes: bytes
    ) -> tuple[bytes, bool]:
        """Применяет программную RGB-коррекцию к кадру до CV-пайплайна и загрузки на сервер."""
        r, g, b = self._get_camera_rgb(inst, cam_hw_id)
        if abs(r - 1.0) < 1e-4 and abs(g - 1.0) < 1e-4 and abs(b - 1.0) < 1e-4:
            return raw_bytes, False
        corrected = apply_rgb_to_jpeg_bytes(raw_bytes, r=r, g=g, b=b)
        return corrected, True

    def _handle_start_live(self, cmd: CommandItem) -> None:
        """Обработка команды START_LIVE."""
        payload = cmd.payload or {}
        hw_id = payload.get("camera_hardware_id")
        w = int(payload.get("width", 1280))
        h = int(payload.get("height", 720))
        fmt = payload.get("pixel_format", "MJPG")
        if not hw_id:
            self.client.send_command_result(
                cmd.id,
                CommandResult(
                    status="error",
                    result_message="Не указан camera_hardware_id",
                ),
            )
            return

        self._update_live_camera_context(hw_id, inst=cmd.installation)
        success = self.live_stream.start(hw_id, width=w, height=h, pixel_format=fmt)

        raw_rgb = payload.get("rgb")
        if isinstance(raw_rgb, dict):
            norm_rgb = self._normalize_rgb_tuple(raw_rgb)
            self._camera_rgb[hw_id] = norm_rgb
            self._last_server_rgb[hw_id] = norm_rgb
            self.live_stream.set_rgb_gains(*norm_rgb)
        elif (
            cmd.installation
            and isinstance(getattr(cmd.installation, "camera_rgb", None), dict)
            and hw_id in cmd.installation.camera_rgb
        ):
            norm_rgb = self._normalize_rgb_tuple(cmd.installation.camera_rgb[hw_id])
            self._camera_rgb[hw_id] = norm_rgb
            self._last_server_rgb[hw_id] = norm_rgb
            self.live_stream.set_rgb_gains(*norm_rgb)
        elif hw_id in self._camera_rgb:
            self.live_stream.set_rgb_gains(*self._camera_rgb[hw_id])

        raw_seg = payload.get("segmentation_mode")
        if raw_seg is not None:
            norm_seg = self._normalize_segmentation_mode(raw_seg)
            self._camera_segmentation_modes[hw_id] = norm_seg
            self._last_server_segmentation_modes[hw_id] = norm_seg
            if hasattr(self.live_stream, "set_segmentation_mode"):
                self.live_stream.set_segmentation_mode(norm_seg)
        elif (
            cmd.installation
            and isinstance(
                getattr(cmd.installation, "camera_segmentation_modes", None), dict
            )
            and hw_id in cmd.installation.camera_segmentation_modes
        ):
            norm_seg = self._normalize_segmentation_mode(
                cmd.installation.camera_segmentation_modes[hw_id]
            )
            self._camera_segmentation_modes[hw_id] = norm_seg
            self._last_server_segmentation_modes[hw_id] = norm_seg
            if hasattr(self.live_stream, "set_segmentation_mode"):
                self.live_stream.set_segmentation_mode(norm_seg)
        elif hw_id in self._camera_segmentation_modes and hasattr(
            self.live_stream, "set_segmentation_mode"
        ):
            self.live_stream.set_segmentation_mode(
                self._camera_segmentation_modes[hw_id]
            )

        st = "done" if success else "error"
        msg = (
            f"Live-стрим запущен для {hw_id}"
            if success
            else "Ошибка запуска Live-стрима"
        )
        data: dict[str, str] | None = None
        stream_url = getattr(self.live_stream, "stream_url", None)
        actual_w = getattr(self.live_stream, "current_width", w)
        actual_h = getattr(self.live_stream, "current_height", h)
        actual_res = (
            f"{actual_w}x{actual_h}"
            if isinstance(actual_w, int) and isinstance(actual_h, int)
            else f"{w}x{h}"
        )
        if success:
            data = {"resolution": actual_res}
            if isinstance(stream_url, str) and stream_url:
                data["stream_url"] = stream_url
            self._sync_camera_controls_to_server(
                hw_id,
                current_format=fmt,
                current_resolution=actual_res,
            )
        self.client.send_command_result(
            cmd.id,
            CommandResult(status=st, result_message=msg, data=data),
        )

    def _handle_stop_live(self, cmd: CommandItem) -> None:
        """Обработка команды STOP_LIVE."""
        self.live_stream.stop()
        self.client.send_command_result(
            cmd.id,
            CommandResult(status="done", result_message="Live-стрим остановлен"),
        )

    def _handle_get_camera_controls(self, cmd: CommandItem) -> None:
        """Обработка команды GET_CAMERA_CONTROLS: опрос реальных форматов и контролов."""
        payload = cmd.payload or {}
        hw_id = payload.get("camera_hardware_id")
        if not hw_id:
            self.client.send_command_result(
                cmd.id,
                CommandResult(
                    status="error",
                    result_message="Не указан camera_hardware_id",
                ),
            )
            return

        formats, controls = self._query_and_sync_controls(hw_id, sync_to_server=True)
        self.client.send_command_result(
            cmd.id,
            CommandResult(
                status="done",
                result_message=f"Получено контролов: {len(controls)}",
                data={"formats": formats, "controls": controls},
            ),
        )

    def _handle_set_camera_controls(self, cmd: CommandItem) -> None:
        """Обработка команды SET_CAMERA_CONTROLS."""
        payload = cmd.payload or {}
        hw_id = payload.get("camera_hardware_id")
        controls = payload.get("controls") or {}
        fmt = payload.get("format")
        res = payload.get("resolution")
        rgb = payload.get("rgb")

        if rgb and isinstance(rgb, dict):
            r = float(rgb.get("r", 1.0))
            g = float(rgb.get("g", 1.0))
            b = float(rgb.get("b", 1.0))
            if hw_id:
                norm_rgb = self._normalize_rgb_tuple({"r": r, "g": g, "b": b})
                self._camera_rgb[hw_id] = norm_rgb
                self._last_server_rgb[hw_id] = norm_rgb
            self.live_stream.set_rgb_gains(r, g, b)

        seg_mode_raw = payload.get("segmentation_mode")
        if seg_mode_raw is not None:
            norm_seg = self._normalize_segmentation_mode(seg_mode_raw)
            if hw_id:
                self._camera_segmentation_modes[hw_id] = norm_seg
                self._last_server_segmentation_modes[hw_id] = norm_seg
            if (
                not hw_id
                or getattr(self.live_stream, "active_camera_id", None) in (None, hw_id)
            ) and hasattr(self.live_stream, "set_segmentation_mode"):
                self.live_stream.set_segmentation_mode(norm_seg)

        from edge_agent.camera_controls import set_v4l2_control

        dev_path = self._resolve_camera_dev_path(hw_id)
        is_mock = self._use_mock_cameras
        for cname, cval in controls.items():
            set_v4l2_control(dev_path, cname, cval, mock_mode=is_mock)

        if (
            (fmt or res)
            and self.live_stream.is_running
            and self.live_stream.active_camera_id == hw_id
        ):
            w, h = self.live_stream.current_width, self.live_stream.current_height
            if res and "x" in res:
                parts = res.split("x")
                w, h = int(parts[0]), int(parts[1])
            cur_fmt = fmt or self.live_stream.current_format
            self.live_stream.start(hw_id, width=w, height=h, pixel_format=cur_fmt)

        if hw_id and not is_mock and (controls or fmt or res):
            # Перечитываем реальные контролы камеры (чтобы обновить flags=inactive для зависимых пар)
            self._sync_camera_controls_to_server(
                hw_id,
                current_format=fmt if isinstance(fmt, str) else None,
                current_resolution=res if isinstance(res, str) else None,
            )

        self.client.send_command_result(
            cmd.id,
            CommandResult(
                status="done",
                result_message="Настройки камеры успешно применены",
            ),
        )

    def _handle_auto_color_calibrate(self, cmd: CommandItem) -> None:
        """Обработка команды AUTO_COLOR_CALIBRATE по исходному кадру без RGB-искажений."""
        payload = cmd.payload or {}
        hw_id = payload.get("camera_hardware_id")
        if (
            not hw_id
            and cmd.installation
            and getattr(cmd.installation, "camera_hardware_ids", None)
        ):
            hw_id = cmd.installation.camera_hardware_ids[0]
        if not hw_id:
            hw_id = getattr(self.live_stream, "active_camera_id", None)

        if not hw_id:
            self.client.send_command_result(
                cmd.id,
                CommandResult(
                    status="error",
                    result_message="Не указан camera_hardware_id",
                ),
            )
            return

        # 1. Получаем свежий исходный кадр с камеры БЕЗ применения предыдущих коэффициентов RGB
        raw_frame: bytes | None = None
        cal_getter = getattr(self.live_stream, "get_raw_frame_for_calibration", None)
        if callable(cal_getter):
            raw_frame = cal_getter(hw_id)

        if not raw_frame:
            is_live_for_cam = bool(
                getattr(self.live_stream, "is_running", False)
                and getattr(self.live_stream, "active_camera_id", None) == hw_id
            )
            inst = cmd.installation
            light_pin = inst.light_gpio_pin if inst else 4
            flash_pin = inst.flash_gpio_pin if inst else 4
            if is_live_for_cam:
                self.live_stream.set_paused(True)
            try:
                raw_frame = self.hardware.capture_frame_with_sequence(
                    camera_hardware_id=hw_id,
                    light_pin=light_pin,
                    flash_pin=flash_pin,
                    warmup_frames=1,
                    switch_delay_ms=0,
                    capture_delay_sec=0.0,
                    frame_index=0,
                )
            finally:
                if is_live_for_cam:
                    self.live_stream.set_paused(False)

        if not raw_frame:
            self.client.send_command_result(
                cmd.id,
                CommandResult(
                    status="error",
                    result_message="Не удалось получить кадр с камеры для калибровки цвета",
                ),
            )
            return

        preview = bool(payload.get("preview", False))
        roi_bytes: bytes | None = None
        detect_with_roi = getattr(self.vision, "detect_color_marker_with_roi", None)
        if callable(detect_with_roi):
            _, gains, roi_bytes = detect_with_roi(raw_frame)
        else:
            gains = self.vision.calibrate_color_from_marker(raw_frame)
        if gains is None:
            self.client.send_command_result(
                cmd.id,
                CommandResult(
                    status="error",
                    result_message="Цветовой маркер не обнаружен в кадре",
                ),
            )
            return

        norm = self._normalize_rgb_tuple(gains)
        if not preview:
            self._camera_rgb[hw_id] = norm
            self._last_server_rgb[hw_id] = norm
            if (
                getattr(self.live_stream, "is_running", False)
                and getattr(self.live_stream, "active_camera_id", None) == hw_id
            ):
                self.live_stream.set_rgb_gains(*norm)

        rgb_dict = {"r": norm[0], "g": norm[1], "b": norm[2]}
        result_data: dict[str, Any] = {
            "rgb": rgb_dict,
            "recommended_rgb": rgb_dict,
            "preview": preview,
        }
        if isinstance(roi_bytes, (bytes, bytearray)) and len(roi_bytes) > 0:
            result_data["roi_image_base64"] = base64.b64encode(bytes(roi_bytes)).decode(
                "ascii"
            )

        msg_prefix = (
            "Предпросмотр автоцветокоррекции" if preview else "Автоцветокоррекция"
        )
        self.client.send_command_result(
            cmd.id,
            CommandResult(
                status="done",
                result_message=(
                    f"{msg_prefix}: R={norm[0]:.2f}, G={norm[1]:.2f}, B={norm[2]:.2f}"
                ),
                data=result_data,
                debug_log_line=(
                    f"[COLOR] AUTO_COLOR_CALIBRATE preview={preview} {hw_id} -> {rgb_dict}"
                ),
            ),
        )

    def _handle_snap(self, cmd: CommandItem) -> None:
        """Обработка команды SNAP (все камеры установки, последовательность реле, загрузка кадра перед result)."""
        snap_start = time.perf_counter()
        targets = []
        if cmd.is_global and cmd.installations:
            targets = cmd.installations
        elif cmd.installation:
            targets = [cmd.installation]

        if not targets:
            self.client.send_command_result(
                cmd.id,
                CommandResult(
                    status="error",
                    result_message="Установка не найдена",
                ),
            )
            return

        success_count = 0
        last_error_message = ""
        debug_log_lines: list[str] = []

        for inst in targets:
            cam_ids = inst.camera_hardware_ids
            if not cam_ids:
                last_error_message = f"У установки #{inst.id} нет привязанных камер"
                continue

            inst_captured_count = 0
            inst_delay = (
                inst.capture_delay_sec
                if getattr(inst, "capture_delay_sec", None) is not None
                else self.global_capture_delay_sec
            )

            # Снимаем ВСЕ привязанные камеры данной установки
            for cam_hw_id in cam_ids:
                try:
                    frame_idx = self._get_and_inc_frame_index(inst.id, cam_hw_id)
                    # 1. Захват кадра (через /snapshot без паузы Live, либо через реле + capture_frame_with_sequence)
                    raw_bytes = self._capture_frame_for_camera(
                        inst, cam_hw_id, inst_delay, frame_idx
                    )
                    prepared_raw_bytes, rgb_applied = self._prepare_capture_rgb(
                        inst, cam_hw_id, raw_bytes
                    )

                    now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                    seg_mode = self._get_camera_segmentation_mode(inst, cam_hw_id)
                    # 2. Компьютерное зрение по скорректированному кадру
                    ann_bytes, aruco_area, measurements = self.vision.process_capture(
                        prepared_raw_bytes,
                        inst_name=getattr(inst, "name", None),
                        culture_name=getattr(inst, "culture_name", None),
                        camera_name=cam_hw_id,
                        captured_at=now_str,
                        aruco_size_mm=getattr(inst, "aruco_size_mm", None),
                        segmentation_mode=seg_mode,
                    )

                    # 3. Метаданные (тестовый кадр не сохраняется в БД/media сервера)
                    meta = CaptureMeta(
                        installation_id=inst.id,
                        camera_hardware_id=cam_hw_id,
                        captured_at=now_str,
                        is_manual=True,
                        is_test=True,
                        aruco_area_px=aruco_area,
                        command_id=cmd.id,
                        rgb_applied=rgb_applied,
                        measurements=measurements,
                    )

                    # 4. Загрузка кадра на сервер строго перед отправкой result
                    status_code, resp_body = self.client.upload_capture(
                        meta,
                        prepared_raw_bytes,
                        ann_bytes,
                    )

                    if status_code in (200, 201):
                        success_count += 1
                        inst_captured_count += 1
                    elif status_code == 0:
                        last_error_message = "Ошибка сети при отправке тестового кадра"
                    else:
                        detail = resp_body.get("detail", f"Код сервера {status_code}")
                        last_error_message = f"Ошибка загрузки кадра: {detail}"

                except Exception as e:  # noqa: BLE001
                    logger.error(
                        f"Ошибка при захвате кадра {cam_hw_id} (установка #{inst.id}): {e}"
                    )
                    last_error_message = str(e)

            debug_log_lines.append(
                f"[GPIO] Inst#{inst.id}: light=0, flash=1 -> captured {inst_captured_count} frames"
            )

        elapsed_sec = time.perf_counter() - snap_start
        logger.info(
            f"[SNAP] Команда #{cmd.id} выполнена за {elapsed_sec:.2f} с "
            f"(успешно: {success_count} кадр., режим: {self.config.hardware_mode})"
        )
        debug_log_lines.append(f"[SNAP] elapsed={elapsed_sec:.2f}s")
        combined_debug_log = "; ".join(debug_log_lines)

        if success_count > 0:
            self.client.send_command_result(
                cmd.id,
                CommandResult(
                    status="done",
                    result_message=f"Снимок выполнен успешно ({success_count} кадр.)",
                    debug_log_line=combined_debug_log,
                ),
            )
        else:
            self.client.send_command_result(
                cmd.id,
                CommandResult(
                    status="error",
                    result_message=last_error_message or "Не удалось выполнить снимок",
                    debug_log_line=combined_debug_log,
                ),
            )

    def trigger_scheduled_capture(self) -> str | None:
        """Запускает разовую плановую автосъемку по всем активным установкам."""
        if not self.active_installations:
            logger.debug("Нет активных установок для плановой съемки")
            return None

        logger.info(
            f"Старт плановой автосъемки для {len(self.active_installations)} установок"
        )
        self.is_scheduled_capture_running.set()
        self.hardware.acquire_lock(blocking=True)
        total_captured = 0

        try:
            for inst in self.active_installations:
                inst_delay = (
                    inst.capture_delay_sec
                    if getattr(inst, "capture_delay_sec", None) is not None
                    else self.global_capture_delay_sec
                )
                for cam_hw_id in inst.camera_hardware_ids:
                    try:
                        frame_idx = self._get_and_inc_frame_index(inst.id, cam_hw_id)
                        raw_bytes = self._capture_frame_for_camera(
                            inst, cam_hw_id, inst_delay, frame_idx
                        )
                        prepared_raw_bytes, rgb_applied = self._prepare_capture_rgb(
                            inst, cam_hw_id, raw_bytes
                        )
                        now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                        seg_mode = self._get_camera_segmentation_mode(inst, cam_hw_id)
                        ann_bytes, aruco_area, measurements = (
                            self.vision.process_capture(
                                prepared_raw_bytes,
                                inst_name=getattr(inst, "name", None),
                                culture_name=getattr(inst, "culture_name", None),
                                camera_name=cam_hw_id,
                                captured_at=now_str,
                                aruco_size_mm=getattr(inst, "aruco_size_mm", None),
                                segmentation_mode=seg_mode,
                            )
                        )

                        meta = CaptureMeta(
                            installation_id=inst.id,
                            camera_hardware_id=cam_hw_id,
                            captured_at=now_str,
                            is_manual=False,
                            aruco_area_px=aruco_area,
                            command_id=None,
                            rgb_applied=rgb_applied,
                            measurements=measurements,
                        )

                        status_code, _ = self.client.upload_capture(
                            meta,
                            prepared_raw_bytes,
                            ann_bytes,
                        )

                        if status_code in (200, 201):
                            total_captured += 1
                        else:
                            logger.warning(
                                f"Плановый кадр для установки {inst.id} не загружен ({status_code}). Сохранение в очередь."
                            )
                            save_to_queue(
                                self.config.offline_queue_dir,
                                meta,
                                prepared_raw_bytes,
                                ann_bytes,
                            )
                    except Exception as exc:  # noqa: BLE001
                        logger.error(
                            f"Сбой при плановой съемке установки {inst.id}: {exc}"
                        )
        finally:
            self.hardware.release_lock()
            self.is_scheduled_capture_running.clear()

        debug_lines = []
        if self.config.test_capture_interval_sec is not None:
            debug_lines.append(
                f"[TEST_INTERVAL={self.config.test_capture_interval_sec}s]"
            )
        debug_lines.append(
            f"Scheduled capture completed for {len(self.active_installations)} installations ({total_captured} frames)"
        )
        debug_log = " ".join(debug_lines)
        self.last_scheduled_debug_log_line = debug_log
        logger.info(f"Плановая автосъемка завершена: {debug_log}")

        self.trigger_async_flush()
        return debug_log

    def _scheduler_loop(self) -> None:
        """Фоновый цикл плановой автосъемки: по тестовому интервалу в секундах или по часовой сетке UTC."""
        if self.config.test_capture_interval_sec is not None:
            interval_sec = float(self.config.test_capture_interval_sec)
            last_run = time.time()
            while self.running.is_set():
                time.sleep(0.5)
                if not self.running.is_set():
                    break
                now_t = time.time()
                if now_t - last_run >= interval_sec:
                    last_run = now_t
                    try:
                        self.trigger_scheduled_capture()
                    except Exception as exc:  # noqa: BLE001
                        logger.error(
                            f"Исключение в автосъемке по тестовому интервалу: {exc}"
                        )
            return

        while self.running.is_set():
            time.sleep(1.0)
            if not self.running.is_set():
                break

            now = datetime.now(UTC)
            n_hours = max(1, self.capture_interval_hours)

            # Срабатывание в начале каждого N-го часа UTC (HH % N == 0, MM == 0)
            if now.hour % n_hours == 0 and now.minute == 0:
                current_slot = (now.year, now.month, now.day, now.hour)
                if self._last_scheduled_hour != current_slot:
                    self._last_scheduled_hour = current_slot
                    try:
                        self.trigger_scheduled_capture()
                    except Exception as exc:  # noqa: BLE001
                        logger.error(f"Исключение в автосъемке по расписанию: {exc}")
