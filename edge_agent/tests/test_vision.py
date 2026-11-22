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
