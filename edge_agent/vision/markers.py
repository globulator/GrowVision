from typing import Any

import edge_agent.vision as _v
from shared.markers import (
    COLOR_MARKER_CANON_H,
    COLOR_MARKER_CANON_W,
    COLOR_MARKER_PATCHES,
    COLOR_MARKER_WHITE_FIELD,
)


def _build_aruco_detector_params() -> Any:
    """Создаёт настроенные DetectorParameters для детекции с камеры и экрана монитора."""
    if not _v.CV2_AVAILABLE or not hasattr(_v.cv2, "aruco"):
        return None
    if hasattr(_v.cv2.aruco, "DetectorParameters"):
        params = _v.cv2.aruco.DetectorParameters()
    elif hasattr(_v.cv2.aruco, "DetectorParameters_create"):
        params = _v.cv2.aruco.DetectorParameters_create()
    else:
        return None

    # Подстройка адаптивного порога и аппроксимации под неравномерный свет и экран монитора
    params.adaptiveThreshWinSizeMin = 3
    params.adaptiveThreshWinSizeMax = 35
    params.adaptiveThreshWinSizeStep = 8
    params.adaptiveThreshConstant = 7
    params.minMarkerPerimeterRate = 0.015
    params.maxMarkerPerimeterRate = 4.0
    params.polygonalApproxAccuracyRate = 0.05
    params.perspectiveRemovePixelPerCell = 8
    params.perspectiveRemoveIgnoredMarginPerCell = 0.13
    params.errorCorrectionRate = 0.6
    if hasattr(_v.cv2.aruco, "CORNER_REFINE_SUBPIX"):
        params.cornerRefinementMethod = _v.cv2.aruco.CORNER_REFINE_SUBPIX
    params.cornerRefinementWinSize = 5
    params.cornerRefinementMaxIterations = 30
    params.cornerRefinementMinAccuracy = 0.1
    return params


def compute_aruco_homography(
    aruco_corners: Any = None,
    aruco_size_mm: float | None = None,
    aruco_area_px: float | None = None,
) -> tuple[Any | None, float, float | None]:
    """Вычисляет матрицу перспективного преобразования (px -> мм) по углам ArUco.

    Возвращает (H_px_to_mm, effective_aruco_size_mm, mm_per_px).
    """
    effective_size_mm = (
        float(aruco_size_mm)
        if (aruco_size_mm is not None and float(aruco_size_mm) > 0)
        else _v.DEFAULT_ARUCO_SIZE_MM
    )
    if not _v.CV2_AVAILABLE:
        return None, effective_size_mm, None

    if aruco_corners is not None:
        pts_src = _v.np.asarray(aruco_corners, dtype=_v.np.float32).reshape(-1, 2)
        if len(pts_src) >= 4:
            pts_src = pts_src[:4]
            area_px = float(_v.cv2.contourArea(pts_src))
            if area_px > 1.0:
                pts_dst = _v.np.array(
                    [
                        [0.0, 0.0],
                        [effective_size_mm, 0.0],
                        [effective_size_mm, effective_size_mm],
                        [0.0, effective_size_mm],
                    ],
                    dtype=_v.np.float32,
                )
                H_px_to_mm = _v.cv2.getPerspectiveTransform(pts_src, pts_dst)
                mm_per_px = effective_size_mm / (area_px**0.5)
                return H_px_to_mm, effective_size_mm, mm_per_px

    if aruco_area_px is not None and float(aruco_area_px) > 0:
        mm_per_px = effective_size_mm / (float(aruco_area_px) ** 0.5)
        return None, effective_size_mm, mm_per_px

    return None, effective_size_mm, None


def measure_contour_metrics(
    cnt: Any,
    H_px_to_mm: Any | None = None,
    fallback_mm_per_px: float | None = None,
) -> dict[str, float | None]:
    """Рассчитывает пиксельные и перспективно-выпрямленные метрические параметры контура."""
    area_px = round(float(_v.cv2.contourArea(cnt)), 1)
    _, _, bw, bh = _v.cv2.boundingRect(cnt)
    width_px = float(bw)
    height_px = float(bh)

    width_mm: float | None = None
    height_mm: float | None = None
    area_cm2: float | None = None

    if H_px_to_mm is not None and len(cnt) >= 3:
        cnt_pts = _v.np.asarray(cnt, dtype=_v.np.float32).reshape(-1, 1, 2)
        # Проверяем, что все точки лежат по правильную сторону линии горизонта гомографии
        homog_w = (
            H_px_to_mm[2, 0] * cnt_pts[:, 0, 0]
            + H_px_to_mm[2, 1] * cnt_pts[:, 0, 1]
            + H_px_to_mm[2, 2]
        )
        if _v.np.all(_v.np.abs(homog_w) > 1e-4) and (
            _v.np.all(homog_w > 0) or _v.np.all(homog_w < 0)
        ):
            cnt_mm = _v.cv2.perspectiveTransform(cnt_pts, H_px_to_mm)
            area_mm2 = abs(float(_v.cv2.contourArea(cnt_mm)))
            cnt_mm_2d = cnt_mm.reshape(-1, 2)
            w_mm = float(cnt_mm_2d[:, 0].max() - cnt_mm_2d[:, 0].min())
            h_mm = float(cnt_mm_2d[:, 1].max() - cnt_mm_2d[:, 1].min())
            if (
                _v.np.isfinite(area_mm2)
                and _v.np.isfinite(w_mm)
                and _v.np.isfinite(h_mm)
                and w_mm < 1e5
                and h_mm < 1e5
            ):
                width_mm = round(w_mm, 1)
                height_mm = round(h_mm, 1)
                area_cm2 = round(area_mm2 / 100.0, 2)
    elif fallback_mm_per_px is not None and fallback_mm_per_px > 0:
        width_mm = round(width_px * fallback_mm_per_px, 1)
        height_mm = round(height_px * fallback_mm_per_px, 1)
        area_cm2 = round(area_px * (fallback_mm_per_px**2) / 100.0, 2)

    return {
        "area_px": area_px,
        "width_px": width_px,
        "height_px": height_px,
        "width_mm": width_mm,
        "height_mm": height_mm,
        "area_cm2": area_cm2,
    }


def _order_quad_long_horizontal(pts: Any) -> Any:
    """Упорядочивает 4 угла четырёхугольника по часовой стрелке с длинной стороной вдоль оси X."""
    pts_arr = _v.np.asarray(pts, dtype=_v.np.float32).reshape(4, 2)
    center = pts_arr.mean(axis=0)
    angles = _v.np.arctan2(pts_arr[:, 1] - center[1], pts_arr[:, 0] - center[0])
    ordered = pts_arr[_v.np.argsort(angles)]
    len_01 = float(
        _v.np.linalg.norm(ordered[1] - ordered[0])
        + _v.np.linalg.norm(ordered[2] - ordered[3])
    )
    len_12 = float(
        _v.np.linalg.norm(ordered[2] - ordered[1])
        + _v.np.linalg.norm(ordered[3] - ordered[0])
    )
    if len_12 > len_01:
        ordered = _v.np.roll(ordered, -1, axis=0)
    return ordered


def _compute_auto_rgb_gains(
    p_r: Any, p_g: Any, p_b: Any, p_w: Any, p_gray: Any
) -> tuple[float, float, float]:
    """Вычисляет коэффициенты автоцветокоррекции (r, g, b) в [0.00, 1.00] по нейтральным плашкам."""
    w_arr = _v.np.asarray(p_w, dtype=_v.np.float64)
    g_arr = _v.np.asarray(p_gray, dtype=_v.np.float64)

    # Баланс белого считаем исключительно по нейтральным плашкам (белая + серая),
    # чтобы цветные плашки (R/G/B) не перекашивали нейтральный фон в красный/зелёный.
    if float(_v.np.max(w_arr)) < 245.0 and float(_v.np.min(w_arr)) > 15.0:
        w_mean = max(1.0, float(_v.np.mean(w_arr)))
        g_mean = max(1.0, float(_v.np.mean(g_arr)))
        w_scaled = w_arr * (g_mean / w_mean)
        wb_ref = 0.60 * g_arr + 0.40 * w_scaled
    else:
        wb_ref = g_arr

    wb_gains = 1.0 / _v.np.maximum(wb_ref, 5.0)
    max_wb = float(_v.np.max(wb_gains))
    if max_wb > 0:
        wb_gains = wb_gains / max_wb

    # Мягкое демпфирование к 1.0 и нижний разумный порог разброса каналов
    damp_alpha = 0.12
    gains = (1.0 - damp_alpha) * wb_gains + damp_alpha
    gains = _v.np.clip(gains, 0.35, 1.0)
    max_g = float(_v.np.max(gains))
    if max_g > 0:
        gains = gains / max_g

    r_out = round(max(0.0, min(1.0, float(gains[0]))), 2)
    g_out = round(max(0.0, min(1.0, float(gains[1]))), 2)
    b_out = round(max(0.0, min(1.0, float(gains[2]))), 2)
    return (r_out, g_out, b_out)


def _reduce_poly_to_quad(pts: Any) -> Any | None:
    """Сводит 4–6-угольник к 4 вершинам, удаляя наименее значимые вершины по площади треугольника."""
    arr = _v.np.asarray(pts, dtype=_v.np.float32).reshape(-1, 2)
    if len(arr) < 4:
        return None
    while len(arr) > 4:
        n = len(arr)
        min_area = float("inf")
        drop_idx = 0
        for i in range(n):
            p_prev = arr[(i - 1) % n]
            p_curr = arr[i]
            p_next = arr[(i + 1) % n]
            # Удвоенная площадь треугольника (p_prev, p_curr, p_next)
            tri_area2 = abs(
                float(
                    (p_curr[0] - p_prev[0]) * (p_next[1] - p_prev[1])
                    - (p_curr[1] - p_prev[1]) * (p_next[0] - p_prev[0])
                )
            )
            if tri_area2 < min_area:
                min_area = tri_area2
                drop_idx = i
        arr = _v.np.delete(arr, drop_idx, axis=0)
    return arr.astype(_v.np.float32)


def crop_color_marker_roi_jpeg(
    img: Any,
    corners: Any,
    padding_ratio: float = 0.12,
    max_width: int = 480,
) -> bytes | None:
    """Вырезает ROI-фрагмент обнаруженного цветового маркера из BGR-кадра и кодирует в JPEG."""
    if not _v.CV2_AVAILABLE or img is None or corners is None:
        return None
    h, w = img.shape[:2]
    pts = _v.np.asarray(corners, dtype=_v.np.float32).reshape(-1, 2)
    if len(pts) < 4:
        return None

    x_min, y_min = float(_v.np.min(pts[:, 0])), float(_v.np.min(pts[:, 1]))
    x_max, y_max = float(_v.np.max(pts[:, 0])), float(_v.np.max(pts[:, 1]))
    bw = max(1.0, x_max - x_min)
    bh = max(1.0, y_max - y_min)
    pad_x = bw * padding_ratio
    pad_y = bh * padding_ratio

    x1 = max(0, int(_v.np.floor(x_min - pad_x)))
    y1 = max(0, int(_v.np.floor(y_min - pad_y)))
    x2 = min(w, int(_v.np.ceil(x_max + pad_x)))
    y2 = min(h, int(_v.np.ceil(y_max + pad_y)))
    if x2 <= x1 or y2 <= y1:
        return None

    roi = img[y1:y2, x1:x2]
    if roi.size == 0:
        return None

    rh, rw = roi.shape[:2]
    if rw > max_width > 0:
        scale = float(max_width) / float(rw)
        new_h = max(1, round(rh * scale))
        roi = _v.cv2.resize(roi, (max_width, new_h), interpolation=_v.cv2.INTER_AREA)

    ok, enc = _v.cv2.imencode(".jpg", roi, [int(_v.cv2.IMWRITE_JPEG_QUALITY), 88])
    return enc.tobytes() if ok else None


def _verify_warped_color_marker(
    warped_bgr: Any,
) -> tuple[bool, tuple[float, float, float] | None]:
    """Проверяет выпрямленного кандидата цветового маркера (500x200) и вычисляет RGB-коэффициенты."""
    # 1. Проверка контрастной чёрной рамки и внутреннего белого поля (с допуском на блики экрана)
    border_strips = [
        warped_bgr[3:14, 30:470],
        warped_bgr[186:197, 30:470],
        warped_bgr[30:170, 3:14],
        warped_bgr[30:170, 486:497],
    ]
    border_pixels = _v.np.concatenate([s.reshape(-1, 3) for s in border_strips], axis=0)
    border_luma = float(_v.np.median(border_pixels))

    field_strips = [
        warped_bgr[24:42, 40:460],
        warped_bgr[158:176, 40:460],
    ]
    field_pixels = _v.np.concatenate([s.reshape(-1, 3) for s in field_strips], axis=0)
    field_luma = float(_v.np.median(field_pixels))
    field_std = float(_v.np.max(_v.np.std(field_pixels, axis=0)))

    if border_luma > 175.0 or field_luma < 70.0:
        return False, None
    if field_luma < border_luma + 20.0:
        return False, None
    if field_std > 68.0:
        return False, None

    # 2. Извлекаем 5 плашек (медиана RGB по центральной зоне каждой плашки)
    patches_rgb: list[Any] = []
    for _, _, _, (px, py, pw, ph) in COLOR_MARKER_PATCHES:
        roi_bgr = warped_bgr[py + 18 : py + ph - 18, px + 14 : px + pw - 14]
        if roi_bgr.size == 0:
            return False, None
        roi_rgb = roi_bgr[:, :, ::-1].astype(_v.np.float64)
        if float(_v.np.max(_v.np.std(roi_rgb, axis=(0, 1)))) > 60.0:
            return False, None
        med_rgb = _v.np.median(roi_rgb.reshape(-1, 3), axis=0)
        patches_rgb.append(med_rgb)

    # Проверяем прямой (0°) и перевёрнутый (180°) порядок плашек
    for order in ((0, 1, 2, 3, 4), (4, 3, 2, 1, 0)):
        p_r, p_g, p_b, p_w, p_gray = (patches_rgb[i] for i in order)

        # Красная плашка (устойчивое относительное доминирование R > G, B)
        if not (
            p_r[0] > p_r[1] * 1.12
            and p_r[0] > p_r[2] * 1.12
            and (p_r[0] - max(p_r[1], p_r[2])) > 10.0
        ):
            continue
        # Зелёная плашка (устойчивое относительное доминирование G > R, B)
        if not (
            p_g[1] > p_g[0] * 1.12
            and p_g[1] > p_g[2] * 1.12
            and (p_g[1] - max(p_g[0], p_g[2])) > 10.0
        ):
            continue
        # Синяя плашка (устойчивое относительное доминирование B > R, G)
        if not (
            p_b[2] > p_b[0] * 1.12
            and p_b[2] > p_b[1] * 1.12
            and (p_b[2] - max(p_b[0], p_b[1])) > 10.0
        ):
            continue

        w_mean = float(_v.np.mean(p_w))
        gray_mean = float(_v.np.mean(p_gray))
        w_ratio = float(_v.np.max(p_w)) / max(1.0, float(_v.np.min(p_w)))
        gray_ratio = float(_v.np.max(p_gray)) / max(1.0, float(_v.np.min(p_gray)))

        # Белая и нейтральная серая плашки (низкая насыщенность и разница яркости)
        if w_mean < 95.0 or float(_v.np.min(p_w)) < 45.0 or w_ratio > 3.2:
            continue
        if gray_mean < 25.0 or gray_mean > 235.0 or gray_ratio > 3.2:
            continue
        if w_mean <= gray_mean + 10.0:
            continue

        gains = _compute_auto_rgb_gains(p_r, p_g, p_b, p_w, p_gray)
        return True, gains

    return False, None


def detect_color_marker_in_bgr(
    img: Any,
) -> tuple[Any | None, tuple[float, float, float] | None]:
    """Ищет эталонный цветовой маркер на BGR-кадре.

    Возвращает (внешние_углы_4x2, (r, g, b)) или (None, None).
    """
    if not _v.CV2_AVAILABLE or img is None:
        return None, None

    h, w = img.shape[:2]
    min_area = max(600.0, float(w * h) * 0.0015)
    max_area = float(w * h) * 0.92

    gray = _v.cv2.cvtColor(img, _v.cv2.COLOR_BGR2GRAY)
    blurred = _v.cv2.GaussianBlur(gray, (3, 3), 0)

    bin_maps = [
        _v.cv2.adaptiveThreshold(
            blurred,
            255,
            _v.cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            _v.cv2.THRESH_BINARY,
            25,
            4,
        ),
        _v.cv2.threshold(blurred, 0, 255, _v.cv2.THRESH_BINARY + _v.cv2.THRESH_OTSU)[1],
        _v.cv2.dilate(
            _v.cv2.Canny(blurred, 40, 140),
            _v.np.ones((3, 3), dtype=_v.np.uint8),
            iterations=1,
        ),
    ]

    candidates: list[tuple[float, Any]] = []
    seen_centers: list[tuple[float, float, float]] = []

    def _register_quad(quad_pts: Any, ref_area: float) -> None:
        pts = _order_quad_long_horizontal(quad_pts)
        l1 = float(_v.np.linalg.norm(pts[1] - pts[0]))
        l2 = float(_v.np.linalg.norm(pts[2] - pts[1]))
        if min(l1, l2) < 12.0:
            return
        aspect = max(l1, l2) / min(l1, l2)
        if not (1.1 <= aspect <= 8.5):
            return
        cx, cy = float(pts[:, 0].mean()), float(pts[:, 1].mean())
        dup = any(
            abs(cx - sx) < 8.0
            and abs(cy - sy) < 8.0
            and abs(ref_area - sa) < max(200.0, ref_area * 0.08)
            for sx, sy, sa in seen_centers
        )
        if not dup:
            seen_centers.append((cx, cy, ref_area))
            candidates.append((ref_area, pts))

    for bmap in bin_maps:
        contours, _ = _v.cv2.findContours(
            bmap, _v.cv2.RETR_LIST, _v.cv2.CHAIN_APPROX_SIMPLE
        )
        for cnt in contours:
            area = float(_v.cv2.contourArea(cnt))
            if area < min_area or area > max_area:
                continue
            peri = _v.cv2.arcLength(cnt, True)
            if peri <= 0:
                continue
            for eps_factor in (0.02, 0.035, 0.05, 0.075, 0.1):
                approx = _v.cv2.approxPolyDP(cnt, eps_factor * peri, True)
                n_pts = len(approx)
                if n_pts == 4 and _v.cv2.isContourConvex(approx):
                    _register_quad(approx, area)
                    break
                if n_pts in (5, 6):
                    hull = _v.cv2.convexHull(approx)
                    if len(hull) == 4:
                        _register_quad(hull, area)
                        break
                    if len(hull) in (5, 6):
                        reduced = _reduce_poly_to_quad(hull)
                        if reduced is not None and _v.cv2.isContourConvex(
                            reduced.reshape(-1, 1, 2)
                        ):
                            _register_quad(reduced, area)
                        # Перспективный запасной вариант через minAreaRect при скошенных углах
                        box = _v.cv2.boxPoints(_v.cv2.minAreaRect(cnt))
                        _register_quad(box, area)

    # Проверяем от самых крупных (чтобы внешний контур рамки имел приоритет над внутренним белым полем)
    candidates.sort(key=lambda x: x[0], reverse=True)

    wx, wy, ww, wh = COLOR_MARKER_WHITE_FIELD
    outer_dst = _v.np.array(
        [
            [0.0, 0.0],
            [float(COLOR_MARKER_CANON_W), 0.0],
            [float(COLOR_MARKER_CANON_W), float(COLOR_MARKER_CANON_H)],
            [0.0, float(COLOR_MARKER_CANON_H)],
        ],
        dtype=_v.np.float32,
    )
    inner_dst = _v.np.array(
        [
            [float(wx), float(wy)],
            [float(wx + ww), float(wy)],
            [float(wx + ww), float(wy + wh)],
            [float(wx), float(wy + wh)],
        ],
        dtype=_v.np.float32,
    )

    for _, pts in candidates:
        # Вариант 1: кандидат — внешняя граница чёрной рамки
        M_outer = _v.cv2.getPerspectiveTransform(pts, outer_dst)
        warped_outer = _v.cv2.warpPerspective(
            img, M_outer, (COLOR_MARKER_CANON_W, COLOR_MARKER_CANON_H)
        )
        ok, gains = _verify_warped_color_marker(warped_outer)
        if ok and gains is not None:
            return pts.copy(), gains

        # Вариант 2: кандидат — внутреннее белое поле
        M_inner = _v.cv2.getPerspectiveTransform(pts, inner_dst)
        warped_inner = _v.cv2.warpPerspective(
            img, M_inner, (COLOR_MARKER_CANON_W, COLOR_MARKER_CANON_H)
        )
        ok_in, gains_in = _verify_warped_color_marker(warped_inner)
        if ok_in and gains_in is not None:
            with_inv = _v.np.linalg.inv(M_inner)
            outer_pts = _v.cv2.perspectiveTransform(
                outer_dst.reshape(-1, 1, 2), with_inv
            ).reshape(4, 2)
            return outer_pts.astype(_v.np.float32), gains_in

    return None, None
