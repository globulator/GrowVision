import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from edge_agent.config import EdgeConfig
from edge_agent.live_stream import LiveStreamManager
from edge_agent.orchestrator import EdgeOrchestrator
from shared.schemas import CommandInstallation, CommandItem, HeartbeatActiveInstallation


def _make_jpeg_bytes(color_bgr: tuple[int, int, int] = (40, 180, 60)) -> bytes:
    img = np.full((120, 160, 3), color_bgr, dtype=np.uint8)
    ok, enc = cv2.imencode(".jpg", img)
    assert ok
    return enc.tobytes()


class _FakeHTTPResp:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_capture_ustreamer_snapshot_without_pausing_live(tmp_path: Path):
    """При активном ustreamer плановый и ручной снимок забирают кадр из /snapshot без паузы Live."""
    cfg = EdgeConfig(
        edge_api_key="k",
        hardware_mode="mock",
        mock_cameras=True,
        offline_queue_dir=tmp_path / "queue",
    )
    mock_client = MagicMock()
    mock_client.upload_capture.return_value = (201, {"capture_id": 1})
    orch = EdgeOrchestrator(config=cfg, client=mock_client)

    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    orch.live_stream.is_running = True
    orch.live_stream.active_camera_id = "cam-live-1"
    orch.live_stream.process = fake_proc
    orch.live_stream.current_port = 8089

    jpeg_snap = _make_jpeg_bytes((20, 200, 40))
    state_json = json.dumps(
        {"ok": True, "result": {"source": {"online": True}}}
    ).encode()

    def fake_urlopen(req, timeout=2.5):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if url.endswith("/state"):
            return _FakeHTTPResp(200, state_json)
        if url.endswith("/snapshot"):
            return _FakeHTTPResp(200, jpeg_snap)
        raise AssertionError(f"Unexpected URL: {url}")

    inst = HeartbeatActiveInstallation(
        id=1,
        name="Тестовая",
        culture_name="Базилик",
        light_gpio_pin=4,
        flash_gpio_pin=17,
        camera_hardware_ids=["cam-live-1"],
    )
    cmd_inst = CommandInstallation(
        id=1,
        name="Тестовая",
        culture_name="Базилик",
        light_gpio_pin=4,
        flash_gpio_pin=17,
        camera_hardware_ids=["cam-live-1"],
    )
    orch.active_installations = [inst]

    with (
        patch("urllib.request.urlopen", side_effect=fake_urlopen),
        patch.object(orch.live_stream, "set_paused") as pause_mock,
        patch.object(orch.hardware, "capture_frame_with_sequence") as hw_cap_mock,
    ):
        # 1. Ручной SNAP
        orch.handle_command(
            CommandItem(id=10, command_type="SNAP", installation=cmd_inst)
        )
        # 2. Плановый снимок
        orch.trigger_scheduled_capture()

        pause_mock.assert_not_called()
        hw_cap_mock.assert_not_called()
        assert mock_client.upload_capture.call_count == 2
        uploaded_raw = mock_client.upload_capture.call_args_list[0][0][1]
        assert uploaded_raw == jpeg_snap


def test_snap_pauses_live_only_when_snapshot_unavailable(tmp_path: Path):
    """Если /snapshot ustreamer недоступен или стримит не ustreamer, стрим ставится на паузу и снимает hardware."""
    cfg = EdgeConfig(
        edge_api_key="k",
        hardware_mode="mock",
        mock_cameras=True,
        offline_queue_dir=tmp_path / "queue",
    )
    mock_client = MagicMock()
    mock_client.upload_capture.return_value = (201, {"capture_id": 2})
    orch = EdgeOrchestrator(config=cfg, client=mock_client)

    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    orch.live_stream.is_running = True
    orch.live_stream.active_camera_id = "cam-live-2"
    orch.live_stream.process = fake_proc
    orch.live_stream.current_port = 8090

    inst = CommandInstallation(
        id=2,
        name="Fallback",
        culture_name="Салат",
        light_gpio_pin=4,
        flash_gpio_pin=4,
        camera_hardware_ids=["cam-live-2"],
    )

    def failing_urlopen(req, timeout=2.5):
        raise OSError("connection refused")

    fallback_jpeg = _make_jpeg_bytes((10, 120, 10))
    with (
        patch("urllib.request.urlopen", side_effect=failing_urlopen),
        patch.object(orch.live_stream, "set_paused") as pause_mock,
        patch.object(
            orch.hardware, "capture_frame_with_sequence", return_value=fallback_jpeg
        ) as hw_cap_mock,
    ):
        orch.handle_command(CommandItem(id=11, command_type="SNAP", installation=inst))
        assert pause_mock.call_args_list[0][0] == (True,)
        assert pause_mock.call_args_list[-1][0] == (False,)
        hw_cap_mock.assert_called_once()


def test_ustreamer_device_offline_state_handling():
    """При online: false в /state ustreamer LiveStreamManager помечает device_online=False и не отдаёт заглушку ustreamer."""
    mgr = LiveStreamManager(mode="mock", mock_cameras=False, client=MagicMock())
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    mgr.is_running = True
    mgr.active_camera_id = "cam-offline-1"
    mgr.process = fake_proc
    mgr.current_port = 8091

    offline_state = json.dumps(
        {"ok": True, "result": {"source": {"online": False}}}
    ).encode()
    no_dev_jpeg = _make_jpeg_bytes((100, 100, 100))

    def fake_urlopen(req, timeout=2.5):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if url.endswith("/state"):
            return _FakeHTTPResp(200, offline_state)
        if url.endswith("/snapshot"):
            return _FakeHTTPResp(200, no_dev_jpeg)
        raise AssertionError(url)

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        frame = mgr._generate_frame()
        assert frame is None
        assert mgr.device_online is False
        assert mgr.capture_ustreamer_snapshot("cam-offline-1") is None


def test_live_stream_annotated_mode_uses_vision_pipeline():
    """В режиме 'annotated' LiveStreamManager пропускает кадр через VisionPipeline и возвращает размеченный JPEG."""
    mock_vision = MagicMock()
    annotated_jpeg = _make_jpeg_bytes((240, 32, 160))
    mock_vision.process_capture.return_value = (annotated_jpeg, 400.0, [])

    mgr = LiveStreamManager(
        mode="mock",
        mock_cameras=True,
        client=MagicMock(),
        vision=mock_vision,
    )
    mgr.is_running = True
    mgr.active_camera_id = "mock-usb-cam-1"
    mgr.set_camera_context(
        inst_name="Грядка-1", culture_name="Руккола", aruco_size_mm=50.0
    )
    mgr.set_stream_mode("annotated")

    out_frame = mgr._generate_frame()
    assert out_frame == annotated_jpeg
    mock_vision.process_capture.assert_called_once()
    call_kwargs = mock_vision.process_capture.call_args.kwargs
    assert call_kwargs["inst_name"] == "Грядка-1"
    assert call_kwargs["culture_name"] == "Руккола"
    assert call_kwargs["aruco_size_mm"] == 50.0

    # Обратное переключение на 'raw' отключает вызов VisionPipeline
    mock_vision.reset_mock()
    mgr.set_stream_mode("raw")
    raw_frame = mgr._generate_frame()
    assert raw_frame is not None and raw_frame.startswith(b"\xff\xd8")
    mock_vision.process_capture.assert_not_called()


def test_live_stream_rgb_gains_supports_zero():
    """set_rgb_gains поддерживает диапазон от 0.00 (включая 0.0) без зажатия в 0.1."""
    mgr = LiveStreamManager(client=MagicMock())
    mgr.set_rgb_gains(0.0, 0.5, 1.0)
    assert mgr.rgb_gains == (0.0, 0.5, 1.0)

    img_bgr = np.full((4, 4, 3), 200, dtype=np.uint8)
    out = mgr.apply_rgb_gains(img_bgr)
    assert np.all(out[:, :, 2] == 0)
    assert np.all(out[:, :, 1] == 100)
    assert np.all(out[:, :, 0] == 200)


def test_ustreamer_local_requests_bypass_system_proxy(monkeypatch):
    """Запросы к локальному ustreamer используют ProxyHandler({}) и игнорируют http_proxy/ALL_PROXY."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    jpeg_snap = _make_jpeg_bytes((10, 180, 30))

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/state":
                body = json.dumps(
                    {"ok": True, "result": {"source": {"online": True}}}
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/snapshot":
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.end_headers()
                self.wfile.write(jpeg_snap)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *_args):
            return

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        # Несуществующий прокси не должен блокировать запросы к 127.0.0.1
        monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
        monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")

        mgr = LiveStreamManager(mode="mock", mock_cameras=False, client=MagicMock())
        fake_proc = MagicMock()
        fake_proc.poll.return_value = None
        mgr.is_running = True
        mgr.active_camera_id = "cam-proxy-test"
        mgr.process = fake_proc
        mgr.current_port = port

        assert (
            mgr.capture_ustreamer_snapshot("cam-proxy-test", timeout=2.0) == jpeg_snap
        )
    finally:
        server.shutdown()
        server.server_close()
