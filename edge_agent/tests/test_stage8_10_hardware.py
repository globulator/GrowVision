from unittest.mock import MagicMock, patch

from edge_agent.hardware import (
    HardwareManager,
    probe_camera_capabilities,
)
from edge_agent.orchestrator import EdgeOrchestrator
from shared.schemas import CommandItem


def test_probe_camera_capabilities_parsing():
    """Парсинг вывода v4l2-ctl: предпочтение MJPG и поиск максимального разрешения и автофокуса."""
    mock_list_formats = """
ioctl: VIDIOC_ENUM_FMT
	Type: Video Capture

	[0]: 'YUYV' (YUYV 4:2:2)
		Size: Discrete 640x480
			Interval: Discrete 0.033s (30.000 fps)
		Size: Discrete 1280x720
			Interval: Discrete 0.100s (10.000 fps)
	[1]: 'MJPG' (Motion-JPEG, compressed)
		Size: Discrete 640x480
			Interval: Discrete 0.033s (30.000 fps)
		Size: Discrete 1280x720
			Interval: Discrete 0.033s (30.000 fps)
		Size: Discrete 1920x1080
			Interval: Discrete 0.033s (30.000 fps)
"""
    mock_all = """
User Controls
                     brightness 0x00980900 (int)    : min=-64 max=64 step=1 default=0 value=0
                     focus_auto 0x0098090c (bool)   : default=1 value=1
                 focus_absolute 0x0098090a (int)    : min=0 max=1024 step=1 default=0 value=0
"""

    def fake_subprocess_run(cmd, **kwargs):
        res = MagicMock()
        res.returncode = 0
        if "--list-formats-ext" in cmd:
            res.stdout = mock_list_formats
        elif "--all" in cmd:
            res.stdout = mock_all
        else:
            res.stdout = ""
        return res

    with patch("subprocess.run", side_effect=fake_subprocess_run):
        profile = probe_camera_capabilities("/dev/video0", "usb-test-cam")
        assert profile.hardware_id == "usb-test-cam"
        assert profile.pixel_format == "MJPG"
        assert profile.max_width == 1920
        assert profile.max_height == 1080
        assert profile.has_autofocus is True
        assert "1920x1080 (MJPG)" in profile.summary_text()
        assert "автофокус: поддерживается" in profile.summary_text()


def test_probe_camera_capabilities_fallback_on_error():
    """При отсутствии v4l2-ctl или ошибке возвращается безопасный дефолтный профиль."""
    with patch("subprocess.run", side_effect=FileNotFoundError("v4l2-ctl not found")):
        profile = probe_camera_capabilities("/dev/video99", "cam-fallback")
        assert profile.max_width == 1920
        assert profile.max_height == 1080
        assert profile.pixel_format == "MJPG"
        assert profile.has_autofocus is False


def test_hardware_manager_calibrate_camera_in_mock():
    """В mock-режиме calibrate_camera возвращает детерминированный профиль максимального качества."""
    mgr = HardwareManager(mode="mock", mock_cameras=True)
    profile = mgr.calibrate_camera("mock-usb-cam-1")
    assert profile.hardware_id == "mock-usb-cam-1"
    assert profile.max_width == 1920
    assert profile.max_height == 1080
    assert profile.pixel_format == "MJPG"
    assert profile.has_autofocus is True
    # Проверяем кэширование
    cached = mgr.get_camera_profile("mock-usb-cam-1")
    assert cached is profile


def test_orchestrator_handle_autocalibrate_camera_command():
    """Orchestrator обрабатывает команду AUTOCALIBRATE_CAMERA и отправляет результат."""
    from unittest.mock import MagicMock

    from edge_agent.config import EdgeConfig

    cfg = EdgeConfig(
        server_url="http://test-server:8000",
        edge_api_key="test-key",
        hardware_mode="mock",
        mock_cameras=True,
    )
    client_mock = MagicMock()
    orch = EdgeOrchestrator(cfg, client=client_mock)

    cmd = CommandItem(
        id=77,
        command_type="AUTOCALIBRATE_CAMERA",
        is_global=True,
        payload={"camera_hardware_id": "mock-usb-cam-1"},
    )

    orch.handle_command(cmd)

    # Проверяем, что результат отправлен клиентом
    client_mock.send_command_result.assert_called_once()
    args = client_mock.send_command_result.call_args[0]
    cmd_id, cmd_res = args
    assert cmd_id == 77
    assert cmd_res.status == "done"
    assert "1920x1080 (MJPG)" in cmd_res.result_message
    assert "автофокус: поддерживается" in cmd_res.result_message
