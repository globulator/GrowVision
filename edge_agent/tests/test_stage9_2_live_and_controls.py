# edge_agent/tests/test_stage9_2_live_and_controls.py
import time
from unittest.mock import MagicMock

import numpy as np

from edge_agent.camera_controls import (
    categorize_control,
    parse_v4l2_controls,
)
from edge_agent.config import EdgeConfig
from edge_agent.live_stream import LiveStreamManager
from edge_agent.orchestrator import EdgeOrchestrator
from shared.schemas import CommandItem


def test_parse_v4l2_controls_and_flags():
    """Тест парсинга вывода v4l2-ctl --list-ctrls-menus с флагами inactive и меню."""
    v4l2_output = """
User Controls

                     brightness 0x00980900 (int)    : min=-64 max=64 step=1 default=0 value=5
                       contrast 0x00980901 (int)    : min=0 max=64 step=1 default=32 value=32
                     saturation 0x00980902 (int)    : min=0 max=128 step=1 default=64 value=64 flags=inactive

Camera Controls

                  auto_exposure 0x009a0901 (menu)   : min=0 max=3 default=0 value=0
                                1: Auto Mode
                                3: Manual Mode
        exposure_time_absolute 0x009a0902 (int)    : min=1 max=10000 step=1 default=156 value=156 flags=inactive
                     focus_auto 0x009a090c (bool)   : default=1 value=1
    """
    ctrls = parse_v4l2_controls(v4l2_output)
    by_name = {c["name"]: c for c in ctrls}

    assert "brightness" in by_name
    assert by_name["brightness"]["type"] == "int"
    assert by_name["brightness"]["min"] == -64
    assert by_name["brightness"]["max"] == 64
    assert by_name["brightness"]["value"] == 5
    assert by_name["brightness"]["is_inactive"] is False
    assert by_name["brightness"]["category"] == "exposure"

    assert "saturation" in by_name
    assert by_name["saturation"]["is_inactive"] is True
    assert by_name["saturation"]["category"] == "color"

    assert "auto_exposure" in by_name
    assert by_name["auto_exposure"]["type"] == "menu"
    assert by_name["auto_exposure"]["category"] == "exposure"
    assert by_name["auto_exposure"]["menu_items"] == [
        {"id": 1, "label": "Auto Mode"},
        {"id": 3, "label": "Manual Mode"},
    ]

    assert "exposure_time_absolute" in by_name
    assert by_name["exposure_time_absolute"]["is_inactive"] is True
    assert by_name["exposure_time_absolute"]["category"] == "exposure"

    assert "focus_auto" in by_name
    assert by_name["focus_auto"]["type"] == "bool"
    assert by_name["focus_auto"]["category"] == "focus"


def test_categorize_controls():
    """Проверка правильной классификации по папкам категорий."""
    assert categorize_control("focus_auto") == "focus"
    assert categorize_control("focus_absolute") == "focus"
    assert categorize_control("power_line_frequency") == "focus"

    assert categorize_control("auto_exposure") == "exposure"
    assert categorize_control("exposure_time_absolute") == "exposure"
    assert categorize_control("exposure_dynamic_framerate") == "exposure"
    assert categorize_control("brightness") == "exposure"
    assert categorize_control("contrast") == "exposure"

    assert categorize_control("saturation") == "color"
    assert categorize_control("white_balance_temperature") == "color"


def test_live_stream_manager_lifecycle():
    """Тест управления жизненным циклом LiveStreamManager: старт, кадры, пауза, автостоп."""
    mock_client = MagicMock()
    mock_client.send_live_frame.return_value = {"status": "ok", "active_subscribers": 1}

    mgr = LiveStreamManager(client=mock_client)

    # 1. Запуск стрима
    mgr.start("cam_hw_1", width=640, height=480, pixel_format="MJPG")
    assert mgr.is_running is True
    assert mgr.active_camera_id == "cam_hw_1"

    # Даем mock потоку сгенерировать хотя бы 1 кадр
    time.sleep(0.1)
    assert mock_client.send_live_frame.called

    # 2. Пауза стрима
    mgr.set_paused(True)
    assert mgr.is_paused is True
    mgr.set_paused(False)
    assert mgr.is_paused is False

    # 3. Автостоп при неактивности > 120 с
    mgr.last_activity_time = time.time() - 125.0
    mgr.check_inactivity_timeout()
    assert mgr.is_running is False

    # 4. Явный стоп
    mgr.start("cam_hw_1", width=640, height=480)
    assert mgr.is_running is True
    mgr.stop()
    assert mgr.is_running is False


def test_rgb_software_gain_application():
    """Тест программной цветокоррекции каналов R/G/B на изображении."""
    mgr = LiveStreamManager(client=MagicMock())
    mgr.set_rgb_gains(r=1.5, g=1.0, b=0.5)

    # Создаем тестовое BGR изображение (10x10, все пиксели 100, 100, 100)
    img_bgr = np.full((10, 10, 3), 100, dtype=np.uint8)

    corrected = mgr.apply_rgb_gains(img_bgr)
    # Blue: 100 * 0.5 = 50
    assert np.all(corrected[:, :, 0] == 50)
    # Green: 100 * 1.0 = 100
    assert np.all(corrected[:, :, 1] == 100)
    # Red: 100 * 1.5 = 150
    assert np.all(corrected[:, :, 2] == 150)


def test_orchestrator_handles_live_commands():
    """Тест обработки команд START_LIVE, STOP_LIVE, SET_CAMERA_CONTROLS оркестратором."""
    mock_client = MagicMock()
    mock_hw = MagicMock()
    cfg = EdgeConfig(hardware_mode="mock")

    orch = EdgeOrchestrator(config=cfg, client=mock_client, hardware=mock_hw)
    orch.live_stream = MagicMock()

    # 1. START_LIVE
    orch.handle_command(
        CommandItem(
            id=1,
            command_type="START_LIVE",
            payload={
                "camera_hardware_id": "cam_live_1",
                "width": 1280,
                "height": 720,
                "pixel_format": "MJPG",
            },
        )
    )
    orch.live_stream.start.assert_called_once_with(
        "cam_live_1",
        width=1280,
        height=720,
        pixel_format="MJPG",
    )

    # 2. SET_CAMERA_CONTROLS
    orch.handle_command(
        CommandItem(
            id=2,
            command_type="SET_CAMERA_CONTROLS",
            payload={
                "camera_hardware_id": "cam_live_1",
                "controls": {"brightness": 10},
                "rgb": {"r": 1.1, "g": 1.0, "b": 0.9},
            },
        )
    )
    orch.live_stream.set_rgb_gains.assert_called_once_with(1.1, 1.0, 0.9)

    # 3. STOP_LIVE
    orch.handle_command(
        CommandItem(
            id=3,
            command_type="STOP_LIVE",
            payload={"camera_hardware_id": "cam_live_1"},
        )
    )
    orch.live_stream.stop.assert_called_once()
