"""Тесты этапа 12.1 (Edge-агент): ночная съёмка, сохранение HEX-цвета при смене фаз и защита во время захвата."""

import threading
from unittest.mock import MagicMock

from edge_agent.config import EdgeConfig
from edge_agent.hardware import MOCK_JPEG_FRAME, HardwareManager
from edge_agent.orchestrator import EdgeOrchestrator
from shared.pins import hex_to_rgb01
from shared.schemas import (
    CommandResult,
    HeartbeatActiveInstallation,
    HeartbeatResponse,
)


class _DummyClient:
    def __init__(self) -> None:
        self.heartbeat_response: HeartbeatResponse | None = None
        self.results: dict[int, CommandResult] = {}

    def send_heartbeat(self, _req) -> HeartbeatResponse | None:
        return self.heartbeat_response

    def claim_command(self):
        return []

    def send_command_result(self, command_id: int, res: CommandResult) -> bool:
        self.results[command_id] = res
        return True

    def upload_capture(self, *_args):
        return 201, {"capture_id": 1}

    def send_camera_controls_state(self, **_kwargs) -> bool:
        return True


def _make_orchestrator(tmp_path):
    cfg = EdgeConfig(
        server_url="http://127.0.0.1:9999",
        edge_api_key="test-key",
        hardware_mode="mock",
        mock_cameras=True,
        offline_queue_dir=tmp_path / "q",
    )
    hw = HardwareManager(mode="mock", mock_cameras=True)
    client = _DummyClient()
    vision = MagicMock()
    vision.get_model_name.return_value = "mock-vision"
    vision.process_capture.return_value = (MOCK_JPEG_FRAME, 100.0, [])
    orch = EdgeOrchestrator(config=cfg, client=client, hardware=hw, vision=vision)
    return orch, client, hw


def test_night_capture_uses_white_flash_and_restores_light_off_and_saved_color(
    tmp_path,
):
    """Ночная съёмка (light_state=False) включает вспышку #FFFFFF, возвращает light=OFF и сохраняет light_color."""
    orch, client, hw = _make_orchestrator(tmp_path)
    inst_night = HeartbeatActiveInstallation(
        id=1,
        light_gpio_pin="17,27,22",
        flash_gpio_pin="5,6,13",
        light_color="#FF4400",
        flash_color="#00FFAA",
        camera_hardware_ids=["mock-usb-cam-1"],
        light_state=False,
        flash_state=False,
    )
    client.heartbeat_response = HeartbeatResponse(
        capture_interval_hours=1,
        debug_mode=False,
        active_installations=[inst_night],
        global_light_mode="default",
        global_flash_mode="default",
    )
    orch.perform_heartbeat()

    # Ночь: свет выключен, но цвет #FF4400 сохранён в desired
    assert hw.get_pin_state(17) is False
    assert hw.get_pin_state(27) is False
    assert hw.get_pin_state(22) is False
    assert hw.get_desired_channel_color([17, 27, 22]) == "#FF4400"

    observed: dict[str, object] = {}

    def spy_capture(camera_hardware_id, warmup_frames=0, frame_index=None):
        observed["light"] = hw.get_pin_state(17)
        observed["flash"] = (
            hw.get_pin_state(5),
            hw.get_pin_state(6),
            hw.get_pin_state(13),
        )
        observed["flash_pwm"] = (hw._mock_pwm[5], hw._mock_pwm[6], hw._mock_pwm[13])
        return MOCK_JPEG_FRAME

    hw.capture_frame = spy_capture  # type: ignore[method-assign]
    hw.capture_frame_with_sequence(
        camera_hardware_id="mock-usb-cam-1",
        light_pin=[17, 27, 22],
        flash_pin=[5, 6, 13],
        light_color="#FF4400",
        flash_color="#00FFAA",
    )

    assert observed["light"] is False
    assert observed["flash"] == (True, True, True)
    assert observed["flash_pwm"] == (1.0, 1.0, 1.0)  # нейтральный белый #FFFFFF

    # После съёмки: свет остаётся OFF, вспышка OFF, а при наступлении дня цвет #FF4400 восстанавливается
    assert hw.get_pin_state(17) is False
    assert hw.get_pin_state(5) is False
    assert hw.get_desired_channel_color([17, 27, 22]) == "#FF4400"

    inst_day = inst_night.model_copy(update={"light_state": True})
    client.heartbeat_response = HeartbeatResponse(
        capture_interval_hours=1,
        debug_mode=False,
        active_installations=[inst_day],
    )
    orch.perform_heartbeat()
    expected_rgb = hex_to_rgb01("#FF4400")
    assert hw.get_pin_state(17) is True
    assert (hw._mock_pwm[17], hw._mock_pwm[27], hw._mock_pwm[22]) == expected_rgb


def test_phase_change_during_capture_does_not_interrupt_pins_and_applies_on_finish(
    tmp_path,
):
    """Смена фазы День->Ночь через heartbeat во время захвата не трогает пины, а применяется после end_capture_pins."""
    orch, client, hw = _make_orchestrator(tmp_path)
    inst_day = HeartbeatActiveInstallation(
        id=2,
        light_gpio_pin="17,27,22",
        flash_gpio_pin=23,
        light_color="#2288FF",
        camera_hardware_ids=["mock-usb-cam-1"],
        light_state=True,
        flash_state=False,
    )
    client.heartbeat_response = HeartbeatResponse(
        capture_interval_hours=1,
        debug_mode=False,
        active_installations=[inst_day],
    )
    orch.perform_heartbeat()
    assert hw.get_pin_state(17) is True

    capture_started = threading.Event()
    allow_finish = threading.Event()

    def blocking_capture(camera_hardware_id, warmup_frames=0, frame_index=None):
        capture_started.set()
        assert allow_finish.wait(timeout=3.0)
        # Во время съёмки свет выключен, вспышка включена, несмотря на параллельный heartbeat
        assert hw.get_pin_state(17) is False
        assert hw.get_pin_state(23) is True
        return MOCK_JPEG_FRAME

    hw.capture_frame = blocking_capture  # type: ignore[method-assign]

    cap_thread = threading.Thread(
        target=lambda: hw.capture_frame_with_sequence(
            camera_hardware_id="mock-usb-cam-1",
            light_pin=[17, 27, 22],
            flash_pin=23,
            light_color="#2288FF",
        ),
        daemon=True,
    )
    cap_thread.start()
    assert capture_started.wait(timeout=2.0)

    # Приходит heartbeat со сменой фазы на ночь (light_state=False) во время активного захвата
    inst_night = inst_day.model_copy(update={"light_state": False})
    orch.active_installations = [inst_night]
    orch.sync_installations_light_from_heartbeat()

    # Желаемое состояние обновлено, но вспышка не прервалась до конца захвата
    assert hw.get_desired_pin_state(17) is False
    assert hw.get_pin_state(23) is True

    allow_finish.set()
    cap_thread.join(timeout=3.0)

    # После окончания захвата пины переходят в ночную фазу (light=OFF, flash=OFF), цвет сохранён
    assert hw.get_pin_state(17) is False
    assert hw.get_pin_state(23) is False
    assert hw.get_desired_channel_color([17, 27, 22]) == "#2288FF"
