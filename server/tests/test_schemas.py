import pytest
from pydantic import ValidationError

from shared.schemas import (
    CaptureMeta,
    Command,
    CommandInstallation,
    CommandItem,
    CommandResult,
    DetectedCamera,
    Heartbeat,
    HeartbeatRequest,
    HeartbeatResponse,
    PlantMeasurementMeta,
)


def test_heartbeat_request_validation():
    """Проверяет валидацию Heartbeat / HeartbeatRequest по API_CONTRACT.md разд. 3.1."""
    data = {
        "software": {
            "edge_version": "0.2.0-a3f9c12",
            "python_version": "3.11.2",
            "model_name": "lettuce_seg_v1.onnx",
        },
        "hardware": {
            "board_model": "Raspberry Pi 5 Model B Rev 1.0",
            "os_info": "Debian GNU/Linux 12 (bookworm)",
            "uptime_sec": 84210,
            "cpu_temp_c": 48.5,
            "cpu_usage_percent": 14.2,
            "ram_used_mb": 612,
            "ram_total_mb": 8062,
            "disk_free_gb": 18.4,
            "gpio_mode": "rpi",
        },
        "offline_queue_count": 2,
        "detected_cameras": [
            {
                "hardware_id": "usb-Digma_DiCam_300_SN01-video-index0",
                "auto_name": "Digma DiCam 300",
            }
        ],
    }

    hb = Heartbeat.model_validate(data)
    assert hb.software.edge_version == "0.2.0-a3f9c12"
    assert hb.hardware.uptime_sec == 84210
    assert hb.offline_queue_count == 2
    assert len(hb.detected_cameras) == 1
    assert hb.detected_cameras[0].hardware_id == "usb-Digma_DiCam_300_SN01-video-index0"

    # Проверка синонима
    hb2 = HeartbeatRequest.model_validate(data)
    assert hb2.software.model_name == "lettuce_seg_v1.onnx"


def test_heartbeat_response_validation():
    """Проверяет валидацию HeartbeatResponse."""
    data = {
        "capture_interval_hours": 1,
        "debug_mode": False,
        "active_installations": [
            {
                "id": 1,
                "light_gpio_pin": 17,
                "flash_gpio_pin": 27,
                "camera_hardware_ids": ["usb-cam-1"],
            }
        ],
    }
    resp = HeartbeatResponse.model_validate(data)
    assert resp.capture_interval_hours == 1
    assert resp.debug_mode is False
    assert len(resp.active_installations) == 1
    assert resp.active_installations[0].light_gpio_pin == 17


def test_command_validation():
    """Проверяет валидацию Command / CommandItem по API_CONTRACT.md разд. 3.2."""
    data = {
        "id": 42,
        "command_type": "SNAP",
        "is_global": False,
        "installation": {
            "id": 1,
            "light_gpio_pin": 17,
            "flash_gpio_pin": 27,
            "camera_hardware_ids": ["cam_01"],
        },
        "installations": None,
        "payload": None,
    }
    cmd = Command.model_validate(data)
    assert cmd.id == 42
    assert cmd.command_type == "SNAP"
    assert cmd.is_global is False
    assert isinstance(cmd.installation, CommandInstallation)
    assert cmd.installation.id == 1

    # Синоним
    cmd2 = CommandItem.model_validate(data)
    assert cmd2.id == 42


def test_command_global_validation():
    """Проверяет глобальную команду с массивом установок."""
    data = {
        "id": 43,
        "command_type": "LIGHT_MAIN",
        "is_global": True,
        "installation": None,
        "installations": [
            {
                "id": 1,
                "light_gpio_pin": 17,
                "flash_gpio_pin": 27,
                "camera_hardware_ids": ["cam_01"],
            },
            {
                "id": 2,
                "light_gpio_pin": 22,
                "flash_gpio_pin": 23,
                "camera_hardware_ids": ["cam_02"],
            },
        ],
        "payload": {"state": True},
    }
    cmd = Command.model_validate(data)
    assert cmd.is_global is True
    assert len(cmd.installations) == 2
    assert cmd.payload == {"state": True}


def test_command_result_validation():
    """Проверяет валидацию отчета о выполнении команды."""
    data = {
        "status": "done",
        "result_message": "Снимок выполнен успешно",
        "debug_log_line": "[2026-11-18T19:30:02Z] [INFO] [GPIO] test",
        "scanned_cameras": [{"hardware_id": "usb-cam", "auto_name": "Camera USB"}],
    }
    res = CommandResult.model_validate(data)
    assert res.status == "done"
    assert len(res.scanned_cameras) == 1
    assert isinstance(res.scanned_cameras[0], DetectedCamera)

    # Невалидный статус
    with pytest.raises(ValidationError):
        CommandResult.model_validate(
            {
                "status": "invalid_status",
                "result_message": "test",
            }
        )


def test_capture_meta_validation():
    """Проверяет валидацию метаданных кадра по API_CONTRACT.md разд. 3.4."""
    data = {
        "installation_id": 1,
        "camera_hardware_id": "usb-Digma_DiCam_300_SN01-video-index0",
        "captured_at": "2026-11-18T19:00:05Z",
        "is_manual": False,
        "aruco_area_px": 4120.5,
        "command_id": None,
        "measurements": [{"plant_index": 0, "area_px": 15430.0}],
    }
    meta = CaptureMeta.model_validate(data)
    assert meta.installation_id == 1
    assert meta.aruco_area_px == 4120.5
    assert len(meta.measurements) == 1
    assert isinstance(meta.measurements[0], PlantMeasurementMeta)
    assert meta.measurements[0].area_px == 15430.0


def test_capture_meta_missing_required_fields():
    """Проверяет отклонение CaptureMeta без обязательных полей."""
    with pytest.raises(ValidationError):
        CaptureMeta.model_validate(
            {
                "installation_id": 1
                # отсутствует camera_hardware_id и captured_at
            }
        )


def test_measurement_and_aruco_bounds_validation():
    """Критерий: PlantMeasurementMeta.area_px: ge=0, CaptureMeta.aruco_area_px: gt=0.

    Значения -1 и 0 для aruco_area_px и -1 для area_px вызывают ValidationError.
    """
    # 1. PlantMeasurementMeta: 0 разрешено, -1 вызывает ValidationError
    m_valid = PlantMeasurementMeta(plant_index=0, area_px=0.0)
    assert m_valid.area_px == 0.0

    with pytest.raises(ValidationError):
        PlantMeasurementMeta(plant_index=0, area_px=-1.0)

    # 2. CaptureMeta: aruco_area_px=None разрешено, >0 разрешено
    c_none = CaptureMeta(
        installation_id=1,
        camera_hardware_id="cam_01",
        captured_at="2026-11-18T19:00:00Z",
        aruco_area_px=None,
    )
    assert c_none.aruco_area_px is None

    c_valid = CaptureMeta(
        installation_id=1,
        camera_hardware_id="cam_01",
        captured_at="2026-11-18T19:00:00Z",
        aruco_area_px=100.5,
    )
    assert c_valid.aruco_area_px == 100.5

    # 0 и -1 для aruco_area_px вызывают ValidationError
    with pytest.raises(ValidationError):
        CaptureMeta(
            installation_id=1,
            camera_hardware_id="cam_01",
            captured_at="2026-11-18T19:00:00Z",
            aruco_area_px=0.0,
        )

    with pytest.raises(ValidationError):
        CaptureMeta(
            installation_id=1,
            camera_hardware_id="cam_01",
            captured_at="2026-11-18T19:00:00Z",
            aruco_area_px=-1.0,
        )
