from pathlib import Path
from typing import Any

from shared.logger import get_logger
from shared.schemas import PlantMeasurementMeta

logger = get_logger("edge_agent.vision")

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


def draw_cv_annotations(
    img: Any,
    contours_with_area: list[tuple[Any, float]],
    aruco_area_px: float | None = None,
    aruco_size_mm: float | None = None,
    inst_name: str | None = None,
    culture_name: str | None = None,
    camera_name: str | None = None,
    captured_at: str | None = None,
) -> Any:
    """Накладывает улучшенную CV-разметку:

    - Линейка слева и снизу в см (при калибровке ArUco) или px, яркий фиолетовый (BGR: 240, 32, 160);
    - Красная точка в центре каждого найденного растения;
    - Метаданные в углах кадра с поддержкой кириллицы через Pillow TTF-шрифты;
    - Тонкая тёмная обводка ко всем элементам.
    """
    if not CV2_AVAILABLE or img is None:
        return img

    h, w = img.shape[:2]
    annotated = img.copy()

    scale_ref = min(w, h)
    font_scale = max(0.5, scale_ref / 1000.0)
    thickness = max(2, round(scale_ref / 400.0))
    outline_thickness = thickness + 2

    # BGR цвета для OpenCV
    COLOR_PURPLE = (240, 32, 160)  # Яркий фиолетовый (BGR)
    COLOR_RED = (0, 0, 255)  # Красный
    COLOR_GREEN = (0, 220, 0)  # Зеленый контур
    COLOR_DARK = (15, 15, 15)  # Темная обводка

    # Расчет масштабного коэффициента калибровки ArUco
    px_per_cm: float | None = None
    if aruco_area_px and aruco_area_px > 0 and aruco_size_mm and aruco_size_mm > 0:
        marker_side_px = aruco_area_px**0.5
        marker_side_cm = aruco_size_mm / 10.0
        if marker_side_cm > 0:
            px_per_cm = marker_side_px / marker_side_cm

    total_area_px = sum(area for _, area in contours_with_area)
    if px_per_cm and px_per_cm > 0:
        total_area_cm2 = total_area_px / (px_per_cm**2)
        area_str = f"{total_area_cm2:.1f} см²"
    else:
        area_str = f"{round(total_area_px)} px"

    # Списки для отрисовки меток линеек: (lx, ly1, ly2, h_val_str, bx1, bx2, ly_bot, w_val_str, tick_len)
    rulers_info: list[tuple[int, int, int, str, int, int, int, str, int]] = []

    # Геометрическая обработка каждого обнаруженного растения
    for cnt, _ in contours_with_area:
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

        # 3. Линейка слева и снизу
        bx, by, bw, bh = cv2.boundingRect(cnt)

        # Линейка слева (вертикальная)
        tick_len = max(5, round(scale_ref / 120.0))
        lx = max(tick_len + 5, bx - 15)
        ly1, ly2 = by, by + bh

        cv2.line(
            annotated, (lx, ly1), (lx, ly2), COLOR_DARK, outline_thickness, cv2.LINE_AA
        )
        cv2.line(annotated, (lx, ly1), (lx, ly2), COLOR_PURPLE, thickness, cv2.LINE_AA)
        for y_tick in (ly1, (ly1 + ly2) // 2, ly2):
            cv2.line(
                annotated,
                (lx - tick_len, y_tick),
                (lx + tick_len, y_tick),
                COLOR_DARK,
                outline_thickness,
                cv2.LINE_AA,
            )
            cv2.line(
                annotated,
                (lx - tick_len, y_tick),
                (lx + tick_len, y_tick),
                COLOR_PURPLE,
                thickness,
                cv2.LINE_AA,
            )

        h_val_str = f"{bh / px_per_cm:.1f} см" if px_per_cm else f"{bh} px"

        # Линейка снизу (горизонтальная)
        bx1, bx2 = bx, bx + bw
        ly_bot = min(h - tick_len - 5, by + bh + 15)

        cv2.line(
            annotated,
            (bx1, ly_bot),
            (bx2, ly_bot),
            COLOR_DARK,
            outline_thickness,
            cv2.LINE_AA,
        )
        cv2.line(
            annotated,
            (bx1, ly_bot),
            (bx2, ly_bot),
            COLOR_PURPLE,
            thickness,
            cv2.LINE_AA,
        )
        for x_tick in (bx1, (bx1 + bx2) // 2, bx2):
            cv2.line(
                annotated,
                (x_tick, ly_bot - tick_len),
                (x_tick, ly_bot + tick_len),
                COLOR_DARK,
                outline_thickness,
                cv2.LINE_AA,
            )
            cv2.line(
                annotated,
                (x_tick, ly_bot - tick_len),
                (x_tick, ly_bot + tick_len),
                COLOR_PURPLE,
                thickness,
                cv2.LINE_AA,
            )

        w_val_str = f"{bw / px_per_cm:.1f} см" if px_per_cm else f"{bw} px"

        rulers_info.append(
            (lx, ly1, ly2, h_val_str, bx1, bx2, ly_bot, w_val_str, tick_len)
        )

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
            tick_len,
        ) in rulers_info:
            # Вертикальная линейка (слева)
            bbox_h = draw.textbbox(
                (0, 0), h_val_str, font=ruler_font, stroke_width=ruler_stroke_w
            )
            tw_h = bbox_h[2] - bbox_h[0]
            th_h = bbox_h[3] - bbox_h[1]
            pos_x_h = max(5, lx - tw_h - tick_len - 4)
            pos_y_h = max(2, (ly1 + ly2) // 2 - th_h // 2)
            draw.text(
                (pos_x_h, pos_y_h),
                h_val_str,
                font=ruler_font,
                fill=RGB_PURPLE,
                stroke_width=ruler_stroke_w,
                stroke_fill=RGB_DARK,
            )

            # Горизонтальная линейка (снизу)
            bbox_w = draw.textbbox(
                (0, 0), w_val_str, font=ruler_font, stroke_width=ruler_stroke_w
            )
            tw_w = bbox_w[2] - bbox_w[0]
            th_w = bbox_w[3] - bbox_w[1]
            pos_x_w = (bx1 + bx2) // 2 - tw_w // 2
            pos_y_w = min(h - th_w - 5, ly_bot + tick_len + 4)
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
            _,
        ) in rulers_info:
            draw_text_with_outline(
                h_val_str, (max(5, lx - 55), (ly1 + ly2) // 2), color=COLOR_PURPLE
            )
            draw_text_with_outline(
                w_val_str,
                ((bx1 + bx2) // 2 - 25, min(h - 6, ly_bot + 18)),
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

        text_area = f"Площадь: {area_str}"
        (tw, _), _ = cv2.getTextSize(text_area, font, font_scale, thickness)
        draw_text_with_outline(
            text_area, (w - margin - tw, h - margin), color=COLOR_YELLOW
        )

    return annotated


class VisionPipeline:
    """Пайплайн компьютерного зрения: детекция ArUco-маркеров и сегментация растений."""

    def __init__(self, model_path: Path | None = None, mock_mode: bool = False):
        self.model_path = model_path
        self.mock_mode = mock_mode
        self._session: Any = None
        self._model_loaded = False

        if (
            not self.mock_mode
            and self.model_path
            and self.model_path.is_file()
            and ORT_AVAILABLE
        ):
            try:
                self._session = ort.InferenceSession(
                    str(self.model_path),
                    providers=["CPUExecutionProvider"],
                )
                self._model_loaded = True
                logger.info(f"ONNX модель успешно загружена: {self.model_path.name}")
            except Exception as e:  # noqa: BLE001
                logger.error(f"Ошибка загрузки ONNX модели {self.model_path}: {e}")
                self._session = None
                self._model_loaded = False
        elif not self.mock_mode and (
            not self.model_path or not self.model_path.is_file()
        ):
            logger.error(
                f"Файл ONNX модели не найден: {self.model_path}. Модель не будет использоваться."
            )

    def get_model_name(self) -> str:
        """Возвращает имя загруженной модели или 'none'."""
        if self.mock_mode:
            return self.model_path.name if self.model_path else "mock_model.onnx"
        if self._model_loaded and self.model_path:
            return self.model_path.name
        return "none"

    def detect_aruco(self, image_bytes: bytes) -> float | None:
        """Обнаруживает ArUco-маркер и вычисляет его площадь в пикселях.

        Возвращает None, если маркер не найден или если cv2 недоступен.
        """
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
                first_marker = corners[0][0]
                area = float(cv2.contourArea(first_marker))
                logger.debug(f"ArUco маркер обнаружен, площадь: {area:.1f} px")
                return round(area, 1)

            return None
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Ошибка при детекции ArUco маркера: {e}")
            return None

    def segment_plants(
        self,
        image_bytes: bytes,
        aruco_area_px: float | None = None,
        aruco_size_mm: float | None = None,
        inst_name: str | None = None,
        culture_name: str | None = None,
        camera_name: str | None = None,
        captured_at: str | None = None,
    ) -> tuple[bytes, list[PlantMeasurementMeta]]:
        """Выполняет сегментацию растений и отрисовку маски на кадре.

        Возвращает кортеж (annotated_image_bytes, measurements).
        """
        if not CV2_AVAILABLE:
            logger.error(
                "OpenCV недоступен. Сегментация невозможна, возврат сырого кадра."
            )
            return image_bytes, []

        # 1. Mock-режим: настоящая сегментация по цветовому порогу HSV
        if self.mock_mode:
            return self._segment_plants_hsv(
                image_bytes,
                aruco_area_px=aruco_area_px,
                aruco_size_mm=aruco_size_mm,
                inst_name=inst_name,
                culture_name=culture_name,
                camera_name=camera_name,
                captured_at=captured_at,
            )

        # 2. Боевой режим без загруженной ONNX модели / сессии:
        # При отсутствии .onnx-модели в боевом режиме (mock_mode=False) использовать _segment_plants_hsv как фолбэк
        if not self._model_loaded or self._session is None:
            logger.warning(
                "ONNX-модель не загружена в боевом режиме. Применяется fallback-сегментация по HSV."
            )
            return self._segment_plants_hsv(
                image_bytes,
                aruco_area_px=aruco_area_px,
                aruco_size_mm=aruco_size_mm,
                inst_name=inst_name,
                culture_name=culture_name,
                camera_name=camera_name,
                captured_at=captured_at,
            )

        try:
            nparr = np.frombuffer(image_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                return image_bytes, []

            h, w = img.shape[:2]

            input_name = self._session.get_inputs()[0].name
            input_shape = self._session.get_inputs()[0].shape
            target_h = (
                input_shape[2]
                if len(input_shape) >= 4 and isinstance(input_shape[2], int)
                else 256
            )
            target_w = (
                input_shape[3]
                if len(input_shape) >= 4 and isinstance(input_shape[3], int)
                else 256
            )

            resized = cv2.resize(img, (target_w, target_h))
            rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
            tensor = np.transpose(rgb, (2, 0, 1))[np.newaxis, ...]

            outputs = self._session.run(None, {input_name: tensor})
            pred_mask = outputs[0][0]
            if pred_mask.ndim == 3:
                pred_mask = pred_mask[0]

            bin_mask = (pred_mask > 0.5).astype(np.uint8)
            bin_mask_full = cv2.resize(
                bin_mask, (w, h), interpolation=cv2.INTER_NEAREST
            )

            raw_contours, _ = cv2.findContours(
                bin_mask_full, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )

            # Адаптивный порог минимальной площади от разрешения кадра: max(150.0, (w * h) * 0.0005)
            min_area = max(150.0, float(w * h) * 0.0005)

            # Сортировка контуров по площади (убывание)
            valid_contours_with_area = []
            for cnt in raw_contours:
                area = float(cv2.contourArea(cnt))
                if area >= min_area:  # Фильтрация шума
                    valid_contours_with_area.append((cnt, area))

            valid_contours_with_area.sort(key=lambda x: x[1], reverse=True)

            measurements: list[PlantMeasurementMeta] = []
            annotated = img.copy()

            color_mask = np.zeros_like(img)
            color_mask[bin_mask_full > 0] = (0, 255, 0)
            annotated = cv2.addWeighted(annotated, 1.0, color_mask, 0.4, 0)

            # Нумерация plant_index подряд с 0
            for plant_idx, (cnt, area) in enumerate(valid_contours_with_area):
                measurements.append(
                    PlantMeasurementMeta(
                        plant_index=plant_idx,
                        area_px=round(area, 1),
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
            )

            success, enc_img = cv2.imencode(".jpg", annotated)
            annotated_bytes = enc_img.tobytes() if success else image_bytes
            return annotated_bytes, measurements

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
            # Адаптивный порог минимальной площади от разрешения кадра: max(150.0, (w * h) * 0.0005)
            min_area = max(150.0, float(w * h) * 0.0005)

            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
            # Повышен порог насыщенности S (65 вместо 40), чтобы слабо-зелёный серый фон не сливался в контур
            lower_green = np.array([30, 65, 40], dtype=np.uint8)
            upper_green = np.array([85, 255, 255], dtype=np.uint8)
            mask = cv2.inRange(hsv, lower_green, upper_green)

            # Морфологическая очистка: удаление мелкого шума (OPEN) и закрытие разрывов в листьях (CLOSE)
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)

            raw_contours, _ = cv2.findContours(
                mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )

            valid_contours_with_area = []
            for cnt in raw_contours:
                area = float(cv2.contourArea(cnt))
                if area >= min_area:  # Фильтрация шума адаптивным порогом
                    valid_contours_with_area.append((cnt, area))

            valid_contours_with_area.sort(key=lambda x: x[1], reverse=True)

            if not valid_contours_with_area:
                return image_bytes, []

            measurements: list[PlantMeasurementMeta] = []
            annotated = img.copy()

            color_mask = np.zeros_like(img)
            color_mask[mask > 0] = (0, 255, 0)
            annotated = cv2.addWeighted(annotated, 1.0, color_mask, 0.4, 0)

            for plant_idx, (cnt, area) in enumerate(valid_contours_with_area):
                measurements.append(
                    PlantMeasurementMeta(
                        plant_index=plant_idx,
                        area_px=round(area, 1),
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
            )

            success, enc_img = cv2.imencode(".jpg", annotated)
            annotated_bytes = enc_img.tobytes() if success else image_bytes
            return annotated_bytes, measurements

        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Ошибка при HSV-сегментации: {exc}")
            return image_bytes, []

    def _generate_fallback_annotated(self, image_bytes: bytes) -> bytes:
        """Создает аннотированное изображение исключительно в mock-режиме."""
        if not CV2_AVAILABLE:
            return image_bytes

        try:
            nparr = np.frombuffer(image_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                return image_bytes

            h, w = img.shape[:2]
            center = (w // 2, h // 2)
            axes = (max(2, w // 4), max(2, h // 4))
            cv2.ellipse(img, center, axes, 30, 0, 360, (0, 255, 0), 2)
            cv2.putText(
                img,
                "Plant [Mock]",
                (max(10, center[0] - 50), max(20, center[1])),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 255, 0),
                1,
            )

            success, enc = cv2.imencode(".jpg", img)
            return enc.tobytes() if success else image_bytes
        except Exception:  # noqa: BLE001
            return image_bytes

    def process_capture(
        self,
        image_bytes: bytes,
        inst_name: str | None = None,
        culture_name: str | None = None,
        camera_name: str | None = None,
        captured_at: str | None = None,
        aruco_size_mm: float | None = None,
    ) -> tuple[bytes, float | None, list[PlantMeasurementMeta]]:
        """Полная обработка кадра: детекция ArUco + сегментация растений."""
        aruco_area = self.detect_aruco(image_bytes)
        annotated_bytes, measurements = self.segment_plants(
            image_bytes,
            aruco_area_px=aruco_area,
            aruco_size_mm=aruco_size_mm,
            inst_name=inst_name,
            culture_name=culture_name,
            camera_name=camera_name,
            captured_at=captured_at,
        )
        return annotated_bytes, aruco_area, measurements
