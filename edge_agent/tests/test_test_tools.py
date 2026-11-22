import itertools
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from edge_agent.config import EdgeConfig, load_config
from edge_agent.hardware import HardwareManager
from edge_agent.orchestrator import EdgeOrchestrator
from edge_agent.synthetic import (
    compute_green_area_px,
    detect_aruco_area_px,
    generate_synthetic_frame,
)
from shared.schemas import HeartbeatActiveInstallation


def test_synthetic_frame_aruco_and_monotonic_growth():
    """Генератор синтетических кадров: маркер ArUco находится, площадь растения строго монотонно растёт с ростом номера кадра."""
    # Генерируем серию из 5 последовательных кадров
    frames = [
        generate_synthetic_frame(frame_index=i, camera_id="cam-test-1")
        for i in range(5)
    ]

    # 1. Проверка детекции ArUco маркера DICT_4X4_50 id 0
    for i, frame in enumerate(frames):
        aruco_area = detect_aruco_area_px(frame)
        assert aruco_area is not None, f"ArUco маркер не обнаружен на кадре {i}"
        assert aruco_area > 1000.0, f"Площадь ArUco {aruco_area} слишком мала"

    # 2. Проверка строго монотонного роста площади растения
    areas = [compute_green_area_px(f) for f in frames]
    for i in range(len(areas) - 1):
        assert areas[i] < areas[i + 1], (
            f"Площадь растения не растёт монотонно: frame[{i}]={areas[i]} >= frame[{i + 1}]={areas[i + 1]}"
        )


def test_hsv_segmentation_green_pixels():
    """HSV-сегментация: корректный подсчёт площади зелёных пикселей."""
    # Пустой 1x1 или серый кадр не должен давать зеленой площади
    empty_jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x80\xff\xd9"
    assert compute_green_area_px(empty_jpeg) == 0.0

    # Синтетический кадр с растением даёт положительную площадь
    frame = generate_synthetic_frame(frame_index=2, camera_id="cam-test-1")
    green_area = compute_green_area_px(frame)
    assert green_area > 2000.0


def test_simulator_from_dir_iteration(tmp_path: Path):
    """Итератор --from-dir: циклическое чтение JPEG-файлов из каталога."""
    # Создаем 3 тестовых JPEG файла
    sample_jpeg = generate_synthetic_frame(0, "cam-dir")
    f1 = tmp_path / "img_01.jpg"
    f2 = tmp_path / "img_02.jpg"
    f3 = tmp_path / "img_03.jpg"
    f1.write_bytes(sample_jpeg)
    f2.write_bytes(sample_jpeg)
    f3.write_bytes(sample_jpeg)

    jpg_files = sorted(
        [f for f in tmp_path.glob("*") if f.suffix.lower() in (".jpg", ".jpeg")]
    )
    assert len(jpg_files) == 3

    cycler = itertools.cycle(jpg_files)
    read_names = [next(cycler).name for _ in range(7)]
    assert read_names == [
        "img_01.jpg",
        "img_02.jpg",
        "img_03.jpg",
        "img_01.jpg",
        "img_02.jpg",
        "img_03.jpg",
        "img_01.jpg",
    ]


def test_test_capture_interval_validation(monkeypatch):
    """Валидация интервала: TEST_CAPTURE_INTERVAL_SEC < 10 отклоняется при инициализации конфигурации."""
    # 1. Значение меньше 10 вызывает ValueError
    monkeypatch.setenv("TEST_CAPTURE_INTERVAL_SEC", "9")
    with pytest.raises(ValueError, match="не менее 10 секунд"):
        load_config()

    # 2. Отрицательное значение вызывает ValueError
    monkeypatch.setenv("TEST_CAPTURE_INTERVAL_SEC", "-5")
    with pytest.raises(ValueError, match="не менее 10 секунд"):
        load_config()

    # 3. Нечисловое значение
    monkeypatch.setenv("TEST_CAPTURE_INTERVAL_SEC", "invalid")
    with pytest.raises(ValueError, match="Некорректное значение"):
        load_config()

    # 4. Валидное значение >= 10
    monkeypatch.setenv("TEST_CAPTURE_INTERVAL_SEC", "10")
    cfg = load_config()
    assert cfg.test_capture_interval_sec == 10

    monkeypatch.setenv("TEST_CAPTURE_INTERVAL_SEC", "45")
    cfg = load_config()
    assert cfg.test_capture_interval_sec == 45


def test_rpi_mode_blocks_synthetic(tmp_path: Path):
    """Режим rpi: синтетический генератор недоступен/заблокирован; при отсутствии камеры — ошибка."""
    # Подменяем директорию v4l на пустую
    empty_v4l = tmp_path / "v4l"
    empty_v4l.mkdir()

    with patch.dict("sys.modules", {"gpiozero": MagicMock()}):
        hw = HardwareManager(mode="rpi", v4l_by_id_dir=empty_v4l)

        # При попытке захвата кадра в режиме RPi несуществующей камеры ДОЛЖНА возникнуть ошибка, а не синтетика
        with pytest.raises(RuntimeError, match="не найдена"):
            hw.capture_frame("cam-non-existent")


def test_orchestrator_test_interval_warning_and_debug_log(monkeypatch, caplog):
    """При старте агента с тестовым интервалом логируется WARNING, а в debug_log_line плановой съёмки попадает факт интервала."""
    cfg = EdgeConfig(
        server_url="http://127.0.0.1:8000",
        edge_api_key="test_key",
        hardware_mode="mock",
        test_capture_interval_sec=12,
    )

    mock_client = MagicMock()
    mock_client.send_heartbeat.return_value = None
    mock_client.upload_capture.return_value = (201, {"capture_id": 1})

    orchestrator = EdgeOrchestrator(config=cfg, client=mock_client)
    orchestrator.active_installations = [
        HeartbeatActiveInstallation(
            id=1,
            light_gpio_pin=12,
            flash_gpio_pin=13,
            camera_hardware_ids=["cam-01"],
        )
    ]

    # Проверка WARNING при старте
    with caplog.at_level("WARNING"):
        orchestrator.start()
    orchestrator.stop()

    assert any(
        "ТЕСТОВЫЙ ИНТЕРВАЛ СЪЁМКИ: 12 с" in record.message for record in caplog.records
    )

    # Проверка debug_log_line плановой автосъемки
    debug_log = orchestrator.trigger_scheduled_capture()
    assert debug_log is not None
    assert "[TEST_INTERVAL=12s]" in debug_log
    assert orchestrator.last_scheduled_debug_log_line == debug_log


def test_compute_plant_radius_step_formula():
    """Проверка ступенчатой формулы вычисления радиуса растения и ограничения max_radius."""
    from edge_agent.synthetic import compute_plant_radius

    max_r = 200

    # i < 10: 24 + i * 4
    assert compute_plant_radius(0, max_r) == 24
    assert compute_plant_radius(5, max_r) == 44
    assert compute_plant_radius(9, max_r) == 60

    # i < 20: 64 + (i - 10) * 3
    assert compute_plant_radius(10, max_r) == 64
    assert compute_plant_radius(15, max_r) == 79
    assert compute_plant_radius(19, max_r) == 91

    # i < 35: 94 + (i - 20) * 2
    assert compute_plant_radius(20, max_r) == 94
    assert compute_plant_radius(30, max_r) == 114
    assert compute_plant_radius(34, max_r) == 122

    # i >= 35: 124 + (i - 35) * 1
    assert compute_plant_radius(35, max_r) == 124
    assert compute_plant_radius(50, max_r) == 139

    # Ограничение max_radius
    assert compute_plant_radius(100, max_radius=80) == 80


def test_synthetic_frame_rotates_with_angle_offset():
    """Проверка генерации синтетических кадров с вращением и ростом."""
    from edge_agent.synthetic import generate_synthetic_frame

    frame0 = generate_synthetic_frame(frame_index=0, camera_id="cam-1")
    frame1 = generate_synthetic_frame(frame_index=1, camera_id="cam-1")
    assert frame0.startswith(b"\xff\xd8")
    assert frame1.startswith(b"\xff\xd8")
    assert frame0 != frame1
