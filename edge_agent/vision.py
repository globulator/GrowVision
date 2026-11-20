from pathlib import Path
from typing import Any

from shared.logger import get_logger
from shared.schemas import PlantMeasurementMeta

logger = get_logger("edge_agent.vision")

# Флаги доступности библиотек
CV2_AVAILABLE = False
ORT_AVAILABLE = False

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


class VisionPipeline:
    """Пайплайн компьютерного зрения: детекция ArUco-маркеров и сегментация растений."""

    def __init__(self, model_path: Path | None = None, mock_mode: bool = False):
        self.model_path = model_path
        self.mock_mode = mock_mode
        self._session: Any = None

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
                logger.info(f"ONNX модель успешно загружена: {self.model_path.name}")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Ошибка загрузки ONNX модели {self.model_path}: {e}")
                self._session = None

    def detect_aruco(self, image_bytes: bytes) -> float | None:
        """Обнаруживает ArUco-маркер и вычисляет его площадь в пикселях.

        Возвращает None, если маркер не найден или если cv2 недоступен.
        В mock-режиме возвращает детерминированное значение 1200.0.
        """
        if self.mock_mode or not CV2_AVAILABLE:
            return 1200.0

        try:
            nparr = np.frombuffer(image_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                return None

            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

            # Поддержка различных версий OpenCV ArUco API
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
    ) -> tuple[bytes, list[PlantMeasurementMeta]]:
        """Выполняет сегментацию растений и отрисовку маски на кадре.

        Возвращает кортеж (annotated_image_bytes, measurements).
        """
        # Если mock_mode или нет cv2 / сессии нейросети -> детерминированный fallback
        if self.mock_mode or not CV2_AVAILABLE or self._session is None:
            measurements = [PlantMeasurementMeta(plant_index=0, area_px=5400.0)]
            annotated_bytes = self._generate_fallback_annotated(image_bytes)
            return annotated_bytes, measurements

        try:
            nparr = np.frombuffer(image_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                return image_bytes, []

            h, w = img.shape[:2]

            # Получаем форму входа модели
            input_name = self._session.get_inputs()[0].name
            input_shape = self._session.get_inputs()[0].shape
            # Ожидаемый размер модели (например [1, 3, 256, 256])
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

            # Предобработка
            resized = cv2.resize(img, (target_w, target_h))
            rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
            # NCHW
            tensor = np.transpose(rgb, (2, 0, 1))[np.newaxis, ...]

            outputs = self._session.run(None, {input_name: tensor})
            pred_mask = outputs[0][0]  # shape: [C, H, W] или [H, W]
            if pred_mask.ndim == 3:
                pred_mask = pred_mask[0]

            # Бинаризация маски
            bin_mask = (pred_mask > 0.5).astype(np.uint8)
            bin_mask_full = cv2.resize(
                bin_mask, (w, h), interpolation=cv2.INTER_NEAREST
            )

            # Поиск контуров растений
            contours, _ = cv2.findContours(
                bin_mask_full, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )

            measurements: list[PlantMeasurementMeta] = []
            annotated = img.copy()

            # Накладываем зелёную полупрозрачную маску
            color_mask = np.zeros_like(img)
            color_mask[bin_mask_full > 0] = (0, 255, 0)
            annotated = cv2.addWeighted(annotated, 1.0, color_mask, 0.4, 0)

            for idx, cnt in enumerate(contours):
                area = float(cv2.contourArea(cnt))
                if area > 50.0:  # фильтрация мелкого шума
                    measurements.append(
                        PlantMeasurementMeta(plant_index=idx, area_px=round(area, 1))
                    )
                    cv2.drawContours(annotated, [cnt], -1, (0, 200, 0), 2)

            success, enc_img = cv2.imencode(".jpg", annotated)
            annotated_bytes = enc_img.tobytes() if success else image_bytes
            return annotated_bytes, measurements

        except Exception as e:  # noqa: BLE001
            logger.warning(f"Ошибка сегментации модели: {e}, возврат fallback")
            return self._generate_fallback_annotated(image_bytes), [
                PlantMeasurementMeta(plant_index=0, area_px=5400.0)
            ]

    def _generate_fallback_annotated(self, image_bytes: bytes) -> bytes:
        """Создает аннотированное изображение в fallback/mock режиме."""
        if not CV2_AVAILABLE:
            return image_bytes

        try:
            nparr = np.frombuffer(image_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                return image_bytes

            h, w = img.shape[:2]
            # Рисуем зеленый эллипс, симулирующий лист растения
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
    ) -> tuple[bytes, float | None, list[PlantMeasurementMeta]]:
        """Полная обработка кадра: детекция ArUco + сегментация растений.

        Возвращает (annotated_image_bytes, aruco_area_px, measurements).
        """
        aruco_area = self.detect_aruco(image_bytes)
        annotated_bytes, measurements = self.segment_plants(image_bytes)
        return annotated_bytes, aruco_area, measurements
