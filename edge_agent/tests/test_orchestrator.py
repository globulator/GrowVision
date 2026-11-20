import pytest

from edge_agent.config import EdgeConfig
from edge_agent.hardware import HardwareManager
from edge_agent.offline_queue import get_queue_count
from edge_agent.orchestrator import EdgeOrchestrator
from edge_agent.vision import VisionPipeline
from shared.schemas import (
    CommandInstallation,
    CommandItem,
    CommandResult,
    HeartbeatActiveInstallation,
)


class DummyEdgeClient:
    """Эмулятор Edge API клиента для юнит-тестов оркестратора."""

    def __init__(self):
        self.heartbeat_calls = []
        self.claim_calls = []
        self.results = {}
        self.uploaded_captures = []
        self.fail_network_upload = False

    def send_heartbeat(self, telemetry):
        self.heartbeat_calls.append(telemetry)

    def claim_command(self):
        self.claim_calls.append(True)
        return []

    def send_command_result(self, command_id: int, result: CommandResult, **_kwargs):
        self.results[command_id] = result
        return True

    def upload_capture(self, metadata, raw_bytes, annotated_bytes):
        if self.fail_network_upload:
            return 0, {"error": "Connection error"}
        self.uploaded_captures.append((metadata, raw_bytes, annotated_bytes))
        return 201, {"capture_id": 99}


@pytest.fixture
def dummy_client() -> DummyEdgeClient:
    return DummyEdgeClient()


@pytest.fixture
def orchestrator(
    test_config: EdgeConfig,
    dummy_client: DummyEdgeClient,
    mock_hardware: HardwareManager,
    mock_vision: VisionPipeline,
) -> EdgeOrchestrator:
    return EdgeOrchestrator(
        config=test_config,
        client=dummy_client,
        hardware=mock_hardware,
        vision=mock_vision,
    )


def test_init_installations_light_turns_main_light_on(
    orchestrator: EdgeOrchestrator,
    mock_hardware: HardwareManager,
):
    """При старте основной свет активных установок переводится в состояние ВКЛ."""
    orchestrator.active_installations = [
        HeartbeatActiveInstallation(
            id=1,
            light_gpio_pin=17,
            flash_gpio_pin=27,
            camera_hardware_ids=["cam-1"],
        )
    ]
    orchestrator.init_installations_light()
    assert mock_hardware.get_pin_state(17) is True
    assert mock_hardware.get_pin_state(27) is False


def test_handle_command_light_main(
    orchestrator: EdgeOrchestrator,
    dummy_client: DummyEdgeClient,
    mock_hardware: HardwareManager,
):
    """Проверка выполнения команды LIGHT_MAIN."""
    cmd = CommandItem(
        id=1,
        command_type="LIGHT_MAIN",
        is_global=False,
        installation=CommandInstallation(
            id=10,
            light_gpio_pin=17,
            flash_gpio_pin=27,
            camera_hardware_ids=["cam-1"],
        ),
        payload={"state": True},
    )

    orchestrator.handle_command(cmd)

    assert mock_hardware.get_pin_state(17) is True
    assert 1 in dummy_client.results
    res = dummy_client.results[1]
    assert res.status == "done"
    assert "успешно включен" in res.result_message


def test_handle_command_relay_test_zero_duration(
    orchestrator: EdgeOrchestrator,
    dummy_client: DummyEdgeClient,
):
    """Проверка выполнения команды RELAY_TEST с нулевой задержкой."""
    cmd = CommandItem(
        id=3,
        command_type="RELAY_TEST",
        is_global=False,
        installation=CommandInstallation(
            id=10,
            light_gpio_pin=17,
            flash_gpio_pin=27,
            camera_hardware_ids=["cam-1"],
        ),
    )

    orchestrator._handle_relay_test(cmd, duration=0.0)

    assert 3 in dummy_client.results
    res = dummy_client.results[3]
    assert res.status == "done"
    assert "успешно завершен" in res.result_message


def test_handle_command_snap_multiple_cameras_debug_log(
    orchestrator: EdgeOrchestrator,
    dummy_client: DummyEdgeClient,
):
    """Проверка SNAP: снимает все камеры установки, формирует строку [GPIO] Inst#N... в debug_log_line."""
    cmd = CommandItem(
        id=5,
        command_type="SNAP",
        is_global=False,
        installation=CommandInstallation(
            id=10,
            light_gpio_pin=17,
            flash_gpio_pin=27,
            camera_hardware_ids=["mock-usb-cam-1", "mock-csi-cam-2"],
        ),
    )

    orchestrator.handle_command(cmd)

    # Должно быть отправлено 2 кадра (по 1 для каждой камеры)
    assert len(dummy_client.uploaded_captures) == 2

    assert 5 in dummy_client.results
    res = dummy_client.results[5]
    assert res.status == "done"
    assert "2 кадр" in res.result_message
    assert res.debug_log_line is not None
    assert "[GPIO] Inst#10: light=0, flash=1 -> captured 2 frames" in res.debug_log_line


def test_handle_command_mutex_conflict_scheduled_capture_priority(
    orchestrator: EdgeOrchestrator,
    dummy_client: DummyEdgeClient,
):
    """Проверка: при активной плановой съемке ручная команда мгновенно отклоняется."""
    orchestrator.is_scheduled_capture_running.set()

    cmd = CommandItem(
        id=6,
        command_type="SNAP",
        is_global=False,
        installation=CommandInstallation(
            id=10,
            light_gpio_pin=17,
            flash_gpio_pin=27,
            camera_hardware_ids=["mock-usb-cam-1"],
        ),
    )

    orchestrator.handle_command(cmd)

    assert len(dummy_client.uploaded_captures) == 0
    assert 6 in dummy_client.results
    res = dummy_client.results[6]
    assert res.status == "error"
    assert res.result_message == "Камера недоступна, попробуйте позже"


def test_scheduled_capture_buffered_to_queue_on_network_error(
    orchestrator: EdgeOrchestrator,
    dummy_client: DummyEdgeClient,
    test_config: EdgeConfig,
):
    """Проверка: при сетевом сбое плановый снимок сохраняется в офлайн-очередь."""
    dummy_client.fail_network_upload = True
    orchestrator.active_installations = [
        HeartbeatActiveInstallation(
            id=10,
            light_gpio_pin=17,
            flash_gpio_pin=27,
            camera_hardware_ids=["mock-usb-cam-1"],
        )
    ]

    assert get_queue_count(test_config.offline_queue_dir) == 0

    orchestrator.trigger_scheduled_capture()

    assert get_queue_count(test_config.offline_queue_dir) == 1
