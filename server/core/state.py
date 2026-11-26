import asyncio
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

TEST_SNAPSHOT_TTL_SECONDS = 600.0


@dataclass
class EphemeralSnapshot:
    """Эфемерный тестовый снимок, хранимый только в памяти сервера с TTL."""

    snapshot_id: str
    installation_id: int
    camera_id: int
    camera_hardware_id: str
    captured_at: str
    raw_bytes: bytes
    annotated_bytes: bytes
    area_display: str | None = None
    measurements: list[dict[str, Any]] = field(default_factory=list)
    aruco_area_px: float | None = None
    command_id: int | None = None
    created_at: float = 0.0
    expires_at: float = 0.0


class InMemoryState:
    """Хранилище временного состояния в памяти одного воркера сервера.

    Используется для связи command_id -> capture_id, результатов сканирования
    камер и эфемерного кэша тестовых снимков (TTL 10 минут) без записи в БД.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._command_to_captures: dict[int, list[int]] = {}
        self._command_to_scanned_cameras: dict[int, list[str]] = {}
        self._command_to_data: dict[int, dict[str, Any]] = {}
        self._pin_states: dict[int, bool] = {}
        self._test_snapshots: dict[str, EphemeralSnapshot] = {}
        self._command_to_test_snapshots: dict[int, list[str]] = {}
        self._latest_test_snapshot_by_installation: dict[int, str] = {}
        self._command_events: dict[
            int, list[tuple[asyncio.AbstractEventLoop, asyncio.Event]]
        ] = {}

    def set_pin_state(self, pin: int, state: bool) -> None:
        """Сохраняет состояние реле для GPIO-пина."""
        with self._lock:
            self._pin_states[pin] = state

    def get_pin_state(self, pin: int, default: bool = False) -> bool:
        """Возвращает текущее состояние реле для GPIO-пина."""
        with self._lock:
            return self._pin_states.get(pin, default)

    def get_all_pin_states(self) -> dict[int, bool]:
        """Возвращает снимок состояний всех известных GPIO-пинов."""
        with self._lock:
            return dict(self._pin_states)

    def bind_command_capture(self, command_id: int, capture_id: int) -> None:
        """Связывает команду со снимком."""
        with self._lock:
            if command_id not in self._command_to_captures:
                self._command_to_captures[command_id] = []
            if capture_id not in self._command_to_captures[command_id]:
                self._command_to_captures[command_id].append(capture_id)

    def get_capture_by_command(self, command_id: int) -> int | None:
        """Возвращает ID снимка по ID команды."""
        with self._lock:
            caps = self._command_to_captures.get(command_id)
            return caps[-1] if caps else None

    def pop_capture_by_command(self, command_id: int) -> int | None:
        """Извлекает и удаляет ID снимка по ID команды."""
        with self._lock:
            caps = self._command_to_captures.pop(command_id, None)
            return caps[-1] if caps else None

    def pop_captures_by_command(self, command_id: int) -> list[int]:
        """Извлекает и удаляет все ID снимков по ID команды."""
        with self._lock:
            return self._command_to_captures.pop(command_id, [])

    def save_scanned_cameras(self, command_id: int, hardware_ids: list[str]) -> None:
        """Сохраняет список обнаруженных камер для команды сканирования."""
        with self._lock:
            self._command_to_scanned_cameras[command_id] = list(hardware_ids)

    def get_scanned_cameras(self, command_id: int) -> list[str] | None:
        """Возвращает список обнаруженных камер для команды сканирования."""
        with self._lock:
            return self._command_to_scanned_cameras.get(command_id)

    def pop_scanned_cameras(self, command_id: int) -> list[str] | None:
        """Извлекает и удаляет список обнаруженных камер для команды сканирования."""
        with self._lock:
            return self._command_to_scanned_cameras.pop(command_id, None)

    def save_command_data(self, command_id: int, data: dict[str, Any]) -> None:
        """Сохраняет дополнительные данные результата команды."""
        with self._lock:
            self._command_to_data[command_id] = dict(data)

    def pop_command_data(self, command_id: int) -> dict[str, Any] | None:
        """Извлекает и удаляет дополнительные данные результата команды."""
        with self._lock:
            return self._command_to_data.pop(command_id, None)

    def _purge_expired_test_snapshots_locked(self, now: float | None = None) -> int:
        """Удаляет просроченные эфемерные снимки (вызывается под self._lock)."""
        ts = time.monotonic() if now is None else now
        expired_ids = [
            sid for sid, snap in self._test_snapshots.items() if ts >= snap.expires_at
        ]
        if not expired_ids:
            return 0

        expired_set = set(expired_ids)
        for sid in expired_ids:
            self._test_snapshots.pop(sid, None)

        for inst_id, latest_sid in list(
            self._latest_test_snapshot_by_installation.items()
        ):
            if latest_sid in expired_set:
                self._latest_test_snapshot_by_installation.pop(inst_id, None)

        for cmd_id, sids in list(self._command_to_test_snapshots.items()):
            alive = [s for s in sids if s not in expired_set]
            if alive:
                self._command_to_test_snapshots[cmd_id] = alive
            else:
                self._command_to_test_snapshots.pop(cmd_id, None)

        return len(expired_ids)

    def save_test_snapshot(
        self,
        *,
        installation_id: int,
        camera_id: int,
        camera_hardware_id: str,
        captured_at: str,
        raw_bytes: bytes,
        annotated_bytes: bytes,
        area_display: str | None = None,
        measurements: list[dict[str, Any]] | None = None,
        aruco_area_px: float | None = None,
        command_id: int | None = None,
        ttl_seconds: float = TEST_SNAPSHOT_TTL_SECONDS,
        snapshot_id: str | None = None,
    ) -> EphemeralSnapshot:
        """Сохраняет тестовый снимок в эфемерный in-memory кэш с TTL."""
        now = time.monotonic()
        sid = snapshot_id or uuid.uuid4().hex
        snap = EphemeralSnapshot(
            snapshot_id=sid,
            installation_id=installation_id,
            camera_id=camera_id,
            camera_hardware_id=camera_hardware_id,
            captured_at=captured_at,
            raw_bytes=raw_bytes,
            annotated_bytes=annotated_bytes,
            area_display=area_display,
            measurements=list(measurements) if measurements else [],
            aruco_area_px=aruco_area_px,
            command_id=command_id,
            created_at=now,
            expires_at=now + ttl_seconds,
        )
        with self._lock:
            self._purge_expired_test_snapshots_locked(now=now)
            self._test_snapshots[sid] = snap
            self._latest_test_snapshot_by_installation[installation_id] = sid
            if command_id is not None:
                bucket = self._command_to_test_snapshots.setdefault(command_id, [])
                if sid not in bucket:
                    bucket.append(sid)
        return snap

    def get_test_snapshot(self, snapshot_id: str) -> EphemeralSnapshot | None:
        """Возвращает эфемерный тестовый снимок по ID или None, если отсутствует/истёк."""
        with self._lock:
            self._purge_expired_test_snapshots_locked()
            return self._test_snapshots.get(snapshot_id)

    def get_latest_test_snapshot_by_installation(
        self, installation_id: int
    ) -> EphemeralSnapshot | None:
        """Возвращает последний эфемерный снимок установки (если не истёк TTL)."""
        with self._lock:
            self._purge_expired_test_snapshots_locked()
            sid = self._latest_test_snapshot_by_installation.get(installation_id)
            if not sid:
                return None
            return self._test_snapshots.get(sid)

    def pop_test_snapshots_by_command(
        self, command_id: int, remove_snapshots: bool = False
    ) -> list[EphemeralSnapshot]:
        """Извлекает привязку command_id -> snapshots.

        Сами снимки остаются в кэше на время TTL (если remove_snapshots=False).
        """
        with self._lock:
            self._purge_expired_test_snapshots_locked()
            sids = self._command_to_test_snapshots.pop(command_id, [])
            result: list[EphemeralSnapshot] = []
            for sid in sids:
                snap = (
                    self._test_snapshots.pop(sid, None)
                    if remove_snapshots
                    else self._test_snapshots.get(sid)
                )
                if snap is not None:
                    result.append(snap)
            if remove_snapshots and sids:
                removed_set = set(sids)
                for inst_id, latest_sid in list(
                    self._latest_test_snapshot_by_installation.items()
                ):
                    if latest_sid in removed_set:
                        self._latest_test_snapshot_by_installation.pop(inst_id, None)
            return result

    def cleanup_expired_test_snapshots(self, now: float | None = None) -> int:
        """Очищает просроченные эфемерные снимки и возвращает количество удалённых."""
        with self._lock:
            return self._purge_expired_test_snapshots_locked(now=now)

    def clear(self) -> None:
        """Очищает память процесса (для тестов)."""
        with self._lock:
            self._command_to_captures.clear()
            self._command_to_scanned_cameras.clear()
            self._command_to_data.clear()
            self._pin_states.clear()
            self._test_snapshots.clear()
            self._command_to_test_snapshots.clear()
            self._latest_test_snapshot_by_installation.clear()
            self._command_events.clear()

    def register_command_event(self, command_id: int) -> asyncio.Event:
        """Регистрирует asyncio.Event ожидания завершения команды для текущего event loop."""
        loop = asyncio.get_running_loop()
        event = asyncio.Event()
        with self._lock:
            self._command_events.setdefault(command_id, []).append((loop, event))
        return event

    def unregister_command_event(self, command_id: int, event: asyncio.Event) -> None:
        """Удаляет зарегистрированный asyncio.Event ожидания команды."""
        with self._lock:
            entries = self._command_events.get(command_id)
            if not entries:
                return
            remaining = [(lp, evt) for lp, evt in entries if evt is not event]
            if remaining:
                self._command_events[command_id] = remaining
            else:
                self._command_events.pop(command_id, None)

    def notify_command_event(self, command_id: int) -> None:
        """Потокобезопасно уведомляет все корутины, ожидающие завершения команды."""
        with self._lock:
            entries = list(self._command_events.get(command_id, ()))
        for loop, event in entries:
            if loop.is_closed():
                continue
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:
                continue

    def cleanup_stale_entries(self, db: Any, max_age_seconds: float = 300.0) -> int:
        """Удаляет записи app_state для команд, завершенных более max_age_seconds назад."""
        from datetime import UTC, datetime

        from server.db.models import EdgeCommand

        with self._lock:
            cleaned_count = self._purge_expired_test_snapshots_locked()
            candidate_ids = (
                set(self._command_to_captures.keys())
                | set(self._command_to_scanned_cameras.keys())
                | set(self._command_to_test_snapshots.keys())
            )

        if not candidate_ids:
            return cleaned_count

        finished_cmds = (
            db.query(EdgeCommand)
            .filter(
                EdgeCommand.id.in_(candidate_ids),
                EdgeCommand.status.in_(["timeout", "error", "done"]),
            )
            .all()
        )

        now = datetime.now(UTC)
        to_delete = []

        for cmd in finished_cmds:
            ref_dt = cmd.started_at or cmd.created_at
            if ref_dt:
                if ref_dt.tzinfo is None:
                    ref_dt = ref_dt.replace(tzinfo=UTC)
                else:
                    ref_dt = ref_dt.astimezone(UTC)
                if (now - ref_dt).total_seconds() > max_age_seconds:
                    to_delete.append(cmd.id)

        with self._lock:
            for cid in to_delete:
                if cid in self._command_to_captures:
                    del self._command_to_captures[cid]
                    cleaned_count += 1
                if cid in self._command_to_scanned_cameras:
                    del self._command_to_scanned_cameras[cid]
                    cleaned_count += 1
                if cid in self._command_to_test_snapshots:
                    del self._command_to_test_snapshots[cid]
                    cleaned_count += 1

        return cleaned_count


app_state = InMemoryState()
