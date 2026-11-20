import contextlib
import os
import platform
import shutil
import threading
import time
from pathlib import Path
from typing import Any

import psutil

from shared.logger import get_logger
from shared.schemas import DetectedCamera, HeartbeatHardware

logger = get_logger("edge_agent.hardware")

BOOT_TIME = time.time()

# Минимальный валидный JPEG (1x1 пиксель) для тестов и mock-режима без cv2/PIL
MOCK_JPEG_FRAME = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00"
    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
    b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
    b"\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342"
    b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
    b"\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b"
    b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x80\xff\xd9"
)


class HardwareManager:
    """Менеджер аппаратного обеспечения (GPIO, камеры, телеметрия)."""

    def __init__(
        self,
        mode: str = "mock",
        relay_active_high: bool = True,
        v4l_by_id_dir: Path | None = None,
        sys_dir: Path | None = None,
    ):
        self.mode = mode.lower()
        if self.mode not in ("rpi", "mock"):
            raise ValueError(
                f"Недопустимый режим HARDWARE_MODE='{self.mode}'. Допустимы только 'rpi' или 'mock'."
            )

        self.relay_active_high = relay_active_high
        self.v4l_by_id_dir = v4l_by_id_dir or Path("/dev/v4l/by-id")
        self.sys_dir = sys_dir or Path("/sys")

        self.lock = threading.Lock()
        self._gpio_devices: dict[int, Any] = {}
        self._mock_pins: dict[int, bool] = {}

        if self.mode == "rpi":
            try:
                import gpiozero  # noqa: F401

                logger.info("Инициализирован аппаратный режим RPi (gpiozero доступен)")
            except ImportError as exc:
                # Строго без тихого отката в mock!
                raise RuntimeError(
                    "В режиме HARDWARE_MODE=rpi необходима библиотека gpiozero (python3-gpiozero / python3-lgpio)."
                ) from exc

    def acquire_lock(self, blocking: bool = True, timeout: float = -1.0) -> bool:
        """Захватывает аппаратный мьютекс."""
        return self.lock.acquire(blocking=blocking, timeout=timeout)

    def release_lock(self) -> None:
        """Освобождает аппаратный мьютекс."""
        if self.lock.locked():
            self.lock.release()

    def get_pin_state(self, pin: int) -> bool:
        """Возвращает логическое состояние пина (True = включено, False = выключено)."""
        if self.mode == "rpi":
            dev = self._get_gpio_device(pin)
            return bool(dev.value) if dev else False
        return self._mock_pins.get(pin, False)

    def _get_gpio_device(self, pin: int) -> Any:
        """Получает или создает объект OutputDevice для указанного пина с учетом полярности."""
        if pin not in self._gpio_devices:
            try:
                from gpiozero import OutputDevice

                self._gpio_devices[pin] = OutputDevice(
                    pin,
                    active_high=self.relay_active_high,
                    initial_value=False,
                )
            except Exception as e:
                logger.error(f"Ошибка инициализации GPIO пина {pin}: {e}")
                raise
        return self._gpio_devices[pin]

    def set_pin(self, pin: int, state: bool) -> None:
        """Устанавливает логическое состояние пина (True = ВКЛ, False = ВЫКЛ)."""
        logger.debug(
            f"[GPIO] Pin {pin} -> {'ON' if state else 'OFF'} (active_high={self.relay_active_high}, mode={self.mode})"
        )
        if self.mode == "rpi":
            dev = self._get_gpio_device(pin)
            if dev:
                if state:
                    dev.on()
                else:
                    dev.off()
        else:
            self._mock_pins[pin] = state

    def set_light(self, pin: int, state: bool) -> None:
        """Управляет основным светом."""
        self.set_pin(pin, state)

    def set_flash(self, pin: int, state: bool) -> None:
        """Управляет вспышкой."""
        self.set_pin(pin, state)

    def test_relay(self, pins: list[int], duration: float = 3.0) -> None:
        """Кратковременный тест реле на указанных пинах."""
        logger.info(f"[RELAY_TEST] Включение пинов {pins} на {duration} с")
        for pin in pins:
            self.set_pin(pin, True)
        try:
            if duration > 0:
                time.sleep(duration)
        finally:
            for pin in pins:
                self.set_pin(pin, False)
            logger.info(f"[RELAY_TEST] Пины {pins} выключены")

    def scan_cameras(
        self,
        v4l_by_id_dir: Path | None = None,
        sys_dir: Path | None = None,
    ) -> list[DetectedCamera]:
        """Сканирует подключенные камеры через /dev/v4l/by-id (*-video-index0)."""
        search_dir = v4l_by_id_dir or self.v4l_by_id_dir

        # В mock-режиме при системном пути по умолчанию всегда отдаем детерминированные моки
        if self.mode == "mock" and (
            search_dir == Path("/dev/v4l/by-id")
            or not search_dir.exists()
            or not list(search_dir.glob("*-video-index0"))
        ):
            return [
                DetectedCamera(
                    hardware_id="mock-usb-cam-1",
                    auto_name="Mock USB Camera 1",
                ),
                DetectedCamera(
                    hardware_id="mock-csi-cam-2",
                    auto_name="Mock CSI Camera 2",
                ),
            ]

        cameras: list[DetectedCamera] = []
        if search_dir.exists():
            for cam_file in sorted(search_dir.glob("*-video-index0")):
                hw_id = cam_file.name
                # Формируем автоимя из hardware_id
                clean_name = (
                    hw_id.replace("usb-", "")
                    .replace("-video-index0", "")
                    .replace("_", " ")
                )
                cameras.append(
                    DetectedCamera(
                        hardware_id=hw_id,
                        auto_name=clean_name,
                    )
                )

        return cameras

    def capture_frame(
        self,
        camera_hardware_id: str,
        warmup_frames: int = 5,
    ) -> bytes:
        """Захватывает сырой кадр с указанной камеры (с прогревом)."""
        if self.mode == "mock":
            return MOCK_JPEG_FRAME

        # В режиме RPi строгая проверка OpenCV
        try:
            import cv2  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "В режиме HARDWARE_MODE=rpi необходима библиотека opencv-python-headless для захвата кадров."
            ) from exc

        cam_by_id_file = self.v4l_by_id_dir / camera_hardware_id
        if not cam_by_id_file.exists():
            raise RuntimeError(
                f"Камера с hardware_id '{camera_hardware_id}' не найдена в {self.v4l_by_id_dir}. "
                "Откат на дефолтные устройства запрещен."
            )

        real_dev_path = os.path.realpath(str(cam_by_id_file))
        logger.debug(f"Открытие камеры {camera_hardware_id} по пути {real_dev_path}")

        cap = cv2.VideoCapture(real_dev_path)
        if not cap.isOpened():
            raise RuntimeError(
                f"Не удалось открыть видеоустройство {real_dev_path} ({camera_hardware_id})"
            )

        try:
            # Прогрев камеры: считываем и отбрасываем первые N кадров
            for _ in range(max(0, warmup_frames)):
                cap.read()

            ret, frame = cap.read()
            if not ret or frame is None:
                raise RuntimeError(
                    f"Не удалось прочитать целевой кадр с камеры {camera_hardware_id}"
                )

            success, enc_frame = cv2.imencode(".jpg", frame)
            if not success:
                raise RuntimeError("Ошибка кодирования кадра в JPEG")
            return enc_frame.tobytes()
        finally:
            cap.release()

    def capture_frame_with_sequence(
        self,
        camera_hardware_id: str,
        light_pin: int,
        flash_pin: int,
        warmup_frames: int = 5,
        switch_delay_ms: int = 200,
    ) -> bytes:
        """Последовательность съемки кадра со вспышкой:

        1. Выключение основного света (light=0).
        2. Включение вспышки (flash=1).
        3. Задержка стабилизации света (switch_delay_ms).
        4. Захват кадра с прогревом (warmup_frames).
        5. Гарантированный возврат в исходное состояние (light=1, flash=0) в finally.
        """
        try:
            self.set_light(light_pin, False)
            self.set_flash(flash_pin, True)

            if switch_delay_ms > 0:
                time.sleep(switch_delay_ms / 1000.0)

            return self.capture_frame(camera_hardware_id, warmup_frames=warmup_frames)
        finally:
            self.set_flash(flash_pin, False)
            self.set_light(light_pin, True)

    def cleanup(self) -> None:
        """Очистка используемых ресурсов GPIO."""
        for dev in self._gpio_devices.values():
            with contextlib.suppress(Exception):
                dev.close()
        self._gpio_devices.clear()
        self._mock_pins.clear()


def get_hardware_telemetry(gpio_mode: str = "mock") -> HeartbeatHardware:
    """Сбор аппаратной телеметрии хоста."""
    # 1. Модель платы
    board_model = "Raspberry Pi 5 Model B Rev 1.0 (Mock)"
    model_path = Path("/proc/device-tree/model")
    if model_path.exists():
        with contextlib.suppress(Exception):
            raw_model = model_path.read_text(encoding="utf-8").strip("\x00\n ")
            if raw_model:
                board_model = raw_model
    elif gpio_mode == "rpi":
        board_model = f"Linux ({platform.machine()})"

    # 2. ОС
    os_info = f"{platform.system()} {platform.release()} ({platform.machine()})"

    # 3. Uptime
    uptime_path = Path("/proc/uptime")
    if uptime_path.exists():
        try:
            uptime_sec = int(float(uptime_path.read_text().split()[0]))
        except Exception:  # noqa: BLE001
            uptime_sec = int(time.time() - BOOT_TIME)
    else:
        uptime_sec = int(time.time() - BOOT_TIME)

    # 4. Температура процессора
    cpu_temp_c = 42.0
    temp_path = Path("/sys/class/thermal/thermal_zone0/temp")
    if temp_path.exists():
        with contextlib.suppress(Exception):
            raw_temp = float(temp_path.read_text().strip())
            cpu_temp_c = round(raw_temp / 1000.0, 1)

    # 5. Использование CPU (%)
    try:
        cpu_usage_percent = float(psutil.cpu_percent(interval=None))
    except Exception:  # noqa: BLE001
        cpu_usage_percent = 5.0

    # 6. RAM
    try:
        vm = psutil.virtual_memory()
        ram_used_mb = int(vm.used / (1024 * 1024))
        ram_total_mb = int(vm.total / (1024 * 1024))
    except Exception:  # noqa: BLE001
        ram_used_mb = 512
        ram_total_mb = 4096

    # 7. Диск (свободное место на корневом разделе в ГБ)
    try:
        disk = shutil.disk_usage("/")
        disk_free_gb = round(disk.free / (1024**3), 1)
    except Exception:  # noqa: BLE001
        disk_free_gb = 10.0

    return HeartbeatHardware(
        board_model=board_model,
        os_info=os_info,
        uptime_sec=uptime_sec,
        cpu_temp_c=cpu_temp_c,
        cpu_usage_percent=cpu_usage_percent,
        ram_used_mb=ram_used_mb,
        ram_total_mb=ram_total_mb,
        disk_free_gb=disk_free_gb,
        gpio_mode=gpio_mode,
    )
