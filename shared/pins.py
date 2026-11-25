"""Утилиты парсинга GPIO-пинов (1 пин или 3 уникальных пина RGB) и работы с HEX/HSV цветами."""

from __future__ import annotations

import colorsys
import re
from typing import Any

DEFAULT_COLOR_HEX = "#FFFFFF"

_HEX_RE = re.compile(r"^[0-9a-fA-F]{6}$")
_INT_STR_RE = re.compile(r"^-?\d+$")


def parse_gpio_pins(
    value: Any,
    *,
    field_label: str = "GPIO пин",
) -> list[int]:
    """Парсит 1 пин (0..53) или ровно 3 уникальных пина через запятую (0..53).

    Возвращает список из 1 или 3 целых чисел.
    """
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
        if any(not p for p in parts):
            raise ValueError(
                f"{field_label}: некорректный формат списка пинов (используйте 1 число или 3 числа через запятую)"
            )
        if len(parts) not in (1, 3):
            raise ValueError(
                f"{field_label}: укажите 1 пин (для белой лампы) или ровно 3 уникальных пина через запятую (для RGB-ленты)"
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
                    f"{field_label}: пин {num} вне допустимого диапазона от 0 до 53"
                )
            pins.append(num)
        if len(pins) == 3 and len(set(pins)) != 3:
            raise ValueError(
                f"{field_label}: пины RGB-ленты не должны повторяться (укажите 3 уникальных пина)"
            )
        return pins

    if isinstance(value, (list, tuple)):
        if len(value) not in (1, 3):
            raise ValueError(
                f"{field_label}: укажите 1 пин или ровно 3 уникальных пина"
            )
        pins_seq: list[int] = []
        for item in value:
            if isinstance(item, bool) or not isinstance(item, int):
                raise TypeError(
                    f"{field_label}: каждый пин должен быть целым от 0 до 53"
                )
            if not (0 <= item <= 53):
                raise ValueError(
                    f"{field_label}: пин {item} вне допустимого диапазона от 0 до 53"
                )
            pins_seq.append(item)
        if len(pins_seq) == 3 and len(set(pins_seq)) != 3:
            raise ValueError(
                f"{field_label}: пины RGB-ленты не должны повторяться (укажите 3 уникальных пина)"
            )
        return pins_seq

    raise ValueError(
        f"{field_label}: ожидается целое число (0..53) или строка из 3 пинов через запятую"
    )


def format_gpio_pins(pins: list[int] | int | str) -> int | str:
    """Форматирует пины для API-ответа: int если 1 пин, 'R,G,B' если 3 пина."""
    parsed = parse_gpio_pins(pins)
    if len(parsed) == 1:
        return parsed[0]
    return ",".join(str(p) for p in parsed)


def serialize_pins_setting(pins: list[int]) -> str:
    """Сериализует список пинов для SystemSetting."""
    parsed = parse_gpio_pins(pins)
    return ",".join(str(p) for p in parsed)


def parse_pins_setting(raw: str | None, fallback_pin: int) -> list[int]:
    """Читает список пинов из SystemSetting с безопасным фолбэком на [fallback_pin]."""
    if raw is not None and str(raw).strip():
        try:
            return parse_gpio_pins(str(raw).strip())
        except ValueError:
            pass
    return [int(fallback_pin)]


def validate_light_flash_disjoint(light_pins: list[int], flash_pins: list[int]) -> None:
    """Проверяет отсутствие пересечения между пинами света и вспышки одной установки."""
    overlap = sorted(set(light_pins) & set(flash_pins))
    if overlap:
        overlap_str = ", ".join(str(p) for p in overlap)
        raise ValueError(
            f"Пины основного света и вспышки не должны пересекаться (совпадают: {overlap_str})"
        )


def normalize_hex_color(value: Any, *, default: str | None = None) -> str:
    """Нормализует HEX-цвет к формату '#RRGGBB'. Поддерживает ввод с '#' и без '#'."""
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


def hex_to_rgb255(hex_color: str) -> tuple[int, int, int]:
    """Конвертирует '#RRGGBB' в кортеж целых (R, G, B) в диапазоне 0..255."""
    norm = normalize_hex_color(hex_color)
    return (
        int(norm[1:3], 16),
        int(norm[3:5], 16),
        int(norm[5:7], 16),
    )


def hex_to_rgb01(hex_color: str) -> tuple[float, float, float]:
    """Конвертирует '#RRGGBB' в кортеж (r, g, b) в диапазоне [0.0, 1.0]."""
    r, g, b = hex_to_rgb255(hex_color)
    return (round(r / 255.0, 4), round(g / 255.0, 4), round(b / 255.0, 4))


def rgb01_to_hex(r: float, g: float, b: float) -> str:
    """Конвертирует (r, g, b) в диапазоне [0.0, 1.0] в '#RRGGBB'."""
    ri = round(max(0.0, min(1.0, float(r))) * 255)
    gi = round(max(0.0, min(1.0, float(g))) * 255)
    bi = round(max(0.0, min(1.0, float(b))) * 255)
    return f"#{ri:02X}{gi:02X}{bi:02X}"


def hsv_to_rgb01(
    h: float, s: float = 1.0, v: float = 1.0
) -> tuple[float, float, float]:
    """Конвертирует HSV (h, s, v в [0.0, 1.0]) в (r, g, b) в [0.0, 1.0]."""
    hh = float(h) % 1.0
    ss = max(0.0, min(1.0, float(s)))
    vv = max(0.0, min(1.0, float(v)))
    r, g, b = colorsys.hsv_to_rgb(hh, ss, vv)
    return (round(r, 4), round(g, 4), round(b, 4))


def hsv_to_hex(h: float, s: float = 1.0, v: float = 1.0) -> str:
    """Конвертирует HSV (h, s, v в [0.0, 1.0]) в '#RRGGBB'."""
    r, g, b = hsv_to_rgb01(h, s, v)
    return rgb01_to_hex(r, g, b)


def rainbow_hex_steps(steps: int = 36) -> list[str]:
    """Генерирует плавный спектр радуги от красного через оранжевый/жёлтый/зелёный/голубой/синий до фиолетового."""
    count = max(1, int(steps))
    if count == 1:
        return ["#FF0000"]
    # H от 0° (красный) до 300° (фиолетовый/пурпурный)
    max_hue = 300.0 / 360.0
    return [hsv_to_hex((i / (count - 1)) * max_hue, 1.0, 1.0) for i in range(count)]
