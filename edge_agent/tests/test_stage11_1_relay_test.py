import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from edge_agent.config import EdgeConfig
from edge_agent.hardware import HardwareManager
from edge_agent.orchestrator import EdgeOrchestrator
from shared.schemas import CommandInstallation, CommandItem, CommandResult


class DummyEdgeClient:
    def __init__(self) -> None:
        self.results: dict[int, CommandResult] = {}

    def send_command_result(self, command_id: int, res: CommandResult) -> bool:
        self.results[command_id] = res
        return True


def test_rpi_relay_test_does_not_reset_uninit_pin_and_restores_states(
    monkeypatch: pytest.MonkeyPatch,
):
    """RELAY_TEST в rpi-режиме не сбрасывает пины до чтения и возвращает исходные состояния."""
    created_devices: dict[int, MagicMock] = {}

    def fake_output_device(
        pin: int, active_high: bool = True, initial_value: bool = False
    ):
        dev = MagicMock()
        dev.is_active = bool(initial_value)
        dev.initial_value = initial_value
        dev.on.side_effect = lambda: setattr(dev, "is_active", True)
        dev.off.side_effect = lambda: setattr(dev, "is_active", False)
        created_devices[pin] = dev
        return dev

    monkeypatch.setitem(
        sys.modules,
        "gpiozero",
        SimpleNamespace(OutputDevice=fake_output_device),
    )

    hw = HardwareManager(mode="rpi")
    # Пин 17 не инициализирован физически до теста, но по данным сервера это свет ON;
    # пин 27 — вспышка OFF.
    assert hw.get_pin_state(17) is False
    assert 17 not in created_devices  # get_pin_state не должен создавать OutputDevice

    hw._pin_roles[17] = "light"
    hw._pin_roles[27] = "flash"

    restored = hw.test_relay(
        [17, 27],
        duration=0.0,
        initial_states={17: True, 27: False},
    )

    assert restored == {17: True, 27: False}
    assert hw.get_pin_state(17) is True
    assert hw.get_pin_state(27) is False
    assert hw._pin_roles[17] == "light"
    assert hw._pin_roles[27] == "flash"
    assert created_devices[17].is_active is True
    assert created_devices[27].is_active is False


def test_relay_test_preserves_off_light_and_restores_on_exception(
    mock_hardware: HardwareManager,
    monkeypatch: pytest.MonkeyPatch,
):
    """Если свет был OFF — после RELAY_TEST он остаётся OFF; при сбое sleep finally возвращает пины."""
    mock_hardware.set_light(17, False, source="manual")
    mock_hardware.set_flash(27, False, source="manual")

    def boom(_sec: float) -> None:
        raise RuntimeError("sleep interrupted")

    monkeypatch.setattr("edge_agent.hardware.time.sleep", boom)

    with pytest.raises(RuntimeError, match="sleep interrupted"):
        mock_hardware.test_relay([17, 27], duration=0.5)

    assert mock_hardware.get_pin_state(17) is False
    assert mock_hardware.get_pin_state(27) is False
    assert mock_hardware._pin_roles[17] == "light"
    assert mock_hardware._pin_roles[27] == "flash"


def test_orchestrator_relay_test_uses_payload_initial_states_and_keeps_roles(
    tmp_path,
):
    """Оркестратор при RELAY_TEST учитывает initial_states от сервера и отдаёт pin_states в result."""
    cfg = EdgeConfig(
        server_url="http://127.0.0.1:9999",
        edge_api_key="test-key",
        hardware_mode="mock",
        mock_cameras=True,
        offline_queue_dir=tmp_path / "q",
    )
    hw = HardwareManager(mode="mock", mock_cameras=True)
    client = DummyEdgeClient()
    orch = EdgeOrchestrator(config=cfg, client=client, hardware=hw)

    # Пин 17 выключен, пин 27 выключен; затем свет включён (17=True, 27=False)
    cmd_on = CommandItem(
        id=11,
        command_type="RELAY_TEST",
        is_global=True,
        installations=[
            CommandInstallation(
                id=1,
                light_gpio_pin=17,
                flash_gpio_pin=27,
                camera_hardware_ids=[],
            )
        ],
        payload={"initial_states": {"17": True, "27": False}},
    )

    orch._handle_relay_test(cmd_on, duration=0.0)

    assert hw.get_pin_state(17) is True
    assert hw.get_pin_state(27) is False
    assert hw._pin_roles[17] == "light"
    assert hw._pin_roles[27] == "flash"
    res = client.results[11]
    assert res.status == "done"
    assert res.data == {"pin_states": {"17": True, "27": False}}

    # Теперь основной свет был выключен вручную: OFF -> после теста остаётся OFF
    hw.set_light(17, False, source="manual")
    cmd_off = CommandItem(
        id=12,
        command_type="RELAY_TEST",
        is_global=True,
        installations=[
            CommandInstallation(
                id=1,
                light_gpio_pin=17,
                flash_gpio_pin=27,
                camera_hardware_ids=[],
            )
        ],
        payload={"initial_states": {"17": False, "27": False}},
    )
    orch._handle_relay_test(cmd_off, duration=0.0)

    assert hw.get_pin_state(17) is False
    assert hw.get_pin_state(27) is False
    assert client.results[12].data == {"pin_states": {"17": False, "27": False}}
