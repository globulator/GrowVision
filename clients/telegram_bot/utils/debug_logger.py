import json
import logging
import re
import time
from collections import deque
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
        # Маскируем пароли в URL вида ://user:pass@
        res = re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", data)
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

        with contextlib_suppress_all():
            self.log_dir.mkdir(parents=True, exist_ok=True)
            self._init_bug_counter()
            self.cleanup_old_logs(max_days=7)

    def _init_bug_counter(self) -> None:
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

    def log_event(
        self,
        event_type: str,
        user_id: int | None = None,
        state: str | None = None,
        **data: Any,
    ) -> dict[str, Any]:
        """Логирует событие в кольцевой буфер и на диск (если enabled=True)."""
        clean_data = mask_sensitive_data(data)
        event_record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event_type": event_type,
            "user_id": user_id,
            "state": state,
            "data": clean_data,
        }

        self._buffer.append(event_record)

        if self.enabled:
            try:
                self.log_dir.mkdir(parents=True, exist_ok=True)
                with open(self._get_today_log_path(), "a", encoding="utf-8") as f:
                    f.write(json.dumps(event_record, ensure_ascii=False) + "\n")
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Не удалось записать событие в лог: {exc}")

        return event_record

    def record_bug(
        self,
        note: str,
        user_id: int | None = None,
        state: str | None = None,
    ) -> int:
        """Записывает заметку о баге (сообщение на *) с контекстом последних 20 событий."""
        self._bug_counter += 1
        bug_id = self._bug_counter
        bug_tag = f"BUG-LOG-{bug_id}"

        # Делаем снимок событий из буфера до текущего репорта
        recent = list(self._buffer)

        clean_note = mask_sensitive_data(note)
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
        self.log_event(
            event_type="bug_report_created",
            user_id=user_id,
            state=state,
            bug_id=bug_id,
            bug_tag=bug_tag,
            note=clean_note,
        )

        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            with open(self.log_dir / "bug_notes.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(bug_record, ensure_ascii=False) + "\n")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Не удалось сохранить баг-заметку: {exc}")

        return bug_id

    def get_recent_events(self) -> list[dict[str, Any]]:
        """Возвращает текущие события из кольцевого буфера."""
        return list(self._buffer)


class contextlib_suppress_all:
    """Вспомогательный контекстный менеджер для подавления ошибок при инициализации."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return True


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


def set_debug_logger(instance: DebugLogger) -> None:
    global _global_logger
    _global_logger = instance
