from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from edge_agent.hardware import HardwareManager, apply_power_line_frequency_50hz
from edge_agent.vision import (
    VisionPipeline,
    _get_pil_font,
    draw_cv_annotations,
)


def test_pil_font_loading():
    """Проверка загрузки системного TTF-шрифта для поддержки кириллицы."""
    font = _get_pil_font(20)
    assert font is not None


def test_cv_annotations_cyrillic_rendering():
    """Проверка корректной отрисовки кириллических строк в draw_cv_annotations без знаков вопроса."""
    # Создаем черный кадр 800x600
    img = np.zeros((600, 800, 3), dtype=np.uint8)
    cnt = np.array(
        [[[200, 200]], [[200, 300]], [[300, 300]], [[300, 200]]], dtype=np.int32
    )
    contours_with_area = [(cnt, 10000.0)]

    inst_name = "Бокс №1"
    culture_name = "Базилик овощной"
    camera_name = "Верхняя камера"
    captured_at = "2026-11-24 22:30:00"

    annotated = draw_cv_annotations(
        img,
        contours_with_area,
        aruco_area_px=2500.0,
        aruco_size_mm=50.0,
        inst_name=inst_name,
        culture_name=culture_name,
        camera_name=camera_name,
        captured_at=captured_at,
    )

    assert annotated is not None
    assert annotated.shape == (600, 800, 3)

    # Проверяем, что в левом верхнем углу (где Установка и Культура) появились белые/светлые пиксели текста
    header_crop = annotated[10:100, 10:350]
    white_text_pixels = np.any(header_crop > 200)
    assert white_text_pixels, "Кириллический текст должен быть нанесен на изображение"


def test_hsv_segmentation_adaptive_area_threshold():
    """Проверка адаптивного порога минимальной площади: отсечение мелкого шума на 1280x960."""
    pipeline = VisionPipeline(mock_mode=True)

    # Разрешение 1280x960 -> min_area = max(150.0, 1280*960*0.0005) = 614.4 px
    h, w = 960, 1280
    test_img = np.full((h, w, 3), 48, dtype=np.uint8)

    # 1. Мелкое зеленое пятно: круг радиуса 8 px (площадь ~200 px < 614.4 px)
    cv2.circle(test_img, (200, 200), 8, (36, 185, 42), -1)

    # 2. Крупное зеленое пятно: круг радиуса 30 px (площадь ~2800 px > 614.4 px)
    cv2.circle(test_img, (600, 500), 30, (36, 185, 42), -1)

    _, enc_bytes = cv2.imencode(".jpg", test_img)
    _, measurements = pipeline.segment_plants(enc_bytes.tobytes())

    # Должно быть найдено ровно 1 крупное растение, мелкое пятно отсеяно адаптивным порогом
    assert len(measurements) == 1
    assert measurements[0].area_px > 1000.0


def test_hsv_segmentation_cuts_slightly_green_gray_background():
    """Проверка, что слабо-зеленый серый фон (S < 65) не объединяется в гигантский контур."""
    pipeline = VisionPipeline(mock_mode=True)

    # Создаем кадр со слабо-зеленым серым фоном (BGR: 45, 52, 46 -> S ~ 34)
    h, w = 400, 400
    test_img = np.full((h, w, 3), (45, 52, 46), dtype=np.uint8)

    _, enc_bytes = cv2.imencode(".jpg", test_img)
    _, measurements = pipeline.segment_plants(enc_bytes.tobytes())

    # Серый фон должен быть полностью отсечен порогом S >= 65
    assert len(measurements) == 0


def test_apply_power_line_frequency_50hz_success():
    """Проверка успешного вызова v4l2-ctl для установки частоты сети 50 Гц."""
    mock_run_result = MagicMock(returncode=0, stdout="", stderr="")
    with patch("subprocess.run", return_value=mock_run_result) as mock_run:
        res = apply_power_line_frequency_50hz("/dev/video0")
        assert res is True
        mock_run.assert_called_once_with(
            ["v4l2-ctl", "-d", "/dev/video0", "-c", "power_line_frequency=1"],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )


def test_apply_power_line_frequency_50hz_failure_graceful():
    """Проверка безопасного игнорирования ошибок, если v4l2-ctl завершился с ошибкой или исключением."""
    mock_run_result = MagicMock(returncode=1, stdout="", stderr="Unknown control")
    with patch("subprocess.run", return_value=mock_run_result):
        res = apply_power_line_frequency_50hz("/dev/video0")
        assert res is False

    with patch("subprocess.run", side_effect=FileNotFoundError("No v4l2-ctl")):
        res = apply_power_line_frequency_50hz("/dev/video0")
        assert res is False


def test_hardware_calibrate_calls_50hz_in_rpi_mode(tmp_path: Path):
    """При автокалибровке камеры в режиме rpi вызывается apply_power_line_frequency_50hz."""
    v4l_dir = tmp_path / "by-id"
    v4l_dir.mkdir(parents=True)
    cam_file = v4l_dir / "cam-01"
    cam_file.touch()

    with (
        patch.dict("sys.modules", {"gpiozero": MagicMock()}),
        patch("edge_agent.hardware.apply_power_line_frequency_50hz") as mock_apply,
        patch("edge_agent.hardware.probe_camera_capabilities") as mock_probe,
    ):
        mock_probe.return_value = MagicMock(summary_text=lambda: "1920x1080 (MJPG)")
        hw = HardwareManager(mode="rpi", v4l_by_id_dir=v4l_dir)
        hw.calibrate_camera("cam-01")

        mock_apply.assert_called_once()


def test_hardware_capture_frame_calls_50hz_in_rpi_mode(tmp_path: Path):
    """При захвате кадра в режиме rpi вызывается apply_power_line_frequency_50hz."""
    v4l_dir = tmp_path / "by-id"
    v4l_dir.mkdir(parents=True)
    cam_file = v4l_dir / "cam-01"
    cam_file.touch()

    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = True
    test_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    mock_cap.read.return_value = (True, test_frame)

    with (
        patch.dict("sys.modules", {"gpiozero": MagicMock()}),
        patch("cv2.VideoCapture", return_value=mock_cap),
        patch("edge_agent.hardware.apply_power_line_frequency_50hz") as mock_apply,
        patch("edge_agent.hardware.probe_camera_capabilities") as mock_probe,
    ):
        mock_probe.return_value = MagicMock(
            summary_text=lambda: "640x480 (MJPG)",
            max_width=640,
            max_height=480,
            pixel_format="MJPG",
        )
        hw = HardwareManager(mode="rpi", v4l_by_id_dir=v4l_dir)
        raw_bytes = hw.capture_frame("cam-01")

        assert isinstance(raw_bytes, bytes)
        assert len(raw_bytes) > 0
        mock_apply.assert_called_once()
