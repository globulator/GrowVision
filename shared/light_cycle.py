"""Утилиты валидации времени светового дня (HH:MM) и расчёта фазы День/Ночь."""

from __future__ import annotations

import re
from datetime import datetime, time
from typing import Any, Literal

DEFAULT_DAY_START = "08:00"
DEFAULT_DAY_END = "22:00"

DayNightPhase = Literal["day", "night"]

_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def normalize_hhmm(value: Any, *, field_label: str = "Время") -> str:
    """Проверяет строку времени HH:MM (00:00–23:59) и нормализацию в формат 'HH:MM'."""
    if isinstance(value, bool) or not isinstance(value, str):
        raise TypeError(
            f"{field_label}: ожидается строка времени в формате HH:MM (00:00–23:59)"
        )
    raw = value.strip()
    match = _HHMM_RE.match(raw)
    if not match:
        raise ValueError(
            f"{field_label}: некорректный формат времени '{raw}'. "
            "Используйте HH:MM в диапазоне 00:00–23:59 (например, 08:00)"
        )
    hours = int(match.group(1))
    minutes = int(match.group(2))
    return f"{hours:02d}:{minutes:02d}"


def parse_hhmm(value: Any, *, field_label: str = "Время") -> time:
    """Валидирует время HH:MM и возвращает объект datetime.time."""
    norm = normalize_hhmm(value, field_label=field_label)
    hours_str, minutes_str = norm.split(":")
    return time(int(hours_str), int(minutes_str))


def validate_day_night_window(start_raw: Any, end_raw: Any) -> tuple[str, str]:
    """Нормализует начало и конец светового дня и запрещает start == end."""
    start = normalize_hhmm(start_raw, field_label="Начало дня")
    end = normalize_hhmm(end_raw, field_label="Конец дня")
    if start == end:
        raise ValueError(
            "Время начала и окончания светового дня не могут совпадать (start == end)"
        )
    return start, end


def is_day_phase(
    start_hhmm: str,
    end_hhmm: str,
    now_time: time | datetime | None = None,
) -> bool:
    """Определяет, является ли текущее время дневной фазой светового цикла.

    - Обычный интервал (start < end, например 08:00–22:00): True при start <= now < end.
    - Переход через полночь (start > end, например 22:00–06:00): True при now >= start or now < end.
    - При now_time=None используется локальное время сервера datetime.now().time().
    """
    start_t = parse_hhmm(start_hhmm, field_label="Начало дня")
    end_t = parse_hhmm(end_hhmm, field_label="Конец дня")
    if start_t == end_t:
        raise ValueError(
            "Время начала и окончания светового дня не могут совпадать (start == end)"
        )

    if now_time is None:
        cur = datetime.now().time()
    elif isinstance(now_time, datetime):
        cur = now_time.time()
    elif isinstance(now_time, time):
        cur = now_time
    else:
        raise TypeError(
            "now_time должен быть datetime.time, datetime.datetime или None"
        )

    if cur.tzinfo is not None:
        cur = cur.replace(tzinfo=None)

    if start_t < end_t:
        return start_t <= cur < end_t
    return cur >= start_t or cur < end_t


def get_day_night_phase(
    start_hhmm: str,
    end_hhmm: str,
    now_time: time | datetime | None = None,
) -> DayNightPhase:
    """Возвращает 'day' или 'night' для заданного интервала светового дня."""
    return "day" if is_day_phase(start_hhmm, end_hhmm, now_time=now_time) else "night"
