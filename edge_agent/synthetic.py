import logging

from edge_agent.hardware import MOCK_JPEG_FRAME

logger = logging.getLogger("edge_agent.synthetic")

CV2_AVAILABLE = False
try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore

    CV2_AVAILABLE = True
except ImportError:
    cv2 = None  # type: ignore
    np = None  # type: ignore


def _camera_offset(camera_id: int | str) -> tuple[int, int]:
    """Вычисляет детерминированный сдвиг ракурса в зависимости от идентификатора камеры."""
    if isinstance(camera_id, int):
        val = camera_id
    else:
        val = sum(ord(c) for c in str(camera_id))
    dx = ((val * 37) % 61) - 30  # диапазон [-30, 30]
    dy = ((val * 43) % 41) - 20  # диапазон [-20, 20]
    return dx, dy


def compute_plant_radius(frame_index: int, max_radius: int) -> int:
    """Вычисляет ступенчатый радиус розетки растения в зависимости от номера кадра."""
    i = max(0, int(frame_index))
    if i < 10:
        r = 24 + i * 4
    elif i < 20:
        r = 64 + (i - 10) * 3
    elif i < 35:
        r = 94 + (i - 20) * 2
    else:
        r = 124 + (i - 35) * 1
    return min(r, max_radius)


def generate_synthetic_frame(
    frame_index: int = 0,
    camera_id: int | str = 0,
    width: int = 1280,
    height: int = 720,
) -> bytes:
    """Генерирует синтетический кадр 1280x720:

    - Нейтральный фон (лабораторный бокс);
    - Зелёное растение по центру с монотонным ростом площади от frame_index;
    - Маркер ArUco DICT_4X4_50 id 0 известного размера;
    - Ракурс и смещение зависят от camera_id;
    - При отсутствии OpenCV возвращает запасной 1x1 JPEG с обязательным предупреждением в лог.
    """
    if not CV2_AVAILABLE:
        logger.warning(
            "OpenCV недоступен: используется запасной 1x1 JPEG вместо синтетического кадра 1280x720"
        )
        return MOCK_JPEG_FRAME

    try:
        # 1. Нейтральный фон (темно-серый)
        frame = np.full((height, width, 3), 48, dtype=np.uint8)

        dx, dy = _camera_offset(camera_id)

        # 2. Маркер ArUco DICT_4X4_50 (id 0) с белой рамкой для контраста
        aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        marker_size = 80
        if hasattr(cv2.aruco, "generateImageMarker"):
            marker_core = cv2.aruco.generateImageMarker(aruco_dict, 0, marker_size)
        else:
            marker_core = cv2.aruco.drawMarker(aruco_dict, 0, marker_size)

        pad = 12
        marker_patch = np.full(
            (marker_size + 2 * pad, marker_size + 2 * pad), 255, dtype=np.uint8
        )
        marker_patch[pad : pad + marker_size, pad : pad + marker_size] = marker_core
        marker_bgr = cv2.cvtColor(marker_patch, cv2.COLOR_GRAY2BGR)

        m_h, m_w = marker_patch.shape
        m_x = max(10, min(width - m_w - 10, 50 + dx))
        m_y = max(10, min(height - m_h - 10, 50 + dy))
        frame[m_y : m_y + m_h, m_x : m_x + m_w] = marker_bgr

        # 3. Зеленое растение с монотонным ростом площади
        # Центр растения со смещением от ракурса
        cx = max(100, min(width - 100, (width // 2) + dx))
        cy = max(100, min(height - 100, (height // 2) + 20 + dy))

        max_radius = min(width, height) // 2 - 30
        base_radius = compute_plant_radius(frame_index, max_radius)
        angle_offset = (frame_index * 7) % 360
        # Отрисовка лепестков растения под разными углами
        leaf_color = (36, 185, 42)  # Яркий зеленый в BGR
        angles = [0, 60, 120, 180, 240, 300]
        for angle in angles:
            cv2.ellipse(
                frame,
                (cx, cy),
                (base_radius, max(12, int(base_radius * 0.62))),
                (angle + angle_offset) % 360,
                0,
                360,
                leaf_color,
                -1,
            )

        # Центральная розетка
        cv2.circle(
            frame,
            (cx, cy),
            max(10, int(base_radius * 0.78)),
            (32, 205, 38),
            -1,
        )

        success, enc = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not success:
            logger.warning(
                "Не удалось закодировать синтетический кадр в JPEG. Возврат MOCK_JPEG_FRAME."
            )
            return MOCK_JPEG_FRAME
        return enc.tobytes()

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"Ошибка при генерации синтетического кадра: {exc}. Возврат MOCK_JPEG_FRAME."
        )
        return MOCK_JPEG_FRAME


def compute_green_area_px(image_bytes: bytes) -> float:
    """Вычисляет суммарную площадь зелёных пикселей методом цветового порога HSV."""
    if not CV2_AVAILABLE:
        return 0.0

    try:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return 0.0

        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        lower_green = np.array([35, 40, 40], dtype=np.uint8)
        upper_green = np.array([85, 255, 255], dtype=np.uint8)
        mask = cv2.inRange(hsv, lower_green, upper_green)

        green_px = float(cv2.countNonZero(mask))
        return round(green_px, 1)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Ошибка вычисления зеленой площади HSV: {exc}")
        return 0.0


def detect_aruco_area_px(image_bytes: bytes) -> float | None:
    """Обнаруживает маркер ArUco DICT_4X4_50 и вычисляет его площадь в пикселях."""
    if not CV2_AVAILABLE:
        return None

    try:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return None

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)

        if hasattr(cv2.aruco, "ArucoDetector"):
            detector = cv2.aruco.ArucoDetector(aruco_dict)
            corners, ids, _ = detector.detectMarkers(gray)
        else:
            corners, ids, _ = cv2.aruco.detectMarkers(gray, aruco_dict)

        if ids is not None and len(corners) > 0:
            area = float(cv2.contourArea(corners[0][0]))
            return round(area, 1)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Ошибка при детекции ArUco маркера: {exc}")
        return None
