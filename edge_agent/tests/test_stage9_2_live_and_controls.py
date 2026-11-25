# edge_agent/tests/test_stage9_2_live_and_controls.py
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from edge_agent.camera_controls import (
    apply_control_dependencies,
    categorize_control,
    parse_v4l2_controls,
    query_camera_controls,
    query_camera_formats_and_sizes,
)
from edge_agent.config import EdgeConfig
from edge_agent.hardware import HardwareManager, clean_camera_name
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

    mgr = LiveStreamManager(client=mock_client, mock_cameras=True)

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
    cfg = EdgeConfig(hardware_mode="mock", mock_cameras=True)

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


def test_clean_camera_name_and_sysfs_scan(tmp_path: Path):
    """Тест получения человекочитаемого имени камеры из sysfs и очистки от дублей."""
    assert clean_camera_name("HD Webcam C270: HD Webcam C270") == "HD Webcam C270"
    assert (
        clean_camera_name("", "usb-046d_0825_123456-video-index0") == "046d 0825 123456"
    )

    by_id_dir = tmp_path / "dev" / "v4l" / "by-id"
    by_id_dir.mkdir(parents=True)
    dev_dir = tmp_path / "dev"
    video0 = dev_dir / "video0"
    video0.touch()
    cam_link = by_id_dir / "usb-046d_0825_ABCDEF-video-index0"
    cam_link.symlink_to(video0)

    sys_dir = tmp_path / "sys"
    sys_name_dir = sys_dir / "class" / "video4linux" / "video0"
    sys_name_dir.mkdir(parents=True)
    (sys_name_dir / "name").write_text("UVC Camera (046d:0825)", encoding="utf-8")

    hw = HardwareManager(mode="mock", v4l_by_id_dir=by_id_dir, sys_dir=sys_dir)
    cams = hw.scan_cameras()
    assert len(cams) == 1
    assert cams[0].hardware_id == "usb-046d_0825_ABCDEF-video-index0"
    assert cams[0].auto_name == "UVC Camera (046d:0825)"


def test_ustreamer_command_flags_and_no_fake_frame_in_rpi():
    """В режиме rpi ustreamer запускается с --device-timeout 5 и без --quality; фейковый кадр не генерируется."""
    mgr = LiveStreamManager(mode="rpi", client=MagicMock())
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None

    with (
        patch("shutil.which", return_value="/usr/bin/ustreamer"),
        patch("subprocess.Popen", return_value=fake_proc) as popen_mock,
    ):
        ok = mgr.start(
            "usb-046d_0825-video-index0", width=1280, height=960, pixel_format="MJPG"
        )
        assert ok is True
        cmd = popen_mock.call_args[0][0]
        assert "--device-timeout" in cmd
        idx = cmd.index("--device-timeout")
        assert cmd[idx + 1] == "5"
        assert "--quality" not in cmd
        assert "-m" in cmd and cmd[cmd.index("-m") + 1] == "MJPEG"

        # Если ustreamer не отдал кадр (ошибка/таймаут), в rpi не подставляется синтетический кадр
        with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
            assert mgr._generate_frame() is None

        mgr.stop()


def test_rpi_controls_and_formats_no_fake_fallback():
    """В режиме rpi (mock_mode=False) при ошибке v4l2-ctl не подставляются фейковые контролы и разрешения."""
    with patch("subprocess.run", side_effect=FileNotFoundError("no v4l2-ctl")):
        assert query_camera_formats_and_sizes("/dev/video0", mock_mode=False) == []
        assert query_camera_controls("/dev/video0", mock_mode=False) == []


def test_apply_control_dependencies():
    """Проверка пересчёта is_inactive для зависимых пар контролов."""
    ctrls = [
        {"name": "white_balance_automatic", "value": 1, "is_inactive": False},
        {"name": "white_balance_temperature", "value": 4600, "is_inactive": True},
        {
            "name": "auto_exposure",
            "value": 3,
            "is_inactive": False,
            "menu_items": [
                {"id": 1, "label": "Manual Mode"},
                {"id": 3, "label": "Aperture Priority Mode"},
            ],
        },
        {"name": "exposure_time_absolute", "value": 156, "is_inactive": True},
    ]

    apply_control_dependencies(
        ctrls, {"white_balance_automatic": 0, "auto_exposure": 1}
    )
    by_name = {c["name"]: c for c in ctrls}
    assert by_name["white_balance_temperature"]["is_inactive"] is False
    assert by_name["exposure_time_absolute"]["is_inactive"] is False

    apply_control_dependencies(
        ctrls, {"white_balance_automatic": 1, "auto_exposure": 3}
    )
    assert by_name["white_balance_temperature"]["is_inactive"] is True
    assert by_name["exposure_time_absolute"]["is_inactive"] is True


def test_orchestrator_autocalibrate_summary_without_id_and_get_controls():
    """AUTOCALIBRATE_CAMERA не дублирует hardware_id в сообщении; GET_CAMERA_CONTROLS синхронизирует состояние."""
    cfg = EdgeConfig(hardware_mode="mock", mock_cameras=True)
    mock_client = MagicMock()
    orch = EdgeOrchestrator(config=cfg, client=mock_client)

    orch.handle_command(
        CommandItem(
            id=10,
            command_type="AUTOCALIBRATE_CAMERA",
            payload={"camera_hardware_id": "usb-046d_0825-video-index0"},
        )
    )
    _, res_calib = mock_client.send_command_result.call_args[0]
    assert res_calib.status == "done"
    assert "usb-046d_0825" not in res_calib.result_message
    assert "1920x1080 (MJPG)" in res_calib.result_message

    mock_client.reset_mock()
    orch.handle_command(
        CommandItem(
            id=11,
            command_type="GET_CAMERA_CONTROLS",
            payload={"camera_hardware_id": "mock-usb-cam-1"},
        )
    )
    mock_client.send_camera_controls_state.assert_called_once()
    _, res_ctrl = mock_client.send_command_result.call_args[0]
    assert res_ctrl.status == "done"
    assert isinstance(res_ctrl.data, dict)
    assert "controls" in res_ctrl.data
    assert "formats" in res_ctrl.data
