import threading
from typing import Any


class InMemoryState:
    """Хранилище временного состояния в памяти одного воркера сервера.

    Используется для связи command_id -> capture_id и результатов сканирования
    камер на время ожидания клиентских запросов по API_CONTRACT.md п. 3.3, 3.4.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._command_to_captures: dict[int, list[int]] = {}
        self._command_to_scanned_cameras: dict[int, list[str]] = {}

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

    def clear(self) -> None:
        """Очищает память процесса (для тестов)."""
        with self._lock:
            self._command_to_captures.clear()
            self._command_to_scanned_cameras.clear()

    def cleanup_stale_entries(self, db: Any, max_age_seconds: float = 300.0) -> int:
        """Удаляет записи app_state для команд, завершенных более max_age_seconds назад."""
        from datetime import UTC, datetime

        from server.db.models import EdgeCommand

        with self._lock:
            candidate_ids = set(self._command_to_captures.keys()) | set(
                self._command_to_scanned_cameras.keys()
            )

        if not candidate_ids:
            return 0

        finished_cmds = (
            db.query(EdgeCommand)
            .filter(
                EdgeCommand.id.in_(candidate_ids),
                EdgeCommand.status.in_(["timeout", "error", "done"]),
            )
            .all()
        )

        now = datetime.now(UTC)
        cleaned_count = 0
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

        return cleaned_count


app_state = InMemoryState()
