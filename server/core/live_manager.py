import asyncio
import time
from typing import Any


class LiveCameraSession:
    """Сессия Live-стрима для одной камеры."""

    def __init__(self, hardware_id: str, camera_id: int | None = None):
        self.hardware_id = hardware_id
        self.camera_id = camera_id
        self.latest_frame: bytes | None = None
        self.frame_timestamp: float = time.time()
        self.resolution: str = "1280x720"
        self.fps: float = 0.0
        self.latency_ms: int = 0
        self.is_paused: bool = False
        self.is_active: bool = False
        self.last_heartbeat: float = time.time()
        self.active_subscribers: int = 0
        self.frame_counter: int = 0
        self._last_fps_time: float = time.time()
        self._frames_since_fps: int = 0
        self.condition = asyncio.Condition()

    def push_frame(
        self,
        frame_bytes: bytes,
        frame_timestamp: float | None = None,
        is_paused: bool = False,
        resolution: str | None = None,
    ) -> None:
        """Принимает свежий кадр от агента, отбрасывая старые."""
        now = time.time()
        self.latest_frame = frame_bytes
        self.is_paused = is_paused
        self.is_active = True
        self.last_heartbeat = now
        self.frame_counter += 1

        if resolution:
            self.resolution = resolution

        if frame_timestamp:
            self.frame_timestamp = frame_timestamp
            self.latency_ms = max(0, int((now - frame_timestamp) * 1000))
        else:
            self.frame_timestamp = now
            self.latency_ms = 15

        # Расчет FPS каждые 1.0 с
        self._frames_since_fps += 1
        elapsed = now - self._last_fps_time
        if elapsed >= 1.0:
            self.fps = round(self._frames_since_fps / elapsed, 1)
            self._frames_since_fps = 0
            self._last_fps_time = now

    def to_status_dict(self) -> dict[str, Any]:
        """Возвращает статус Live-сессии для клиентов."""
        return {
            "hardware_id": self.hardware_id,
            "camera_id": self.camera_id,
            "is_active": self.is_active,
            "is_paused": self.is_paused,
            "fps": self.fps if self.is_active and not self.is_paused else 0.0,
            "latency_ms": self.latency_ms if self.is_active else 0,
            "resolution": self.resolution,
            "active_subscribers": self.active_subscribers,
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
        if hardware_id in self._sessions:
            self._sessions[hardware_id].is_active = False

    def clear(self) -> None:
        self._sessions.clear()


# Глобальный инстанс
live_manager = LiveSessionManager()
