import contextlib
import os
import shutil
import subprocess
import threading
import time
from typing import Any

from shared.logger import get_logger

logger = get_logger("edge_agent.live_stream")


class LiveStreamManager:
    """Менеджер Live-стрима камеры на базе uStreamer или mock-генератора."""

    def __init__(
        self,
        mode: str = "mock",
        v4l_by_id_dir: Any = None,
        client: Any = None,
    ):
        self.mode = mode.lower()
        self.v4l_by_id_dir = v4l_by_id_dir
        self.client = client

        self.lock = threading.Lock()
        self.process: subprocess.Popen[Any] | None = None
        self.active_camera_id: str | None = None
        self.current_width: int = 1280
        self.current_height: int = 720
        self.current_format: str = "MJPG"
        self.current_port: int = 8080
        self.is_running: bool = False
        self.is_paused: bool = False
        self.last_activity_time: float = time.time()
        self.rgb_gains: tuple[float, float, float] = (1.0, 1.0, 1.0)

        self._last_frame: bytes | None = None
        self._push_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._mock_counter: int = 0

    def set_rgb_gains(self, r: float, g: float, b: float) -> None:
        """Устанавливает программные коэффициенты усиления R/G/B."""
        self.rgb_gains = (max(0.1, float(r)), max(0.1, float(g)), max(0.1, float(b)))

    def apply_rgb_gains(self, img: Any) -> Any:
        """Применяет программные коэффициенты R/G/B к OpenCV кадру (BGR)."""
        if self.rgb_gains != (1.0, 1.0, 1.0):
            import numpy as np

            r_gain, g_gain, b_gain = self.rgb_gains
            if b_gain != 1.0:
                img[:, :, 0] = np.clip(
                    img[:, :, 0].astype(np.float32) * b_gain, 0, 255
                ).astype(np.uint8)
            if g_gain != 1.0:
                img[:, :, 1] = np.clip(
                    img[:, :, 1].astype(np.float32) * g_gain, 0, 255
                ).astype(np.uint8)
            if r_gain != 1.0:
                img[:, :, 2] = np.clip(
                    img[:, :, 2].astype(np.float32) * r_gain, 0, 255
                ).astype(np.uint8)
        return img

    def set_paused(self, paused: bool) -> None:
        """Переключает статус паузы (при выполнении SNAP)."""
        with self.lock:
            self.is_paused = paused
            self.last_activity_time = time.time()
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

            self._stop_internal()

            self.active_camera_id = camera_hardware_id
            self.current_width = width
            self.current_height = height
            self.current_format = pixel_format
            self.current_port = port
            self.last_activity_time = time.time()
            self._stop_event.clear()

            has_ustreamer = shutil.which("ustreamer") is not None
            if self.mode == "rpi" and has_ustreamer:
                dev_path = f"/dev/v4l/by-id/{camera_hardware_id}"
                if (
                    self.v4l_by_id_dir
                    and (self.v4l_by_id_dir / camera_hardware_id).exists()
                ):
                    dev_path = os.path.realpath(
                        str(self.v4l_by_id_dir / camera_hardware_id)
                    )

                cmd = [
                    "ustreamer",
                    "--device",
                    dev_path,
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "-m",
                    pixel_format,
                    "-r",
                    f"{width}x{height}",
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
                        stderr=subprocess.DEVNULL,
                    )
                    time.sleep(0.3)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        f"[LIVE] Не удалось запустить ustreamer: {exc}. Переход на внутренний генератор."
                    )
                    self.process = None

            self.is_running = True
            self.is_paused = False

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
            self.process = None

        self.is_running = False
        self.is_paused = False

    def stop(self) -> None:
        """Останавливает Live-стрим."""
        with self.lock:
            self._stop_internal()
            logger.info("[LIVE] Стрим остановлен")

    def _generate_frame(self) -> bytes:
        """Генерирует или считывает свежий кадр с применением RGB-коррекции."""
        if self.is_paused and self._last_frame:
            return self._last_frame

        raw_frame: bytes | None = None

        # 1. Попытка забрать из ustreamer /snapshot при его наличии
        if self.process and self.process.poll() is None:
            with contextlib.suppress(Exception):
                import urllib.request

                req = urllib.request.Request(
                    f"http://127.0.0.1:{self.current_port}/snapshot"
                )
                with urllib.request.urlopen(req, timeout=0.4) as resp:
                    if resp.status == 200:
                        raw_frame = resp.read()

        # 2. Mock или fallback генерация кадра
        if raw_frame is None:
            from edge_agent.synthetic import generate_synthetic_frame

            raw_frame = generate_synthetic_frame(
                frame_index=self._mock_counter,
                camera_id=self.active_camera_id or "live-cam",
                width=self.current_width,
                height=self.current_height,
            )
            self._mock_counter += 1

        # 3. Применение программной RGB-коррекции
        if self.rgb_gains != (1.0, 1.0, 1.0):
            with contextlib.suppress(Exception):
                import cv2
                import numpy as np

                nparr = np.frombuffer(raw_frame, np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                if img is not None:
                    img = self.apply_rgb_gains(img)
                    success, enc = cv2.imencode(".jpg", img)
                    if success:
                        raw_frame = enc.tobytes()

        self._last_frame = raw_frame
        return raw_frame

    def _push_loop(self) -> None:
        """Фоновый цикл отправки кадров на сервер и watchdog неактивности."""
        consecutive_idle_sec = 0

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

            # Получаем свежий кадр
            frame_bytes = self._generate_frame()
            now_ts = time.time()

            # Отправка кадра на сервер через HTTP клиент, если доступен
            if self.client and self.active_camera_id:
                try:
                    res = self.client.send_live_frame(
                        hardware_id=self.active_camera_id,
                        frame_bytes=frame_bytes,
                        frame_timestamp=now_ts,
                        is_paused=self.is_paused,
                        resolution=f"{self.current_width}x{self.current_height}",
                    )
                    if res and res.get("active_subscribers", 0) > 0:
                        self.touch()
                        consecutive_idle_sec = 0
                    else:
                        consecutive_idle_sec += 0.05
                        if consecutive_idle_sec >= 120.0:
                            logger.info(
                                "[LIVE] Нет активных подписчиков в браузере более 120 с, автоостановка"
                            )
                            self.stop()
                            break
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"[LIVE] Ошибка отправки кадра на сервер: {exc}")

            # Регулировка FPS: целевой FPS ~25 (интервал ~40 мс)
            elapsed = time.time() - loop_start
            sleep_time = max(0.01, 0.04 - elapsed)
            time.sleep(sleep_time)
