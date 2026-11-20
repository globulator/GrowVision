from pathlib import Path

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


def test_mock_relay_test(mock_hardware: HardwareManager):
    """Проверка кратковременного теста реле без реального sleep."""
    pins = [17, 27]
    mock_hardware.test_relay(pins, duration=0.0)

    # После завершения теста все пины должны быть выключены
    assert mock_hardware.get_pin_state(17) is False
    assert mock_hardware.get_pin_state(27) is False


def test_rpi_mode_without_gpiozero_raises_error(monkeypatch: pytest.MonkeyPatch):
    """В режиме HARDWARE_MODE=rpi отсутствие gpiozero вызывает падение с ошибкой."""
    import sys

    # Имитируем отсутствие gpiozero
    monkeypatch.setitem(sys.modules, "gpiozero", None)
    with pytest.raises(
        RuntimeError, match="В режиме HARDWARE_MODE=rpi необходима библиотека gpiozero"
    ):
        HardwareManager(mode="rpi")


def test_scan_cameras_by_id_directory(tmp_path: Path):
    """Проверка сканирования камер через /dev/v4l/by-id (*-video-index0)."""
    by_id_dir = tmp_path / "by-id"
    by_id_dir.mkdir()

    # Создаем тестовые файлы
    cam1 = by_id_dir / "usb-Digma_DiCam_300_SN01-video-index0"
    cam1.touch()
    cam2 = by_id_dir / "usb-Logitech_C270_ABC-video-index0"
    cam2.touch()
    # Файл с index1 не должен попадать в список
    not_cam = by_id_dir / "usb-Digma_DiCam_300_SN01-video-index1"
    not_cam.touch()

    hw = HardwareManager(mode="mock", v4l_by_id_dir=by_id_dir)
    scanned = hw.scan_cameras(v4l_by_id_dir=by_id_dir)

    assert len(scanned) == 2
    hw_ids = [c.hardware_id for c in scanned]
    assert "usb-Digma_DiCam_300_SN01-video-index0" in hw_ids
    assert "usb-Logitech_C270_ABC-video-index0" in hw_ids
    assert "usb-Digma_DiCam_300_SN01-video-index1" not in hw_ids


def test_capture_unknown_camera_hardware_id_raises_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """В боевом режиме захват кадра с неизвестным hardware_id вызывает ошибку."""
    import sys
    from unittest.mock import MagicMock

    monkeypatch.setitem(sys.modules, "cv2", MagicMock())
    hw = HardwareManager(mode="mock", v4l_by_id_dir=tmp_path)
    hw.mode = "rpi"
    with pytest.raises(RuntimeError, match="не найдена"):
        hw.capture_frame("non_existent_camera_hw_id")


def test_capture_frame_with_sequence(mock_hardware: HardwareManager):
    """Проверка последовательности съемки: выключение света, включение вспышки, возврат света."""
    # Исходное состояние: свет включен, вспышка выключена
    mock_hardware.set_light(17, True)
    mock_hardware.set_flash(27, False)

    frame_bytes = mock_hardware.capture_frame_with_sequence(
        camera_hardware_id="mock-usb-cam-1",
        light_pin=17,
        flash_pin=27,
        warmup_frames=0,
        switch_delay_ms=0,
    )

    assert frame_bytes.startswith(b"\xff\xd8")
    assert frame_bytes.endswith(b"\xff\xd9")

    # В итоге свет должен остаться ВКЛЮЧЕННЫМ, а вспышка ВЫКЛЮЧЕННОЙ
    assert mock_hardware.get_pin_state(17) is True
    assert mock_hardware.get_pin_state(27) is False


def test_hardware_mutex(mock_hardware: HardwareManager):
    """Проверка работы аппаратного мьютекса."""
    assert mock_hardware.acquire_lock(blocking=False) is True
    assert mock_hardware.acquire_lock(blocking=False) is False
    mock_hardware.release_lock()
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
