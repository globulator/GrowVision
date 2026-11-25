"""Общие спецификации и генераторы SVG-маркеров (ArUco и цветовой эталон)."""

from __future__ import annotations

# Матрица ArUco DICT_4X4_50, ID=0 (6x6 с 1-битной чёрной границей; 0 = чёрный, 1 = белый)
ARUCO_4X4_50_ID0_MATRIX: tuple[tuple[int, ...], ...] = (
    (0, 0, 0, 0, 0, 0),
    (0, 1, 0, 1, 1, 0),
    (0, 0, 1, 0, 1, 0),
    (0, 0, 0, 1, 1, 0),
    (0, 0, 0, 1, 0, 0),
    (0, 0, 0, 0, 0, 0),
)

# Каноническая геометрия эталонного цветового маркера (в условных единицах 500x200)
COLOR_MARKER_CANON_W = 500
COLOR_MARKER_CANON_H = 200
COLOR_MARKER_WHITE_FIELD = (20, 20, 460, 160)  # (x, y, w, h)

# 5 эталонных плашек в фиксированном порядке слева направо: Red, Green, Blue, White, Gray
COLOR_MARKER_PATCHES: tuple[
    tuple[str, str, tuple[int, int, int], tuple[int, int, int, int]], ...
] = (
    ("red", "#FF0000", (255, 0, 0), (40, 48, 72, 104)),
    ("green", "#00FF00", (0, 255, 0), (127, 48, 72, 104)),
    ("blue", "#0000FF", (0, 0, 255), (214, 48, 72, 104)),
    ("white", "#FFFFFF", (255, 255, 255), (301, 48, 72, 104)),
    ("gray", "#808080", (128, 128, 128), (388, 48, 72, 104)),
)


def _clamp_marker_size_mm(size_mm: float) -> float:
    """Ограничивает размер маркера разумным диапазоном для печати."""
    try:
        val = float(size_mm)
    except (TypeError, ValueError):
        return 50.0
    if val <= 0:
        return 50.0
    return max(10.0, min(300.0, val))


def _build_mm_ruler_svg(
    x0: float,
    y0: float,
    length_mm: float,
    label_suffix: str = "мм",
) -> list[str]:
    """Строит векторную миллиметровую контрольную линейку в единицах мм."""
    lines: list[str] = []
    total_mm = max(10, round(length_mm))
    x_end = x0 + length_mm
    lines.append(
        f'<line x1="{x0:.2f}" y1="{y0:.2f}" x2="{x_end:.2f}" y2="{y0:.2f}" '
        f'stroke="#111827" stroke-width="0.35"/>'
    )
    for mm in range(total_mm + 1):
        tx = x0 + mm
        if tx > x_end + 0.05:
            break
        if mm % 10 == 0:
            tick_h = 3.2
            sw = 0.35
            lines.append(
                f'<text x="{tx:.2f}" y="{y0 + tick_h + 2.8:.2f}" '
                f'font-family="DejaVu Sans, Arial, sans-serif" font-size="2.5" '
                f'fill="#111827" text-anchor="middle">{mm}</text>'
            )
        elif mm % 5 == 0:
            tick_h = 2.3
            sw = 0.25
        else:
            tick_h = 1.4
            sw = 0.18
        lines.append(
            f'<line x1="{tx:.2f}" y1="{y0:.2f}" x2="{tx:.2f}" y2="{y0 + tick_h:.2f}" '
            f'stroke="#111827" stroke-width="{sw}"/>'
        )
    lines.append(
        f'<text x="{x_end + 1.5:.2f}" y="{y0 + 1.2:.2f}" '
        f'font-family="DejaVu Sans, Arial, sans-serif" font-size="2.4" '
        f'fill="#374151">{label_suffix}</text>'
    )
    return lines


def generate_aruco_marker_svg(size_mm: float = 50.0) -> str:
    """Генерирует готовый для печати SVG-файл с маркером ArUco DICT_4X4_50 (ID=0) и мм-линейкой."""
    s_mm = _clamp_marker_size_mm(size_mm)
    margin_mm = 10.0
    ruler_area_mm = 16.0
    total_w = s_mm + 2.0 * margin_mm
    total_h = s_mm + 2.0 * margin_mm + ruler_area_mm

    grid_n = len(ARUCO_4X4_50_ID0_MATRIX)
    cell_mm = s_mm / grid_n

    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{total_w:.2f}mm" height="{total_h:.2f}mm" '
            f'viewBox="0 0 {total_w:.2f} {total_h:.2f}">'
        ),
        f'  <rect x="0" y="0" width="{total_w:.2f}" height="{total_h:.2f}" fill="#FFFFFF"/>',
        (
            f'  <text x="{total_w / 2.0:.2f}" y="{margin_mm * 0.58:.2f}" '
            f'font-family="DejaVu Sans, Arial, sans-serif" font-size="2.8" '
            f'fill="#374151" text-anchor="middle">'
            f"GrowVision ArUco DICT_4X4_50 ID=0 ({s_mm:g} мм)</text>"
        ),
        (
            f'  <g id="aruco-marker" data-dict="DICT_4X4_50" data-id="0" '
            f'data-size-mm="{s_mm:g}">'
        ),
        # Заливаем квадрат белым, поверх рисуем чёрные ячейки с микроперекрытием 0.03 мм против субпиксельных швов
        (
            f'    <rect x="{margin_mm:.2f}" y="{margin_mm:.2f}" '
            f'width="{s_mm:.2f}" height="{s_mm:.2f}" fill="#FFFFFF"/>'
        ),
    ]

    for r_idx, row in enumerate(ARUCO_4X4_50_ID0_MATRIX):
        for c_idx, bit in enumerate(row):
            if bit == 0:
                rx = margin_mm + c_idx * cell_mm
                ry = margin_mm + r_idx * cell_mm
                rw = cell_mm + (0.03 if c_idx < grid_n - 1 else 0.0)
                rh = cell_mm + (0.03 if r_idx < grid_n - 1 else 0.0)
                parts.append(
                    f'    <rect x="{rx:.3f}" y="{ry:.3f}" '
                    f'width="{rw:.3f}" height="{rh:.3f}" fill="#000000"/>'
                )

    parts.append("  </g>")

    ruler_y = margin_mm + s_mm + 6.0
    parts.append('  <g id="scale-ruler">')
    for line in _build_mm_ruler_svg(margin_mm, ruler_y, s_mm, "мм"):
        parts.append(f"    {line}")
    parts.append("  </g>")
    parts.append("</svg>")
    return "\n".join(parts)


def generate_color_marker_svg(size_mm: float = 50.0) -> str:
    """Генерирует готовый для печати SVG-файл с эталонным цветовым маркером и мм-линейкой."""
    s_mm = _clamp_marker_size_mm(size_mm)
    # Карточка 2.5 : 1 (соответствует канону 500x200), при size_mm=50 -> 100x40 мм (card_w = size_mm * 2)
    card_w_mm = s_mm * 2.0
    card_h_mm = card_w_mm * (COLOR_MARKER_CANON_H / COLOR_MARKER_CANON_W)
    scale_x = card_w_mm / COLOR_MARKER_CANON_W
    scale_y = card_h_mm / COLOR_MARKER_CANON_H

    margin_mm = 10.0
    ruler_area_mm = 16.0
    total_w = card_w_mm + 2.0 * margin_mm
    total_h = card_h_mm + 2.0 * margin_mm + ruler_area_mm

    wx, wy, ww, wh = COLOR_MARKER_WHITE_FIELD

    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{total_w:.2f}mm" height="{total_h:.2f}mm" '
            f'viewBox="0 0 {total_w:.2f} {total_h:.2f}">'
        ),
        f'  <rect x="0" y="0" width="{total_w:.2f}" height="{total_h:.2f}" fill="#FFFFFF"/>',
        (
            f'  <text x="{total_w / 2.0:.2f}" y="{margin_mm * 0.58:.2f}" '
            f'font-family="DejaVu Sans, Arial, sans-serif" font-size="2.8" '
            f'fill="#374151" text-anchor="middle">'
            f"GrowVision Color Calibration Marker ({card_w_mm:g}×{card_h_mm:g} мм)</text>"
        ),
        (
            f'  <g id="color-marker" data-width-mm="{card_w_mm:g}" '
            f'data-height-mm="{card_h_mm:g}">'
        ),
        # Внешняя контрастная чёрная рамка
        (
            f'    <rect x="{margin_mm:.2f}" y="{margin_mm:.2f}" '
            f'width="{card_w_mm:.2f}" height="{card_h_mm:.2f}" fill="#000000"/>'
        ),
        # Внутреннее белое поле
        (
            f'    <rect x="{margin_mm + wx * scale_x:.2f}" y="{margin_mm + wy * scale_y:.2f}" '
            f'width="{ww * scale_x:.2f}" height="{wh * scale_y:.2f}" fill="#FFFFFF"/>'
        ),
    ]

    for name, hex_color, _, (px, py, pw, ph) in COLOR_MARKER_PATCHES:
        x_mm = margin_mm + px * scale_x
        y_mm = margin_mm + py * scale_y
        w_mm = pw * scale_x
        h_mm = ph * scale_y
        stroke_attr = ' stroke="#D1D5DB" stroke-width="0.2"' if name == "white" else ""
        parts.append(
            f'    <rect data-patch="{name}" x="{x_mm:.2f}" y="{y_mm:.2f}" '
            f'width="{w_mm:.2f}" height="{h_mm:.2f}" fill="{hex_color}"{stroke_attr}/>'
        )

    parts.append("  </g>")

    ruler_len_mm = min(s_mm, card_w_mm)
    ruler_y = margin_mm + card_h_mm + 6.0
    parts.append('  <g id="scale-ruler">')
    for line in _build_mm_ruler_svg(margin_mm, ruler_y, ruler_len_mm, "мм"):
        parts.append(f"    {line}")
    parts.append("  </g>")
    parts.append("</svg>")
    return "\n".join(parts)
