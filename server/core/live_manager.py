import asyncio
import contextlib
import time
from collections import deque
from typing import Any


class LiveCameraSession:
    """Сессия Live-стрима для одной камеры."""

    def __init__(self, hardware_id: str, camera_id: int | None = None):
        self.hardware_id = hardware_id
        self.camera_id = camera_id
        self.latest_frame: bytes | None = None
        self.latest_raw_frame: bytes | None = None
        self.latest_annotated_frame: bytes | None = None
        self.frame_timestamp: float = time.time()
        self.last_frame_at: float = 0.0
        self.last_proxied_at: float = 0.0
        self.stream_url: str | None = None
        self.resolution: str = "1280x720"
        self.fps: float = 0.0
        self.latency_ms: int = 0
        self.is_paused: bool = False
        self.is_active: bool = False
        self.mode: str = "raw"
        self.segmentation_mode: str = "auto"
        self.device_online: bool = True
        self.color_marker_detected: bool = False
        self.stream_error: str | None = None
        self._last_state_probe_at: float = 0.0
        self.last_heartbeat: float = time.time()
        self.started_at: float = time.time()
        self.active_subscribers: int = 0
        self.frame_counter: int = 0
        self._frame_times: deque[float] = deque(maxlen=30)
        self.condition = asyncio.Condition()

    def set_mode(self, mode: str) -> None:
        """Переключает режим Live-потока ('raw' или 'annotated') со сбросом окна метрик."""
        norm = "annotated" if str(mode).lower() == "annotated" else "raw"
        if norm != self.mode:
            self.mode = norm
            self._frame_times.clear()
            self.fps = 0.0
            self.latency_ms = 0
            self.last_proxied_at = 0.0
            self.latest_frame = (
                self.latest_annotated_frame
                if norm == "annotated"
                else self.latest_raw_frame
            )

    def set_segmentation_mode(self, mode: str) -> None:
        """Устанавливает режим сегментации ('auto', 'onnx' или 'hsv')."""
        raw = str(mode or "auto").strip().lower()
        self.segmentation_mode = raw if raw in ("auto", "onnx", "hsv") else "auto"

    def _update_fps_window(self, now: float) -> None:
        """Обновляет мгновенный FPS по скользящему окну последних кадров."""
        self._frame_times.append(now)
        while len(self._frame_times) > 2 and (now - self._frame_times[0]) > 3.0:
            self._frame_times.popleft()
        if len(self._frame_times) >= 2:
            span = self._frame_times[-1] - self._frame_times[0]
            if span >= 0.05:
                self.fps = round((len(self._frame_times) - 1) / span, 1)

    def push_frame(
        self,
        frame_bytes: bytes,
        frame_timestamp: float | None = None,
        is_paused: bool = False,
        resolution: str | None = None,
        latency_ms: int | None = None,
        stream_url: str | None = None,
        frame_mode: str = "raw",
        device_online: bool | None = None,
    ) -> None:
        """Принимает свежий кадр от агента, отбрасывая старые."""
        now = time.time()
        self.is_paused = is_paused
        self.is_active = True
        self.last_heartbeat = now

        if device_online is not None:
            self.device_online = bool(device_online)
            self.stream_error = None if self.device_online else "Камера отключена"

        if resolution:
            self.resolution = resolution
        if stream_url:
            self.stream_url = stream_url

        norm_mode = "annotated" if str(frame_mode).lower() == "annotated" else "raw"
        if norm_mode == "annotated":
            self.latest_annotated_frame = frame_bytes
        else:
            self.latest_raw_frame = frame_bytes

        # Отбрасываем кадры несоответствующего режима (переходный период при переключении)
        if norm_mode != self.mode:
            return

        # При активном прямом проксировании raw-потока не дублируем кадры в _frame_times и не затираем latency/frame
        if norm_mode == "raw" and (now - self.last_proxied_at) < 1.5:
            return

        self.latest_frame = frame_bytes
        self.last_frame_at = now
        self.frame_counter += 1

        measured_lat = (
            int(latency_ms) if latency_ms is not None and latency_ms > 0 else 0
        )
        if frame_timestamp:
            self.frame_timestamp = frame_timestamp
            delta = now - frame_timestamp
            if 0.0 < delta < 5.0:
                self.latency_ms = max(1, measured_lat, int(delta * 1000))
            else:
                self.latency_ms = max(1, measured_lat) if measured_lat > 0 else 15
        else:
            self.frame_timestamp = now
            self.latency_ms = max(1, measured_lat) if measured_lat > 0 else 15

        if not is_paused:
            self._update_fps_window(now)

    def record_proxied_frame(self, latency_ms: int = 10) -> None:
        """Фиксирует кадр, полученный сервером напрямую через проксирование ustreamer."""
        now = time.time()
        self.last_frame_at = now
        self.last_proxied_at = now
        self.last_heartbeat = now
        self.is_active = True
        self.device_online = True
        self.stream_error = None
        self.frame_counter += 1
        self.latency_ms = max(1, int(latency_ms))
        if not self.is_paused:
            self._update_fps_window(now)

    def to_status_dict(self) -> dict[str, Any]:
        """Возвращает статус Live-сессии для клиентов."""
        now = time.time()
        stale = self.last_frame_at > 0 and (now - self.last_frame_at) > 3.5
        effective_fps = (
            self.fps
            if self.is_active
            and not self.is_paused
            and self.device_online
            and not stale
            else 0.0
        )
        effective_lat = (
            self.latency_ms
            if self.is_active
            and self.device_online
            and not stale
            and self.last_frame_at > 0
            else 0
        )
        return {
            "hardware_id": self.hardware_id,
            "camera_id": self.camera_id,
            "is_active": self.is_active,
            "is_paused": self.is_paused,
            "mode": self.mode,
            "segmentation_mode": self.segmentation_mode,
            "device_online": self.device_online,
            "color_marker_detected": self.color_marker_detected,
            "stream_error": self.stream_error,
            "fps": effective_fps,
            "latency_ms": effective_lat,
            "resolution": self.resolution,
            "active_subscribers": self.active_subscribers,
            "stream_url": self.stream_url,
            "has_frame": self.latest_frame is not None,
        }


class LiveSessionManager:
    """Глобальный менеджер Live-сессий камер."""

    def __init__(self):
        self._sessions: dict[str, LiveCameraSession] = {}
        self._lock = asyncio.Lock()
        self.shutdown_event: asyncio.Event = asyncio.Event()
        self._is_shutting_down: bool = False
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def is_shutting_down(self) -> bool:
        """True, если сервер начал остановку."""
        return self._is_shutting_down or self.shutdown_event.is_set()

    def bind_loop(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Привязывает event loop сервера для безопасного пробуждения при shutdown."""
        if loop is None:
            with contextlib.suppress(RuntimeError):
                loop = asyncio.get_running_loop()
        if loop is not None:
            self._loop = loop

    def reset_shutdown(self) -> None:
        """Сбрасывает состояние завершения при запуске приложения или очистке тестов."""
        self._is_shutting_down = False
        self.shutdown_event = asyncio.Event()

    def _notify_session_async(self, session: LiveCameraSession) -> None:
        """Будит подписчиков сессии внутри активного event loop."""
        with contextlib.suppress(RuntimeError):
            loop = asyncio.get_running_loop()

            async def _notify() -> None:
                async with session.condition:
                    session.condition.notify_all()

            loop.create_task(_notify())

    def _apply_shutdown_in_loop(self) -> None:
        """Устанавливает shutdown_event и будит все ожидающие live-стримы в event loop."""
        self.shutdown_event.set()
        for session in list(self._sessions.values()):
            session.is_active = False
            self._notify_session_async(session)

    def request_shutdown(self) -> None:
        """Сигнализирует о мгновенном завершении всех активных Live-стримов."""
        self._is_shutting_down = True
        for hw_id in list(self._sessions.keys()):
            self.stop(hw_id)

        loop = self._loop
        if loop is not None and not loop.is_closed():
            with contextlib.suppress(RuntimeError):
                running = asyncio.get_running_loop()
                if running is loop:
                    self._apply_shutdown_in_loop()
                    return
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(self._apply_shutdown_in_loop)
                return

        self.shutdown_event.set()

    def get_or_create(
        self, hardware_id: str, camera_id: int | None = None
    ) -> LiveCameraSession:
        if hardware_id not in self._sessions:
            self._sessions[hardware_id] = LiveCameraSession(
                hardware_id=hardware_id, camera_id=camera_id
            )
        session = self._sessions[hardware_id]
        if camera_id is not None:
            session.camera_id = camera_id
        return session

    def get(self, hardware_id: str) -> LiveCameraSession | None:
        return self._sessions.get(hardware_id)

    def stop(self, hardware_id: str) -> None:
        session = self._sessions.pop(hardware_id, None)
        if session:
            session.is_active = False
            session.stream_url = None
            session.latest_frame = None
            session.latest_raw_frame = None
            session.latest_annotated_frame = None
            session.mode = "raw"
            session.device_online = True
            session.color_marker_detected = False
            session.stream_error = None
            session.last_proxied_at = 0.0
            session.fps = 0.0
            session.latency_ms = 0
            session._frame_times.clear()
            self._notify_session_async(session)

    def stop_all(self) -> None:
        """Останавливает все активные Live-сессии."""
        for hw_id in list(self._sessions.keys()):
            self.stop(hw_id)

    def clear(self) -> None:
        self._sessions.clear()
        self.reset_shutdown()


# Глобальный инстанс
live_manager = LiveSessionManager()
