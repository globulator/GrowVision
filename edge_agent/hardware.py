import contextlib
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

    def __init__(self, mode: str = "mock"):
        self.mode = mode.lower()
        self.lock = threading.Lock()
        self._gpio_devices: dict[int, Any] = {}
        self._mock_pins: dict[int, bool] = {}
        self._gpio_available = False

        if self.mode == "rpi":
            try:
                import gpiozero  # noqa: F401

                self._gpio_available = True
                logger.info("Инициализирован аппаратный режим RPi (gpiozero доступен)")
            except ImportError:
                logger.warning(
                    "gpiozero не найден. Переключение в fallback mock для GPIO."
                )
                self.mode = "mock"

    def acquire_lock(self, blocking: bool = True, timeout: float = -1.0) -> bool:
        """Захватывает аппаратный мьютекс."""
        return self.lock.acquire(blocking=blocking, timeout=timeout)

    def release_lock(self) -> None:
        """Освобождает аппаратный мьютекс."""
        if self.lock.locked():
            self.lock.release()

    def get_pin_state(self, pin: int) -> bool:
        """Возвращает текущее состояние пина (ВКЛ / ВЫКЛ)."""
        if self.mode == "rpi" and self._gpio_available:
            dev = self._get_gpio_device(pin)
            return bool(dev.value) if dev else False
        return self._mock_pins.get(pin, False)

    def _get_gpio_device(self, pin: int) -> Any:
        """Получает или создает объект OutputDevice для указанного пина."""
        if pin not in self._gpio_devices:
            try:
                from gpiozero import OutputDevice

                self._gpio_devices[pin] = OutputDevice(
                    pin, active_high=True, initial_value=False
                )
            except Exception as e:  # noqa: BLE001
                logger.error(f"Ошибка инициализации GPIO пина {pin}: {e}")
                return None
        return self._gpio_devices[pin]

    def set_pin(self, pin: int, state: bool) -> None:
        """Устанавливает логическое состояние пина."""
        logger.debug(
            f"[GPIO] Pin {pin} -> {'ON' if state else 'OFF'} (mode={self.mode})"
        )
        if self.mode == "rpi" and self._gpio_available:
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
            time.sleep(duration)
        finally:
            for pin in pins:
                self.set_pin(pin, False)
            logger.info(f"[RELAY_TEST] Пины {pins} выключены")

    def scan_cameras(self) -> list[DetectedCamera]:
        """Сканирует подключенные камеры."""
        if self.mode == "mock":
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

        # Сканирование в режиме RPi через sysfs / video4linux
        cameras: list[DetectedCamera] = []
        v4l_dir = Path("/sys/class/video4linux")
        if v4l_dir.exists():
            for dev_path in sorted(v4l_dir.glob("video*")):
                try:
                    name_file = dev_path / "name"
                    dev_num = dev_path.name
                    camera_name = (
                        name_file.read_text(encoding="utf-8").strip()
                        if name_file.exists()
                        else f"Camera {dev_num}"
                    )

                    # Формируем стабильный hardware_id
                    # Проверяем наличие uevent или device symlink
                    device_link = dev_path / "device"
                    hw_id = f"v4l2-{dev_num}"
                    if device_link.exists():
                        with contextlib.suppress(Exception):
                            resolved = device_link.resolve().name
                            hw_id = f"v4l2-{resolved}-{dev_num}"

                    cameras.append(
                        DetectedCamera(hardware_id=hw_id, auto_name=camera_name)
                    )
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"Ошибка при сканировании {dev_path}: {e}")

        if not cameras:
            # Fallback на проверку /dev/video*
            for dev in sorted(Path("/dev").glob("video*")):
                cameras.append(
                    DetectedCamera(
                        hardware_id=f"dev-{dev.name}",
                        auto_name=f"V4L2 {dev.name}",
                    )
                )

        return cameras

    def capture_frame(
        self,
        camera_hardware_id: str,
        flash_pin: int | None = None,
    ) -> bytes:
        """Захватывает кадр с камеры со вспышкой (если пин вспышки передан)."""
        if flash_pin is not None:
            self.set_flash(flash_pin, True)

        try:
            if self.mode == "mock":
                # В mock-режиме возвращаем тестовый JPEG
                return MOCK_JPEG_FRAME

            # В режиме RPi пробуем захват через OpenCV
            try:
                import cv2  # type: ignore

                # Определяем индекс видеоустройства
                dev_idx = 0
                if "video" in camera_hardware_id:
                    digits = [
                        c for c in camera_hardware_id.split("video")[-1] if c.isdigit()
                    ]
                    if digits:
                        dev_idx = int("".join(digits))

                cap = cv2.VideoCapture(dev_idx)
                if not cap.isOpened():
                    raise RuntimeError(
                        f"Не удалось открыть камеру с индексом {dev_idx}"
                    )

                ret, frame = cap.read()
                cap.release()

                if not ret or frame is None:
                    raise RuntimeError("Не удалось прочитать кадр с камеры")

                success, enc_frame = cv2.imencode(".jpg", frame)
                if not success:
                    raise RuntimeError("Ошибка кодирования кадра в JPEG")
                return enc_frame.tobytes()

            except ImportError:
                logger.warning("OpenCV не установлен в режиме RPi, возврат mock кадра")
                return MOCK_JPEG_FRAME

        finally:
            if flash_pin is not None:
                self.set_flash(flash_pin, False)

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
    uptime_sec = 0
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
