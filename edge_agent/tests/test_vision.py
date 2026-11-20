from edge_agent.hardware import MOCK_JPEG_FRAME
from edge_agent.vision import VisionPipeline
from shared.schemas import PlantMeasurementMeta


def test_mock_aruco_detection(mock_vision: VisionPipeline):
    """Проверка детекции ArUco-маркера в mock-режиме."""
    area = mock_vision.detect_aruco(MOCK_JPEG_FRAME)
    assert area is not None
    assert area == 1200.0


def test_mock_plant_segmentation(mock_vision: VisionPipeline):
    """Проверка сегментации растений в mock-режиме."""
    ann_bytes, measurements = mock_vision.segment_plants(MOCK_JPEG_FRAME)
    assert isinstance(ann_bytes, bytes)
    assert len(ann_bytes) > 0
    assert len(measurements) == 1
    assert isinstance(measurements[0], PlantMeasurementMeta)
    assert measurements[0].plant_index == 0
    assert measurements[0].area_px == 5400.0


def test_mock_process_capture(mock_vision: VisionPipeline):
    """Проверка комплексной обработки кадра."""
    ann_bytes, aruco_area, measurements = mock_vision.process_capture(MOCK_JPEG_FRAME)
    assert isinstance(ann_bytes, bytes)
    assert aruco_area == 1200.0
    assert len(measurements) >= 1
