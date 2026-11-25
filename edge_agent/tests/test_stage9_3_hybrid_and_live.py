from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import pytest

from edge_agent.camera_controls import parse_v4l2_controls
from edge_agent.config import EdgeConfig
from edge_agent.hardware import HardwareManager, get_hardware_telemetry
from edge_agent.live_stream import LiveStreamManager
from edge_agent.orchestrator import EdgeOrchestrator
from shared.schemas import CommandItem


def test_hybrid_mode_gpio_mock_and_real_v4l2_cameras(tmp_path: Path):
    """Гибридный режим (HARDWARE_MODE=mock, MOCK_CAMERAS=false): GPIO эмулировано, камеры реальные."""
    by_id_dir = tmp_path / "dev" / "v4l" / "by-id"
    by_id_dir.mkdir(parents=True)
    dev_dir = tmp_path / "dev"
    video0 = dev_dir / "video0"
    video0.touch()
    cam_link = by_id_dir / "usb-Chicony_Cam-video-index0"
    cam_link.symlink_to(video0)

    sys_dir = tmp_path / "sys"
    sys_cam_dir = sys_dir / "class" / "video4linux" / "video0"
    sys_cam_dir.mkdir(parents=True)
    (sys_cam_dir / "name").write_text("Integrated Camera", encoding="utf-8")

    hw = HardwareManager(
        mode="mock",
        mock_cameras=False,
        v4l_by_id_dir=by_id_dir,
        sys_dir=sys_dir,
    )

    # 1. GPIO работает в mock-режиме без gpiozero
    hw.set_light(17, True)
    assert hw.get_pin_state(17) is True
    assert hw.use_mock_cameras is False

    # 2. scan_cameras читает реальные симлинки, а не mock-usb-cam-1
    cams = hw.scan_cameras()
    assert len(cams) == 1
    assert cams[0].hardware_id == "usb-Chicony_Cam-video-index0"
    assert cams[0].auto_name == "Integrated Camera"

    # 3. Несуществующая камера вызывает ошибку вместо синтетики
    with pytest.raises(RuntimeError, match="не найдена"):
        hw.capture_frame("mock-usb-cam-1")

    # 4. Реальная камера захватывает кадр через cv2.CAP_V4L2 + v4l2-ctl
    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = True
    mock_cap.read.return_value = (True, np.full((480, 640, 3), 80, dtype=np.uint8))

    with (
        patch("cv2.VideoCapture", return_value=mock_cap) as vc_mock,
        patch("edge_agent.hardware.apply_power_line_frequency_50hz") as freq_mock,
        patch("edge_agent.hardware.probe_camera_capabilities") as probe_mock,
    ):
        probe_mock.return_value = MagicMock(
            max_width=640,
            max_height=480,
            pixel_format="MJPG",
            summary_text=lambda: "640x480 (MJPG)",
        )
        frame = hw.capture_frame("usb-Chicony_Cam-video-index0")
        assert frame.startswith(b"\xff\xd8")
        vc_mock.assert_called_once_with(str(video0.resolve()), cv2.CAP_V4L2)
        freq_mock.assert_called_once_with(str(video0.resolve()))


def test_mock_cameras_true_keeps_synthetic_behavior(tmp_path: Path):
    """При MOCK_CAMERAS=true сохраняется синтетика даже при наличии дефолтного пути."""
    hw = HardwareManager(mode="mock", mock_cameras=True)
    assert hw.use_mock_cameras is True

    cams = hw.scan_cameras()
    hw_ids = [c.hardware_id for c in cams]
    assert "mock-usb-cam-1" in hw_ids
    assert "mock-csi-cam-2" in hw_ids

    frame = hw.capture_frame("mock-usb-cam-1")
    assert frame.startswith(b"\xff\xd8")

    telem = get_hardware_telemetry(gpio_mode="mock", mock_cameras=True)
    assert telem.mock_cameras is True


def test_orchestrator_hybrid_mode_heartbeat_and_controls(tmp_path: Path):
    """В гибридном режиме оркестратор отправляет mock_cameras=False и опрашивает/применяет v4l2-ctl."""
    by_id_dir = tmp_path / "by-id"
    by_id_dir.mkdir(parents=True)
    video0 = tmp_path / "video0"
    video0.touch()
    (by_id_dir / "usb-Cam-video-index0").symlink_to(video0)

    cfg = EdgeConfig(
        edge_api_key="test-key",
        hardware_mode="mock",
        mock_cameras=False,
        v4l_by_id_dir=by_id_dir,
        sys_dir=tmp_path / "sys",
    )
    mock_client = MagicMock()
    mock_client.send_heartbeat.return_value = None
    orch = EdgeOrchestrator(config=cfg, client=mock_client)

    orch.perform_heartbeat()
    hb_req = mock_client.send_heartbeat.call_args[0][0]
    assert hb_req.hardware.gpio_mode == "mock"
    assert hb_req.hardware.mock_cameras is False

    real_formats = [{"format": "MJPG", "resolutions": ["640x480"]}]
    real_controls = [{"name": "brightness", "type": "int", "value": 128}]
    with (
        patch(
            "edge_agent.camera_controls.query_camera_formats_and_sizes",
            return_value=real_formats,
        ) as q_fmt,
        patch(
            "edge_agent.camera_controls.query_camera_controls",
            return_value=real_controls,
        ) as q_ctrl,
        patch(
            "edge_agent.camera_controls.set_v4l2_control", return_value=True
        ) as set_ctrl,
    ):
        orch.handle_command(
            CommandItem(
                id=1,
                command_type="GET_CAMERA_CONTROLS",
                payload={"camera_hardware_id": "usb-Cam-video-index0"},
            )
        )
        q_fmt.assert_called_once_with(str(video0.resolve()), mock_mode=False)
        q_ctrl.assert_called_once_with(str(video0.resolve()), mock_mode=False)
        mock_client.send_camera_controls_state.assert_called_once()

        mock_client.reset_mock()
        orch.handle_command(
            CommandItem(
                id=2,
                command_type="SET_CAMERA_CONTROLS",
                payload={
                    "camera_hardware_id": "usb-Cam-video-index0",
                    "controls": {"brightness": 140},
                },
            )
        )
        set_ctrl.assert_called_once_with(
            str(video0.resolve()), "brightness", 140, mock_mode=False
        )
        # После SET_CAMERA_CONTROLS контролы перечитываются и синхронизируются с сервером
        mock_client.send_camera_controls_state.assert_called_once()


def test_live_stream_opencv_fallback_without_ustreamer_and_on_crash(tmp_path: Path):
    """LiveStreamManager в гибридном/реальном режиме использует OpenCV V4L2 fallback без синтетики."""
    by_id_dir = tmp_path / "by-id"
    by_id_dir.mkdir(parents=True)
    video0 = tmp_path / "video0"
    video0.touch()
    (by_id_dir / "usb-Cam-video-index0").symlink_to(video0)

    mgr = LiveStreamManager(
        mode="mock",
        mock_cameras=False,
        v4l_by_id_dir=by_id_dir,
        client=MagicMock(),
    )

    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = True
    # Фактический кадр камеры 640x480 вместо запрошенных 1280x720
    mock_cap.read.return_value = (True, np.full((480, 640, 3), 50, dtype=np.uint8))

    # 1. ustreamer отсутствует -> OpenCV V4L2 fallback
    with (
        patch("shutil.which", return_value=None),
        patch("cv2.VideoCapture", return_value=mock_cap) as vc_mock,
    ):
        mgr.is_running = True
        mgr.active_camera_id = "usb-Cam-video-index0"
        mgr.current_width = 1280
        mgr.current_height = 720
        frame = mgr._generate_frame()
        assert frame is not None and frame.startswith(b"\xff\xd8")
        assert mgr.current_width == 640
        assert mgr.current_height == 480
        vc_mock.assert_called_once_with(str(video0.resolve()), cv2.CAP_V4L2)

        # Пауза освобождает VideoCapture для SNAP
        mgr.set_paused(True)
        mock_cap.release.assert_called_once()
        mgr.stop()

    # 2. ustreamer упал при старте (returncode=1) -> переход на OpenCV V4L2 fallback
    crashed_proc = MagicMock()
    crashed_proc.poll.return_value = 1
    crashed_proc.returncode = 1
    crashed_proc.stderr = MagicMock()
    crashed_proc.stderr.read.return_value = b"Device busy"

    mock_cap2 = MagicMock()
    mock_cap2.isOpened.return_value = False
    with (
        patch("shutil.which", return_value="/usr/bin/ustreamer"),
        patch("subprocess.Popen", return_value=crashed_proc),
        patch("cv2.VideoCapture", return_value=mock_cap2),
    ):
        ok = mgr.start(
            "usb-Cam-video-index0", width=1280, height=720, pixel_format="MJPG"
        )
        assert ok is True
        assert mgr.process is None
        assert mgr.stream_url is None
        # Если камера не открылась — возвращает None, НЕ синтетическую заглушку!
        assert mgr._generate_frame() is None
        mgr.stop()


def test_parse_v4l2_controls_skips_unsupported_rect_and_bitmask():
    """parse_v4l2_controls отфильтровывает rect и bitmask, оставляя int/bool/menu."""
    out = """
User Controls
                     brightness 0x00980900 (int)    : min=0 max=255 step=1 default=128 value=128
        white_balance_automatic 0x0098090c (bool)   : default=1 value=1
   region_of_interest_rectangle 0x00981ae1 (rect)   : value=(0,0)/65536x65536 flags=has-payload
  region_of_interest_auto_ctrls 0x00981ae2 (bitmask): max=0x00000001 default=0x00000001 value=0x00000001
"""
    parsed = parse_v4l2_controls(out)
    names = [c["name"] for c in parsed]
    assert names == ["brightness", "white_balance_automatic"]
