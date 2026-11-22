from pathlib import Path

from edge_agent.hardware import MOCK_JPEG_FRAME
from edge_agent.synthetic import generate_synthetic_frame
from edge_agent.vision import VisionPipeline
from shared.schemas import PlantMeasurementMeta


def test_mock_aruco_detection(mock_vision: VisionPipeline):
    """Проверка детекции ArUco-маркера в mock-режиме: на синтетическом кадре маркер детектируется, на 1x1 None."""
    synth_frame = generate_synthetic_frame(0, "cam-01")
    area = mock_vision.detect_aruco(synth_frame)
    assert area is not None
    assert area > 1000.0

    # 1x1 пустой кадр
    assert mock_vision.detect_aruco(MOCK_JPEG_FRAME) is None


def test_mock_plant_segmentation(mock_vision: VisionPipeline):
    """Проверка сегментации растений в mock-режиме: настоящая HSV-сегментация на синтетическом кадре."""
    synth_frame = generate_synthetic_frame(0, "cam-01")
    ann_bytes, measurements = mock_vision.segment_plants(synth_frame)
    assert isinstance(ann_bytes, bytes)
    assert len(measurements) >= 1
    assert isinstance(measurements[0], PlantMeasurementMeta)
    assert measurements[0].plant_index == 0
    assert measurements[0].area_px > 1000.0

    # На 1x1 пустом кадре нет контуров
    _, meas_empty = mock_vision.segment_plants(MOCK_JPEG_FRAME)
    assert meas_empty == []


def test_production_mode_without_model_empty_measurements(tmp_path: Path):
    """В боевом режиме (mock_mode=False) без модели: measurements=[], аннотированный кадр равен сырому, model_name='none'."""
    non_existent_model = tmp_path / "missing.onnx"
    pipeline = VisionPipeline(model_path=non_existent_model, mock_mode=False)

    assert pipeline.get_model_name() == "none"
    assert pipeline.detect_aruco(MOCK_JPEG_FRAME) is None

    ann_bytes, measurements = pipeline.segment_plants(MOCK_JPEG_FRAME)
    assert measurements == []
    # Аннотированное изображение строго равно исходному кадру
    assert ann_bytes == MOCK_JPEG_FRAME


def test_process_capture_empty_in_production_without_model(tmp_path: Path):
    """Комплексная обработка в боевом режиме без модели."""
    pipeline = VisionPipeline(model_path=tmp_path / "none.onnx", mock_mode=False)
    ann_bytes, aruco_area, measurements = pipeline.process_capture(MOCK_JPEG_FRAME)

    assert ann_bytes == MOCK_JPEG_FRAME
    assert aruco_area is None
    assert measurements == []


def test_mock_overlay_aruco_flag(mock_vision: VisionPipeline):
    """Проверка флага overlay_aruco: при False синтетический кадр не содержит ArUco."""
    frame_with_aruco = generate_synthetic_frame(0, "cam-01", overlay_aruco=True)
    assert mock_vision.detect_aruco(frame_with_aruco) is not None

    frame_without_aruco = generate_synthetic_frame(0, "cam-01", overlay_aruco=False)
    assert mock_vision.detect_aruco(frame_without_aruco) is None


def test_cv_annotations_ruler_and_red_dot(mock_vision: VisionPipeline):
    """Проверка нанесения фиолетовой линейки, красной точки и метаданных."""
    import cv2
    import numpy as np

    synth_frame = generate_synthetic_frame(0, "cam-01", overlay_aruco=True)
    ann_bytes, aruco_area, measurements = mock_vision.process_capture(
        synth_frame,
        inst_name="Тест Установка",
        culture_name="Базилик",
        camera_name="cam-01",
        captured_at="2026-11-22T12:00:00Z",
        aruco_size_mm=50.0,
    )
    assert len(measurements) >= 1
    assert aruco_area is not None

    nparr = np.frombuffer(ann_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    assert img is not None

    # Проверяем наличие фиолетового цвета (BGR: 240, 32, 160) с допуском на JPEG-сжатие и антиалиасинг
    b_diff = np.abs(img[:, :, 0].astype(int) - 240)
    g_diff = np.abs(img[:, :, 1].astype(int) - 32)
    r_diff = np.abs(img[:, :, 2].astype(int) - 160)
    purple_mask = (b_diff < 35) & (g_diff < 35) & (r_diff < 35)
    assert np.any(purple_mask), (
        "Фиолетовая линейка должна присутствовать на аннотированном кадре"
    )

    # Проверяем наличие красной точки в центре (BGR: 0, 0, 255) с допуском
    red_mask = (img[:, :, 2] > 200) & (img[:, :, 0] < 50) & (img[:, :, 1] < 50)
    assert np.any(red_mask), (
        "Красная точка должна присутствовать на аннотированном кадре"
    )
