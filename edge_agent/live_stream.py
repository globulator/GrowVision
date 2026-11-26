import contextlib
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from edge_agent.vision import apply_rgb_gains_to_bgr, apply_rgb_to_jpeg_bytes
from shared.logger import get_logger

logger = get_logger("edge_agent.live_stream")

_DEFAULT_URLOPEN = urllib.request.urlopen


def _open_local_ustreamer(req: urllib.request.Request, timeout: float) -> Any:
    """Открывает локальный URL ustreamer без системного прокси.

    При активном mock-патче urllib.request.urlopen делегирует вызов ему.
    """
    current_urlopen = urllib.request.urlopen
    if (
        current_urlopen is not _DEFAULT_URLOPEN
        or getattr(current_urlopen, "__module__", None) != "urllib.request"
    ):
        return current_urlopen(req, timeout=timeout)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(req, timeout=timeout)


USTREAMER_FORMAT_MAP: dict[str, str] = {
    "MJPG": "MJPEG",
    "MJPEG": "MJPEG",
    "JPEG": "JPEG",
    "YUYV": "YUYV",
    "YVYU": "YVYU",
    "UYVY": "UYVY",
    "YUV420": "YUV420",
    "YVU420": "YVU420",
    "RGB565": "RGB565",
    "RGB24": "RGB24",
    "BGR24": "BGR24",
    "GREY": "GREY",
}


class LiveStreamManager:
    """Менеджер Live-стрима камеры на базе uStreamer, OpenCV V4L2 или mock-генератора."""

    def __init__(
        self,
        mode: str = "mock",
        v4l_by_id_dir: Any = None,
        client: Any = None,
        mock_cameras: bool = False,
        vision: Any = None,
    ):
        self.mode = mode.lower()
        self.v4l_by_id_dir = Path(v4l_by_id_dir) if v4l_by_id_dir else None
        self.client = client
        self.mock_cameras = bool(mock_cameras)
        self.vision = vision

        self.lock = threading.Lock()
        self._cap_lock = threading.Lock()
        self._cap: Any = None
        self.process: subprocess.Popen[Any] | None = None
        self.active_camera_id: str | None = None
        self.current_width: int = 1280
        self.current_height: int = 720
        self.current_format: str = "MJPG"
        self.current_port: int = 8080
        self.stream_url: str | None = None
        self.is_running: bool = False
        self.is_paused: bool = False
        self.device_online: bool = True
        self.stream_mode: str = "raw"
        self.segmentation_mode: str = "auto"
        self.last_activity_time: float = time.time()
        self.rgb_gains: tuple[float, float, float] = (1.0, 1.0, 1.0)
        self._camera_context: dict[str, Any] = {}

        self._last_frame: bytes | None = None
        self._last_raw_frame: bytes | None = None
        self._push_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._mock_counter: int = 0
        self._last_send_rtt_ms: float = 0.0
        self._resolution_verified: bool = False
        self._last_state_check_at: float = 0.0
        self._last_color_check_at: float = 0.0
        self.color_marker_detected: bool = False
        self._consecutive_read_errors: int = 0
        self._last_offline_notify_at: float = 0.0
        self._started_at: float = 0.0
        self._has_delivered_frame: bool = False
        self.startup_grace_sec: float = 5.0

    @property
    def use_mock_cameras(self) -> bool:
        """True, если стрим использует синтетические кадры (mode='mock' и mock_cameras=True)."""
        return self.mode == "mock" and self.mock_cameras

    def _resolve_dev_path(self, camera_hardware_id: str | None) -> str | None:
        """Возвращает реальный путь к видеоустройству (/dev/videoX) по hardware_id."""
        if not camera_hardware_id:
            return None
        base_by_id = self.v4l_by_id_dir or Path("/dev/v4l/by-id")
        candidates = [
            base_by_id / camera_hardware_id,
            base_by_id.parent / "by-path" / camera_hardware_id,
            Path("/dev/v4l/by-id") / camera_hardware_id,
            Path("/dev/v4l/by-path") / camera_hardware_id,
        ]
        for cand in candidates:
            if cand.exists():
                return os.path.realpath(str(cand))
        if camera_hardware_id.startswith("video"):
            dev_node = Path("/dev") / camera_hardware_id
            if dev_node.exists():
                return str(dev_node)
        if camera_hardware_id.startswith("/dev/") and Path(camera_hardware_id).exists():
            return camera_hardware_id
        # Для тестов с замоканным subprocess/ustreamer (когда устройства нет на диске)
        return str(base_by_id / camera_hardware_id)

    @staticmethod
    def _is_port_open(host: str, port: int) -> bool:
        """Быстрая проверка, слушает ли локальный TCP-порт."""
        with (
            contextlib.suppress(OSError),
            socket.create_connection((host, port), timeout=0.1),
        ):
            return True
        return False

    def _release_capture(self) -> None:
        """Безопасно закрывает OpenCV VideoCapture под замком."""
        with self._cap_lock:
            if self._cap is not None:
                with contextlib.suppress(Exception):
                    self._cap.release()
                self._cap = None

    def _resolve_stream_url(self, port: int) -> str | None:
        """Вычисляет URL ustreamer на интерфейсе, смотрящем в сторону сервера."""
        host_ip = "127.0.0.1"
        base_url = getattr(self.client, "base_url", None) if self.client else None
        if isinstance(base_url, str) and base_url:
            parsed = urlparse(base_url)
            target_host = parsed.hostname
            target_port = parsed.port or (443 if parsed.scheme == "https" else 80)
            if target_host and target_host not in ("127.0.0.1", "localhost"):
                with (
                    contextlib.suppress(OSError),
                    socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock,
                ):
                    sock.connect((target_host, target_port))
                    detected_ip = sock.getsockname()[0]
                    if detected_ip:
                        host_ip = detected_ip
        return f"http://{host_ip}:{port}"

    def set_rgb_gains(self, r: float, g: float, b: float) -> None:
        """Устанавливает программные коэффициенты усиления R/G/B."""
        self.rgb_gains = (max(0.0, float(r)), max(0.0, float(g)), max(0.0, float(b)))

    def set_stream_mode(self, mode: str) -> None:
        """Переключает режим Live-потока ('raw' или 'annotated')."""
        norm = "annotated" if str(mode).lower() == "annotated" else "raw"
        if norm != self.stream_mode:
            self.stream_mode = norm
            logger.info(f"[LIVE] Режим потока переключён на {norm}")

    def set_segmentation_mode(self, mode: str) -> None:
        """Переключает движок сегментации ('auto', 'onnx' или 'hsv')."""
        raw = str(mode or "auto").strip().lower()
        norm = raw if raw in ("auto", "onnx", "hsv") else "auto"
        if norm != self.segmentation_mode:
            self.segmentation_mode = norm
            logger.info(f"[LIVE] Режим сегментации переключён на {norm}")

    def set_camera_context(
        self,
        inst_name: str | None = None,
        culture_name: str | None = None,
        aruco_size_mm: float | None = None,
    ) -> None:
        """Сохраняет контекст установки для CV-разметки в Live-режиме."""
        self._camera_context = {
            "inst_name": inst_name,
            "culture_name": culture_name,
            "aruco_size_mm": aruco_size_mm,
        }

    def _fetch_ustreamer_state(self, timeout: float = 1.0) -> dict[str, Any] | None:
        """Запрашивает локальный /state у ustreamer."""
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{self.current_port}/state")
            with _open_local_ustreamer(req, timeout=timeout) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8", errors="ignore"))
                    if isinstance(data, dict):
                        return data
        except Exception:  # noqa: BLE001
            return None
        return None

    @staticmethod
    def _parse_ustreamer_online(state_data: dict[str, Any] | None) -> bool | None:
        """Извлекает флаг online источника из ответа ustreamer /state."""
        if not isinstance(state_data, dict):
            return None
        result = (
            state_data.get("result")
            if isinstance(state_data.get("result"), dict)
            else state_data
        )
        source = (
            result.get("source")
            if isinstance(result, dict) and isinstance(result.get("source"), dict)
            else None
        )
        if source is not None and "online" in source:
            return bool(source["online"])
        if isinstance(result, dict) and "online" in result:
            return bool(result["online"])
        return None

    def capture_ustreamer_snapshot(
        self, camera_hardware_id: str, timeout: float = 2.5
    ) -> bytes | None:
        """Забирает свежий JPEG через локальный /snapshot ustreamer без паузы стрима."""
        if (
            not self.is_running
            or self.is_paused
            or self.active_camera_id != camera_hardware_id
        ):
            return None
        if self.process is None or self.process.poll() is not None:
            return None

        state_data = self._fetch_ustreamer_state(timeout=min(1.0, timeout))
        online_flag = self._parse_ustreamer_online(state_data)
        if online_flag is False:
            self.device_online = False
            logger.warning(
                f"[LIVE] Камера {camera_hardware_id} offline по /state ustreamer"
            )
            return None
        if online_flag is True:
            self.device_online = True

        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{self.current_port}/snapshot"
            )
            with _open_local_ustreamer(req, timeout=timeout) as resp:
                if resp.status == 200:
                    data = resp.read()
                    if data and data.startswith(b"\xff\xd8"):
                        return data
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[LIVE] Ошибка /snapshot ustreamer: {exc}")
        return None

    def _get_vision(self) -> Any:
        """Возвращает или лениво создаёт экземпляр VisionPipeline."""
        if self.vision is None:
            from edge_agent.vision import VisionPipeline

            self.vision = VisionPipeline(mock_mode=(self.mode == "mock"))
        return self.vision

    @staticmethod
    def _decode_jpeg_bgr(raw_frame: bytes) -> Any:
        """Декодирует JPEG-байты в BGR np.ndarray один раз для переиспользования в кадре."""
        if not raw_frame:
            return None
        with contextlib.suppress(Exception):
            import cv2  # type: ignore
            import numpy as np  # type: ignore

            nparr = np.frombuffer(raw_frame, np.uint8)
            return cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        return None

    def _update_color_marker_status(
        self,
        raw_frame: bytes | Any,
        force: bool = False,
        decoded_bgr: Any = None,
    ) -> Any:
        """Периодически проверяет наличие цветового маркера на исходном кадре без RGB-искажений."""
        now_m = time.monotonic()
        if not force and (now_m - self._last_color_check_at) < 0.4:
            return decoded_bgr
        self._last_color_check_at = now_m
        if decoded_bgr is None:
            decoded_bgr = (
                self._decode_jpeg_bgr(raw_frame)
                if isinstance(raw_frame, (bytes, bytearray))
                else raw_frame
            )
        with contextlib.suppress(Exception):
            vision = self._get_vision()
            detector = getattr(vision, "detect_color_marker_details", None)
            if callable(detector):
                cv_input = decoded_bgr if decoded_bgr is not None else raw_frame
                corners, _ = detector(cv_input)
                self.color_marker_detected = corners is not None
        return decoded_bgr

    def get_raw_frame_for_calibration(self, camera_hardware_id: str) -> bytes | None:
        """Возвращает свежий неискажённый (без RGB-гейнов) кадр для автоцветокоррекции."""
        if (
            self.is_running
            and self.active_camera_id == camera_hardware_id
            and self.process is not None
            and self.process.poll() is None
        ):
            snap = self.capture_ustreamer_snapshot(camera_hardware_id, timeout=2.5)
            if snap:
                return snap
        if self.is_running and self.active_camera_id == camera_hardware_id:
            if self.use_mock_cameras:
                from edge_agent.synthetic import generate_synthetic_frame

                return generate_synthetic_frame(
                    frame_index=self._mock_counter,
                    camera_id=self.active_camera_id or "live-cam",
                    width=self.current_width,
                    height=self.current_height,
                )
            if self._last_raw_frame:
                return self._last_raw_frame
        return None

    def _annotate_frame(self, raw_frame: bytes, decoded_bgr: Any = None) -> bytes:
        """Применяет RGB-коррекцию до CV-анализа и накладывает разметку для режима 'Разметка'."""
        vision = self._get_vision()

        try:
            if decoded_bgr is None:
                decoded_bgr = self._decode_jpeg_bgr(raw_frame)
            if decoded_bgr is not None:
                cv_input: Any = apply_rgb_gains_to_bgr(decoded_bgr, *self.rgb_gains)
            else:
                cv_input = apply_rgb_to_jpeg_bytes(raw_frame, *self.rgb_gains)
            now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            ann_bytes, _, _ = vision.process_capture(
                cv_input,
                inst_name=self._camera_context.get("inst_name"),
                culture_name=self._camera_context.get("culture_name"),
                camera_name=self.active_camera_id,
                captured_at=now_str,
                aruco_size_mm=self._camera_context.get("aruco_size_mm"),
                segmentation_mode=self.segmentation_mode,
            )
            return ann_bytes
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[LIVE] Ошибка CV-разметки кадра: {exc}")
            return raw_frame

    def apply_rgb_gains(self, img: Any) -> Any:
        """Применяет программные коэффициенты R/G/B к OpenCV кадру (BGR)."""
        if self.rgb_gains == (1.0, 1.0, 1.0):
            return img
        corrected = apply_rgb_gains_to_bgr(img, *self.rgb_gains)
        if img is not None and corrected is not img:
            img[:] = corrected
        return img

    def set_paused(self, paused: bool) -> None:
        """Переключает статус паузы (при выполнении SNAP)."""
        with self.lock:
            self.is_paused = paused
            self.last_activity_time = time.time()
            if paused:
                self._release_capture()
            logger.info(f"[LIVE] Статус паузы изменен: {paused}")

    def touch(self) -> None:
        """Обновляет таймер активности."""
        self.last_activity_time = time.time()

    def check_inactivity_timeout(self) -> None:
        """Проверяет таймаут неактивности (> 120 с) и при необходимости останавливает стрим."""
        with self.lock:
            if self.is_running and (time.time() - self.last_activity_time) > 120.0:
                self._stop_internal()
                logger.info("[LIVE] Стрим остановлен по таймауту неактивности")

    def start(
        self,
        camera_hardware_id: str,
        width: int = 1280,
        height: int = 720,
        pixel_format: str = "MJPG",
        port: int = 8080,
    ) -> bool:
        """Запускает Live-стрим для указанной камеры."""
        with self.lock:
            # Если уже запущен для этой камеры с теми же параметрами
            if (
                self.is_running
                and self.active_camera_id == camera_hardware_id
                and self.current_width == width
                and self.current_height == height
                and self.current_format == pixel_format
            ):
                self.touch()
                return True

            prev_cam = self.active_camera_id
            self._stop_internal()
            if prev_cam != camera_hardware_id:
                self.rgb_gains = (1.0, 1.0, 1.0)
                self.segmentation_mode = "auto"

            self.active_camera_id = camera_hardware_id
            self.current_width = width
            self.current_height = height
            self.current_format = pixel_format
            self.current_port = port
            self.last_activity_time = time.time()
            self._resolution_verified = False
            self._stop_event.clear()

            has_ustreamer = shutil.which("ustreamer") is not None
            ustreamer_fmt = USTREAMER_FORMAT_MAP.get(pixel_format.upper())
            if not self.use_mock_cameras and has_ustreamer and ustreamer_fmt:
                dev_path = (
                    self._resolve_dev_path(camera_hardware_id)
                    or f"/dev/v4l/by-id/{camera_hardware_id}"
                )

                # Без --quality для аппаратного MJPEG и с --device-timeout 5 для камер (Logitech C270 ~1-2.3 с)
                cmd = [
                    "ustreamer",
                    "--device",
                    dev_path,
                    "--host",
                    "0.0.0.0",
                    "--port",
                    str(port),
                    "-m",
                    ustreamer_fmt,
                    "-r",
                    f"{width}x{height}",
                    "--device-timeout",
                    "5",
                    "--workers",
                    "2",
                    "--drop-same-frames",
                    "30",
                    "--slowdown",
                ]
                try:
                    logger.info(f"[LIVE] Запуск ustreamer: {' '.join(cmd)}")
                    self.process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                    )
                    # Ждем открытия локального порта ustreamer или его раннего завершения
                    deadline = time.monotonic() + 0.8
                    while time.monotonic() < deadline:
                        if self.process.poll() is not None:
                            break
                        if self._is_port_open("127.0.0.1", port):
                            break
                        time.sleep(0.1)

                    if self.process.poll() is None:
                        self.stream_url = self._resolve_stream_url(port)
                    else:
                        err_out = ""
                        if self.process.stderr:
                            with contextlib.suppress(Exception):
                                err_out = self.process.stderr.read().decode(
                                    "utf-8", errors="ignore"
                                )[:400]
                                self.process.stderr.close()
                        logger.warning(
                            f"[LIVE] ustreamer завершился с кодом {self.process.returncode} "
                            f"({err_out.strip() or 'без вывода'}), переключение на OpenCV V4L2 fallback"
                        )
                        self.process = None
                        self.stream_url = None
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        f"[LIVE] Не удалось запустить ustreamer: {exc}, переключение на OpenCV V4L2 fallback"
                    )
                    self.process = None
                    self.stream_url = None
            elif not self.use_mock_cameras:
                reason = (
                    "ustreamer не найден"
                    if not has_ustreamer
                    else f"формат {pixel_format} не поддерживается ustreamer"
                )
                logger.info(f"[LIVE] {reason} -> используется OpenCV V4L2 fallback")
                self.stream_url = None

            self.is_running = True
            self.is_paused = False
            self.device_online = True
            self.stream_mode = "raw"
            self.color_marker_detected = False
            self._last_raw_frame = None
            self._consecutive_read_errors = 0
            self._last_state_check_at = 0.0
            self._last_color_check_at = 0.0
            self._last_offline_notify_at = 0.0
            self._started_at = time.monotonic()
            self._has_delivered_frame = False

            # Запуск фонового воркера отправки кадров на сервер и watchdog
            self._push_thread = threading.Thread(
                target=self._push_loop, daemon=True, name="live_push_thread"
            )
            self._push_thread.start()
            return True

    def _stop_internal(self) -> None:
        """Внутренняя остановка процессов стрима без блокировки lock."""
        self._stop_event.set()
        if self.process:
            with contextlib.suppress(Exception):
                self.process.terminate()
                self.process.wait(timeout=0.5)
            if self.process.stderr:
                with contextlib.suppress(Exception):
                    self.process.stderr.close()
            self.process = None

        self._release_capture()
        self.stream_url = None
        self._last_frame = None
        self._last_raw_frame = None
        self.is_running = False
        self.is_paused = False
        self.device_online = True
        self.stream_mode = "raw"
        self.color_marker_detected = False
        self._resolution_verified = False
        self._consecutive_read_errors = 0
        self._last_color_check_at = 0.0
        self._started_at = 0.0
        self._has_delivered_frame = False

    def stop(self) -> None:
        """Останавливает Live-стрим."""
        with self.lock:
            self._stop_internal()
            logger.info("[LIVE] Стрим остановлен")

    def _capture_opencv_frame(self) -> bytes | None:
        """Реальный OpenCV V4L2 fallback-захват кадра с физической камеры."""
        try:
            import cv2  # type: ignore
        except ImportError:
            return None

        dev_path = self._resolve_dev_path(self.active_camera_id)
        if not dev_path or not Path(dev_path).exists():
            return None

        with self._cap_lock:
            if self._cap is None or not self._cap.isOpened():
                cap = cv2.VideoCapture(dev_path, cv2.CAP_V4L2)
                if not cap or not cap.isOpened():
                    with contextlib.suppress(Exception):
                        if cap is not None:
                            cap.release()
                    return None
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                fmt = (self.current_format or "MJPG").upper()
                if fmt in ("MJPG", "MJPEG"):
                    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                elif fmt == "YUYV":
                    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"YUYV"))
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(self.current_width))
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(self.current_height))
                self._cap = cap

            ret, img = self._cap.read()
            if not ret or img is None:
                with contextlib.suppress(Exception):
                    self._cap.release()
                self._cap = None
                return None

            actual_h, actual_w = img.shape[:2]
            if actual_w > 0 and actual_h > 0:
                self.current_width = int(actual_w)
                self.current_height = int(actual_h)
                self._resolution_verified = True

            success, enc = cv2.imencode(".jpg", img)
            if not success:
                return None
            return enc.tobytes()

    def _should_mark_offline_now(self) -> bool:
        """Разрешает перевод device_online=False после первого кадра или истечения стартового окна."""
        if self._has_delivered_frame or self._started_at <= 0.0:
            return True
        if (time.monotonic() - self._started_at) >= self.startup_grace_sec:
            return True
        # Если устройство физически отсутствует в OpenCV-режиме (без ustreamer), считаем офлайн сразу
        if self.process is None and not self.use_mock_cameras:
            dev_path = self._resolve_dev_path(self.active_camera_id)
            if not dev_path or not Path(dev_path).exists():
                return True
        return False

    def _compute_loop_sleep(self, elapsed: float) -> float:
        """Вычисляет адаптивную паузу цикла отправки кадров в зависимости от режима."""
        if self.stream_mode == "annotated":
            target_interval = 0.08  # до ~12.5 FPS в режиме разметки
            min_pause = max(0.015, min(0.08, elapsed * 0.25))
            return max(min_pause, target_interval - elapsed)
        target_interval = 0.04  # ~25 FPS в режиме raw
        return max(0.01, target_interval - elapsed)

    def _generate_frame(self) -> bytes | None:
        """Генерирует или считывает свежий кадр (с RGB и CV-разметкой в режиме annotated)."""
        if self.is_paused and self._last_frame:
            return self._last_frame

        raw_frame: bytes | None = None
        decoded_bgr: Any = None

        # 1. Попытка забрать из ustreamer /snapshot и проверить /state при его наличии
        if self.process is not None:
            if self.process.poll() is not None:
                logger.warning(
                    f"[LIVE] ustreamer неожиданно завершился (код {self.process.returncode}), "
                    "переключение на OpenCV V4L2 fallback"
                )
                if self.process.stderr:
                    with contextlib.suppress(Exception):
                        self.process.stderr.close()
                self.process = None
                self.stream_url = None
            else:
                now_m = time.monotonic()
                if (now_m - self._last_state_check_at) >= 0.5:
                    self._last_state_check_at = now_m
                    state_data = self._fetch_ustreamer_state(timeout=0.8)
                    online_flag = self._parse_ustreamer_online(state_data)
                    if online_flag is False:
                        if self._should_mark_offline_now():
                            self.device_online = False
                        return self._last_frame if self.is_paused else None
                    if online_flag is True:
                        self.device_online = True
                        self._consecutive_read_errors = 0

                if not self.device_online:
                    return self._last_frame if self.is_paused else None

                with contextlib.suppress(Exception):
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{self.current_port}/snapshot"
                    )
                    with _open_local_ustreamer(req, timeout=2.5) as resp:
                        if resp.status == 200:
                            raw_frame = resp.read()
                if raw_frame is None:
                    self._consecutive_read_errors += 1
                    if (
                        self._consecutive_read_errors >= 3
                        and self._should_mark_offline_now()
                    ):
                        self.device_online = False
                    # Пока ustreamer работает, не открываем /dev/videoX параллельно во избежание конфликта V4L2
                    return self._last_frame if self.is_paused else None

                self.device_online = True
                self._consecutive_read_errors = 0

                if not self._resolution_verified:
                    decoded_bgr = self._decode_jpeg_bgr(raw_frame)
                    if decoded_bgr is not None:
                        ah, aw = decoded_bgr.shape[:2]
                        if aw > 0 and ah > 0:
                            self.current_width = int(aw)
                            self.current_height = int(ah)
                            self._resolution_verified = True

        # 2. В режиме реальных камер без активного ustreamer — реальный OpenCV V4L2 fallback (без синтетики)
        if raw_frame is None:
            if not self.use_mock_cameras:
                raw_frame = self._capture_opencv_frame()
                if raw_frame is None:
                    self._consecutive_read_errors += 1
                    if (
                        self._consecutive_read_errors >= 3
                        and self._should_mark_offline_now()
                    ):
                        self.device_online = False
                    return self._last_frame if self.is_paused else None
                self.device_online = True
                self._consecutive_read_errors = 0
            else:
                from edge_agent.synthetic import generate_synthetic_frame

                raw_frame = generate_synthetic_frame(
                    frame_index=self._mock_counter,
                    camera_id=self.active_camera_id or "live-cam",
                    width=self.current_width,
                    height=self.current_height,
                )
                self._mock_counter += 1
                self.device_online = True

        if raw_frame is not None:
            self._has_delivered_frame = True
            self._last_raw_frame = raw_frame
            decoded_bgr = self._update_color_marker_status(
                raw_frame, decoded_bgr=decoded_bgr
            )

        # 3. CV-разметка (с предварительной RGB-коррекцией) в режиме 'annotated'
        if self.stream_mode == "annotated" and raw_frame is not None:
            raw_frame = self._annotate_frame(raw_frame, decoded_bgr=decoded_bgr)

        self._last_frame = raw_frame
        return raw_frame

    def _push_loop(self) -> None:
        """Фоновый цикл отправки кадров на сервер и watchdog неактивности."""
        consecutive_idle_sec = 0.0

        while not self._stop_event.is_set():
            loop_start = time.time()

            # Проверка автостопа по неактивности (2 минуты = 120 с)
            idle_time = loop_start - self.last_activity_time
            if idle_time > 120.0:
                logger.info(
                    f"[LIVE] Автоостановка стрима: неактивность {int(idle_time)} с (> 120 с)"
                )
                self.stop()
                break

            # Получаем свежий кадр и замеряем время захвата
            cap_t0 = time.perf_counter()
            frame_bytes = self._generate_frame()
            cap_ms = (time.perf_counter() - cap_t0) * 1000.0
            if frame_bytes is None:
                if not self.device_online:
                    self.color_marker_detected = False
                if (
                    not self.device_online
                    and self.client
                    and self.active_camera_id
                    and (time.monotonic() - self._last_offline_notify_at) >= 0.5
                ):
                    self._last_offline_notify_at = time.monotonic()
                    with contextlib.suppress(Exception):
                        res = self.client.send_live_frame(
                            hardware_id=self.active_camera_id,
                            frame_bytes=b"",
                            frame_timestamp=time.time(),
                            is_paused=self.is_paused,
                            resolution=f"{self.current_width}x{self.current_height}",
                            latency_ms=0,
                            stream_url=self.stream_url,
                            device_online=False,
                            frame_mode=self.stream_mode,
                            color_marker_detected=False,
                        )
                        if isinstance(res, dict):
                            if res.get("mode"):
                                self.set_stream_mode(str(res["mode"]))
                            if res.get("segmentation_mode"):
                                self.set_segmentation_mode(
                                    str(res["segmentation_mode"])
                                )
                self._stop_event.wait(0.1)
                continue

            now_ts = time.time()
            est_latency_ms = max(1, round(cap_ms + self._last_send_rtt_ms / 2.0))

            # Отправка кадра на сервер через HTTP клиент, если доступен
            if self.client and self.active_camera_id:
                try:
                    send_t0 = time.perf_counter()
                    res = self.client.send_live_frame(
                        hardware_id=self.active_camera_id,
                        frame_bytes=frame_bytes,
                        frame_timestamp=now_ts,
                        is_paused=self.is_paused,
                        resolution=f"{self.current_width}x{self.current_height}",
                        latency_ms=est_latency_ms,
                        stream_url=self.stream_url,
                        device_online=self.device_online,
                        frame_mode=self.stream_mode,
                        color_marker_detected=self.color_marker_detected,
                    )
                    self._last_send_rtt_ms = (time.perf_counter() - send_t0) * 1000.0
                    if isinstance(res, dict):
                        if res.get("mode"):
                            self.set_stream_mode(str(res["mode"]))
                        if res.get("segmentation_mode"):
                            self.set_segmentation_mode(str(res["segmentation_mode"]))
                        if res.get("active_subscribers", 0) > 0:
                            self.touch()
                            consecutive_idle_sec = 0.0
                        else:
                            consecutive_idle_sec += max(0.04, time.time() - loop_start)
                            if consecutive_idle_sec >= 120.0:
                                logger.info(
                                    "[LIVE] Нет активных подписчиков в браузере более 120 с, автоостановка"
                                )
                                self.stop()
                                break
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"[LIVE] Ошибка отправки кадра на сервер: {exc}")

            # Адаптивная регулировка FPS (~12.5 FPS в 'annotated', ~25 FPS в 'raw')
            elapsed = time.time() - loop_start
            sleep_time = self._compute_loop_sleep(elapsed)
            self._stop_event.wait(sleep_time)
