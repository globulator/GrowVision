"""Локальные утилиты парсинга GPIO-пинов и HEX-цветов для CLI (автономно от shared)."""

from __future__ import annotations

import re
from typing import Any

DEFAULT_COLOR_HEX = "#FFFFFF"

_HEX_RE = re.compile(r"^[0-9a-fA-F]{6}$")
_INT_STR_RE = re.compile(r"^-?\d+$")


def parse_gpio_pins(value: Any, *, field_label: str = "GPIO пин") -> list[int]:
    """Парсит 1 пин (0..53) или ровно 3 уникальных пина через запятую (0..53)."""
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
                f"{field_label}: укажите 1 пин или ровно 3 уникальных пина через запятую"
            )
        pins: list[int] = []
        for part in parts:
            if not _INT_STR_RE.match(part):
                raise ValueError(
                    f"{field_label}: '{part}' — некорректное значение. Ожидается целое число от 0 до 53 или последовательность из 3 чисел от 0 до 53"
                )
            num = int(part)
            if not (0 <= num <= 53):
                raise ValueError(f"{field_label}: пин {num} вне диапазона. Ожидается целое число от 0 до 53 или последовательность из 3 чисел от 0 до 53")
            pins.append(num)
        if len(pins) == 3 and len(set(pins)) != 3:
            raise ValueError(f"{field_label}: пины RGB-ленты не должны повторяться")
        return pins
    raise ValueError(
        f"{field_label}: ожидается целое число (0..53) или 3 пина через запятую"
    )


def format_gpio_pins(pins: list[int]) -> int | str:
    """Форматирует пины: int если 1 пин, 'R,G,B' если 3 пина."""
    if len(pins) == 1:
        return pins[0]
    return ",".join(str(p) for p in pins)


def validate_light_flash_disjoint(light_pins: list[int], flash_pins: list[int]) -> None:
    """Проверяет отсутствие пересечения между пинами света и вспышки одной установки."""
    overlap = sorted(set(light_pins) & set(flash_pins))
    if overlap:
        overlap_str = ", ".join(str(p) for p in overlap)
        raise ValueError(
            f"Пины основного света и вспышки не должны пересекаться (совпадают: {overlap_str})"
        )


def normalize_hex_color(value: Any, *, default: str | None = None) -> str:
    """Нормализует HEX-цвет к '#RRGGBB'."""
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
