import asyncio
import contextlib
import json
import logging
import re
import threading
import time
from collections import deque
from collections.abc import Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Поля, значения которых подлежат маскированию
SENSITIVE_KEYS = {
    "password",
    "pass",
    "token",
    "client_api_key",
    "client_key",
    "secret",
    "authorization",
    "x-client-key",
}


def mask_sensitive_data(data: Any) -> Any:
    """Рекурсивно маскирует пароли, токены и секретные ключи в структурах данных."""
    if isinstance(data, dict):
        masked_dict = {}
        for k, v in data.items():
            key_lower = str(k).lower().replace("_", "").replace("-", "")
            if any(
                sensitive in key_lower
                for sensitive in (
                    "password",
                    "secret",
                    "clientkey",
                    "token",
                    "apikey",
                )
            ):
                masked_dict[k] = "***"
            else:
                masked_dict[k] = mask_sensitive_data(v)
        return masked_dict
    elif isinstance(data, list):
        return [mask_sensitive_data(item) for item in data]
    elif isinstance(data, str):
        # Маскируем логин и пароль в URL вида ://user:pass@host:port или ://user@host
        res = re.sub(r"://([^:/@]+):([^/@]+)@", r"://***:***@", data)
        res = re.sub(r"://([^:/@]+)@", r"://***@", res)
        # Маскируем токены в строках вида X-Client-Key: ...
        res = re.sub(
            r"(X-Client-Key:\s*)([^\s\r\n]+)",
            r"\1***",
            res,
            flags=re.IGNORECASE,
        )
        return res
    return data


class DebugLogger:
    """Структурированный логгер событий Telegram-бота и заметок о багах.

    Ведёт кольцевой буфер последних 20 событий в памяти.
    При enabled=True записывает все события в суточные JSONL-файлы в log_dir.
    Заметки о багах (BUG-LOG-N) гарантированно сохраняются в bug_notes.jsonl.
    Срок хранения логов — до 7 дней (автоматическая ротация).
    """

    def __init__(self, log_dir: Path | str | None = None, enabled: bool = False):
        if log_dir is None:
            self.log_dir = Path(__file__).resolve().parent.parent / "logs"
        else:
            self.log_dir = Path(log_dir)

        self.enabled = enabled
        self._buffer: deque[dict[str, Any]] = deque(maxlen=20)
        self._bug_counter = 0
        self._io_lock = threading.Lock()
        self._pending_tasks: set[asyncio.Task[Any]] = set()
        self._initialized = False

        loop = self._get_running_loop()
        if loop is None:
            with contextlib.suppress(Exception):
                self._startup_io_sync()
        else:
            self._schedule_async_task(loop, asyncio.to_thread(self._startup_io_sync))

    @staticmethod
    def _get_running_loop() -> asyncio.AbstractEventLoop | None:
        """Возвращает текущий event loop или None, если вызывается вне async-контекста."""
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            return None

    def _schedule_async_task(
        self, loop: asyncio.AbstractEventLoop, coro: Coroutine[Any, Any, Any]
    ) -> None:
        """Планирует фоновую корутину в event loop с отслеживанием ссылки до завершения."""
        task = loop.create_task(coro)
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    def _startup_io_sync(self) -> None:
        """Синхронная первичная инициализация каталога логов, счётчика багов и ротация."""
        with self._io_lock:
            if self._initialized:
                return
            if self.enabled:
                self.log_dir.mkdir(parents=True, exist_ok=True)
            if self.log_dir.exists():
                self._init_bug_counter_unlocked()
                self._cleanup_old_logs_unlocked(max_days=7)
            self._initialized = True

    def _ensure_bug_counter_unlocked(self) -> None:
        """Лениво инициализирует счётчик багов под блокировкой, если старт был отложен."""
        if self._initialized:
            return
        if self.log_dir.exists():
            self._init_bug_counter_unlocked()
        self._initialized = True

    def _init_bug_counter(self) -> None:
        """Определяет стартовый номер баг-репорта по существующим записям."""
        with self._io_lock:
            self._init_bug_counter_unlocked()

    def _init_bug_counter_unlocked(self) -> None:
        """Определяет стартовый номер баг-репорта по существующим записям."""
        notes_file = self.log_dir / "bug_notes.jsonl"
        if not notes_file.exists():
            self._bug_counter = 0
            return

        max_id = 0
        try:
            with open(notes_file, encoding="utf-8") as f:
                for line in f:
                    line_s = line.strip()
                    if not line_s:
                        continue
                    try:
                        record = json.loads(line_s)
                        bid = record.get("bug_id")
                        if isinstance(bid, int) and bid > max_id:
                            max_id = bid
                    except Exception:  # noqa: BLE001, S110
                        pass
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Не удалось прочитать bug_notes.jsonl: {exc}")
        self._bug_counter = max_id

    def cleanup_old_logs(self, max_days: int = 7) -> None:
        """Удаляет JSONL-файлы логов старше max_days дней."""
        with self._io_lock:
            self._cleanup_old_logs_unlocked(max_days=max_days)

    async def acleanup_old_logs(self, max_days: int = 7) -> None:
        """Асинхронно удаляет JSONL-файлы логов старше max_days дней в отдельном потоке."""
        await asyncio.to_thread(self.cleanup_old_logs, max_days)

    def _cleanup_old_logs_unlocked(self, max_days: int = 7) -> None:
        """Удаляет JSONL-файлы логов старше max_days дней (вызывать под _io_lock)."""
        now = time.time()
        max_age_sec = max_days * 86400
        if not self.log_dir.exists():
            return

        for p in self.log_dir.glob("*.jsonl"):
            # Файл bug_notes.jsonl не удаляем
            if p.name == "bug_notes.jsonl":
                continue
            try:
                if now - p.stat().st_mtime > max_age_sec:
                    p.unlink(missing_ok=True)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Ошибка удаления старого лога {p}: {exc}")

    def _get_today_log_path(self) -> Path:
        today_str = datetime.now(UTC).strftime("%Y-%m-%d")
        return self.log_dir / f"events_{today_str}.jsonl"

    def _build_event_record(
        self,
        event_type: str,
        user_id: int | None,
        state: str | None,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Маскирует данные, формирует запись события и добавляет её в буфер."""
        clean_data = mask_sensitive_data(data)
        if state and "password" in state.lower():
            if "text" in clean_data:
                clean_data["text"] = "***"
            if "error" in clean_data:
                clean_data["error"] = "***"

        event_record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event_type": event_type,
            "user_id": user_id,
            "state": state,
            "data": clean_data,
        }

        self._buffer.append(event_record)
        return event_record

    def _write_event_sync(self, event_record: dict[str, Any]) -> None:
        """Синхронно дописывает одно событие в суточный JSONL-файл под блокировкой."""
        with self._io_lock:
            self._write_event_unlocked(event_record)

    def _write_event_unlocked(self, event_record: dict[str, Any]) -> None:
        """Дописывает одно событие в суточный JSONL-файл (вызывать под _io_lock)."""
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            with open(self._get_today_log_path(), "a", encoding="utf-8") as f:
                f.write(json.dumps(event_record, ensure_ascii=False) + "\n")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Не удалось записать событие в лог: {exc}")

    def log_event(
        self,
        event_type: str,
        user_id: int | None = None,
        state: str | None = None,
        **data: Any,
    ) -> dict[str, Any]:
        """Логирует событие в кольцевой буфер и на диск (если enabled=True)."""
        event_record = self._build_event_record(event_type, user_id, state, data)

        if self.enabled:
            loop = self._get_running_loop()
            if loop is None:
                self._write_event_sync(event_record)
            else:
                self._schedule_async_task(
                    loop, asyncio.to_thread(self._write_event_sync, event_record)
                )

        return event_record

    async def alog_event(
        self,
        event_type: str,
        user_id: int | None = None,
        state: str | None = None,
        **data: Any,
    ) -> dict[str, Any]:
        """Логирует событие в буфер и неблокирующе записывает на диск через asyncio.to_thread."""
        event_record = self._build_event_record(event_type, user_id, state, data)

        if self.enabled:
            await asyncio.to_thread(self._write_event_sync, event_record)

        return event_record

    def _prepare_bug_context(
        self,
        note: str,
        state: str | None,
    ) -> tuple[Any, list[dict[str, Any]]]:
        """Готовит снимок буфера и маскированную заметку до записи баг-репорта."""
        # Делаем снимок событий из буфера до текущего репорта
        recent = list(self._buffer)

        if state and "password" in state.lower():
            clean_note: Any = "***"
        else:
            clean_note = mask_sensitive_data(note)

        return clean_note, recent

    def _persist_bug_records_sync(
        self,
        bug_record: dict[str, Any],
        event_record: dict[str, Any],
    ) -> None:
        """Записывает сформированные записи бага и события на диск под блокировкой."""
        with self._io_lock:
            if self.enabled:
                self._write_event_unlocked(event_record)
            try:
                self.log_dir.mkdir(parents=True, exist_ok=True)
                with open(self.log_dir / "bug_notes.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps(bug_record, ensure_ascii=False) + "\n")
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Не удалось сохранить баг-заметку: {exc}")

    def _write_bug_sync(
        self,
        clean_note: Any,
        user_id: int | None,
        state: str | None,
        recent: list[dict[str, Any]],
    ) -> int:
        """Выделяет bug_id, обновляет буфер и синхронно сохраняет баг-заметку на диск."""
        with self._io_lock:
            self._ensure_bug_counter_unlocked()
            self._bug_counter += 1
            bug_id = self._bug_counter
            bug_tag = f"BUG-LOG-{bug_id}"

            bug_record = {
                "timestamp": datetime.now(UTC).isoformat(),
                "event_type": "bug_report",
                "bug_id": bug_id,
                "bug_tag": bug_tag,
                "user_id": user_id,
                "state": state,
                "note": clean_note,
                "recent_events": recent,
            }

            # Фиксируем само событие репорта в буфере
            event_record = self._build_event_record(
                event_type="bug_report_created",
                user_id=user_id,
                state=state,
                data={
                    "bug_id": bug_id,
                    "bug_tag": bug_tag,
                    "note": clean_note,
                },
            )
            if self.enabled:
                self._write_event_unlocked(event_record)

            try:
                self.log_dir.mkdir(parents=True, exist_ok=True)
                with open(self.log_dir / "bug_notes.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps(bug_record, ensure_ascii=False) + "\n")
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Не удалось сохранить баг-заметку: {exc}")

            return bug_id

    def record_bug(
        self,
        note: str,
        user_id: int | None = None,
        state: str | None = None,
    ) -> int:
        """Записывает заметку о баге (сообщение на *) с контекстом последних 20 событий."""
        clean_note, recent = self._prepare_bug_context(note, state)
        loop = self._get_running_loop()
        if loop is None:
            return self._write_bug_sync(clean_note, user_id, state, recent)

        # В async-контексте не выполняем синхронный I/O в потоке event loop
        self._bug_counter += 1
        bug_id = self._bug_counter
        bug_tag = f"BUG-LOG-{bug_id}"

        bug_record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event_type": "bug_report",
            "bug_id": bug_id,
            "bug_tag": bug_tag,
            "user_id": user_id,
            "state": state,
            "note": clean_note,
            "recent_events": recent,
        }

        # Фиксируем само событие репорта в буфере
        event_record = self._build_event_record(
            event_type="bug_report_created",
            user_id=user_id,
            state=state,
            data={
                "bug_id": bug_id,
                "bug_tag": bug_tag,
                "note": clean_note,
            },
        )
        self._schedule_async_task(
            loop,
            asyncio.to_thread(self._persist_bug_records_sync, bug_record, event_record),
        )
        return bug_id

    async def arecord_bug(
        self,
        note: str,
        user_id: int | None = None,
        state: str | None = None,
    ) -> int:
        """Неблокирующе записывает заметку о баге через asyncio.to_thread."""
        await self.aflush()
        clean_note, recent = self._prepare_bug_context(note, state)
        return await asyncio.to_thread(
            self._write_bug_sync, clean_note, user_id, state, recent
        )

    async def aflush(self) -> None:
        """Дожидается завершения всех отложенных фоновых задач записи и ротации."""
        current_loop = asyncio.get_running_loop()
        while True:
            tasks = [
                task
                for task in list(self._pending_tasks)
                if not task.done() and task.get_loop() is current_loop
            ]
            if not tasks:
                for task in list(self._pending_tasks):
                    if task.done():
                        self._pending_tasks.discard(task)
                break
            await asyncio.gather(*tasks, return_exceptions=True)

    def get_recent_events(self) -> list[dict[str, Any]]:
        """Возвращает текущие события из кольцевого буфера."""
        return list(self._buffer)


# Глобальный синглтон логгера для всего бота
_global_logger: DebugLogger | None = None


def get_debug_logger(
    log_dir: Path | str | None = None, enabled: bool | None = None
) -> DebugLogger:
    global _global_logger
    if _global_logger is None:
        _global_logger = DebugLogger(log_dir=log_dir, enabled=bool(enabled))
    elif enabled is not None:
        _global_logger.enabled = enabled
    return _global_logger


def set_debug_logger(instance: DebugLogger | None) -> None:
    global _global_logger
    _global_logger = instance
