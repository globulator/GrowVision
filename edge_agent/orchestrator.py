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
        )
        self.hardware = hardware or HardwareManager(mode=config.hardware_mode)
        model_file = config.models_dir / config.model_name
        self.vision = vision or VisionPipeline(
            model_path=model_file,
            mock_mode=(config.hardware_mode == "mock"),
        )

        self.running = threading.Event()
        self.is_scheduled_capture_running = threading.Event()

        # Динамические настройки, получаемые от сервера
        self.capture_interval_hours = config.capture_interval_hours
        self.debug_mode = config.debug_mode
        self.active_installations: list[HeartbeatActiveInstallation] = []

        self._heartbeat_thread: threading.Thread | None = None
        self._command_thread: threading.Thread | None = None
        self._scheduler_thread: threading.Thread | None = None

        self._last_scheduled_capture_time = 0.0

    def start(self) -> None:
        """Запускает фоновые потоки оркестратора."""
        logger.info("Запуск Edge-агента GrowVision...")
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

    def perform_heartbeat(self) -> None:
        """Выполняет одну итерацию отправки Heartbeat и применения конфигурации."""
        software_info = HeartbeatSoftware(
            edge_version=get_app_version(),
            python_version=platform.python_version(),
            model_name=self.config.model_name,
        )
        hardware_info = get_hardware_telemetry(gpio_mode=self.config.hardware_mode)
        queue_count = get_queue_count(self.config.offline_queue_dir)
        detected_cams = self.hardware.scan_cameras()

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
            logger.debug(
                f"Конфигурация обновлена: интервал={self.capture_interval_hours}ч, "
                f"установок={len(self.active_installations)}"
            )

            # Если в очереди есть неотправленные пакеты, пробуем выгрузить
            if queue_count > 0:
                sent, skipped, failed = flush_queue(
                    self.config.offline_queue_dir,
                    self.client.upload_capture,
                )
                if sent > 0 or failed > 0:
                    logger.info(
                        f"Сброс офлайн-очереди: отправлено={sent}, оставлено={skipped}, ошибок={failed}"
                    )

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
            time.sleep(self.config.claim_poll_interval_sec)
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

        # Проверка приоритета плановой автосъемки
        if self.is_scheduled_capture_running.is_set():
            logger.warning(
                f"Оборудование занято плановой съемкой. Отклонение команды #{cmd.id}"
            )
            self.client.send_command_result(
                cmd.id,
                CommandResult(
                    status="error",
                    result_message="Камера недоступна, попробуйте позже",
                ),
            )
            return

        # Попытка захватить аппаратный мьютекс
        acquired = self.hardware.acquire_lock(blocking=False)
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

    def _handle_relay_test(self, cmd: CommandItem) -> None:
        """Обработка команды RELAY_TEST (3 секунды)."""
        targets = []
        if cmd.is_global and cmd.installations:
            targets = cmd.installations
        elif cmd.installation:
            targets = [cmd.installation]

        pins: list[int] = []
        for inst in targets:
            pins.extend([inst.light_gpio_pin, inst.flash_gpio_pin])

        if not pins:
            self.client.send_command_result(
                cmd.id,
                CommandResult(
                    status="error",
                    result_message="Нет пинов для тестирования реле",
                ),
            )
            return

        self.hardware.test_relay(pins, duration=3.0)
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
        scanned = self.hardware.scan_cameras()
        self.client.send_command_result(
            cmd.id,
            CommandResult(
                status="done",
                result_message=f"Обнаружено камер: {len(scanned)}",
                scanned_cameras=scanned,
            ),
        )

    def _handle_snap(self, cmd: CommandItem) -> None:
        """Обработка команды SNAP."""
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

        for inst in targets:
            cam_ids = inst.camera_hardware_ids
            if not cam_ids:
                last_error_message = f"У установки #{inst.id} нет привязанных камер"
                continue

            cam_hw_id = cam_ids[0]
            try:
                # 1. Захват кадра с использованием вспышки
                raw_bytes = self.hardware.capture_frame(
                    cam_hw_id,
                    flash_pin=inst.flash_gpio_pin,
                )

                # 2. Компьютерное зрение: детекция маркера и сегментация
                ann_bytes, aruco_area, measurements = self.vision.process_capture(
                    raw_bytes
                )

                # 3. Формирование метаданных
                now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                meta = CaptureMeta(
                    installation_id=inst.id,
                    camera_hardware_id=cam_hw_id,
                    captured_at=now_str,
                    is_manual=True,
                    aruco_area_px=aruco_area,
                    command_id=cmd.id,
                    measurements=measurements,
                )

                # 4. Загрузка кадра на сервер (СТРОГО перед отправкой result)
                status_code, resp_body = self.client.upload_capture(
                    meta,
                    raw_bytes,
                    ann_bytes,
                )

                if status_code in (200, 201):
                    success_count += 1
                elif status_code == 0:
                    # Сетевая ошибка — сохраняем в офлайн-очередь
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
                logger.error(f"Ошибка при захвате кадра установки #{inst.id}: {e}")
                last_error_message = str(e)

        if success_count > 0:
            self.client.send_command_result(
                cmd.id,
                CommandResult(
                    status="done",
                    result_message=f"Снимок выполнен успешно ({success_count} кадр.)",
                ),
            )
        else:
            self.client.send_command_result(
                cmd.id,
                CommandResult(
                    status="error",
                    result_message=last_error_message or "Не удалось выполнить снимок",
                ),
            )

    def trigger_scheduled_capture(self) -> None:
        """Запускает разовую плановую автосъемку по всем активным установкам."""
        if not self.active_installations:
            logger.debug("Нет активных установок для плановой съемки")
            return

        logger.info(
            f"Старт плановой автосъемки для {len(self.active_installations)} установок"
        )
        self.is_scheduled_capture_running.set()
        self.hardware.acquire_lock(blocking=True)

        try:
            for inst in self.active_installations:
                for cam_hw_id in inst.camera_hardware_ids:
                    try:
                        raw_bytes = self.hardware.capture_frame(
                            cam_hw_id,
                            flash_pin=inst.flash_gpio_pin,
                        )
                        ann_bytes, aruco_area, measurements = (
                            self.vision.process_capture(raw_bytes)
                        )

                        now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
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

                        if status_code not in (200, 201):
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
            self._last_scheduled_capture_time = time.time()
            logger.info("Плановая автосъемка завершена")

            # Пробуем выгрузить очередь при наличии связи
            flush_queue(self.config.offline_queue_dir, self.client.upload_capture)

    def _scheduler_loop(self) -> None:
        """Фоновый цикл проверки расписания плановой автосъемки."""
        self._last_scheduled_capture_time = time.time()

        while self.running.is_set():
            time.sleep(5.0)
            if not self.running.is_set():
                break

            interval_sec = max(60, self.capture_interval_hours * 3600)
            elapsed = time.time() - self._last_scheduled_capture_time

            if elapsed >= interval_sec:
                try:
                    self.trigger_scheduled_capture()
                except Exception as exc:  # noqa: BLE001
                    logger.error(f"Исключение в автосъемке по расписанию: {exc}")
