"""Тесты этапа 11.3 (Edge-агент): иерархия света, приоритет скрипта съёмки, конкурентные команды и RELAY_TEST."""

import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from edge_agent.config import EdgeConfig
from edge_agent.hardware import MOCK_JPEG_FRAME, HardwareManager
from edge_agent.orchestrator import EdgeOrchestrator
from shared.schemas import (
    CommandInstallation,
    CommandItem,
    CommandResult,
    HeartbeatActiveInstallation,
    HeartbeatResponse,
)


class _DummyClient:
    def __init__(self) -> None:
        self.heartbeat_response: HeartbeatResponse | None = None
        self.results: dict[int, CommandResult] = {}
        self.uploaded_captures: list[tuple] = []

    def send_heartbeat(self, _req) -> HeartbeatResponse | None:
        return self.heartbeat_response

    def claim_command(self):
        return []

    def send_command_result(self, command_id: int, res: CommandResult) -> bool:
        self.results[command_id] = res
        return True

    def upload_capture(self, meta, raw_bytes, ann_bytes):
        self.uploaded_captures.append((meta, raw_bytes, ann_bytes))
        return 201, {"capture_id": len(self.uploaded_captures)}

    def send_camera_controls_state(self, **_kwargs) -> bool:
        return True


def _make_orchestrator(
    tmp_path,
) -> tuple[EdgeOrchestrator, _DummyClient, HardwareManager]:
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


def test_capture_priority_overrides_global_light_on_and_restores_desired(tmp_path):
    """Скрипт съёмки выключает свет и включает вспышку даже при global_light_mode='on', а затем возвращает desired."""
    orch, client, hw = _make_orchestrator(tmp_path)
    inst = HeartbeatActiveInstallation(
        id=1,
        light_gpio_pin=17,
        flash_gpio_pin=27,
        camera_hardware_ids=["mock-usb-cam-1"],
        light_state=True,
        flash_state=False,
    )
    client.heartbeat_response = HeartbeatResponse(
        capture_interval_hours=1,
        debug_mode=False,
        active_installations=[inst],
        global_light_mode="on",
        global_flash_mode="off",
    )
    orch.perform_heartbeat()
    assert hw.get_pin_state(17) is True
    assert hw.get_pin_state(27) is False

    observed_during_capture: dict[str, bool] = {}

    def spy_capture_frame(camera_hardware_id, warmup_frames=0, frame_index=None):
        observed_during_capture["light"] = hw.get_pin_state(17)
        observed_during_capture["flash"] = hw.get_pin_state(27)
        return MOCK_JPEG_FRAME

    hw.capture_frame = spy_capture_frame  # type: ignore[method-assign]

    frame = hw.capture_frame_with_sequence(
        camera_hardware_id="mock-usb-cam-1",
        light_pin=17,
        flash_pin=27,
        warmup_frames=0,
        switch_delay_ms=0,
    )
    assert frame == MOCK_JPEG_FRAME
    assert observed_during_capture == {"light": False, "flash": True}
    assert hw.get_pin_state(17) is True
    assert hw.get_pin_state(27) is False

    # Аналогичная проверка для _capture_via_live_snapshot
    hw.set_desired_pin_state(
        17, False, role="light", apply_if_idle=True, source="manual"
    )
    hw.set_desired_pin_state(
        27, True, role="flash", apply_if_idle=True, source="manual"
    )

    observed_live_snap: dict[str, bool] = {}

    def spy_snapshot(_cam_hw_id):
        observed_live_snap["light"] = hw.get_pin_state(17)
        observed_live_snap["flash"] = hw.get_pin_state(27)
        return MOCK_JPEG_FRAME

    orch.live_stream = SimpleNamespace(
        process=object(),
        capture_ustreamer_snapshot=spy_snapshot,
    )
    live_bytes = orch._capture_via_live_snapshot(inst, "mock-usb-cam-1", 0.0)
    assert live_bytes == MOCK_JPEG_FRAME
    assert observed_live_snap == {"light": False, "flash": True}
    # В finally пины должны вернуться в актуальные _desired_pin_states (17=False, 27=True)
    assert hw.get_pin_state(17) is False
    assert hw.get_pin_state(27) is True


def test_concurrent_light_command_and_heartbeat_during_capture(tmp_path):
    """LIGHT_MAIN во время активной съёмки не ждёт hardware.lock, не сбивает снимаемую установку и применяется после кадра."""
    orch, client, hw = _make_orchestrator(tmp_path)
    inst1 = HeartbeatActiveInstallation(
        id=1,
        light_gpio_pin=17,
        flash_gpio_pin=27,
        camera_hardware_ids=["mock-usb-cam-1"],
        light_state=True,
        flash_state=False,
    )
    inst2 = HeartbeatActiveInstallation(
        id=2,
        light_gpio_pin=22,
        flash_gpio_pin=23,
        camera_hardware_ids=[],
        light_state=True,
        flash_state=False,
    )
    client.heartbeat_response = HeartbeatResponse(
        capture_interval_hours=1,
        debug_mode=False,
        active_installations=[inst1, inst2],
        global_light_mode="default",
        global_flash_mode="default",
    )
    orch.perform_heartbeat()
    assert hw.get_pin_state(17) is True
    assert hw.get_pin_state(22) is True

    entered_capture = threading.Event()
    allow_finish_capture = threading.Event()
    capture_errors: list[Exception] = []

    def blocking_capture_frame(camera_hardware_id, warmup_frames=0, frame_index=None):
        entered_capture.set()
        assert allow_finish_capture.wait(timeout=3.0)
        # Даже после команд и heartbeat во время съёмки свет=False, вспышка=True
        assert hw.get_pin_state(17) is False
        assert hw.get_pin_state(27) is True
        return MOCK_JPEG_FRAME

    hw.capture_frame = blocking_capture_frame  # type: ignore[method-assign]

    # Запускаем плановую съёмку только для inst1 (inst2 не имеет камер, но всё равно hardware.lock захвачен)
    def run_sched():
        try:
            orch.trigger_scheduled_capture()
        except Exception as exc:  # noqa: BLE001
            capture_errors.append(exc)

    sched_thread = threading.Thread(target=run_sched)
    sched_thread.start()
    assert entered_capture.wait(timeout=2.0)

    # 1. Пока съёмка идёт и hardware.lock занят, приходит LIGHT_MAIN выключить свет на inst1 и inst2
    cmd = CommandItem(
        id=77,
        command_type="LIGHT_MAIN",
        is_global=True,
        installations=[
            CommandInstallation(
                id=1, light_gpio_pin=17, flash_gpio_pin=27, camera_hardware_ids=[]
            ),
            CommandInstallation(
                id=2, light_gpio_pin=22, flash_gpio_pin=23, camera_hardware_ids=[]
            ),
        ],
        payload={
            "mode": "off",
            "pin_states": {"17": False, "22": False},
        },
    )
    t0 = time.monotonic()
    orch.handle_command(cmd)
    elapsed = time.monotonic() - t0
    assert elapsed < 0.5  # Не блокируется на 10-секундном таймауте hardware.lock!
    assert client.results[77].status == "done"

    # Не-снимаемая установка 2 выключилась сразу, а снимаемая установка 1 держит capture_warmup (17=False, 27=True)
    assert hw.get_pin_state(22) is False
    assert hw.get_pin_state(17) is False
    assert hw.get_pin_state(27) is True
    assert hw.get_desired_pin_state(17) is False

    # 2. Конкурентный heartbeat с global_light_mode="on" тоже не перебивает снимаемые пины,
    # но обновляет не-снимаемый пин 22 сразу и желаемое состояние пина 17!
    client.heartbeat_response = HeartbeatResponse(
        capture_interval_hours=1,
        debug_mode=False,
        active_installations=[
            HeartbeatActiveInstallation(
                id=1,
                light_gpio_pin=17,
                flash_gpio_pin=27,
                camera_hardware_ids=["mock-usb-cam-1"],
                light_state=True,
                flash_state=False,
            ),
            HeartbeatActiveInstallation(
                id=2,
                light_gpio_pin=22,
                flash_gpio_pin=23,
                camera_hardware_ids=[],
                light_state=True,
                flash_state=False,
            ),
        ],
        global_light_mode="on",
        global_flash_mode="default",
    )
    orch.perform_heartbeat()
    assert hw.get_pin_state(22) is True  # Не снимается -> обновился сразу
    assert hw.get_pin_state(17) is False  # В захвате -> не перебит!
    assert hw.get_pin_state(27) is True
    assert hw.get_desired_pin_state(17) is True

    # 3. Завершаем захват кадра: пины установки 1 автоматически переводятся в самое актуальное желаемое (17=True, 27=False)
    allow_finish_capture.set()
    sched_thread.join(timeout=3.0)
    assert not capture_errors
    assert hw.get_pin_state(17) is True
    assert hw.get_pin_state(27) is False


def test_relay_test_restores_effective_desired_states(tmp_path):
    """RELAY_TEST возвращает пины строго в целевое состояние из иерархии (в т.ч. при смене во время теста)."""
    orch, client, hw = _make_orchestrator(tmp_path)
    # Изначально в железе было свет=True, но сервер прислал initial_states с global_light_mode='off' -> 17=False, 27=True
    hw.set_light(17, True, source="manual")
    hw.set_flash(27, False, source="manual")

    cmd = CommandItem(
        id=91,
        command_type="RELAY_TEST",
        is_global=True,
        installations=[
            CommandInstallation(
                id=1, light_gpio_pin=17, flash_gpio_pin=27, camera_hardware_ids=[]
            )
        ],
        payload={"initial_states": {"17": False, "27": True}},
    )
    orch._handle_relay_test(cmd, duration=0.0)
    assert client.results[91].status == "done"
    assert hw.get_pin_state(17) is False
    assert hw.get_pin_state(27) is True
    assert hw.get_desired_pin_state(17) is False
    assert hw.get_desired_pin_state(27) is True
