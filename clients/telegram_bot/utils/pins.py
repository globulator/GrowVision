"""Локальные утилиты валидации GPIO-пинов и HEX-цветов для Telegram-бота (без зависимости от shared)."""

from __future__ import annotations

import re
from typing import Any

DEFAULT_COLOR_HEX = "#FFFFFF"
DEFAULT_DAY_START = "08:00"
DEFAULT_DAY_END = "22:00"

_HEX_RE = re.compile(r"^[0-9a-fA-F]{6}$")
_INT_STR_RE = re.compile(r"^-?\d+$")
_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def parse_gpio_pins(value: Any, *, field_label: str = "GPIO пин") -> list[int]:
    """Парсит 1 пин (0..53) или 3 уникальных пина через запятую (0..53)."""
    if isinstance(value, bool):
        raise TypeError(f"{field_label} должен быть целым числом от 0 до 53")
    if isinstance(value, int):
        if not (0 <= value <= 53):
            raise ValueError(f"{field_label} должен быть от 0 до 53")
        return [value]
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            raise ValueError(
                f"{field_label}: укажите 1 пин (0..53) или 3 уникальных пина через запятую"
            )
        parts = [p.strip() for p in raw.split(",")]
        if any(not p for p in parts) or len(parts) not in (1, 3):
            raise ValueError(
                f"{field_label}: укажите 1 пин (от 0 до 53) или ровно 3 уникальных пина через запятую"
            )
        pins: list[int] = []
        for part in parts:
            if not _INT_STR_RE.match(part):
                raise ValueError(
                    f"{field_label}: '{part}' не является целым числом от 0 до 53"
                )
            num = int(part)
            if not (0 <= num <= 53):
                raise ValueError(
                    f"{field_label}: пин {num} должен быть в диапазоне от 0 до 53"
                )
            pins.append(num)
        if len(pins) == 3 and len(set(pins)) != 3:
            raise ValueError(
                f"{field_label}: пины RGB-ленты не должны повторяться (укажите 3 уникальных пина)"
            )
        return pins
    raise ValueError(
        f"{field_label}: ожидается целое число от 0 до 53 или 3 пина через запятую"
    )


def format_gpio_pins(pins: list[int]) -> int | str:
    """Форматирует список пинов: int (1 пин) или 'R,G,B' (3 пина)."""
    if len(pins) == 1:
        return pins[0]
    return ",".join(str(p) for p in pins)


def validate_light_flash_disjoint(light_pins: list[int], flash_pins: list[int]) -> None:
    """Проверяет отсутствие пересечения пинов света и вспышки одной установки."""
    overlap = sorted(set(light_pins) & set(flash_pins))
    if overlap:
        overlap_str = ", ".join(str(p) for p in overlap)
        raise ValueError(
            f"Пины основного света и вспышки не должны пересекаться (совпадают: {overlap_str})"
        )


def normalize_hex_color(value: Any, *, default: str | None = None) -> str:
    """Нормализует HEX-цвет к формату '#RRGGBB'."""
    if value is None:
        if default is not None:
            return normalize_hex_color(default)
        raise ValueError("HEX-цвет не может быть пустым")
    if not isinstance(value, str):
        raise TypeError("HEX-цвет должен быть строкой формата #RRGGBB")
    cleaned = value.strip().removeprefix("#")
    if not _HEX_RE.match(cleaned):
        raise ValueError(
            "Некорректный HEX-цвет. Используйте формат #RRGGBB (например, #FF8800)"
        )
    return f"#{cleaned.upper()}"


def normalize_hhmm(value: Any, *, field_label: str = "Время") -> str:
    """Валидирует время HH:MM (00:00–23:59) и нормализует к 'HH:MM'."""
    if isinstance(value, bool) or not isinstance(value, str):
        raise TypeError(f"{field_label}: ожидается время в формате HH:MM (00:00–23:59)")
    raw = value.strip()
    match = _HHMM_RE.match(raw)
    if not match:
        raise ValueError(
            f"{field_label}: некорректное время '{raw}'. Используйте формат HH:MM (например, 08:00)"
        )
    return f"{int(match.group(1)):02d}:{int(match.group(2)):02d}"


def parse_day_night_interval(raw: Any) -> tuple[str, str]:
    """Парсит строку интервала вида 'HH:MM-HH:MM' и запрещает start == end."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(
            "Введите интервал в формате HH:MM-HH:MM (например: 08:00-22:00)"
        )
    cleaned = raw.strip().replace("–", "-").replace("—", "-")
    parts = [p.strip() for p in cleaned.split("-")]
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(
            "Некорректный формат интервала. Введите HH:MM-HH:MM (например: 08:00-22:00)"
        )
    start = normalize_hhmm(parts[0], field_label="Начало дня")
    end = normalize_hhmm(parts[1], field_label="Конец дня")
    if start == end:
        raise ValueError(
            "Время начала и окончания светового дня не могут совпадать (start == end)"
        )
    return start, end
