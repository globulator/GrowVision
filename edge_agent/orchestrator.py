import platform
import threading
import time
from datetime import UTC, datetime

from edge_agent.client import EdgeAPIClient
from edge_agent.config import EdgeConfig
from edge_agent.hardware import HardwareManager, get_hardware_telemetry
from edge_agent.offline_queue import flush_queue, get_queue_count, save_to_queue
from edge_agent.vision import VisionPipeline
from shared.logger import get_logger
from shared.schemas import (
    CaptureMeta,
    CommandItem,
    CommandResult,
    HeartbeatActiveInstallation,
    HeartbeatRequest,
    HeartbeatSoftware,
)
from shared.version import get_app_version

logger = get_logger("edge_agent.orchestrator")


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
        )
        self.vision = vision or VisionPipeline(
            model_path=config.model_path,
            mock_mode=(config.hardware_mode == "mock"),
        )

        self.running = threading.Event()
        self.is_scheduled_capture_running = threading.Event()
        self._flush_lock = threading.Lock()

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

    def start(self) -> None:
        """Запускает фоновые потоки оркестратора."""
        logger.info("Запуск Edge-агента GrowVision...")
        if self.config.test_capture_interval_sec is not None:
            logger.warning(
                f"ТЕСТОВЫЙ ИНТЕРВАЛ СЪЁМКИ: {self.config.test_capture_interval_sec} с"
            )
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
        """Останавливает оркестратор и фоновые потоки."""
        logger.info("Остановка Edge-агента...")
        self.running.clear()
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=2.0)
        if self._command_thread and self._command_thread.is_alive():
            self._command_thread.join(timeout=2.0)
        if self._scheduler_thread and self._scheduler_thread.is_alive():
            self._scheduler_thread.join(timeout=2.0)
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
                self.hardware.set_light(inst.light_gpio_pin, True)
                self.hardware.set_flash(inst.flash_gpio_pin, False)
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
        hardware_info = get_hardware_telemetry(gpio_mode=self.config.hardware_mode)
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
        while self.running.is_set():
            time.sleep(self.config.command_poll_interval_sec)
            if not self.running.is_set():
                break
            try:
                commands = self.client.claim_command()
                for cmd in commands:
                    self.handle_command(cmd)
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
            elif cmd_type == "SNAP":
                self._handle_snap(cmd)
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
                self.hardware.set_flash(pin, state)
            else:
                self.hardware.set_light(pin, state)

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

        pins: list[int] = []
        for inst in targets:
            pins.extend([inst.light_gpio_pin, inst.flash_gpio_pin])
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

        self.hardware.test_relay(pins, duration=duration)
        self.client.send_command_result(
            cmd.id,
            CommandResult(
                status="done",
                result_message=f"Тест реле успешно завершен для пинов {pins}",
                debug_log_line=f"[GPIO] RELAY_TEST done for pins {pins}",
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

    def _handle_snap(self, cmd: CommandItem) -> None:
        """Обработка команды SNAP (все камеры установки, последовательность реле, загрузка кадра перед result)."""
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
                    # 1. Захват кадра с последовательностью переключения реле (light=0, flash=1, delay, capture, light=1, flash=0)
                    raw_bytes = self.hardware.capture_frame_with_sequence(
                        camera_hardware_id=cam_hw_id,
                        light_pin=inst.light_gpio_pin,
                        flash_pin=inst.flash_gpio_pin,
                        warmup_frames=self.config.camera_warmup_frames,
                        switch_delay_ms=self.config.relay_switch_delay_ms,
                        capture_delay_sec=inst_delay,
                        frame_index=frame_idx,
                    )

                    now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                    # 2. Компьютерное зрение
                    ann_bytes, aruco_area, measurements = self.vision.process_capture(
                        raw_bytes,
                        inst_name=getattr(inst, "name", None),
                        culture_name=getattr(inst, "culture_name", None),
                        camera_name=cam_hw_id,
                        captured_at=now_str,
                        aruco_size_mm=getattr(inst, "aruco_size_mm", None),
                    )

                    # 3. Метаданные
                    meta = CaptureMeta(
                        installation_id=inst.id,
                        camera_hardware_id=cam_hw_id,
                        captured_at=now_str,
                        is_manual=True,
                        aruco_area_px=aruco_area,
                        command_id=cmd.id,
                        measurements=measurements,
                    )

                    # 4. Загрузка кадра на сервер строго перед отправкой result
                    status_code, resp_body = self.client.upload_capture(
                        meta,
                        raw_bytes,
                        ann_bytes,
                    )

                    if status_code in (200, 201):
                        success_count += 1
                        inst_captured_count += 1
                    elif status_code == 0:
                        save_to_queue(
                            self.config.offline_queue_dir, meta, raw_bytes, ann_bytes
                        )
                        last_error_message = (
                            "Ошибка сети при отправке кадра (кадр сохранен локально)"
                        )
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
                        raw_bytes = self.hardware.capture_frame_with_sequence(
                            camera_hardware_id=cam_hw_id,
                            light_pin=inst.light_gpio_pin,
                            flash_pin=inst.flash_gpio_pin,
                            warmup_frames=self.config.camera_warmup_frames,
                            switch_delay_ms=self.config.relay_switch_delay_ms,
                            capture_delay_sec=inst_delay,
                            frame_index=frame_idx,
                        )
                        now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                        ann_bytes, aruco_area, measurements = (
                            self.vision.process_capture(
                                raw_bytes,
                                inst_name=getattr(inst, "name", None),
                                culture_name=getattr(inst, "culture_name", None),
                                camera_name=cam_hw_id,
                                captured_at=now_str,
                                aruco_size_mm=getattr(inst, "aruco_size_mm", None),
                            )
                        )

                        meta = CaptureMeta(
                            installation_id=inst.id,
                            camera_hardware_id=cam_hw_id,
                            captured_at=now_str,
                            is_manual=False,
                            aruco_area_px=aruco_area,
                            command_id=None,
                            measurements=measurements,
                        )

                        status_code, _ = self.client.upload_capture(
                            meta,
                            raw_bytes,
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
                                raw_bytes,
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
