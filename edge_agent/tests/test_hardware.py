import pytest

from edge_agent.hardware import HardwareManager, get_hardware_telemetry
from shared.schemas import HeartbeatHardware


def test_mock_gpio_control(mock_hardware: HardwareManager):
    """Проверка управления виртуальными пинами GPIO."""
    assert mock_hardware.get_pin_state(17) is False

    mock_hardware.set_light(17, True)
    assert mock_hardware.get_pin_state(17) is True

    mock_hardware.set_light(17, False)
    assert mock_hardware.get_pin_state(17) is False

    mock_hardware.set_flash(27, True)
    assert mock_hardware.get_pin_state(27) is True

    mock_hardware.set_flash(27, False)
    assert mock_hardware.get_pin_state(27) is False


def test_mock_relay_test(
    mock_hardware: HardwareManager, monkeypatch: pytest.MonkeyPatch
):
    """Проверка кратковременного теста реле."""
    slept = []
    monkeypatch.setattr("time.sleep", lambda s: slept.append(s))

    pins = [17, 27]
    mock_hardware.test_relay(pins, duration=3.0)

    assert slept == [3.0]
    # После завершения теста все пины должны быть выключены
    assert mock_hardware.get_pin_state(17) is False
    assert mock_hardware.get_pin_state(27) is False


def test_mock_camera_scanning(mock_hardware: HardwareManager):
    """Проверка сканирования камер в mock-режиме."""
    cameras = mock_hardware.scan_cameras()
    assert len(cameras) >= 1
    hw_ids = [c.hardware_id for c in cameras]
    assert "mock-usb-cam-1" in hw_ids


def test_mock_capture_frame(mock_hardware: HardwareManager):
    """Проверка захвата кадра со вспышкой в mock-режиме."""
    frame_bytes = mock_hardware.capture_frame("mock-usb-cam-1", flash_pin=27)
    assert isinstance(frame_bytes, bytes)
    assert len(frame_bytes) > 0
    # Проверка сигнатуры JPEG (SOI 0xFFD8 ... EOI 0xFFD9)
    assert frame_bytes.startswith(b"\xff\xd8")
    assert frame_bytes.endswith(b"\xff\xd9")
    # После захвата вспышка должна быть выключена
    assert mock_hardware.get_pin_state(27) is False


def test_hardware_mutex(mock_hardware: HardwareManager):
    """Проверка работы аппаратного мьютекса."""
    assert mock_hardware.acquire_lock(blocking=False) is True
    # Повторный неблокирующий захват должен вернуть False
    assert mock_hardware.acquire_lock(blocking=False) is False

    mock_hardware.release_lock()
    # После освобождения снова доступен
    assert mock_hardware.acquire_lock(blocking=False) is True
    mock_hardware.release_lock()


def test_hardware_telemetry():
    """Проверка сбора аппаратной телеметрии."""
    telemetry = get_hardware_telemetry(gpio_mode="mock")
    assert isinstance(telemetry, HeartbeatHardware)
    assert telemetry.gpio_mode == "mock"
    assert telemetry.uptime_sec >= 0
    assert telemetry.cpu_temp_c > 0
    assert 0.0 <= telemetry.cpu_usage_percent <= 100.0
    assert telemetry.ram_used_mb > 0
    assert telemetry.ram_total_mb > 0
    assert telemetry.disk_free_gb >= 0
    assert len(telemetry.board_model) > 0
    assert len(telemetry.os_info) > 0
