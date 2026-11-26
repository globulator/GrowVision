from shared.logger import get_logger

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

from .annotation import (
    _draw_bbox_rulers,
    _get_pil_font,
    draw_cv_annotations,
)
from .markers import (
    _build_aruco_detector_params,
    _compute_auto_rgb_gains,
    _order_quad_long_horizontal,
    _reduce_poly_to_quad,
    _verify_warped_color_marker,
    compute_aruco_homography,
    crop_color_marker_roi_jpeg,
    detect_color_marker_in_bgr,
    measure_contour_metrics,
)
from .pipeline import (
    VisionPipeline,
    _decode_bgr_input,
    _ensure_jpeg_bytes,
    _is_ndarray,
    _postprocess_onnx_mask,
    _resolve_onnx_input_size,
    apply_rgb_gains_to_bgr,
    apply_rgb_to_jpeg_bytes,
)

__all__ = [
    "ARUCO_DICTIONARY_IDS",
    "CANDIDATE_FONTS",
    "CV2_AVAILABLE",
    "DEFAULT_ARUCO_SIZE_MM",
    "ORT_AVAILABLE",
    "PIL_AVAILABLE",
    "Image",
    "ImageDraw",
    "ImageFont",
    "VisionPipeline",
    "_build_aruco_detector_params",
    "_compute_auto_rgb_gains",
    "_decode_bgr_input",
    "_draw_bbox_rulers",
    "_ensure_jpeg_bytes",
    "_get_pil_font",
    "_is_ndarray",
    "_order_quad_long_horizontal",
    "_postprocess_onnx_mask",
    "_reduce_poly_to_quad",
    "_resolve_onnx_input_size",
    "_verify_warped_color_marker",
    "apply_rgb_gains_to_bgr",
    "apply_rgb_to_jpeg_bytes",
    "compute_aruco_homography",
    "crop_color_marker_roi_jpeg",
    "cv2",
    "detect_color_marker_in_bgr",
    "draw_cv_annotations",
    "logger",
    "measure_contour_metrics",
    "np",
    "ort",
]
