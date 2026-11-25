import contextlib
import os
import platform
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
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


@dataclass
class CameraProfile:
    """Аппаратный профиль возможностей видеокамеры."""

    hardware_id: str
    max_width: int = 1920
    max_height: int = 1080
    pixel_format: str = "MJPG"
    has_autofocus: bool = False
    formats_summary: list[dict[str, Any]] = field(default_factory=list)

    def summary_text(self) -> str:
        af_text = "поддерживается" if self.has_autofocus else "нет"
        return f"{self.max_width}x{self.max_height} ({self.pixel_format}), автофокус: {af_text}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "hardware_id": self.hardware_id,
            "max_width": self.max_width,
            "max_height": self.max_height,
            "pixel_format": self.pixel_format,
            "has_autofocus": self.has_autofocus,
            "summary": self.summary_text(),
            "formats_summary": self.formats_summary,
        }


def probe_camera_capabilities(
    dev_path: str,
    hardware_id: str = "",
) -> CameraProfile:
    """Опрашивает возможности камеры через v4l2-ctl (--list-formats-ext, --all).

    Безопасный фолбэк на дефолты (1920x1080, MJPG) при любых ошибках.
    """
    max_width = 1920
    max_height = 1080
    pixel_format = "MJPG"
    has_autofocus = False
    formats_summary: list[dict[str, Any]] = []

    # 1. Опрос форматов и максимального разрешения
    try:
        res = subprocess.run(
            ["v4l2-ctl", "-d", dev_path, "--list-formats-ext"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        if res.returncode == 0 and res.stdout:
            current_fmt = None
            fmt_sizes: dict[str, list[tuple[int, int]]] = {}
            for line in res.stdout.splitlines():
                fmt_match = re.search(r"\[\d+\]:\s*'([A-Z0-9]{4})'", line)
                if fmt_match:
                    current_fmt = fmt_match.group(1)
                    if current_fmt not in fmt_sizes:
                        fmt_sizes[current_fmt] = []
                    continue
                size_match = re.search(r"Size:\s+Discrete\s+(\d+)x(\d+)", line)
                if size_match and current_fmt:
                    w = int(size_match.group(1))
                    h = int(size_match.group(2))
                    fmt_sizes[current_fmt].append((w, h))

            if fmt_sizes:
                for fmt, sizes in fmt_sizes.items():
                    formats_summary.append(
                        {
                            "format": fmt,
                            "resolutions": [f"{w}x{h}" for w, h in sizes],
                        }
                    )

                # Выбираем лучший кодек: приоритет MJPG, затем YUYV, затем любой
                chosen_fmt = None
                if fmt_sizes.get("MJPG"):
                    chosen_fmt = "MJPG"
                elif fmt_sizes.get("YUYV"):
                    chosen_fmt = "YUYV"
                elif fmt_sizes:
                    chosen_fmt = next(iter(fmt_sizes.keys()))

                if chosen_fmt and fmt_sizes[chosen_fmt]:
                    pixel_format = chosen_fmt
                    best_w, best_h = max(
                        fmt_sizes[chosen_fmt], key=lambda wh: wh[0] * wh[1]
                    )
                    max_width = best_w
                    max_height = best_h
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"Не удалось выполнить v4l2-ctl --list-formats-ext для {dev_path}: {exc}"
        )

    # 2. Опрос автофокуса через v4l2-ctl --all
    try:
        res_ctrls = subprocess.run(
            ["v4l2-ctl", "-d", dev_path, "--all"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        if res_ctrls.returncode == 0 and res_ctrls.stdout:
            out_lower = res_ctrls.stdout.lower()
            if (
                "focus_auto" in out_lower
                or "focus_absolute" in out_lower
                or "focus" in out_lower
            ):
                has_autofocus = True
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"Не удалось выполнить v4l2-ctl --all для {dev_path}: {exc}")

    return CameraProfile(
        hardware_id=hardware_id,
        max_width=max_width,
        max_height=max_height,
        pixel_format=pixel_format,
        has_autofocus=has_autofocus,
        formats_summary=formats_summary,
    )


def clean_camera_name(raw_name: str, fallback_hw_id: str = "") -> str:
    """Очищает имя камеры от технических префиксов/суффиксов и дублей через двоеточие."""
    name = (raw_name or "").strip()
    if name:
        # Часто UVC отдает "HD Webcam C270: HD Webcam C270" — оставляем одну часть
        if ":" in name:
            parts = [p.strip() for p in name.split(":") if p.strip()]
            if len(parts) >= 2 and parts[0].lower() == parts[1].lower():
                name = parts[0]
            elif len(parts) >= 2 and parts[1].lower().startswith(parts[0].lower()):
                name = parts[1]
        # Убираем возможные остатки usb-...-video-index0
        name = re.sub(r"^usb-", "", name)
        name = re.sub(r"-video-index\d+$", "", name)
        name = name.strip()

    if name:
        return name

    fb = (fallback_hw_id or "").strip()
    fb = re.sub(r"^usb-", "", fb)
    fb = re.sub(r"-video-index\d+$", "", fb)
    return fb.replace("_", " ").strip() or fallback_hw_id


def resolve_camera_name(
    cam_by_id_file: Path,
    sys_dir: Path,
    use_v4l2: bool = True,
) -> str:
    """Определяет человекочитаемое имя камеры из sysfs или v4l2-ctl."""
    hw_id = cam_by_id_file.name
    real_dev = os.path.realpath(str(cam_by_id_file))
    video_node = Path(real_dev).name

    # 1. Пробуем /sys/class/video4linux/videoX/name
    if video_node.startswith("video"):
        sys_name_file = sys_dir / "class" / "video4linux" / video_node / "name"
        if sys_name_file.exists():
            with contextlib.suppress(OSError):
                raw_sys_name = sys_name_file.read_text(
                    encoding="utf-8", errors="ignore"
                ).strip()
                if raw_sys_name:
                    return clean_camera_name(raw_sys_name, fallback_hw_id=hw_id)

    # 2. Пробуем v4l2-ctl -d <real_dev> --info (в режиме rpi)
    if use_v4l2:
        with contextlib.suppress(Exception):
            res = subprocess.run(
                ["v4l2-ctl", "-d", real_dev, "--info"],
                capture_output=True,
                text=True,
                timeout=1.5,
                check=False,
            )
            if res.returncode == 0 and res.stdout:
                m = re.search(r"Card type\s*:\s*(.+)", res.stdout)
                if m:
                    return clean_camera_name(m.group(1), fallback_hw_id=hw_id)

    return clean_camera_name("", fallback_hw_id=hw_id)


def apply_power_line_frequency_50hz(dev_path: str) -> bool:
    """Устанавливает подавление мерцания частоты сети 50 Гц (power_line_frequency=1).

    Безопасно игнорирует ошибки, если контрол не поддерживается устройством или драйвером.
    """
    try:
        res = subprocess.run(
            ["v4l2-ctl", "-d", dev_path, "-c", "power_line_frequency=1"],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
        if res.returncode == 0:
            logger.debug(f"[CAM] Установлена частота сети 50 Гц для {dev_path}")
            return True
        logger.debug(
            f"[CAM] v4l2-ctl power_line_frequency=1 для {dev_path} код {res.returncode}: {res.stderr.strip()}"
        )
        return False
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"[CAM] Не удалось установить power_line_frequency=1 для {dev_path}: {exc}"
        )
        return False


class HardwareManager:
    """Менеджер аппаратного обеспечения (GPIO, камеры, телеметрия)."""

    def __init__(
        self,
        mode: str = "mock",
        relay_active_high: bool = True,
        v4l_by_id_dir: Path | None = None,
        sys_dir: Path | None = None,
        mock_overlay_aruco: bool = True,
        mock_cameras: bool = False,
    ):
        self.mode = mode.lower()
        if self.mode not in ("rpi", "mock"):
            raise ValueError(
                f"Недопустимый режим HARDWARE_MODE='{self.mode}'. Допустимы только 'rpi' или 'mock'."
            )

        self.relay_active_high = relay_active_high
        self.v4l_by_id_dir = v4l_by_id_dir or Path("/dev/v4l/by-id")
        self.sys_dir = sys_dir or Path("/sys")
        self.mock_overlay_aruco = mock_overlay_aruco
        self.mock_cameras = bool(mock_cameras)

        self.lock = threading.Lock()
        self._gpio_devices: dict[int, Any] = {}
        self._mock_pins: dict[int, bool] = {}
        self._mock_frame_counter: int = 0
        self._camera_profiles: dict[str, CameraProfile] = {}

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
            if dev:
                if hasattr(dev, "is_active"):
                    return bool(dev.is_active)
                return bool(dev.value)
            return False
        return bool(self._mock_pins.get(pin, False))

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
        """Кратковременный тест реле на указанных пинах с возвратом исходного состояния."""
        logger.info(f"[RELAY_TEST] Включение пинов {pins} на {duration} с")
        prev_states = {pin: self.get_pin_state(pin) for pin in pins}
        for pin in pins:
            self.set_pin(pin, True)
        try:
            if duration > 0:
                time.sleep(duration)
        finally:
            for pin, state in prev_states.items():
                self.set_pin(pin, state)
            logger.info(
                f"[RELAY_TEST] Пины {pins} возвращены в исходное состояние: {prev_states}"
            )

    @property
    def use_mock_cameras(self) -> bool:
        """True, если камеры эмулируются синтетикой (только mode='mock' и mock_cameras=True)."""
        return self.mode == "mock" and self.mock_cameras

    def resolve_device_path(self, camera_hardware_id: str) -> str | None:
        """Возвращает реальный путь к видеоустройству (/dev/videoX) по hardware_id."""
        if not camera_hardware_id:
            return None
        by_id_candidate = self.v4l_by_id_dir / camera_hardware_id
        if by_id_candidate.exists():
            return os.path.realpath(str(by_id_candidate))
        by_path_candidate = self.v4l_by_id_dir.parent / "by-path" / camera_hardware_id
        if by_path_candidate.exists():
            return os.path.realpath(str(by_path_candidate))
        if camera_hardware_id.startswith("video"):
            dev_node = Path("/dev") / camera_hardware_id
            if dev_node.exists():
                return str(dev_node)
        if camera_hardware_id.startswith("/dev/") and Path(camera_hardware_id).exists():
            return camera_hardware_id
        return None

    def _is_capture_video_node(self, dev_node: str, sys_name: str) -> bool:
        """Проверяет, является ли /dev/videoX реальной камерой (Video Capture, не ISP/кодек)."""
        lower_name = sys_name.lower()
        ignored_markers = ("pispbe", "bcm2835", "rpi-hevc", "v4l2loopback", "metadata")
        if any(m in lower_name for m in ignored_markers):
            return False
        try:
            res = subprocess.run(
                ["v4l2-ctl", "-d", dev_node, "--info"],
                capture_output=True,
                text=True,
                timeout=1.5,
                check=False,
            )
            if res.returncode == 0 and res.stdout:
                return "Video Capture" in res.stdout
        except Exception:  # noqa: BLE001
            return True
        return False

    def scan_cameras(
        self,
        v4l_by_id_dir: Path | None = None,
        sys_dir: Path | None = None,
    ) -> list[DetectedCamera]:
        """Сканирует подключенные камеры через /dev/v4l/by-id, by-path и sysfs."""
        search_dir = v4l_by_id_dir or self.v4l_by_id_dir

        # В режиме эмуляции камер при системном пути по умолчанию или пустом каталоге отдаем детерминированные моки
        if self.use_mock_cameras and (
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
        seen_real_devs: set[str] = set()
        actual_sys_dir = sys_dir or self.sys_dir
        use_v4l2 = not self.use_mock_cameras

        if search_dir.exists():
            for cam_file in sorted(search_dir.glob("*-video-index0")):
                hw_id = cam_file.name
                seen_real_devs.add(os.path.realpath(str(cam_file)))
                clean_name = resolve_camera_name(
                    cam_file,
                    actual_sys_dir,
                    use_v4l2=use_v4l2,
                )
                cameras.append(
                    DetectedCamera(
                        hardware_id=hw_id,
                        auto_name=clean_name,
                    )
                )

        # Дополнительно проверяем by-path (например, встроенные/CSI-камеры без by-id)
        if use_v4l2:
            by_path_dir = search_dir.parent / "by-path"
            if by_path_dir.exists():
                by_path_files = sorted(
                    by_path_dir.glob("*-video-index0"),
                    key=lambda p: ("usbv2-" in p.name, p.name),
                )
                for cam_file in by_path_files:
                    real_dev = os.path.realpath(str(cam_file))
                    if real_dev in seen_real_devs:
                        continue
                    seen_real_devs.add(real_dev)
                    clean_name = resolve_camera_name(
                        cam_file,
                        actual_sys_dir,
                        use_v4l2=True,
                    )
                    cameras.append(
                        DetectedCamera(
                            hardware_id=cam_file.name,
                            auto_name=clean_name,
                        )
                    )

            # Fallback на /sys/class/video4linux/video*, если в /dev/v4l/by-id и by-path ничего не найдено
            if not cameras and search_dir == Path("/dev/v4l/by-id"):
                v4l_sys_dir = actual_sys_dir / "class" / "video4linux"
                if v4l_sys_dir.exists():
                    for node_dir in sorted(v4l_sys_dir.glob("video*")):
                        index_file = node_dir / "index"
                        if index_file.exists():
                            with contextlib.suppress(OSError):
                                if (
                                    index_file.read_text(encoding="utf-8").strip()
                                    != "0"
                                ):
                                    continue
                        dev_node = f"/dev/{node_dir.name}"
                        if dev_node in seen_real_devs or not Path(dev_node).exists():
                            continue
                        raw_name = ""
                        name_file = node_dir / "name"
                        if name_file.exists():
                            with contextlib.suppress(OSError):
                                raw_name = name_file.read_text(
                                    encoding="utf-8", errors="ignore"
                                ).strip()
                        if not self._is_capture_video_node(dev_node, raw_name):
                            continue
                        seen_real_devs.add(dev_node)
                        cameras.append(
                            DetectedCamera(
                                hardware_id=node_dir.name,
                                auto_name=clean_camera_name(
                                    raw_name, fallback_hw_id=node_dir.name
                                ),
                            )
                        )

        return cameras

    def get_camera_profile(
        self,
        camera_hardware_id: str,
        force_refresh: bool = False,
    ) -> CameraProfile:
        """Возвращает кэшированный профиль камеры или опрашивает её возможности."""
        if not force_refresh and camera_hardware_id in self._camera_profiles:
            return self._camera_profiles[camera_hardware_id]

        if self.use_mock_cameras:
            profile = CameraProfile(
                hardware_id=camera_hardware_id,
                max_width=1920,
                max_height=1080,
                pixel_format="MJPG",
                has_autofocus=True,
                formats_summary=[
                    {
                        "format": "MJPG",
                        "resolutions": ["1920x1080", "1280x720", "640x480"],
                    },
                    {
                        "format": "YUYV",
                        "resolutions": ["1920x1080", "640x480"],
                    },
                ],
            )
            self._camera_profiles[camera_hardware_id] = profile
            return profile

        # Реальная камера (режим rpi или гибридный режим mock_cameras=False):
        real_dev_path = self.resolve_device_path(camera_hardware_id)
        if not real_dev_path:
            profile = CameraProfile(hardware_id=camera_hardware_id)
            self._camera_profiles[camera_hardware_id] = profile
            return profile

        profile = probe_camera_capabilities(
            real_dev_path, hardware_id=camera_hardware_id
        )
        self._camera_profiles[camera_hardware_id] = profile
        return profile

    def calibrate_camera(self, camera_hardware_id: str) -> CameraProfile:
        """Выполняет автокалибровку камеры, обновляет кэш и возвращает профиль."""
        if not self.use_mock_cameras:
            real_dev_path = self.resolve_device_path(camera_hardware_id)
            if real_dev_path:
                apply_power_line_frequency_50hz(real_dev_path)

        profile = self.get_camera_profile(camera_hardware_id, force_refresh=True)
        logger.info(
            f"[CAM] Автокалибровка камеры {camera_hardware_id} завершена: {profile.summary_text()}"
        )
        return profile

    def capture_frame(
        self,
        camera_hardware_id: str,
        warmup_frames: int = 5,
        frame_index: int | None = None,
    ) -> bytes:
        """Захватывает сырой кадр с указанной камеры (с прогревом и автоподбором качества)."""
        if self.use_mock_cameras:
            from edge_agent.synthetic import generate_synthetic_frame

            idx = frame_index if frame_index is not None else self._mock_frame_counter
            frame = generate_synthetic_frame(
                frame_index=idx,
                camera_id=camera_hardware_id,
                overlay_aruco=self.mock_overlay_aruco,
            )
            self._mock_frame_counter += 1
            return frame

        # Реальная камера (rpi или гибридный режим mock_cameras=False): строгая проверка OpenCV
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "В режиме HARDWARE_MODE=rpi необходима библиотека opencv-python-headless для захвата кадров."
            ) from exc

        real_dev_path = self.resolve_device_path(camera_hardware_id)
        if not real_dev_path:
            raise RuntimeError(
                f"Камера с hardware_id '{camera_hardware_id}' не найдена в {self.v4l_by_id_dir}. "
                "Откат на дефолтные устройства запрещен."
            )

        apply_power_line_frequency_50hz(real_dev_path)
        profile = self.get_camera_profile(camera_hardware_id)
        logger.debug(
            f"Открытие камеры {camera_hardware_id} по пути {real_dev_path} (профиль: {profile.summary_text()})"
        )

        # До 3 повторов открытия устройства при ошибке
        cap = None
        for attempt in range(1, 4):
            cap = cv2.VideoCapture(real_dev_path, cv2.CAP_V4L2)
            if cap.isOpened():
                break
            logger.warning(
                f"Попытка {attempt}/3 открыть видеоустройство {real_dev_path} не удалась, повтор..."
            )
            time.sleep(0.3)

        if cap is None or not cap.isOpened():
            raise RuntimeError(
                f"Не удалось открыть видеоустройство {real_dev_path} ({camera_hardware_id}) после 3 попыток"
            )

        try:
            # Настройка свойств захвата
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if profile.pixel_format == "MJPG":
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            elif profile.pixel_format == "YUYV":
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"YUYV"))

            cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(profile.max_width))
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(profile.max_height))

            # Умный прогрев: чтение кадров до стабилизации яркости (|luma(t) - luma(t-1)| < 1.5 на 2-3 кадрах подряд)
            start_warmup = time.time()
            initial_luma: float | None = None
            final_luma: float | None = None
            stable_frames = 0
            warmup_frames_read = 0
            prev_luma: float | None = None
            target_frame = None
            warmup_timeout = 3.5

            while (time.time() - start_warmup) < warmup_timeout:
                ret, frame = cap.read()
                if not ret or frame is None:
                    continue
                warmup_frames_read += 1
                target_frame = frame

                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                curr_luma = float(np.mean(gray))

                if initial_luma is None:
                    initial_luma = curr_luma
                final_luma = curr_luma

                if prev_luma is not None:
                    diff = abs(curr_luma - prev_luma)
                    if diff < 1.5:
                        stable_frames += 1
                        if stable_frames >= 2:
                            break
                    else:
                        stable_frames = 0
                prev_luma = curr_luma

            if target_frame is None:
                ret, target_frame = cap.read()
                if not ret or target_frame is None:
                    raise RuntimeError(
                        f"Не удалось прочитать целевой кадр с камеры {camera_hardware_id}"
                    )

            actual_h, actual_w = target_frame.shape[:2]
            elapsed = time.time() - start_warmup

            # Логирование параметров в формате [SNAP]
            logger.info(
                f"[SNAP] Камера: {camera_hardware_id}, Разрешение: {actual_w}x{actual_h}, "
                f"Кодек: {profile.pixel_format}, Прогревочных кадров: {warmup_frames_read}, "
                f"Luma: {initial_luma if initial_luma is not None else 0.0:.1f} -> "
                f"{final_luma if final_luma is not None else 0.0:.1f}, Время: {elapsed:.2f} с"
            )

            success, enc_frame = cv2.imencode(".jpg", target_frame)
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
        capture_delay_sec: float = 0.0,
        frame_index: int | None = None,
    ) -> bytes:
        """Последовательность съемки кадра со вспышкой/светом:

        - Если light_pin == flash_pin: свет не выключается при съемке.
        - Если пины разные:
          1. Выключение основного света (light=0).
          2. Включение вспышки (flash=1).
          3. Базовая пауза (0.5-1.0 с) плюс capture_delay_sec.
          4. Захват кадра с автоподбором и умным прогревом.
          5. Гарантированный возврат в исходное состояние (light=1, flash=0) в finally.
        """
        same_pins = light_pin == flash_pin
        try:
            if not same_pins:
                self.set_light(light_pin, False)
                self.set_flash(flash_pin, True)
            else:
                self.set_light(light_pin, True)

            # В mock-режиме исключаем искусственные задержки стабилизации
            if self.mode != "mock":
                switch_pause = (
                    max(0.5, switch_delay_ms / 1000.0) if not same_pins else 0.5
                )
                total_delay = switch_pause + max(0.0, float(capture_delay_sec))
                if total_delay > 0:
                    time.sleep(total_delay)

            effective_warmup = 0 if self.use_mock_cameras else warmup_frames
            return self.capture_frame(
                camera_hardware_id,
                warmup_frames=effective_warmup,
                frame_index=frame_index,
            )
        finally:
            if not same_pins:
                self.set_flash(flash_pin, False)
            self.set_light(light_pin, True)

    def cleanup(self) -> None:
        """Очистка используемых ресурсов GPIO."""
        for dev in self._gpio_devices.values():
            with contextlib.suppress(Exception):
                dev.close()
        self._gpio_devices.clear()
        self._mock_pins.clear()


def get_hardware_telemetry(
    gpio_mode: str = "mock", mock_cameras: bool = False
) -> HeartbeatHardware:
    """Сбор аппаратной телеметрии хоста."""
    # 1. Модель платы
    board_model = "Raspberry Pi 5 Model B Rev 1.0 (Mock)"
    model_paths = [
        Path("/proc/device-tree/model"),
        Path("/sys/firmware/devicetree/base/model"),
    ]
    detected_model = None
    for mp in model_paths:
        if mp.exists():
            with contextlib.suppress(Exception):
                raw_model = mp.read_text(encoding="utf-8").strip("\x00\n ")
                if raw_model:
                    detected_model = raw_model
                    break

    if detected_model:
        board_model = detected_model
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
        mock_cameras=mock_cameras,
    )
