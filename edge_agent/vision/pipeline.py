from pathlib import Path
from typing import Any

import edge_agent.vision as _v
from shared.schemas import PlantMeasurementMeta


def apply_rgb_gains_to_bgr(
    img: Any, r: float = 1.0, g: float = 1.0, b: float = 1.0
) -> Any:
    """Применяет программные коэффициенты R/G/B (0.0..1.0+) к OpenCV BGR-изображению."""
    if img is None or not _v.CV2_AVAILABLE:
        return img
    rg = max(0.0, float(r))
    gg = max(0.0, float(g))
    bg = max(0.0, float(b))
    if abs(rg - 1.0) < 1e-4 and abs(gg - 1.0) < 1e-4 and abs(bg - 1.0) < 1e-4:
        return img
    out = img.copy()
    if abs(bg - 1.0) >= 1e-4:
        out[:, :, 0] = _v.np.clip(
            out[:, :, 0].astype(_v.np.float32) * bg, 0, 255
        ).astype(_v.np.uint8)
    if abs(gg - 1.0) >= 1e-4:
        out[:, :, 1] = _v.np.clip(
            out[:, :, 1].astype(_v.np.float32) * gg, 0, 255
        ).astype(_v.np.uint8)
    if abs(rg - 1.0) >= 1e-4:
        out[:, :, 2] = _v.np.clip(
            out[:, :, 2].astype(_v.np.float32) * rg, 0, 255
        ).astype(_v.np.uint8)
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
    if not _v.CV2_AVAILABLE:
        return image_bytes
    nparr = _v.np.frombuffer(image_bytes, _v.np.uint8)
    img = _v.cv2.imdecode(nparr, _v.cv2.IMREAD_COLOR)
    if img is None:
        return image_bytes
    corrected = apply_rgb_gains_to_bgr(img, r=rg, g=gg, b=bg)
    ok, enc = _v.cv2.imencode(".jpg", corrected, [_v.cv2.IMWRITE_JPEG_QUALITY, 92])
    return enc.tobytes() if ok else image_bytes


def _is_ndarray(image_input: Any) -> bool:
    """Проверяет, является ли вход готовым BGR-массивом numpy.ndarray."""
    return (
        _v.CV2_AVAILABLE
        and _v.np is not None
        and isinstance(image_input, _v.np.ndarray)
    )


def _decode_bgr_input(image_input: bytes | Any) -> Any:
    """Декодирует байты изображения в BGR np.ndarray или возвращает переданный np.ndarray."""
    if not _v.CV2_AVAILABLE or image_input is None:
        return None
    if _is_ndarray(image_input):
        return image_input if image_input.size > 0 else None
    if not image_input:
        return None
    nparr = _v.np.frombuffer(image_input, _v.np.uint8)
    return _v.cv2.imdecode(nparr, _v.cv2.IMREAD_COLOR)


def _ensure_jpeg_bytes(image_input: bytes | Any) -> bytes:
    """Гарантирует возврат bytes (кодирует np.ndarray в JPEG только при фолбэке)."""
    if isinstance(image_input, (bytes, bytearray)):
        return bytes(image_input)
    if _is_ndarray(image_input) and image_input.size > 0:
        ok, enc = _v.cv2.imencode(".jpg", image_input)
        return enc.tobytes() if ok else b""
    return b""


def _resolve_onnx_input_size(input_shape: Any) -> tuple[int, int, str]:
    """Определяет (target_h, target_w, layout) из формы входа ONNX, фолбэк (256, 256, 'NCHW')."""

    def _valid_dim(val: Any) -> int | None:
        if isinstance(val, bool):
            return None
        if isinstance(val, int) and val > 0:
            return int(val)
        if _v.np is not None and isinstance(val, _v.np.integer) and int(val) > 0:
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
    arr = _v.np.asarray(raw_output, dtype=_v.np.float32)

    if arr.ndim == 4:
        # (1, C, H, W) или (1, H, W, C)
        if arr.shape[1] in (1, 2):
            arr = arr[0]
        elif arr.shape[3] in (1, 2):
            arr = _v.np.transpose(arr[0], (2, 0, 1))
        else:
            arr = arr[0]

    if arr.ndim == 3:
        if arr.shape[0] == 2:
            # 2 канала: фон (0) и растение (1) -> softmax, если это логиты
            min_v = float(_v.np.min(arr))
            max_v = float(_v.np.max(arr))
            chan_sum = _v.np.sum(arr, axis=0)
            is_probs = (
                min_v >= -1e-4
                and max_v <= 1.0 + 1e-4
                and bool(_v.np.allclose(chan_sum, 1.0, atol=1e-2))
            )
            if is_probs:
                prob_mask = arr[1]
            else:
                shifted = arr - _v.np.max(arr, axis=0, keepdims=True)
                exp_vals = _v.np.exp(_v.np.clip(shifted, -60.0, 60.0))
                probs = exp_vals / _v.np.sum(exp_vals, axis=0, keepdims=True)
                prob_mask = probs[1]
        elif arr.shape[0] == 1:
            prob_mask = arr[0]
        elif arr.shape[2] == 1:
            prob_mask = arr[:, :, 0]
        elif arr.shape[2] == 2:
            logits = _v.np.transpose(arr, (2, 0, 1))
            shifted = logits - _v.np.max(logits, axis=0, keepdims=True)
            exp_vals = _v.np.exp(_v.np.clip(shifted, -60.0, 60.0))
            probs = exp_vals / _v.np.sum(exp_vals, axis=0, keepdims=True)
            prob_mask = probs[1]
        else:
            prob_mask = arr[0]
    elif arr.ndim == 2:
        prob_mask = arr
    else:
        prob_mask = _v.np.squeeze(arr)

    # Для одноканальных логитов вне [0, 1] применяем сигмоиду
    if (arr.ndim != 3 or arr.shape[0] != 2) and (
        float(_v.np.min(prob_mask)) < 0.0 or float(_v.np.max(prob_mask)) > 1.0
    ):
        prob_mask = 1.0 / (1.0 + _v.np.exp(-_v.np.clip(prob_mask, -60.0, 60.0)))

    prob_full = _v.cv2.resize(
        prob_mask.astype(_v.np.float32),
        (orig_w, orig_h),
        interpolation=_v.cv2.INTER_LINEAR,
    )
    bin_mask_full = (prob_full > 0.5).astype(_v.np.uint8) * 255

    kernel_open = _v.cv2.getStructuringElement(_v.cv2.MORPH_ELLIPSE, (3, 3))
    kernel_close = _v.cv2.getStructuringElement(_v.cv2.MORPH_ELLIPSE, (5, 5))
    bin_mask_full = _v.cv2.morphologyEx(
        bin_mask_full, _v.cv2.MORPH_OPEN, kernel_open, iterations=1
    )
    bin_mask_full = _v.cv2.morphologyEx(
        bin_mask_full, _v.cv2.MORPH_CLOSE, kernel_close, iterations=1
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
        self._aruco_params: Any = None
        self._aruco_dicts: dict[int, Any] = {}
        self._aruco_detectors: dict[int, Any] = {}
        self._aruco_dict_entries: list[tuple[str, int]] = []
        self._aruco_module_id: int | None = None
        self._init_aruco_detectors()

    def _init_aruco_detectors(self) -> None:
        """Предсоздаёт и кэширует DetectorParameters, словари и ArucoDetector для всех ARUCO_DICTIONARY_IDS."""
        self._aruco_params = None
        self._aruco_dicts = {}
        self._aruco_detectors = {}
        self._aruco_dict_entries = []
        self._aruco_module_id = None
        if not _v.CV2_AVAILABLE or _v.cv2 is None or not hasattr(_v.cv2, "aruco"):
            return
        self._aruco_module_id = id(_v.cv2.aruco)
        params = _v._build_aruco_detector_params()
        self._aruco_params = params
        has_detector_cls = hasattr(_v.cv2.aruco, "ArucoDetector")
        for dict_name in _v.ARUCO_DICTIONARY_IDS:
            dict_id = getattr(_v.cv2.aruco, dict_name, None)
            if dict_id is None:
                continue
            aruco_dict = _v.cv2.aruco.getPredefinedDictionary(dict_id)
            self._aruco_dicts[dict_id] = aruco_dict
            self._aruco_dict_entries.append((dict_name, dict_id))
            if has_detector_cls:
                self._aruco_detectors[dict_id] = (
                    _v.cv2.aruco.ArucoDetector(aruco_dict, params)
                    if params is not None
                    else _v.cv2.aruco.ArucoDetector(aruco_dict)
                )

    def _ensure_aruco_detectors(
        self,
    ) -> tuple[list[tuple[str, int]], dict[int, Any], dict[int, Any], Any]:
        """Возвращает кэш детекторов ArUco, обновляя его при появлении или подмене cv2.aruco."""
        if not _v.CV2_AVAILABLE or _v.cv2 is None or not hasattr(_v.cv2, "aruco"):
            return [], {}, {}, None
        if id(_v.cv2.aruco) != self._aruco_module_id:
            self._init_aruco_detectors()
        return (
            self._aruco_dict_entries,
            self._aruco_dicts,
            self._aruco_detectors,
            self._aruco_params,
        )

    def _ensure_session(self, allow_in_mock: bool = False) -> Any:
        """Лениво загружает и кэширует ONNX InferenceSession с CPUExecutionProvider."""
        if self._session is not None:
            self._model_loaded = True
            return self._session
        if self.mock_mode and not allow_in_mock:
            return None
        if self._session_load_failed and self._last_failed_path == self.model_path:
            return None
        if not _v.ORT_AVAILABLE or _v.ort is None:
            self._session_load_failed = True
            self._last_failed_path = self.model_path
            _v.logger.warning(
                "onnxruntime недоступен. Для сегментации используется HSV-пайплайн."
            )
            return None
        if not self.model_path or not self.model_path.is_file():
            self._session_load_failed = True
            self._last_failed_path = self.model_path
            _v.logger.warning(
                f"Файл ONNX-модели не найден: {self.model_path}. Используется HSV-пайплайн."
            )
            return None
        try:
            self._session = _v.ort.InferenceSession(
                str(self.model_path),
                providers=["CPUExecutionProvider"],
            )
            self._model_loaded = True
            self._session_load_failed = False
            self._last_failed_path = None
            _v.logger.info(f"ONNX модель успешно загружена: {self.model_path.name}")
            return self._session
        except Exception as exc:  # noqa: BLE001
            self._session = None
            self._model_loaded = False
            self._session_load_failed = True
            self._last_failed_path = self.model_path
            _v.logger.warning(
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
            and _v.ORT_AVAILABLE
            and self.model_path
            and self.model_path.is_file()
        ):
            return self.model_path.name
        return "none"

    @staticmethod
    def _extract_best_aruco_corner(
        corners: Any, ids: Any, dict_label: Any
    ) -> tuple[float | None, Any | None]:
        """Выбирает ArUco-маркер с наибольшей площадью, отсеивая мелкие ложные срабатывания."""
        if ids is not None and len(corners) > 0:
            # Выбираем маркер с наибольшей площадью, отсеивая мелкие ложные срабатывания
            best_corners = max(
                (c[0] for c in corners),
                key=lambda pts: float(_v.cv2.contourArea(pts)),
            )
            area = float(_v.cv2.contourArea(best_corners))
            if area >= 64.0:
                _v.logger.debug(
                    f"ArUco маркер ({dict_label}) обнаружен, площадь: {area:.1f} px"
                )
                return round(area, 1), best_corners.copy()
        return None, None

    def _detect_aruco_in_gray(
        self,
        gray_img: Any,
        params: Any = None,
        detectors: dict[Any, Any] | None = None,
    ) -> tuple[float | None, Any | None]:
        """Ищет маркер ArUco по набору поддерживаемых словарей на полутоновом кадре."""
        if detectors is not None:
            for dict_label, detector in detectors.items():
                corners, ids, _ = detector.detectMarkers(gray_img)
                area, best_corners = self._extract_best_aruco_corner(
                    corners, ids, dict_label
                )
                if area is not None:
                    return area, best_corners
            return None, None

        entries, dicts, cached_detectors, cached_params = self._ensure_aruco_detectors()
        active_params = params if params is not None else cached_params
        use_cached_detectors = params is None or params is cached_params

        for dict_name, dict_id in entries:
            detector = cached_detectors.get(dict_id) if use_cached_detectors else None
            if detector is not None:
                corners, ids, _ = detector.detectMarkers(gray_img)
            else:
                aruco_dict = dicts[dict_id]
                if hasattr(_v.cv2.aruco, "ArucoDetector"):
                    detector = (
                        _v.cv2.aruco.ArucoDetector(aruco_dict, active_params)
                        if active_params is not None
                        else _v.cv2.aruco.ArucoDetector(aruco_dict)
                    )
                    corners, ids, _ = detector.detectMarkers(gray_img)
                else:
                    corners, ids, _ = _v.cv2.aruco.detectMarkers(
                        gray_img,
                        aruco_dict,
                        parameters=active_params,
                    )

            area, best_corners = self._extract_best_aruco_corner(
                corners, ids, dict_name
            )
            if area is not None:
                return area, best_corners
        return None, None

    def detect_aruco_details(
        self,
        image_bytes: bytes | Any,
        detectors: dict[Any, Any] | None = None,
    ) -> tuple[float | None, Any | None]:
        """Обнаруживает ArUco-маркер и возвращает (площадь_px, углы_маркера)."""
        if not _v.CV2_AVAILABLE:
            return None, None

        try:
            img = _decode_bgr_input(image_bytes)
            if img is None:
                return None, None

            gray = _v.cv2.cvtColor(img, _v.cv2.COLOR_BGR2GRAY)

            area, corners = self._detect_aruco_in_gray(gray, detectors=detectors)
            if area is not None:
                return area, corners

            # Второй проход для сложных условий (блики/низкий контраст/муар экрана монитора)
            blurred = _v.cv2.GaussianBlur(gray, (3, 3), 0)
            clahe = _v.cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            enhanced = clahe.apply(blurred)
            return self._detect_aruco_in_gray(enhanced, detectors=detectors)
        except Exception as e:  # noqa: BLE001
            _v.logger.warning(f"Ошибка при детекции ArUco маркера: {e}")
            return None, None

    def detect_aruco(self, image_bytes: bytes | Any) -> float | None:
        """Обнаруживает ArUco-маркер и вычисляет его площадь в пикселях.

        Возвращает None, если маркер не найден или если cv2 недоступен.
        """
        area, _ = self.detect_aruco_details(image_bytes)
        return area

    def detect_color_marker_details(
        self, image_bytes: bytes | Any
    ) -> tuple[Any | None, tuple[float, float, float] | None]:
        """Обнаруживает цветовой маркер в кадре и возвращает (углы_маркера, (r, g, b))."""
        if not _v.CV2_AVAILABLE or image_bytes is None:
            return None, None
        try:
            img = _decode_bgr_input(image_bytes)
            if img is None:
                return None, None
            return _v.detect_color_marker_in_bgr(img)
        except Exception as exc:  # noqa: BLE001
            _v.logger.debug(f"Ошибка при детекции цветового маркера: {exc}")
            return None, None

    def detect_color_marker_with_roi(
        self, image_bytes: bytes | Any
    ) -> tuple[Any | None, tuple[float, float, float] | None, bytes | None]:
        """Обнаруживает цветовой маркер и возвращает (углы_маркера, (r, g, b), jpeg_roi_кроп)."""
        if not _v.CV2_AVAILABLE or image_bytes is None:
            return None, None, None
        try:
            img = _decode_bgr_input(image_bytes)
            if img is None:
                return None, None, None
            corners, gains = _v.detect_color_marker_in_bgr(img)
            if corners is None or gains is None:
                return None, None, None
            roi_bytes = _v.crop_color_marker_roi_jpeg(img, corners)
            return corners, gains, roi_bytes
        except Exception as exc:  # noqa: BLE001
            _v.logger.debug(f"Ошибка при детекции цветового маркера с ROI: {exc}")
            return None, None, None

    def calibrate_color_from_marker(
        self, image_bytes: bytes | Any
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
            c_pts = _v.np.asarray(color_marker_corners, dtype=_v.np.float32).reshape(
                -1, 2
            )
            if len(c_pts) >= 4:
                color_poly = _v.np.round(c_pts).astype(_v.np.int32)

        valid_contours_with_area: list[tuple[Any, float]] = []
        for cnt in raw_contours:
            area = float(_v.cv2.contourArea(cnt))
            if area < min_area:
                continue
            if color_poly is not None:
                m = _v.cv2.moments(cnt)
                if m["m00"] != 0:
                    cx = float(m["m10"] / m["m00"])
                    cy = float(m["m01"] / m["m00"])
                else:
                    (cx, cy), _ = _v.cv2.minEnclosingCircle(cnt)
                if (
                    _v.cv2.pointPolygonTest(color_poly, (float(cx), float(cy)), False)
                    >= 0
                ):
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
            H_px_to_mm, _, fallback_mm_per_px = _v.compute_aruco_homography(
                aruco_corners=aruco_corners,
                aruco_size_mm=aruco_size_mm,
                aruco_area_px=aruco_area_px,
            )

        measurements: list[PlantMeasurementMeta] = []
        annotated = img.copy()

        if valid_contours_with_area:
            overlay_mask = bin_mask_full.copy()
            if color_poly is not None:
                _v.cv2.fillPoly(overlay_mask, [color_poly], 0)
            color_mask = _v.np.zeros_like(img)
            color_mask[overlay_mask > 0] = (0, 255, 0)
            annotated = _v.cv2.addWeighted(annotated, 1.0, color_mask, 0.4, 0)

        for plant_idx, (cnt, _) in enumerate(valid_contours_with_area):
            metrics = _v.measure_contour_metrics(
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

        annotated = _v.draw_cv_annotations(
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

        success, enc_img = _v.cv2.imencode(".jpg", annotated)
        annotated_bytes = enc_img.tobytes() if success else b""
        return annotated_bytes, measurements

    @staticmethod
    def _normalize_segmentation_mode(segmentation_mode: str | None) -> str:
        raw = str(segmentation_mode or "auto").strip().lower()
        return raw if raw in ("auto", "onnx", "hsv") else "auto"

    def segment_plants(
        self,
        image_bytes: bytes | Any,
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
        if not _v.CV2_AVAILABLE:
            _v.logger.error(
                "OpenCV недоступен. Сегментация невозможна, возврат сырого кадра."
            )
            return _ensure_jpeg_bytes(image_bytes), []

        mode = self._normalize_segmentation_mode(segmentation_mode)

        # 1. Явный HSV-режим либо auto в mock_mode без доступного ONNX-файла/сессии
        has_loadable_onnx_in_mock = bool(
            _v.ORT_AVAILABLE
            and _v.ort is not None
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
            img = _decode_bgr_input(image_bytes)
            if img is None:
                return _ensure_jpeg_bytes(image_bytes), []

            h, w = img.shape[:2]

            input_meta = session.get_inputs()[0]
            input_name = input_meta.name
            target_h, target_w, layout = _resolve_onnx_input_size(input_meta.shape)

            resized = _v.cv2.resize(
                img, (target_w, target_h), interpolation=_v.cv2.INTER_LINEAR
            )
            rgb = (
                _v.cv2.cvtColor(resized, _v.cv2.COLOR_BGR2RGB).astype(_v.np.float32)
                / 255.0
            )
            if layout == "NHWC":
                tensor = rgb[_v.np.newaxis, ...]
            else:
                tensor = _v.np.transpose(rgb, (2, 0, 1))[_v.np.newaxis, ...]

            outputs = session.run(None, {input_name: tensor})
            bin_mask_full = _postprocess_onnx_mask(outputs[0], orig_w=w, orig_h=h)

            raw_contours, _ = _v.cv2.findContours(
                bin_mask_full, _v.cv2.RETR_EXTERNAL, _v.cv2.CHAIN_APPROX_SIMPLE
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
            return (annotated_bytes or _ensure_jpeg_bytes(image_bytes)), measurements

        except Exception as e:  # noqa: BLE001
            _v.logger.warning(
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
        image_bytes: bytes | Any,
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
        if not _v.CV2_AVAILABLE:
            return _ensure_jpeg_bytes(image_bytes), []

        try:
            img = _decode_bgr_input(image_bytes)
            if img is None:
                return _ensure_jpeg_bytes(image_bytes), []

            h, w = img.shape[:2]
            # Адаптивный порог минимальной площади от разрешения кадра: отсекает мелкий шум теней
            min_area = max(350.0, float(w * h) * 0.0012)

            # Шумоподавление перед HSV, чтобы убрать матричный цветовой шум камеры в тёмных участках
            denoised = _v.cv2.medianBlur(img, 5)
            denoised = _v.cv2.GaussianBlur(denoised, (3, 3), 0)
            hsv = _v.cv2.cvtColor(denoised, _v.cv2.COLOR_BGR2HSV)
            # Повышены пороги S >= 80 и V >= 60 для фильтрации тёмного и слабонасыщенного фона
            lower_green = _v.np.array([30, 80, 60], dtype=_v.np.uint8)
            upper_green = _v.np.array([85, 255, 255], dtype=_v.np.uint8)
            mask = _v.cv2.inRange(hsv, lower_green, upper_green)

            # Морфологическая очистка: удаление мелкого шума (OPEN 3x3) и склейка разрывов/теней внутри куста (CLOSE 9x9)
            kernel_open = _v.cv2.getStructuringElement(_v.cv2.MORPH_ELLIPSE, (3, 3))
            kernel_close = _v.cv2.getStructuringElement(_v.cv2.MORPH_ELLIPSE, (9, 9))
            mask = _v.cv2.morphologyEx(
                mask, _v.cv2.MORPH_OPEN, kernel_open, iterations=1
            )
            mask = _v.cv2.morphologyEx(
                mask, _v.cv2.MORPH_CLOSE, kernel_close, iterations=1
            )

            raw_contours, _ = _v.cv2.findContours(
                mask, _v.cv2.RETR_EXTERNAL, _v.cv2.CHAIN_APPROX_SIMPLE
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
            return (annotated_bytes or _ensure_jpeg_bytes(image_bytes)), measurements

        except Exception as exc:  # noqa: BLE001
            _v.logger.warning(f"Ошибка при HSV-сегментации: {exc}")
            return _ensure_jpeg_bytes(image_bytes), []

    def process_capture(
        self,
        image_bytes: bytes | Any,
        inst_name: str | None = None,
        culture_name: str | None = None,
        camera_name: str | None = None,
        captured_at: str | None = None,
        aruco_size_mm: float | None = None,
        segmentation_mode: str = "auto",
    ) -> tuple[bytes, float | None, list[PlantMeasurementMeta]]:
        """Полная обработка кадра: детекция ArUco, цветового маркера и сегментация растений."""
        # Декодируем входной кадр ровно 1 раз и переиспользуем готовый BGR np.ndarray
        img = _decode_bgr_input(image_bytes)
        cv_input = img if img is not None else image_bytes
        aruco_area, aruco_corners = self.detect_aruco_details(cv_input)
        color_corners, _ = self.detect_color_marker_details(cv_input)
        annotated_bytes, measurements = self.segment_plants(
            cv_input,
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
        if not annotated_bytes:
            annotated_bytes = _ensure_jpeg_bytes(image_bytes)
        return annotated_bytes, aruco_area, measurements
