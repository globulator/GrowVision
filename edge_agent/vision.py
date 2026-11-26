from pathlib import Path
from typing import Any

from shared.logger import get_logger
from shared.markers import (
    COLOR_MARKER_CANON_H,
    COLOR_MARKER_CANON_W,
    COLOR_MARKER_PATCHES,
    COLOR_MARKER_WHITE_FIELD,
)
from shared.schemas import PlantMeasurementMeta

logger = get_logger("edge_agent.vision")

DEFAULT_ARUCO_SIZE_MM = 50.0

CV2_AVAILABLE = False
ORT_AVAILABLE = False
PIL_AVAILABLE = False

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore

    CV2_AVAILABLE = True
except ImportError:
    cv2 = None  # type: ignore
    np = None  # type: ignore

try:
    import onnxruntime as ort  # type: ignore

    ORT_AVAILABLE = True
except ImportError:
    ort = None  # type: ignore

try:
    from PIL import Image, ImageDraw, ImageFont  # type: ignore

    PIL_AVAILABLE = True
except ImportError:
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageFont = None  # type: ignore


CANDIDATE_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]

ARUCO_DICTIONARY_IDS: tuple[str, ...] = (
    "DICT_4X4_50",
    "DICT_5X5_50",
    "DICT_6X6_50",
    "DICT_ARUCO_ORIGINAL",
)


def apply_rgb_gains_to_bgr(
    img: Any, r: float = 1.0, g: float = 1.0, b: float = 1.0
) -> Any:
    """Применяет программные коэффициенты R/G/B (0.0..1.0+) к OpenCV BGR-изображению."""
    if img is None or not CV2_AVAILABLE:
        return img
    rg = max(0.0, float(r))
    gg = max(0.0, float(g))
    bg = max(0.0, float(b))
    if abs(rg - 1.0) < 1e-4 and abs(gg - 1.0) < 1e-4 and abs(bg - 1.0) < 1e-4:
        return img
    out = img.copy()
    if abs(bg - 1.0) >= 1e-4:
        out[:, :, 0] = np.clip(out[:, :, 0].astype(np.float32) * bg, 0, 255).astype(
            np.uint8
        )
    if abs(gg - 1.0) >= 1e-4:
        out[:, :, 1] = np.clip(out[:, :, 1].astype(np.float32) * gg, 0, 255).astype(
            np.uint8
        )
    if abs(rg - 1.0) >= 1e-4:
        out[:, :, 2] = np.clip(out[:, :, 2].astype(np.float32) * rg, 0, 255).astype(
            np.uint8
        )
    return out


def apply_rgb_to_jpeg_bytes(
    image_bytes: bytes, r: float = 1.0, g: float = 1.0, b: float = 1.0
) -> bytes:
    """Применяет программные коэффициенты R/G/B к JPEG-байтам до CV-анализа."""
    rg = max(0.0, float(r))
    gg = max(0.0, float(g))
    bg = max(0.0, float(b))
    if abs(rg - 1.0) < 1e-4 and abs(gg - 1.0) < 1e-4 and abs(bg - 1.0) < 1e-4:
        return image_bytes
    if not CV2_AVAILABLE:
        return image_bytes
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return image_bytes
    corrected = apply_rgb_gains_to_bgr(img, r=rg, g=gg, b=bg)
    ok, enc = cv2.imencode(".jpg", corrected, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return enc.tobytes() if ok else image_bytes


def _build_aruco_detector_params() -> Any:
    """Создаёт настроенные DetectorParameters для детекции с камеры и экрана монитора."""
    if not CV2_AVAILABLE or not hasattr(cv2, "aruco"):
        return None
    if hasattr(cv2.aruco, "DetectorParameters"):
        params = cv2.aruco.DetectorParameters()
    elif hasattr(cv2.aruco, "DetectorParameters_create"):
        params = cv2.aruco.DetectorParameters_create()
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
    if hasattr(cv2.aruco, "CORNER_REFINE_SUBPIX"):
        params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    params.cornerRefinementWinSize = 5
    params.cornerRefinementMaxIterations = 30
    params.cornerRefinementMinAccuracy = 0.1
    return params


def _get_pil_font(size: int) -> Any:
    """Возвращает FreeType TTF-шрифт с поддержкой кириллицы или безопасный default fallback."""
    if not PIL_AVAILABLE or ImageFont is None:
        return None
    for font_path in CANDIDATE_FONTS:
        if Path(font_path).is_file():
            try:
                return ImageFont.truetype(font_path, size)
            except Exception:  # noqa: BLE001, S112
                continue
    try:
        return ImageFont.load_default()
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

    cv2.line(
        annotated, (lx, ly1), (lx, ly2), color_outline, outline_thickness, cv2.LINE_AA
    )
    cv2.line(annotated, (lx, ly1), (lx, ly2), color_line, thickness, cv2.LINE_AA)
    for y_tick in (ly1, (ly1 + ly2) // 2, ly2):
        cv2.line(
            annotated,
            (lx - tick_len, y_tick),
            (lx + tick_len, y_tick),
            color_outline,
            outline_thickness,
            cv2.LINE_AA,
        )
        cv2.line(
            annotated,
            (lx - tick_len, y_tick),
            (lx + tick_len, y_tick),
            color_line,
            thickness,
            cv2.LINE_AA,
        )

    h_side = "bottom"
    if adaptive_side and (by + bh + 40) > img_h and by >= 35:
        h_side = "top"

    bx1, bx2 = bx, bx + bw
    if h_side == "bottom":
        ly_bot = min(img_h - tick_len - 5, by + bh + 15)
    else:
        ly_bot = max(tick_len + 5, by - 15)

    cv2.line(
        annotated,
        (bx1, ly_bot),
        (bx2, ly_bot),
        color_outline,
        outline_thickness,
        cv2.LINE_AA,
    )
    cv2.line(
        annotated, (bx1, ly_bot), (bx2, ly_bot), color_line, thickness, cv2.LINE_AA
    )
    for x_tick in (bx1, (bx1 + bx2) // 2, bx2):
        cv2.line(
            annotated,
            (x_tick, ly_bot - tick_len),
            (x_tick, ly_bot + tick_len),
            color_outline,
            outline_thickness,
            cv2.LINE_AA,
        )
        cv2.line(
            annotated,
            (x_tick, ly_bot - tick_len),
            (x_tick, ly_bot + tick_len),
            color_line,
            thickness,
            cv2.LINE_AA,
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
        else DEFAULT_ARUCO_SIZE_MM
    )
    if not CV2_AVAILABLE:
        return None, effective_size_mm, None

    if aruco_corners is not None:
        pts_src = np.asarray(aruco_corners, dtype=np.float32).reshape(-1, 2)
        if len(pts_src) >= 4:
            pts_src = pts_src[:4]
            area_px = float(cv2.contourArea(pts_src))
            if area_px > 1.0:
                pts_dst = np.array(
                    [
                        [0.0, 0.0],
                        [effective_size_mm, 0.0],
                        [effective_size_mm, effective_size_mm],
                        [0.0, effective_size_mm],
                    ],
                    dtype=np.float32,
                )
                H_px_to_mm = cv2.getPerspectiveTransform(pts_src, pts_dst)
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
    area_px = round(float(cv2.contourArea(cnt)), 1)
    _, _, bw, bh = cv2.boundingRect(cnt)
    width_px = float(bw)
    height_px = float(bh)

    width_mm: float | None = None
    height_mm: float | None = None
    area_cm2: float | None = None

    if H_px_to_mm is not None and len(cnt) >= 3:
        cnt_pts = np.asarray(cnt, dtype=np.float32).reshape(-1, 1, 2)
        # Проверяем, что все точки лежат по правильную сторону линии горизонта гомографии
        homog_w = (
            H_px_to_mm[2, 0] * cnt_pts[:, 0, 0]
            + H_px_to_mm[2, 1] * cnt_pts[:, 0, 1]
            + H_px_to_mm[2, 2]
        )
        if np.all(np.abs(homog_w) > 1e-4) and (
            np.all(homog_w > 0) or np.all(homog_w < 0)
        ):
            cnt_mm = cv2.perspectiveTransform(cnt_pts, H_px_to_mm)
            area_mm2 = abs(float(cv2.contourArea(cnt_mm)))
            cnt_mm_2d = cnt_mm.reshape(-1, 2)
            w_mm = float(cnt_mm_2d[:, 0].max() - cnt_mm_2d[:, 0].min())
            h_mm = float(cnt_mm_2d[:, 1].max() - cnt_mm_2d[:, 1].min())
            if (
                np.isfinite(area_mm2)
                and np.isfinite(w_mm)
                and np.isfinite(h_mm)
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
    pts_arr = np.asarray(pts, dtype=np.float32).reshape(4, 2)
    center = pts_arr.mean(axis=0)
    angles = np.arctan2(pts_arr[:, 1] - center[1], pts_arr[:, 0] - center[0])
    ordered = pts_arr[np.argsort(angles)]
    len_01 = float(
        np.linalg.norm(ordered[1] - ordered[0])
        + np.linalg.norm(ordered[2] - ordered[3])
    )
    len_12 = float(
        np.linalg.norm(ordered[2] - ordered[1])
        + np.linalg.norm(ordered[3] - ordered[0])
    )
    if len_12 > len_01:
        ordered = np.roll(ordered, -1, axis=0)
    return ordered


def _compute_auto_rgb_gains(
    p_r: Any, p_g: Any, p_b: Any, p_w: Any, p_gray: Any
) -> tuple[float, float, float]:
    """Вычисляет коэффициенты автоцветокоррекции (r, g, b) в [0.00, 1.00] по нейтральным плашкам."""
    w_arr = np.asarray(p_w, dtype=np.float64)
    g_arr = np.asarray(p_gray, dtype=np.float64)

    # Баланс белого считаем исключительно по нейтральным плашкам (белая + серая),
    # чтобы цветные плашки (R/G/B) не перекашивали нейтральный фон в красный/зелёный.
    if float(np.max(w_arr)) < 245.0 and float(np.min(w_arr)) > 15.0:
        w_mean = max(1.0, float(np.mean(w_arr)))
        g_mean = max(1.0, float(np.mean(g_arr)))
        w_scaled = w_arr * (g_mean / w_mean)
        wb_ref = 0.60 * g_arr + 0.40 * w_scaled
    else:
        wb_ref = g_arr

    wb_gains = 1.0 / np.maximum(wb_ref, 5.0)
    max_wb = float(np.max(wb_gains))
    if max_wb > 0:
        wb_gains = wb_gains / max_wb

    # Мягкое демпфирование к 1.0 и нижний разумный порог разброса каналов
    damp_alpha = 0.12
    gains = (1.0 - damp_alpha) * wb_gains + damp_alpha
    gains = np.clip(gains, 0.35, 1.0)
    max_g = float(np.max(gains))
    if max_g > 0:
        gains = gains / max_g

    r_out = round(max(0.0, min(1.0, float(gains[0]))), 2)
    g_out = round(max(0.0, min(1.0, float(gains[1]))), 2)
    b_out = round(max(0.0, min(1.0, float(gains[2]))), 2)
    return (r_out, g_out, b_out)


def _reduce_poly_to_quad(pts: Any) -> Any | None:
    """Сводит 4–6-угольник к 4 вершинам, удаляя наименее значимые вершины по площади треугольника."""
    arr = np.asarray(pts, dtype=np.float32).reshape(-1, 2)
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
        arr = np.delete(arr, drop_idx, axis=0)
    return arr.astype(np.float32)


def crop_color_marker_roi_jpeg(
    img: Any,
    corners: Any,
    padding_ratio: float = 0.12,
    max_width: int = 480,
) -> bytes | None:
    """Вырезает ROI-фрагмент обнаруженного цветового маркера из BGR-кадра и кодирует в JPEG."""
    if not CV2_AVAILABLE or img is None or corners is None:
        return None
    h, w = img.shape[:2]
    pts = np.asarray(corners, dtype=np.float32).reshape(-1, 2)
    if len(pts) < 4:
        return None

    x_min, y_min = float(np.min(pts[:, 0])), float(np.min(pts[:, 1]))
    x_max, y_max = float(np.max(pts[:, 0])), float(np.max(pts[:, 1]))
    bw = max(1.0, x_max - x_min)
    bh = max(1.0, y_max - y_min)
    pad_x = bw * padding_ratio
    pad_y = bh * padding_ratio

    x1 = max(0, int(np.floor(x_min - pad_x)))
    y1 = max(0, int(np.floor(y_min - pad_y)))
    x2 = min(w, int(np.ceil(x_max + pad_x)))
    y2 = min(h, int(np.ceil(y_max + pad_y)))
    if x2 <= x1 or y2 <= y1:
        return None

    roi = img[y1:y2, x1:x2]
    if roi.size == 0:
        return None

    rh, rw = roi.shape[:2]
    if rw > max_width > 0:
        scale = float(max_width) / float(rw)
        new_h = max(1, round(rh * scale))
        roi = cv2.resize(roi, (max_width, new_h), interpolation=cv2.INTER_AREA)

    ok, enc = cv2.imencode(".jpg", roi, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
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
    border_pixels = np.concatenate([s.reshape(-1, 3) for s in border_strips], axis=0)
    border_luma = float(np.median(border_pixels))

    field_strips = [
        warped_bgr[24:42, 40:460],
        warped_bgr[158:176, 40:460],
    ]
    field_pixels = np.concatenate([s.reshape(-1, 3) for s in field_strips], axis=0)
    field_luma = float(np.median(field_pixels))
    field_std = float(np.max(np.std(field_pixels, axis=0)))

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
        roi_rgb = roi_bgr[:, :, ::-1].astype(np.float64)
        if float(np.max(np.std(roi_rgb, axis=(0, 1)))) > 60.0:
            return False, None
        med_rgb = np.median(roi_rgb.reshape(-1, 3), axis=0)
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

        w_mean = float(np.mean(p_w))
        gray_mean = float(np.mean(p_gray))
        w_ratio = float(np.max(p_w)) / max(1.0, float(np.min(p_w)))
        gray_ratio = float(np.max(p_gray)) / max(1.0, float(np.min(p_gray)))

        # Белая и нейтральная серая плашки (низкая насыщенность и разница яркости)
        if w_mean < 95.0 or float(np.min(p_w)) < 45.0 or w_ratio > 3.2:
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
    if not CV2_AVAILABLE or img is None:
        return None, None

    h, w = img.shape[:2]
    min_area = max(600.0, float(w * h) * 0.0015)
    max_area = float(w * h) * 0.92

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)

    bin_maps = [
        cv2.adaptiveThreshold(
            blurred,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            25,
            4,
        ),
        cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1],
        cv2.dilate(
            cv2.Canny(blurred, 40, 140), np.ones((3, 3), dtype=np.uint8), iterations=1
        ),
    ]

    candidates: list[tuple[float, Any]] = []
    seen_centers: list[tuple[float, float, float]] = []

    def _register_quad(quad_pts: Any, ref_area: float) -> None:
        pts = _order_quad_long_horizontal(quad_pts)
        l1 = float(np.linalg.norm(pts[1] - pts[0]))
        l2 = float(np.linalg.norm(pts[2] - pts[1]))
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
        contours, _ = cv2.findContours(bmap, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            area = float(cv2.contourArea(cnt))
            if area < min_area or area > max_area:
                continue
            peri = cv2.arcLength(cnt, True)
            if peri <= 0:
                continue
            for eps_factor in (0.02, 0.035, 0.05, 0.075, 0.1):
                approx = cv2.approxPolyDP(cnt, eps_factor * peri, True)
                n_pts = len(approx)
                if n_pts == 4 and cv2.isContourConvex(approx):
                    _register_quad(approx, area)
                    break
                if n_pts in (5, 6):
                    hull = cv2.convexHull(approx)
                    if len(hull) == 4:
                        _register_quad(hull, area)
                        break
                    if len(hull) in (5, 6):
                        reduced = _reduce_poly_to_quad(hull)
                        if reduced is not None and cv2.isContourConvex(
                            reduced.reshape(-1, 1, 2)
                        ):
                            _register_quad(reduced, area)
                        # Перспективный запасной вариант через minAreaRect при скошенных углах
                        box = cv2.boxPoints(cv2.minAreaRect(cnt))
                        _register_quad(box, area)

    # Проверяем от самых крупных (чтобы внешний контур рамки имел приоритет над внутренним белым полем)
    candidates.sort(key=lambda x: x[0], reverse=True)

    wx, wy, ww, wh = COLOR_MARKER_WHITE_FIELD
    outer_dst = np.array(
        [
            [0.0, 0.0],
            [float(COLOR_MARKER_CANON_W), 0.0],
            [float(COLOR_MARKER_CANON_W), float(COLOR_MARKER_CANON_H)],
            [0.0, float(COLOR_MARKER_CANON_H)],
        ],
        dtype=np.float32,
    )
    inner_dst = np.array(
        [
            [float(wx), float(wy)],
            [float(wx + ww), float(wy)],
            [float(wx + ww), float(wy + wh)],
            [float(wx), float(wy + wh)],
        ],
        dtype=np.float32,
    )

    for _, pts in candidates:
        # Вариант 1: кандидат — внешняя граница чёрной рамки
        M_outer = cv2.getPerspectiveTransform(pts, outer_dst)
        warped_outer = cv2.warpPerspective(
            img, M_outer, (COLOR_MARKER_CANON_W, COLOR_MARKER_CANON_H)
        )
        ok, gains = _verify_warped_color_marker(warped_outer)
        if ok and gains is not None:
            return pts.copy(), gains

        # Вариант 2: кандидат — внутреннее белое поле
        M_inner = cv2.getPerspectiveTransform(pts, inner_dst)
        warped_inner = cv2.warpPerspective(
            img, M_inner, (COLOR_MARKER_CANON_W, COLOR_MARKER_CANON_H)
        )
        ok_in, gains_in = _verify_warped_color_marker(warped_inner)
        if ok_in and gains_in is not None:
            with_inv = np.linalg.inv(M_inner)
            outer_pts = cv2.perspectiveTransform(
                outer_dst.reshape(-1, 1, 2), with_inv
            ).reshape(4, 2)
            return outer_pts.astype(np.float32), gains_in

    return None, None


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
    if not CV2_AVAILABLE or img is None:
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
        H_px_to_mm, _, fallback_mm_per_px = compute_aruco_homography(
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
        pts = np.asarray(aruco_corners, dtype=np.float32).reshape(-1, 2)
        if len(pts) >= 4:
            pts_int = np.round(pts).astype(np.int32)
            cv2.polylines(
                annotated,
                [pts_int],
                isClosed=True,
                color=COLOR_DARK,
                thickness=outline_thickness,
                lineType=cv2.LINE_AA,
            )
            cv2.polylines(
                annotated,
                [pts_int],
                isClosed=True,
                color=COLOR_ARUCO_YELLOW,
                thickness=thickness,
                lineType=cv2.LINE_AA,
            )
            ax, ay, aw, ah = cv2.boundingRect(pts_int)
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
        c_pts = np.asarray(color_marker_corners, dtype=np.float32).reshape(-1, 2)
        if len(c_pts) >= 4:
            c_pts_int = np.round(c_pts).astype(np.int32)
            cv2.polylines(
                annotated,
                [c_pts_int],
                isClosed=True,
                color=COLOR_DARK,
                thickness=outline_thickness,
                lineType=cv2.LINE_AA,
            )
            cv2.polylines(
                annotated,
                [c_pts_int],
                isClosed=True,
                color=COLOR_MARKER_BLUE,
                thickness=thickness,
                lineType=cv2.LINE_AA,
            )

    total_area_cm2 = 0.0
    has_metric_area = has_aruco

    # Геометрическая обработка каждого обнаруженного растения
    for idx, (cnt, _) in enumerate(contours_with_area):
        # 1. Контур с темной обводкой
        cv2.drawContours(
            annotated, [cnt], -1, COLOR_DARK, outline_thickness, cv2.LINE_AA
        )
        cv2.drawContours(annotated, [cnt], -1, COLOR_GREEN, thickness, cv2.LINE_AA)

        # 2. Красная точка в центре
        m = cv2.moments(cnt)
        if m["m00"] != 0:
            cx = int(m["m10"] / m["m00"])
            cy = int(m["m01"] / m["m00"])
        else:
            (cx_f, cy_f), _ = cv2.minEnclosingCircle(cnt)
            cx, cy = int(cx_f), int(cy_f)

        point_r = max(4, round(scale_ref / 130.0))
        cv2.circle(annotated, (cx, cy), point_r + 2, COLOR_DARK, -1, cv2.LINE_AA)
        cv2.circle(annotated, (cx, cy), point_r, COLOR_RED, -1, cv2.LINE_AA)

        # 3. Линейка слева и снизу (мм при ArUco, px без маркера)
        bx, by, bw, bh = cv2.boundingRect(cnt)
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
                metrics = measure_contour_metrics(
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
    if PIL_AVAILABLE and Image is not None and ImageDraw is not None:
        pil_img = Image.fromarray(cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(pil_img)

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

        annotated = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)

    else:
        # Fallback на cv2.putText при отсутствии Pillow
        font = cv2.FONT_HERSHEY_SIMPLEX
        COLOR_WHITE = (255, 255, 255)
        COLOR_YELLOW = (0, 235, 235)

        def draw_text_with_outline(
            text_str: str, pos: tuple[int, int], color=COLOR_WHITE
        ):
            cv2.putText(
                annotated,
                text_str,
                pos,
                font,
                font_scale,
                COLOR_DARK,
                outline_thickness,
                cv2.LINE_AA,
            )
            cv2.putText(
                annotated,
                text_str,
                pos,
                font,
                font_scale,
                color,
                thickness,
                cv2.LINE_AA,
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
            (tw, _), _ = cv2.getTextSize(text_cam, font, font_scale, thickness)
            draw_text_with_outline(text_cam, (w - margin - tw, cur_y))
            cur_y += line_h
        if captured_at:
            text_time = f"Время: {captured_at}"
            (tw, _), _ = cv2.getTextSize(text_time, font, font_scale, thickness)
            draw_text_with_outline(text_time, (w - margin - tw, cur_y))

        draw_text_with_outline(
            plants_count_str, (margin, h - margin), color=COLOR_WHITE
        )

        text_area = f"Площадь: {area_str}"
        (tw, _), _ = cv2.getTextSize(text_area, font, font_scale, thickness)
        draw_text_with_outline(
            text_area, (w - margin - tw, h - margin), color=COLOR_YELLOW
        )

    return annotated


def _resolve_onnx_input_size(input_shape: Any) -> tuple[int, int, str]:
    """Определяет (target_h, target_w, layout) из формы входа ONNX, фолбэк (256, 256, 'NCHW')."""

    def _valid_dim(val: Any) -> int | None:
        if isinstance(val, bool):
            return None
        if isinstance(val, int) and val > 0:
            return int(val)
        if np is not None and isinstance(val, np.integer) and int(val) > 0:
            return int(val)
        return None

    if not isinstance(input_shape, (list, tuple)):
        return 256, 256, "NCHW"

    if len(input_shape) == 4:
        c1 = _valid_dim(input_shape[1])
        c3 = _valid_dim(input_shape[3])
        if c3 in (1, 3) and c1 not in (1, 3):
            h_val = _valid_dim(input_shape[1]) or 256
            w_val = _valid_dim(input_shape[2]) or 256
            return h_val, w_val, "NHWC"
        h_val = _valid_dim(input_shape[2]) or 256
        w_val = _valid_dim(input_shape[3]) or 256
        return h_val, w_val, "NCHW"

    if len(input_shape) == 3:
        c0 = _valid_dim(input_shape[0])
        c2 = _valid_dim(input_shape[2])
        if c2 in (1, 3) and c0 not in (1, 3):
            h_val = _valid_dim(input_shape[0]) or 256
            w_val = _valid_dim(input_shape[1]) or 256
            return h_val, w_val, "NHWC"
        h_val = _valid_dim(input_shape[1]) or 256
        w_val = _valid_dim(input_shape[2]) or 256
        return h_val, w_val, "NCHW"

    return 256, 256, "NCHW"


def _postprocess_onnx_mask(raw_output: Any, orig_w: int, orig_h: int) -> Any:
    """Преобразует выход ONNX-модели в бинарную очищенную маску размера (orig_h, orig_w)."""
    arr = np.asarray(raw_output, dtype=np.float32)

    if arr.ndim == 4:
        # (1, C, H, W) или (1, H, W, C)
        if arr.shape[1] in (1, 2):
            arr = arr[0]
        elif arr.shape[3] in (1, 2):
            arr = np.transpose(arr[0], (2, 0, 1))
        else:
            arr = arr[0]

    if arr.ndim == 3:
        if arr.shape[0] == 2:
            # 2 канала: фон (0) и растение (1) -> softmax, если это логиты
            min_v = float(np.min(arr))
            max_v = float(np.max(arr))
            chan_sum = np.sum(arr, axis=0)
            is_probs = (
                min_v >= -1e-4
                and max_v <= 1.0 + 1e-4
                and bool(np.allclose(chan_sum, 1.0, atol=1e-2))
            )
            if is_probs:
                prob_mask = arr[1]
            else:
                shifted = arr - np.max(arr, axis=0, keepdims=True)
                exp_vals = np.exp(np.clip(shifted, -60.0, 60.0))
                probs = exp_vals / np.sum(exp_vals, axis=0, keepdims=True)
                prob_mask = probs[1]
        elif arr.shape[0] == 1:
            prob_mask = arr[0]
        elif arr.shape[2] == 1:
            prob_mask = arr[:, :, 0]
        elif arr.shape[2] == 2:
            logits = np.transpose(arr, (2, 0, 1))
            shifted = logits - np.max(logits, axis=0, keepdims=True)
            exp_vals = np.exp(np.clip(shifted, -60.0, 60.0))
            probs = exp_vals / np.sum(exp_vals, axis=0, keepdims=True)
            prob_mask = probs[1]
        else:
            prob_mask = arr[0]
    elif arr.ndim == 2:
        prob_mask = arr
    else:
        prob_mask = np.squeeze(arr)

    # Для одноканальных логитов вне [0, 1] применяем сигмоиду
    if (arr.ndim != 3 or arr.shape[0] != 2) and (
        float(np.min(prob_mask)) < 0.0 or float(np.max(prob_mask)) > 1.0
    ):
        prob_mask = 1.0 / (1.0 + np.exp(-np.clip(prob_mask, -60.0, 60.0)))

    prob_full = cv2.resize(
        prob_mask.astype(np.float32), (orig_w, orig_h), interpolation=cv2.INTER_LINEAR
    )
    bin_mask_full = (prob_full > 0.5).astype(np.uint8) * 255

    kernel_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    kernel_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    bin_mask_full = cv2.morphologyEx(
        bin_mask_full, cv2.MORPH_OPEN, kernel_open, iterations=1
    )
    bin_mask_full = cv2.morphologyEx(
        bin_mask_full, cv2.MORPH_CLOSE, kernel_close, iterations=1
    )
    return bin_mask_full


class VisionPipeline:
    """Пайплайн компьютерного зрения: детекция ArUco-маркеров и сегментация растений."""

    def __init__(self, model_path: Path | None = None, mock_mode: bool = False):
        self.model_path = model_path
        self.mock_mode = mock_mode
        self._session: Any = None
        self._model_loaded = False
        self._session_load_failed = False
        self._last_failed_path: Path | None = None

    def _ensure_session(self, allow_in_mock: bool = False) -> Any:
        """Лениво загружает и кэширует ONNX InferenceSession с CPUExecutionProvider."""
        if self._session is not None:
            self._model_loaded = True
            return self._session
        if self.mock_mode and not allow_in_mock:
            return None
        if self._session_load_failed and self._last_failed_path == self.model_path:
            return None
        if not ORT_AVAILABLE or ort is None:
            self._session_load_failed = True
            self._last_failed_path = self.model_path
            logger.warning(
                "onnxruntime недоступен. Для сегментации используется HSV-пайплайн."
            )
            return None
        if not self.model_path or not self.model_path.is_file():
            self._session_load_failed = True
            self._last_failed_path = self.model_path
            logger.warning(
                f"Файл ONNX-модели не найден: {self.model_path}. Используется HSV-пайплайн."
            )
            return None
        try:
            self._session = ort.InferenceSession(
                str(self.model_path),
                providers=["CPUExecutionProvider"],
            )
            self._model_loaded = True
            self._session_load_failed = False
            self._last_failed_path = None
            logger.info(f"ONNX модель успешно загружена: {self.model_path.name}")
            return self._session
        except Exception as exc:  # noqa: BLE001
            self._session = None
            self._model_loaded = False
            self._session_load_failed = True
            self._last_failed_path = self.model_path
            logger.warning(
                f"Не удалось загрузить ONNX-модель {self.model_path}: {exc}. Используется HSV-пайплайн."
            )
            return None

    def get_model_name(self) -> str:
        """Возвращает имя загруженной/доступной модели или 'none'."""
        if self.mock_mode:
            return self.model_path.name if self.model_path else "mock_model.onnx"
        if self._model_loaded and self.model_path:
            return self.model_path.name
        if (
            not self._session_load_failed
            and ORT_AVAILABLE
            and self.model_path
            and self.model_path.is_file()
        ):
            return self.model_path.name
        return "none"

    def _detect_aruco_in_gray(
        self, gray_img: Any, params: Any
    ) -> tuple[float | None, Any | None]:
        """Ищет маркер ArUco по набору поддерживаемых словарей на полутоновом кадре."""
        for dict_name in ARUCO_DICTIONARY_IDS:
            dict_id = getattr(cv2.aruco, dict_name, None)
            if dict_id is None:
                continue
            aruco_dict = cv2.aruco.getPredefinedDictionary(dict_id)
            if hasattr(cv2.aruco, "ArucoDetector"):
                detector = (
                    cv2.aruco.ArucoDetector(aruco_dict, params)
                    if params is not None
                    else cv2.aruco.ArucoDetector(aruco_dict)
                )
                corners, ids, _ = detector.detectMarkers(gray_img)
            else:
                corners, ids, _ = cv2.aruco.detectMarkers(
                    gray_img,
                    aruco_dict,
                    parameters=params,
                )

            if ids is not None and len(corners) > 0:
                # Выбираем маркер с наибольшей площадью, отсеивая мелкие ложные срабатывания
                best_corners = max(
                    (c[0] for c in corners),
                    key=lambda pts: float(cv2.contourArea(pts)),
                )
                area = float(cv2.contourArea(best_corners))
                if area >= 64.0:
                    logger.debug(
                        f"ArUco маркер ({dict_name}) обнаружен, площадь: {area:.1f} px"
                    )
                    return round(area, 1), best_corners.copy()
        return None, None

    def detect_aruco_details(
        self, image_bytes: bytes
    ) -> tuple[float | None, Any | None]:
        """Обнаруживает ArUco-маркер и возвращает (площадь_px, углы_маркера)."""
        if not CV2_AVAILABLE:
            return None, None

        try:
            nparr = np.frombuffer(image_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                return None, None

            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            params = _build_aruco_detector_params()

            area, corners = self._detect_aruco_in_gray(gray, params)
            if area is not None:
                return area, corners

            # Второй проход для сложных условий (блики/низкий контраст/муар экрана монитора)
            blurred = cv2.GaussianBlur(gray, (3, 3), 0)
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            enhanced = clahe.apply(blurred)
            return self._detect_aruco_in_gray(enhanced, params)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Ошибка при детекции ArUco маркера: {e}")
            return None, None

    def detect_aruco(self, image_bytes: bytes) -> float | None:
        """Обнаруживает ArUco-маркер и вычисляет его площадь в пикселях.

        Возвращает None, если маркер не найден или если cv2 недоступен.
        """
        area, _ = self.detect_aruco_details(image_bytes)
        return area

    def detect_color_marker_details(
        self, image_bytes: bytes
    ) -> tuple[Any | None, tuple[float, float, float] | None]:
        """Обнаруживает цветовой маркер в кадре и возвращает (углы_маркера, (r, g, b))."""
        if not CV2_AVAILABLE or not image_bytes:
            return None, None
        try:
            nparr = np.frombuffer(image_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                return None, None
            return detect_color_marker_in_bgr(img)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"Ошибка при детекции цветового маркера: {exc}")
            return None, None

    def detect_color_marker_with_roi(
        self, image_bytes: bytes
    ) -> tuple[Any | None, tuple[float, float, float] | None, bytes | None]:
        """Обнаруживает цветовой маркер и возвращает (углы_маркера, (r, g, b), jpeg_roi_кроп)."""
        if not CV2_AVAILABLE or not image_bytes:
            return None, None, None
        try:
            nparr = np.frombuffer(image_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                return None, None, None
            corners, gains = detect_color_marker_in_bgr(img)
            if corners is None or gains is None:
                return None, None, None
            roi_bytes = crop_color_marker_roi_jpeg(img, corners)
            return corners, gains, roi_bytes
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"Ошибка при детекции цветового маркера с ROI: {exc}")
            return None, None, None

    def calibrate_color_from_marker(
        self, image_bytes: bytes
    ) -> tuple[float, float, float] | None:
        """Вычисляет коэффициенты автоцветокоррекции (r, g, b) по эталонному цветовому маркеру."""
        _, gains = self.detect_color_marker_details(image_bytes)
        return gains

    @staticmethod
    def _build_measurements_and_annotate(
        img: Any,
        bin_mask_full: Any,
        raw_contours: Any,
        min_area: float,
        aruco_area_px: float | None = None,
        aruco_size_mm: float | None = None,
        inst_name: str | None = None,
        culture_name: str | None = None,
        camera_name: str | None = None,
        captured_at: str | None = None,
        aruco_corners: Any = None,
        color_marker_corners: Any = None,
    ) -> tuple[bytes, list[PlantMeasurementMeta]]:
        """Фильтрует контуры, считает пиксельные/метрические замеры и наносит CV-разметку."""
        color_poly = None
        if color_marker_corners is not None:
            c_pts = np.asarray(color_marker_corners, dtype=np.float32).reshape(-1, 2)
            if len(c_pts) >= 4:
                color_poly = np.round(c_pts).astype(np.int32)

        valid_contours_with_area: list[tuple[Any, float]] = []
        for cnt in raw_contours:
            area = float(cv2.contourArea(cnt))
            if area < min_area:
                continue
            if color_poly is not None:
                m = cv2.moments(cnt)
                if m["m00"] != 0:
                    cx = float(m["m10"] / m["m00"])
                    cy = float(m["m01"] / m["m00"])
                else:
                    (cx, cy), _ = cv2.minEnclosingCircle(cnt)
                if cv2.pointPolygonTest(color_poly, (float(cx), float(cy)), False) >= 0:
                    continue
            valid_contours_with_area.append((cnt, area))

        valid_contours_with_area.sort(key=lambda x: x[1], reverse=True)

        has_aruco = bool(
            aruco_corners is not None
            or (aruco_area_px is not None and float(aruco_area_px) > 0)
        )
        H_px_to_mm = None
        fallback_mm_per_px = None
        if has_aruco:
            H_px_to_mm, _, fallback_mm_per_px = compute_aruco_homography(
                aruco_corners=aruco_corners,
                aruco_size_mm=aruco_size_mm,
                aruco_area_px=aruco_area_px,
            )

        measurements: list[PlantMeasurementMeta] = []
        annotated = img.copy()

        if valid_contours_with_area:
            overlay_mask = bin_mask_full.copy()
            if color_poly is not None:
                cv2.fillPoly(overlay_mask, [color_poly], 0)
            color_mask = np.zeros_like(img)
            color_mask[overlay_mask > 0] = (0, 255, 0)
            annotated = cv2.addWeighted(annotated, 1.0, color_mask, 0.4, 0)

        for plant_idx, (cnt, _) in enumerate(valid_contours_with_area):
            metrics = measure_contour_metrics(
                cnt,
                H_px_to_mm=H_px_to_mm,
                fallback_mm_per_px=fallback_mm_per_px,
            )
            measurements.append(
                PlantMeasurementMeta(
                    plant_index=plant_idx,
                    area_px=metrics["area_px"] or 0.0,
                    width_px=metrics["width_px"],
                    height_px=metrics["height_px"],
                    width_mm=metrics["width_mm"],
                    height_mm=metrics["height_mm"],
                    area_cm2=metrics["area_cm2"],
                )
            )

        annotated = draw_cv_annotations(
            annotated,
            valid_contours_with_area,
            aruco_area_px=aruco_area_px,
            aruco_size_mm=aruco_size_mm,
            inst_name=inst_name,
            culture_name=culture_name,
            camera_name=camera_name,
            captured_at=captured_at,
            aruco_corners=aruco_corners,
            color_marker_corners=color_marker_corners,
            measurements=measurements,
        )

        success, enc_img = cv2.imencode(".jpg", annotated)
        annotated_bytes = enc_img.tobytes() if success else b""
        return annotated_bytes, measurements

    @staticmethod
    def _normalize_segmentation_mode(segmentation_mode: str | None) -> str:
        raw = str(segmentation_mode or "auto").strip().lower()
        return raw if raw in ("auto", "onnx", "hsv") else "auto"

    def segment_plants(
        self,
        image_bytes: bytes,
        aruco_area_px: float | None = None,
        aruco_size_mm: float | None = None,
        inst_name: str | None = None,
        culture_name: str | None = None,
        camera_name: str | None = None,
        captured_at: str | None = None,
        aruco_corners: Any = None,
        color_marker_corners: Any = None,
        segmentation_mode: str = "auto",
    ) -> tuple[bytes, list[PlantMeasurementMeta]]:
        """Выполняет сегментацию растений и отрисовку маски на кадре.

        Поддерживает режимы segmentation_mode: 'auto' (ONNX при наличии модели, иначе HSV),
        'onnx' (принудительно ONNX с безопасным фолбэком в HSV) и 'hsv'.
        Возвращает кортеж (annotated_image_bytes, measurements).
        """
        if not CV2_AVAILABLE:
            logger.error(
                "OpenCV недоступен. Сегментация невозможна, возврат сырого кадра."
            )
            return image_bytes, []

        mode = self._normalize_segmentation_mode(segmentation_mode)

        # 1. Явный HSV-режим либо auto в mock_mode без доступного ONNX-файла/сессии
        has_loadable_onnx_in_mock = bool(
            ORT_AVAILABLE
            and ort is not None
            and self.model_path is not None
            and self.model_path.is_file()
            and not (
                self._session_load_failed and self._last_failed_path == self.model_path
            )
        )
        if mode == "hsv" or (
            mode == "auto"
            and self.mock_mode
            and self._session is None
            and not has_loadable_onnx_in_mock
        ):
            return self._segment_plants_hsv(
                image_bytes,
                aruco_area_px=aruco_area_px,
                aruco_size_mm=aruco_size_mm,
                inst_name=inst_name,
                culture_name=culture_name,
                camera_name=camera_name,
                captured_at=captured_at,
                aruco_corners=aruco_corners,
                color_marker_corners=color_marker_corners,
            )

        # 2. Ленивая загрузка ONNX-сессии (в mock_mode разрешаем при mode=='onnx' или наличии .onnx в auto)
        session = self._ensure_session(
            allow_in_mock=(mode == "onnx" or has_loadable_onnx_in_mock)
        )
        if session is None:
            return self._segment_plants_hsv(
                image_bytes,
                aruco_area_px=aruco_area_px,
                aruco_size_mm=aruco_size_mm,
                inst_name=inst_name,
                culture_name=culture_name,
                camera_name=camera_name,
                captured_at=captured_at,
                aruco_corners=aruco_corners,
                color_marker_corners=color_marker_corners,
            )

        try:
            nparr = np.frombuffer(image_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                return image_bytes, []

            h, w = img.shape[:2]

            input_meta = session.get_inputs()[0]
            input_name = input_meta.name
            target_h, target_w, layout = _resolve_onnx_input_size(input_meta.shape)

            resized = cv2.resize(
                img, (target_w, target_h), interpolation=cv2.INTER_LINEAR
            )
            rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
            if layout == "NHWC":
                tensor = rgb[np.newaxis, ...]
            else:
                tensor = np.transpose(rgb, (2, 0, 1))[np.newaxis, ...]

            outputs = session.run(None, {input_name: tensor})
            bin_mask_full = _postprocess_onnx_mask(outputs[0], orig_w=w, orig_h=h)

            raw_contours, _ = cv2.findContours(
                bin_mask_full, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )

            # Адаптивный порог минимальной площади от разрешения кадра: max(150.0, (w * h) * 0.0005)
            min_area = max(150.0, float(w * h) * 0.0005)
            annotated_bytes, measurements = self._build_measurements_and_annotate(
                img=img,
                bin_mask_full=bin_mask_full,
                raw_contours=raw_contours,
                min_area=min_area,
                aruco_area_px=aruco_area_px,
                aruco_size_mm=aruco_size_mm,
                inst_name=inst_name,
                culture_name=culture_name,
                camera_name=camera_name,
                captured_at=captured_at,
                aruco_corners=aruco_corners,
                color_marker_corners=color_marker_corners,
            )
            return (annotated_bytes or image_bytes), measurements

        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"Ошибка инференса ONNX-модели: {e}. Применяется fallback-сегментация по HSV."
            )
            return self._segment_plants_hsv(
                image_bytes,
                aruco_area_px=aruco_area_px,
                aruco_size_mm=aruco_size_mm,
                inst_name=inst_name,
                culture_name=culture_name,
                camera_name=camera_name,
                captured_at=captured_at,
                aruco_corners=aruco_corners,
                color_marker_corners=color_marker_corners,
            )

    def _segment_plants_hsv(
        self,
        image_bytes: bytes,
        aruco_area_px: float | None = None,
        aruco_size_mm: float | None = None,
        inst_name: str | None = None,
        culture_name: str | None = None,
        camera_name: str | None = None,
        captured_at: str | None = None,
        aruco_corners: Any = None,
        color_marker_corners: Any = None,
    ) -> tuple[bytes, list[PlantMeasurementMeta]]:
        """Сегментация растений в mock-режиме по цветовому порогу HSV."""
        if not CV2_AVAILABLE:
            return image_bytes, []

        try:
            nparr = np.frombuffer(image_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                return image_bytes, []

            h, w = img.shape[:2]
            # Адаптивный порог минимальной площади от разрешения кадра: отсекает мелкий шум теней
            min_area = max(350.0, float(w * h) * 0.0012)

            # Шумоподавление перед HSV, чтобы убрать матричный цветовой шум камеры в тёмных участках
            denoised = cv2.medianBlur(img, 5)
            denoised = cv2.GaussianBlur(denoised, (3, 3), 0)
            hsv = cv2.cvtColor(denoised, cv2.COLOR_BGR2HSV)
            # Повышены пороги S >= 80 и V >= 60 для фильтрации тёмного и слабонасыщенного фона
            lower_green = np.array([30, 80, 60], dtype=np.uint8)
            upper_green = np.array([85, 255, 255], dtype=np.uint8)
            mask = cv2.inRange(hsv, lower_green, upper_green)

            # Морфологическая очистка: удаление мелкого шума (OPEN 3x3) и склейка разрывов/теней внутри куста (CLOSE 9x9)
            kernel_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
            kernel_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel_open, iterations=1)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel_close, iterations=1)

            raw_contours, _ = cv2.findContours(
                mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            annotated_bytes, measurements = self._build_measurements_and_annotate(
                img=img,
                bin_mask_full=mask,
                raw_contours=raw_contours,
                min_area=min_area,
                aruco_area_px=aruco_area_px,
                aruco_size_mm=aruco_size_mm,
                inst_name=inst_name,
                culture_name=culture_name,
                camera_name=camera_name,
                captured_at=captured_at,
                aruco_corners=aruco_corners,
                color_marker_corners=color_marker_corners,
            )
            return (annotated_bytes or image_bytes), measurements

        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Ошибка при HSV-сегментации: {exc}")
            return image_bytes, []

    def process_capture(
        self,
        image_bytes: bytes,
        inst_name: str | None = None,
        culture_name: str | None = None,
        camera_name: str | None = None,
        captured_at: str | None = None,
        aruco_size_mm: float | None = None,
        segmentation_mode: str = "auto",
    ) -> tuple[bytes, float | None, list[PlantMeasurementMeta]]:
        """Полная обработка кадра: детекция ArUco, цветового маркера и сегментация растений."""
        aruco_area, aruco_corners = self.detect_aruco_details(image_bytes)
        color_corners, _ = self.detect_color_marker_details(image_bytes)
        annotated_bytes, measurements = self.segment_plants(
            image_bytes,
            aruco_area_px=aruco_area,
            aruco_size_mm=aruco_size_mm,
            inst_name=inst_name,
            culture_name=culture_name,
            camera_name=camera_name,
            captured_at=captured_at,
            aruco_corners=aruco_corners,
            color_marker_corners=color_corners,
            segmentation_mode=segmentation_mode,
        )
        return annotated_bytes, aruco_area, measurements
