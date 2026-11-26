from pathlib import Path
from typing import Any

import edge_agent.vision as _v
from shared.schemas import PlantMeasurementMeta


def _get_pil_font(size: int) -> Any:
    """Возвращает FreeType TTF-шрифт с поддержкой кириллицы или безопасный default fallback."""
    if not _v.PIL_AVAILABLE or _v.ImageFont is None:
        return None
    for font_path in _v.CANDIDATE_FONTS:
        if Path(font_path).is_file():
            try:
                return _v.ImageFont.truetype(font_path, size)
            except Exception:  # noqa: BLE001, S112
                continue
    try:
        return _v.ImageFont.load_default()
    except Exception:  # noqa: BLE001
        return None


def _draw_bbox_rulers(
    annotated: Any,
    bx: int,
    by: int,
    bw: int,
    bh: int,
    h_val_str: str,
    w_val_str: str,
    img_w: int,
    img_h: int,
    tick_len: int,
    thickness: int,
    outline_thickness: int,
    color_line: tuple[int, int, int],
    color_outline: tuple[int, int, int],
    adaptive_side: bool = False,
) -> tuple[int, int, int, str, int, int, int, str, int, str, str]:
    """Рисует вертикальную и горизонтальную линейки с засечками вокруг прямоугольника."""
    v_side = "left"
    if adaptive_side and bx < 70 and (bx + bw + tick_len + 25) < img_w:
        v_side = "right"

    if v_side == "left":
        lx = max(tick_len + 5, bx - 15)
    else:
        lx = min(img_w - tick_len - 5, bx + bw + 15)
    ly1, ly2 = by, by + bh

    _v.cv2.line(
        annotated,
        (lx, ly1),
        (lx, ly2),
        color_outline,
        outline_thickness,
        _v.cv2.LINE_AA,
    )
    _v.cv2.line(annotated, (lx, ly1), (lx, ly2), color_line, thickness, _v.cv2.LINE_AA)
    for y_tick in (ly1, (ly1 + ly2) // 2, ly2):
        _v.cv2.line(
            annotated,
            (lx - tick_len, y_tick),
            (lx + tick_len, y_tick),
            color_outline,
            outline_thickness,
            _v.cv2.LINE_AA,
        )
        _v.cv2.line(
            annotated,
            (lx - tick_len, y_tick),
            (lx + tick_len, y_tick),
            color_line,
            thickness,
            _v.cv2.LINE_AA,
        )

    h_side = "bottom"
    if adaptive_side and (by + bh + 40) > img_h and by >= 35:
        h_side = "top"

    bx1, bx2 = bx, bx + bw
    if h_side == "bottom":
        ly_bot = min(img_h - tick_len - 5, by + bh + 15)
    else:
        ly_bot = max(tick_len + 5, by - 15)

    _v.cv2.line(
        annotated,
        (bx1, ly_bot),
        (bx2, ly_bot),
        color_outline,
        outline_thickness,
        _v.cv2.LINE_AA,
    )
    _v.cv2.line(
        annotated,
        (bx1, ly_bot),
        (bx2, ly_bot),
        color_line,
        thickness,
        _v.cv2.LINE_AA,
    )
    for x_tick in (bx1, (bx1 + bx2) // 2, bx2):
        _v.cv2.line(
            annotated,
            (x_tick, ly_bot - tick_len),
            (x_tick, ly_bot + tick_len),
            color_outline,
            outline_thickness,
            _v.cv2.LINE_AA,
        )
        _v.cv2.line(
            annotated,
            (x_tick, ly_bot - tick_len),
            (x_tick, ly_bot + tick_len),
            color_line,
            thickness,
            _v.cv2.LINE_AA,
        )

    return (
        lx,
        ly1,
        ly2,
        h_val_str,
        bx1,
        bx2,
        ly_bot,
        w_val_str,
        tick_len,
        v_side,
        h_side,
    )


def draw_cv_annotations(
    img: Any,
    contours_with_area: list[tuple[Any, float]],
    aruco_area_px: float | None = None,
    aruco_size_mm: float | None = None,
    inst_name: str | None = None,
    culture_name: str | None = None,
    camera_name: str | None = None,
    captured_at: str | None = None,
    aruco_corners: Any = None,
    color_marker_corners: Any = None,
    measurements: list[PlantMeasurementMeta] | None = None,
) -> Any:
    """Накладывает улучшенную CV-разметку:

    - Линейка слева и снизу в мм (при калибровке ArUco) или px, яркий фиолетовый (BGR: 240, 32, 160);
    - Жёлтая рамка (BGR: 0, 255, 255) и фиолетовые линейки в px вокруг обнаруженного ArUco-маркера;
    - Синяя рамка (BGR: 255, 136, 0 / #0088FF) вокруг обнаруженного цветового маркера;
    - Красная точка в центре каждого найденного растения;
    - Счётчик «Обнаружено кустов: N» и общая площадь в см² (при ArUco) или px;
    - Метаданные в углах кадра с поддержкой кириллицы через Pillow TTF-шрифты.
    """
    if not _v.CV2_AVAILABLE or img is None:
        return img

    h, w = img.shape[:2]
    annotated = img.copy()

    scale_ref = min(w, h)
    font_scale = max(0.5, scale_ref / 1000.0)
    thickness = max(2, round(scale_ref / 400.0))
    outline_thickness = thickness + 2
    tick_len = max(5, round(scale_ref / 120.0))

    # BGR цвета для OpenCV
    COLOR_PURPLE = (240, 32, 160)  # Яркий фиолетовый (BGR)
    COLOR_RED = (0, 0, 255)  # Красный
    COLOR_GREEN = (0, 220, 0)  # Зеленый контур
    COLOR_ARUCO_YELLOW = (0, 255, 255)  # Жёлтая рамка ArUco (#FFFF00)
    COLOR_MARKER_BLUE = (255, 136, 0)  # Синяя рамка цветового маркера (#0088FF)
    COLOR_DARK = (15, 15, 15)  # Темная обводка

    has_aruco = bool(
        aruco_corners is not None
        or (aruco_area_px is not None and float(aruco_area_px) > 0)
    )
    H_px_to_mm = None
    fallback_mm_per_px = None
    if has_aruco:
        H_px_to_mm, _, fallback_mm_per_px = _v.compute_aruco_homography(
            aruco_corners=aruco_corners,
            aruco_size_mm=aruco_size_mm,
            aruco_area_px=aruco_area_px,
        )

    plants_count = len(contours_with_area)
    total_area_px = sum(area for _, area in contours_with_area)

    # Списки для отрисовки меток линеек
    rulers_info: list[tuple[int, int, int, str, int, int, int, str, int, str, str]] = []

    # 0. Жёлтая рамка и фиолетовые линейки в px вокруг обнаруженного ArUco-маркера
    if aruco_corners is not None:
        pts = _v.np.asarray(aruco_corners, dtype=_v.np.float32).reshape(-1, 2)
        if len(pts) >= 4:
            pts_int = _v.np.round(pts).astype(_v.np.int32)
            _v.cv2.polylines(
                annotated,
                [pts_int],
                isClosed=True,
                color=COLOR_DARK,
                thickness=outline_thickness,
                lineType=_v.cv2.LINE_AA,
            )
            _v.cv2.polylines(
                annotated,
                [pts_int],
                isClosed=True,
                color=COLOR_ARUCO_YELLOW,
                thickness=thickness,
                lineType=_v.cv2.LINE_AA,
            )
            ax, ay, aw, ah = _v.cv2.boundingRect(pts_int)
            if aw > 0 and ah > 0:
                rulers_info.append(
                    _draw_bbox_rulers(
                        annotated=annotated,
                        bx=ax,
                        by=ay,
                        bw=aw,
                        bh=ah,
                        h_val_str=f"{ah} px",
                        w_val_str=f"{aw} px",
                        img_w=w,
                        img_h=h,
                        tick_len=tick_len,
                        thickness=thickness,
                        outline_thickness=outline_thickness,
                        color_line=COLOR_PURPLE,
                        color_outline=COLOR_DARK,
                        adaptive_side=True,
                    )
                )

    # 0.5. Синяя рамка вокруг обнаруженного цветового маркера (#0088FF / BGR: 255, 136, 0)
    if color_marker_corners is not None:
        c_pts = _v.np.asarray(color_marker_corners, dtype=_v.np.float32).reshape(-1, 2)
        if len(c_pts) >= 4:
            c_pts_int = _v.np.round(c_pts).astype(_v.np.int32)
            _v.cv2.polylines(
                annotated,
                [c_pts_int],
                isClosed=True,
                color=COLOR_DARK,
                thickness=outline_thickness,
                lineType=_v.cv2.LINE_AA,
            )
            _v.cv2.polylines(
                annotated,
                [c_pts_int],
                isClosed=True,
                color=COLOR_MARKER_BLUE,
                thickness=thickness,
                lineType=_v.cv2.LINE_AA,
            )

    total_area_cm2 = 0.0
    has_metric_area = has_aruco

    # Геометрическая обработка каждого обнаруженного растения
    for idx, (cnt, _) in enumerate(contours_with_area):
        # 1. Контур с темной обводкой
        _v.cv2.drawContours(
            annotated, [cnt], -1, COLOR_DARK, outline_thickness, _v.cv2.LINE_AA
        )
        _v.cv2.drawContours(
            annotated, [cnt], -1, COLOR_GREEN, thickness, _v.cv2.LINE_AA
        )

        # 2. Красная точка в центре
        m = _v.cv2.moments(cnt)
        if m["m00"] != 0:
            cx = int(m["m10"] / m["m00"])
            cy = int(m["m01"] / m["m00"])
        else:
            (cx_f, cy_f), _ = _v.cv2.minEnclosingCircle(cnt)
            cx, cy = int(cx_f), int(cy_f)

        point_r = max(4, round(scale_ref / 130.0))
        _v.cv2.circle(annotated, (cx, cy), point_r + 2, COLOR_DARK, -1, _v.cv2.LINE_AA)
        _v.cv2.circle(annotated, (cx, cy), point_r, COLOR_RED, -1, _v.cv2.LINE_AA)

        # 3. Линейка слева и снизу (мм при ArUco, px без маркера)
        bx, by, bw, bh = _v.cv2.boundingRect(cnt)
        meas_item = (
            measurements[idx]
            if measurements is not None and idx < len(measurements)
            else None
        )
        if has_aruco:
            if (
                meas_item is not None
                and meas_item.width_mm is not None
                and meas_item.height_mm is not None
                and meas_item.area_cm2 is not None
            ):
                w_mm = meas_item.width_mm
                h_mm = meas_item.height_mm
                a_cm2 = meas_item.area_cm2
            else:
                metrics = _v.measure_contour_metrics(
                    cnt,
                    H_px_to_mm=H_px_to_mm,
                    fallback_mm_per_px=fallback_mm_per_px,
                )
                w_mm = metrics["width_mm"]
                h_mm = metrics["height_mm"]
                a_cm2 = metrics["area_cm2"]

            if w_mm is not None and h_mm is not None and a_cm2 is not None:
                total_area_cm2 += a_cm2
                h_val_str = f"{h_mm:.1f} мм"
                w_val_str = f"{w_mm:.1f} мм"
            else:
                has_metric_area = False
                h_val_str = f"{bh} px"
                w_val_str = f"{bw} px"
        else:
            h_val_str = f"{bh} px"
            w_val_str = f"{bw} px"

        rulers_info.append(
            _draw_bbox_rulers(
                annotated=annotated,
                bx=bx,
                by=by,
                bw=bw,
                bh=bh,
                h_val_str=h_val_str,
                w_val_str=w_val_str,
                img_w=w,
                img_h=h,
                tick_len=tick_len,
                thickness=thickness,
                outline_thickness=outline_thickness,
                color_line=COLOR_PURPLE,
                color_outline=COLOR_DARK,
                adaptive_side=False,
            )
        )

    if has_aruco and has_metric_area:
        area_str = f"{total_area_cm2:.1f} см²"
    else:
        area_str = f"{round(total_area_px)} px"
    plants_count_str = f"Обнаружено кустов: {plants_count}"

    # 4. Отрисовка текста (Pillow с поддержкой TTF кириллицы либо cv2 fallback)
    if _v.PIL_AVAILABLE and _v.Image is not None and _v.ImageDraw is not None:
        pil_img = _v.Image.fromarray(_v.cv2.cvtColor(annotated, _v.cv2.COLOR_BGR2RGB))
        draw = _v.ImageDraw.Draw(pil_img)

        # Цвета в формате RGB
        RGB_PURPLE = (160, 32, 240)  # Фиолетовый
        RGB_WHITE = (255, 255, 255)
        RGB_YELLOW = (235, 235, 0)
        RGB_DARK = (15, 15, 15)

        font_size = max(13, round(scale_ref / 45.0))
        stroke_w = max(1, round(font_size / 10.0))
        main_font = _get_pil_font(font_size)

        ruler_font_size = max(11, round(font_size * 0.85))
        ruler_stroke_w = max(1, round(ruler_font_size / 10.0))
        ruler_font = _get_pil_font(ruler_font_size)

        # Текст на линейках
        for (
            lx,
            ly1,
            ly2,
            h_val_str,
            bx1,
            bx2,
            ly_bot,
            w_val_str,
            r_tick_len,
            v_side,
            h_side,
        ) in rulers_info:
            # Вертикальная линейка
            bbox_h = draw.textbbox(
                (0, 0), h_val_str, font=ruler_font, stroke_width=ruler_stroke_w
            )
            tw_h = bbox_h[2] - bbox_h[0]
            th_h = bbox_h[3] - bbox_h[1]
            if v_side == "left":
                pos_x_h = max(5, lx - tw_h - r_tick_len - 4)
            else:
                pos_x_h = min(w - tw_h - 5, lx + r_tick_len + 4)
            pos_y_h = max(2, (ly1 + ly2) // 2 - th_h // 2)
            draw.text(
                (pos_x_h, pos_y_h),
                h_val_str,
                font=ruler_font,
                fill=RGB_PURPLE,
                stroke_width=ruler_stroke_w,
                stroke_fill=RGB_DARK,
            )

            # Горизонтальная линейка
            bbox_w = draw.textbbox(
                (0, 0), w_val_str, font=ruler_font, stroke_width=ruler_stroke_w
            )
            tw_w = bbox_w[2] - bbox_w[0]
            th_w = bbox_w[3] - bbox_w[1]
            pos_x_w = max(5, min(w - tw_w - 5, (bx1 + bx2) // 2 - tw_w // 2))
            if h_side == "bottom":
                pos_y_w = min(h - th_w - 5, ly_bot + r_tick_len + 4)
            else:
                pos_y_w = max(5, ly_bot - th_w - r_tick_len - 4)
            draw.text(
                (pos_x_w, pos_y_w),
                w_val_str,
                font=ruler_font,
                fill=RGB_PURPLE,
                stroke_width=ruler_stroke_w,
                stroke_fill=RGB_DARK,
            )

        # Метаданные в углах кадра
        margin = max(12, int(scale_ref * 0.025))
        line_spacing = max(4, round(font_size * 0.3))
        line_h = font_size + line_spacing

        # Слева сверху (Установка, Культура)
        cur_y = margin
        if inst_name:
            draw.text(
                (margin, cur_y),
                f"Установка: {inst_name}",
                font=main_font,
                fill=RGB_WHITE,
                stroke_width=stroke_w,
                stroke_fill=RGB_DARK,
            )
            cur_y += line_h
        if culture_name:
            draw.text(
                (margin, cur_y),
                f"Культура: {culture_name}",
                font=main_font,
                fill=RGB_WHITE,
                stroke_width=stroke_w,
                stroke_fill=RGB_DARK,
            )

        # Справа сверху (Камера, Время)
        cur_y = margin
        if camera_name:
            text_cam = f"Камера: {camera_name}"
            bbox = draw.textbbox(
                (0, 0), text_cam, font=main_font, stroke_width=stroke_w
            )
            tw = bbox[2] - bbox[0]
            draw.text(
                (w - margin - tw, cur_y),
                text_cam,
                font=main_font,
                fill=RGB_WHITE,
                stroke_width=stroke_w,
                stroke_fill=RGB_DARK,
            )
            cur_y += line_h
        if captured_at:
            text_time = f"Время: {captured_at}"
            bbox = draw.textbbox(
                (0, 0), text_time, font=main_font, stroke_width=stroke_w
            )
            tw = bbox[2] - bbox[0]
            draw.text(
                (w - margin - tw, cur_y),
                text_time,
                font=main_font,
                fill=RGB_WHITE,
                stroke_width=stroke_w,
                stroke_fill=RGB_DARK,
            )

        # Слева снизу (Обнаружено кустов: N)
        bbox_cnt = draw.textbbox(
            (0, 0), plants_count_str, font=main_font, stroke_width=stroke_w
        )
        th_cnt = bbox_cnt[3] - bbox_cnt[1]
        draw.text(
            (margin, h - margin - th_cnt),
            plants_count_str,
            font=main_font,
            fill=RGB_WHITE,
            stroke_width=stroke_w,
            stroke_fill=RGB_DARK,
        )

        # Справа снизу (Площадь: ...)
        text_area = f"Площадь: {area_str}"
        bbox = draw.textbbox((0, 0), text_area, font=main_font, stroke_width=stroke_w)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw.text(
            (w - margin - tw, h - margin - th),
            text_area,
            font=main_font,
            fill=RGB_YELLOW,
            stroke_width=stroke_w,
            stroke_fill=RGB_DARK,
        )

        annotated = _v.cv2.cvtColor(_v.np.array(pil_img), _v.cv2.COLOR_RGB2BGR)

    else:
        # Fallback на cv2.putText при отсутствии Pillow
        font = _v.cv2.FONT_HERSHEY_SIMPLEX
        COLOR_WHITE = (255, 255, 255)
        COLOR_YELLOW = (0, 235, 235)

        def draw_text_with_outline(
            text_str: str, pos: tuple[int, int], color=COLOR_WHITE
        ):
            _v.cv2.putText(
                annotated,
                text_str,
                pos,
                font,
                font_scale,
                COLOR_DARK,
                outline_thickness,
                _v.cv2.LINE_AA,
            )
            _v.cv2.putText(
                annotated,
                text_str,
                pos,
                font,
                font_scale,
                color,
                thickness,
                _v.cv2.LINE_AA,
            )

        for (
            lx,
            ly1,
            ly2,
            h_val_str,
            bx1,
            bx2,
            ly_bot,
            w_val_str,
            r_tick_len,
            v_side,
            h_side,
        ) in rulers_info:
            pos_x_h = (
                max(5, lx - 55)
                if v_side == "left"
                else min(w - 65, lx + r_tick_len + 4)
            )
            draw_text_with_outline(
                h_val_str, (pos_x_h, (ly1 + ly2) // 2), color=COLOR_PURPLE
            )
            pos_y_w = (
                min(h - 6, ly_bot + 18)
                if h_side == "bottom"
                else max(18, ly_bot - r_tick_len - 6)
            )
            draw_text_with_outline(
                w_val_str,
                (max(5, (bx1 + bx2) // 2 - 25), pos_y_w),
                color=COLOR_PURPLE,
            )

        margin = max(12, int(scale_ref * 0.025))
        line_h = max(18, int(font_scale * 30))

        cur_y = margin + line_h
        if inst_name:
            draw_text_with_outline(f"Установка: {inst_name}", (margin, cur_y))
            cur_y += line_h
        if culture_name:
            draw_text_with_outline(f"Культура: {culture_name}", (margin, cur_y))

        cur_y = margin + line_h
        if camera_name:
            text_cam = f"Камера: {camera_name}"
            (tw, _), _ = _v.cv2.getTextSize(text_cam, font, font_scale, thickness)
            draw_text_with_outline(text_cam, (w - margin - tw, cur_y))
            cur_y += line_h
        if captured_at:
            text_time = f"Время: {captured_at}"
            (tw, _), _ = _v.cv2.getTextSize(text_time, font, font_scale, thickness)
            draw_text_with_outline(text_time, (w - margin - tw, cur_y))

        draw_text_with_outline(
            plants_count_str, (margin, h - margin), color=COLOR_WHITE
        )

        text_area = f"Площадь: {area_str}"
        (tw, _), _ = _v.cv2.getTextSize(text_area, font, font_scale, thickness)
        draw_text_with_outline(
            text_area, (w - margin - tw, h - margin), color=COLOR_YELLOW
        )

    return annotated
