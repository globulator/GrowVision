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


def test_production_mode_without_model_hsv_fallback(tmp_path: Path):
    """В боевом режиме (mock_mode=False) без модели: применяется fallback на HSV-сегментацию."""
    non_existent_model = tmp_path / "missing.onnx"
    pipeline = VisionPipeline(model_path=non_existent_model, mock_mode=False)

    assert pipeline.get_model_name() == "none"

    # 1. На пустом 1x1 кадре контуров растений нет: measurements=[]
    _, empty_meas = pipeline.segment_plants(MOCK_JPEG_FRAME)
    assert empty_meas == []

    # 2. На синтетическом кадре с растением выполняется HSV-сегментация
    synth_frame = generate_synthetic_frame(0, "cam-01")
    ann_bytes, measurements = pipeline.segment_plants(synth_frame)
    assert len(measurements) >= 1
    assert measurements[0].plant_index == 0
    assert measurements[0].area_px > 500.0
    assert ann_bytes != synth_frame


def test_process_capture_in_production_without_model(tmp_path: Path):
    """Комплексная обработка в боевом режиме без модели использует HSV fallback."""
    pipeline = VisionPipeline(model_path=tmp_path / "none.onnx", mock_mode=False)
    synth_frame = generate_synthetic_frame(0, "cam-01", overlay_aruco=True)
    ann_bytes, aruco_area, measurements = pipeline.process_capture(
        synth_frame,
        inst_name="Тест Установка",
        culture_name="Салат",
        camera_name="cam-01",
    )

    assert ann_bytes != synth_frame
    assert aruco_area is not None
    assert len(measurements) >= 1


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


def test_zero_plants_still_draws_metadata_and_zero_area(mock_vision: VisionPipeline):
    """При 0 найденных растений возвращается аннотированный кадр с метаданными и 'Площадь: 0 px'."""
    import cv2
    import numpy as np

    # Полностью чёрный кадр без зелёных растений и без ArUco
    black_img = np.zeros((480, 640, 3), dtype=np.uint8)
    ok, enc = cv2.imencode(".jpg", black_img)
    assert ok
    raw_bytes = enc.tobytes()

    ann_bytes, aruco_area, measurements = mock_vision.process_capture(
        raw_bytes,
        inst_name="Теплица 1",
        culture_name="Салат",
        camera_name="cam-zero",
        captured_at="2026-11-25T09:00:00Z",
    )
    assert aruco_area is None
    assert measurements == []
    assert ann_bytes != raw_bytes

    ann_img = cv2.imdecode(np.frombuffer(ann_bytes, np.uint8), cv2.IMREAD_COLOR)
    assert ann_img is not None
    # В правом нижнем углу должна быть жёлтая надпись "Площадь: 0 px" (RGB yellow -> BGR B<60, G>150, R>150)
    bottom_right = ann_img[410:480, 400:640]
    yellow_pixels = (
        (bottom_right[:, :, 2] > 150)
        & (bottom_right[:, :, 1] > 150)
        & (bottom_right[:, :, 0] < 80)
    )
    assert np.any(yellow_pixels), (
        "При 0 растений на кадре должна отображаться надпись 'Площадь: 0 px'"
    )
    # В левом верхнем углу должны быть белые пиксели метаданных
    top_left = ann_img[0:100, 0:300]
    assert np.any(top_left > 200), "Метаданные в углу кадра должны отрисовываться"


def test_onnx_branch_zero_plants_draws_metadata(tmp_path: Path):
    """Основная ONNX-ветка segment_plants при пустой маске также отрисовывает метаданные и 'Площадь: 0 px'."""
    from unittest.mock import MagicMock

    import cv2
    import numpy as np

    pipeline = VisionPipeline(model_path=tmp_path / "dummy.onnx", mock_mode=False)
    fake_input = MagicMock()
    fake_input.name = "input"
    fake_input.shape = [1, 3, 64, 64]
    fake_session = MagicMock()
    fake_session.get_inputs.return_value = [fake_input]
    fake_session.run.return_value = [np.zeros((1, 1, 64, 64), dtype=np.float32)]
    pipeline._session = fake_session
    pipeline._model_loaded = True

    black_img = np.zeros((480, 640, 3), dtype=np.uint8)
    ok, enc = cv2.imencode(".jpg", black_img)
    assert ok
    raw_bytes = enc.tobytes()

    ann_bytes, measurements = pipeline.segment_plants(
        raw_bytes,
        inst_name="Бокс ONNX",
        culture_name="Укроп",
        camera_name="cam-onnx",
        captured_at="2026-11-25T09:00:00Z",
    )
    assert measurements == []
    assert ann_bytes != raw_bytes
    ann_img = cv2.imdecode(np.frombuffer(ann_bytes, np.uint8), cv2.IMREAD_COLOR)
    assert ann_img is not None
    bottom_right = ann_img[410:480, 400:640]
    assert np.any(
        (bottom_right[:, :, 2] > 150)
        & (bottom_right[:, :, 1] > 150)
        & (bottom_right[:, :, 0] < 80)
    )


def test_hsv_segmentation_filters_dark_noisy_background(mock_vision: VisionPipeline):
    """Тёмный зашумлённый и слабонасыщенный фон отфильтровывается, а реальный лист сегментируется."""
    import cv2
    import numpy as np

    rng = np.random.default_rng(42)
    h, w = 480, 640
    # Тёмный фон с матричным цветовым шумом (средний V ~ 35, разброс +-28)
    noisy_dark = np.full((h, w, 3), (28, 36, 26), dtype=np.int16)
    noise = rng.integers(-28, 29, size=(h, w, 3), dtype=np.int16)
    noisy_dark = np.clip(noisy_dark + noise, 0, 255).astype(np.uint8)
    # Добавляем слабонасыщенную зеленовато-серую область (S < 80, V ~ 90)
    cv2.rectangle(noisy_dark, (40, 40), (220, 220), (75, 92, 74), -1)

    ok, enc_bg = cv2.imencode(".jpg", noisy_dark)
    assert ok
    _, meas_bg = mock_vision.segment_plants(enc_bg.tobytes())
    assert meas_bg == [], (
        "Шумный тёмный и слабонасыщенный фон не должен давать контуров"
    )

    # На том же шумном фоне добавляем яркий зелёный лист -> должен найтись ровно 1 куст
    with_leaf = noisy_dark.copy()
    cv2.circle(with_leaf, (420, 260), 45, (30, 185, 38), -1)
    ok, enc_leaf = cv2.imencode(".jpg", with_leaf)
    assert ok
    _, meas_leaf = mock_vision.segment_plants(enc_leaf.tobytes())
    assert len(meas_leaf) == 1
    assert meas_leaf[0].area_px > 2000.0
