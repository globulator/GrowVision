import asyncio
import time
from collections import deque
from typing import Any


class LiveCameraSession:
    """Сессия Live-стрима для одной камеры."""

    def __init__(self, hardware_id: str, camera_id: int | None = None):
        self.hardware_id = hardware_id
        self.camera_id = camera_id
        self.latest_frame: bytes | None = None
        self.frame_timestamp: float = time.time()
        self.last_frame_at: float = 0.0
        self.last_proxied_at: float = 0.0
        self.stream_url: str | None = None
        self.resolution: str = "1280x720"
        self.fps: float = 0.0
        self.latency_ms: int = 0
        self.is_paused: bool = False
        self.is_active: bool = False
        self.last_heartbeat: float = time.time()
        self.active_subscribers: int = 0
        self.frame_counter: int = 0
        self._frame_times: deque[float] = deque(maxlen=30)
        self.condition = asyncio.Condition()

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
    ) -> None:
        """Принимает свежий кадр от агента, отбрасывая старые."""
        now = time.time()
        self.is_paused = is_paused
        self.is_active = True
        self.last_heartbeat = now

        if resolution:
            self.resolution = resolution
        if stream_url:
            self.stream_url = stream_url

        # При активном прямом проксировании не дублируем кадры в _frame_times и не затираем latency/frame
        if (now - self.last_proxied_at) < 1.5:
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
        self.frame_counter += 1
        self.latency_ms = max(1, int(latency_ms))
        if not self.is_paused:
            self._update_fps_window(now)

    def to_status_dict(self) -> dict[str, Any]:
        """Возвращает статус Live-сессии для клиентов."""
        now = time.time()
        stale = self.last_frame_at > 0 and (now - self.last_frame_at) > 3.5
        effective_fps = (
            self.fps if self.is_active and not self.is_paused and not stale else 0.0
        )
        effective_lat = (
            self.latency_ms
            if self.is_active and not stale and self.last_frame_at > 0
            else 0
        )
        return {
            "hardware_id": self.hardware_id,
            "camera_id": self.camera_id,
            "is_active": self.is_active,
            "is_paused": self.is_paused,
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
        session = self._sessions.get(hardware_id)
        if session:
            session.is_active = False
            session.stream_url = None
            session.latest_frame = None
            session.last_proxied_at = 0.0
            session.fps = 0.0
            session.latency_ms = 0
            session._frame_times.clear()

    def clear(self) -> None:
        self._sessions.clear()


# Глобальный инстанс
live_manager = LiveSessionManager()
