"""Тесты этапа 12: RGB PWM-драйвер, нейтральный белый при съёмке и радужный RELAY_TEST."""

import logging
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from edge_agent.config import EdgeConfig
from edge_agent.hardware import MOCK_JPEG_FRAME, HardwareManager
from edge_agent.orchestrator import EdgeOrchestrator
from shared.schemas import CommandInstallation, CommandItem


def _make_config(tmp_path: Path) -> EdgeConfig:
    return EdgeConfig(
        server_url="http://127.0.0.1:8000",
        edge_api_key="test-key",
        hardware_mode="mock",
        mock_cameras=True,
        offline_queue_dir=tmp_path / "queue",
    )


def test_mock_rgb_pwm_and_rpi_device_switching(monkeypatch: pytest.MonkeyPatch, caplog):
    """Проверка PWM-значений RGB в mock-режиме и переключения OutputDevice<->PWMOutputDevice в rpi."""
    hw = HardwareManager(mode="mock", mock_cameras=True)
    with caplog.at_level(logging.INFO):
        hw.set_light([17, 27, 22], True, source="manual", color="#FF8000")

    assert hw.get_mock_pwm(17) == 1.0
    assert 0.50 <= hw.get_mock_pwm(27) <= 0.51
    assert hw.get_mock_pwm(22) == 0.0
    assert hw.get_pin_state(17) is True
    assert hw.get_pin_state(22) is False
    assert any("color=#FF8000" in r.message for r in caplog.records)

    # Выключение переводит ШИМ в 0, но запоминает желаемый цвет
    hw.set_light([17, 27, 22], False, source="manual")
    assert hw.get_mock_pwm(17) == 0.0
    assert hw.get_desired_channel_color([17, 27, 22]) == "#FF8000"

    # Включение без передачи цвета применяет запомненный #FF8000
    hw.set_light([17, 27, 22], True, source="manual")
    assert hw.get_mock_pwm(17) == 1.0
    assert 0.50 <= hw.get_mock_pwm(27) <= 0.51
    hw.cleanup()

    # Проверка переключения устройств без конфликта в rpi-режиме (фейковый gpiozero)
    created_output: list[int] = []
    closed_output: list[int] = []
    created_pwm: list[int] = []
    closed_pwm: list[int] = []

    class FakeOutputDevice:
        def __init__(self, pin, active_high=True, initial_value=False):
            self.pin = pin
            self.is_active = bool(initial_value)
            created_output.append(pin)

        def on(self):
            self.is_active = True

        def off(self):
            self.is_active = False

        def close(self):
            closed_output.append(self.pin)

    class FakePWMOutputDevice:
        def __init__(self, pin, active_high=True, initial_value=0.0, frequency=1000):
            assert frequency == 1000
            self.pin = pin
            self.value = float(initial_value)
            created_pwm.append(pin)

        def off(self):
            self.value = 0.0

        def close(self):
            closed_pwm.append(self.pin)

    fake_gpiozero = types.ModuleType("gpiozero")
    fake_gpiozero.OutputDevice = FakeOutputDevice
    fake_gpiozero.PWMOutputDevice = FakePWMOutputDevice
    monkeypatch.setitem(sys.modules, "gpiozero", fake_gpiozero)

    rpi_hw = HardwareManager(mode="rpi", relay_active_high=True)
    rpi_hw.set_pin(17, True, role="light")
    assert 17 in rpi_hw._gpio_devices

    # Переключаем пин 17 в PWM RGB-ленту — старый OutputDevice закрывается
    rpi_hw.set_rgb_pins([17, 27, 22], True, color="#00FF00", role="light")
    assert 17 in closed_output
    assert 17 not in rpi_hw._gpio_devices
    assert 17 in rpi_hw._pwm_devices
    assert rpi_hw._pwm_devices[27].value == 1.0

    # Обратно в одиночный OutputDevice — PWMOutputDevice закрывается
    rpi_hw.set_pin(17, False, role="light")
    assert 17 in closed_pwm
    assert 17 not in rpi_hw._pwm_devices
    assert 17 in rpi_hw._gpio_devices
    rpi_hw.cleanup()


def test_capture_uses_neutral_white_flash_and_restores_saved_hex(tmp_path: Path):
    """При съёмке вспышка принудительно #FFFFFF, а в finally возвращаются актуальные состояния и HEX."""
    cfg = _make_config(tmp_path)
    hw = HardwareManager(mode="mock", mock_cameras=True)
    client = MagicMock()
    orch = EdgeOrchestrator(config=cfg, client=client, hardware=hw)

    light_pins = [17, 27, 22]
    flash_pins = [5, 6, 13]

    # Задаём свет ON (#00AAFF) и вспышку OFF (#FF00FF)
    hw.set_light(light_pins, True, source="manual", color="#00AAFF")
    hw.set_flash(flash_pins, False, source="manual", color="#FF00FF")

    observed_during_capture: dict[str, tuple[float, float, float]] = {}

    def fake_capture_frame(cam_hw_id: str, **kwargs) -> bytes:
        observed_during_capture["light"] = (
            hw.get_mock_pwm(17),
            hw.get_mock_pwm(27),
            hw.get_mock_pwm(22),
        )
        observed_during_capture["flash"] = (
            hw.get_mock_pwm(5),
            hw.get_mock_pwm(6),
            hw.get_mock_pwm(13),
        )
        # Симулируем конкурентную команду смены цвета света во время съёмки
        hw.set_desired_channel_state(
            light_pins, True, color="#FF2200", role="light", apply_if_idle=True
        )
        return MOCK_JPEG_FRAME

    hw.capture_frame = fake_capture_frame  # type: ignore[method-assign]

    frame = hw.capture_frame_with_sequence(
        camera_hardware_id="mock-usb-cam-1",
        light_pin=light_pins,
        flash_pin=flash_pins,
        light_color="#00AAFF",
        flash_color="#FF00FF",
    )
    assert frame == MOCK_JPEG_FRAME

    # Во время съёмки свет выключен, вспышка строго нейтрально белая #FFFFFF (1,1,1)
    assert observed_during_capture["light"] == (0.0, 0.0, 0.0)
    assert observed_during_capture["flash"] == (1.0, 1.0, 1.0)

    # После съёмки свет вернулся в актуальный цвет #FF2200, вспышка выключилась и помнит #FF00FF
    assert hw.get_mock_pwm(17) == 1.0
    assert 0.12 <= hw.get_mock_pwm(27) <= 0.14
    assert hw.get_mock_pwm(22) == 0.0
    assert hw.get_mock_pwm(5) == 0.0
    assert hw.get_desired_channel_color(flash_pins) == "#FF00FF"

    # Проверка _capture_via_live_snapshot тоже использует #FFFFFF и восстанавливает цвет
    orch.live_stream = MagicMock()
    orch.live_stream.process = object()
    live_observed: dict[str, tuple[float, float, float]] = {}

    def fake_live_snap(cam_id: str) -> bytes:
        live_observed["flash"] = (
            hw.get_mock_pwm(5),
            hw.get_mock_pwm(6),
            hw.get_mock_pwm(13),
        )
        return MOCK_JPEG_FRAME

    orch.live_stream.capture_ustreamer_snapshot = fake_live_snap
    inst = CommandInstallation(
        id=1,
        light_gpio_pin="17,27,22",
        flash_gpio_pin="5,6,13",
        light_pins=light_pins,
        flash_pins=flash_pins,
        light_color="#FF2200",
        flash_color="#FF00FF",
        light_state=True,
        flash_state=False,
    )
    snap_bytes = orch._capture_via_live_snapshot(inst, "mock-usb-cam-1", 0.0)
    assert snap_bytes == MOCK_JPEG_FRAME
    assert live_observed["flash"] == (1.0, 1.0, 1.0)
    assert hw.get_mock_pwm(5) == 0.0
    assert hw.get_mock_pwm(17) == 1.0
    assert hw.get_desired_channel_color(flash_pins) == "#FF00FF"
    hw.cleanup()


def test_relay_test_plays_rainbow_spectrum_and_restores_hex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """RELAY_TEST плавно прогоняет спектр радуги для RGB-лент и восстанавливает состояние и HEX."""
    cfg = _make_config(tmp_path)
    hw = HardwareManager(mode="mock", mock_cameras=True)
    client = MagicMock()
    orch = EdgeOrchestrator(config=cfg, client=client, hardware=hw)

    light_pins = [17, 27, 22]
    flash_pin = 26

    hw.set_light(light_pins, True, source="manual", color="#11AA44")
    hw.set_flash(flash_pin, False, source="manual")

    recorded_colors: list[str] = []
    orig_set_rgb = hw.set_rgb_pins

    def spy_set_rgb(pins, state, color="#FFFFFF", role="light", source="manual"):
        if source == "relay_test_rainbow":
            recorded_colors.append(color)
        return orig_set_rgb(pins, state, color=color, role=role, source=source)

    monkeypatch.setattr(hw, "set_rgb_pins", spy_set_rgb)

    cmd = CommandItem(
        id=99,
        command_type="RELAY_TEST",
        is_global=False,
        installation=CommandInstallation(
            id=1,
            light_gpio_pin="17,27,22",
            flash_gpio_pin=26,
            light_pins=light_pins,
            flash_pins=[26],
            light_color="#11AA44",
            flash_color="#FFFFFF",
            light_state=True,
            flash_state=False,
        ),
        payload={
            "initial_states": {"17": True, "27": True, "22": True, "26": False},
            "channels": [
                {
                    "installation_id": 1,
                    "role": "light",
                    "pins": light_pins,
                    "state": True,
                    "color": "#11AA44",
                },
                {
                    "installation_id": 1,
                    "role": "flash",
                    "pins": [26],
                    "state": False,
                    "color": "#FFFFFF",
                },
            ],
        },
    )

    orch._handle_relay_test(cmd, duration=0.0)

    # Спектр радуги проигран от красного до фиолетового
    assert len(recorded_colors) >= 12
    assert recorded_colors[0] == "#FF0000"
    assert recorded_colors[-1] == "#FF00FF"

    # Исходное состояние и HEX-цвет #11AA44 полностью восстановлены
    assert hw.get_pin_state(26) is False
    assert hw.get_pin_state(17) is True
    assert hw._channel_colors[tuple(light_pins)] == "#11AA44"
    assert abs(hw.get_mock_pwm(17) - round(0x11 / 255.0, 4)) < 0.01
    assert abs(hw.get_mock_pwm(27) - round(0xAA / 255.0, 4)) < 0.01
    assert abs(hw.get_mock_pwm(22) - round(0x44 / 255.0, 4)) < 0.01

    client.send_command_result.assert_called_once()
    hw.cleanup()
