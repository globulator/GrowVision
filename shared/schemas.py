from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DetectedCamera(BaseModel):
    """Информация об обнаруженной камере."""

    model_config = ConfigDict(extra="ignore")

    hardware_id: str
    auto_name: str


class HeartbeatSoftware(BaseModel):
    """Информация о версиях ПО на Edge-агенте."""

    model_config = ConfigDict(extra="ignore")

    edge_version: str
    python_version: str
    model_name: str


class HeartbeatHardware(BaseModel):
    """Аппаратная телеметрия Raspberry Pi."""

    model_config = ConfigDict(extra="ignore")

    board_model: str
    os_info: str
    uptime_sec: int
    cpu_temp_c: float
    cpu_usage_percent: float
    ram_used_mb: int
    ram_total_mb: int
    disk_free_gb: float
    gpio_mode: str


class HeartbeatRequest(BaseModel):
    """Тело запроса POST /api/edge/heartbeat."""

    model_config = ConfigDict(extra="ignore")

    software: HeartbeatSoftware
    hardware: HeartbeatHardware
    offline_queue_count: int = 0
    detected_cameras: list[DetectedCamera] = Field(default_factory=list)


# Алиас для краткого обращения
Heartbeat = HeartbeatRequest


class HeartbeatActiveInstallation(BaseModel):
    """Конфигурация активной установки в ответе на heartbeat."""

    model_config = ConfigDict(extra="ignore")

    id: int
    light_gpio_pin: int
    flash_gpio_pin: int
    camera_hardware_ids: list[str] = Field(default_factory=list)


class HeartbeatResponse(BaseModel):
    """Тело ответа 200 OK на POST /api/edge/heartbeat."""

    model_config = ConfigDict(extra="ignore")

    capture_interval_hours: int
    debug_mode: bool
    active_installations: list[HeartbeatActiveInstallation] = Field(
        default_factory=list
    )


class CommandInstallation(BaseModel):
    """Параметры установки в очереди команд."""

    model_config = ConfigDict(extra="ignore")

    id: int
    light_gpio_pin: int
    flash_gpio_pin: int
    camera_hardware_ids: list[str] = Field(default_factory=list)


class CommandItem(BaseModel):
    """Элемент массива ответа POST /api/edge/commands/claim."""

    model_config = ConfigDict(extra="ignore")

    id: int
    command_type: str
    is_global: bool = False
    installation: CommandInstallation | None = None
    installations: list[CommandInstallation] | None = None
    payload: dict[str, Any] | None = None


# Алиас для краткого обращения
Command = CommandItem


class CommandResult(BaseModel):
    """Тело запроса POST /api/edge/commands/{command_id}/result."""

    model_config = ConfigDict(extra="ignore")

    status: Literal["done", "error"]
    result_message: str
    debug_log_line: str | None = None
    scanned_cameras: list[DetectedCamera] | None = None


class PlantMeasurementMeta(BaseModel):
    """Замер площади одного растения на кадре."""

    model_config = ConfigDict(extra="ignore")

    plant_index: int = 0
    area_px: float


class CaptureMeta(BaseModel):
    """Метаданные снимка для POST /api/edge/captures (поле metadata)."""

    model_config = ConfigDict(extra="ignore")

    installation_id: int
    camera_hardware_id: str
    captured_at: datetime | str
    is_manual: bool = False
    aruco_area_px: float | None = None
    command_id: int | None = None
    measurements: list[PlantMeasurementMeta] = Field(default_factory=list)


# Алиас для совместимости
CaptureMetadata = CaptureMeta
