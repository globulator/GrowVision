import math
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from server.api.deps import (
    ClientUser,
    get_current_user_optional,
    get_db,
    require_role,
)
from server.core.climate import (
    ClimateMode,
    EspAction,
    EspDevice,
    confirm_esp_command,
    enqueue_manual_command,
    esp_queue,
    format_utc_iso,
    get_climate_history,
    get_climate_summary,
    record_ph_reading,
    record_sensor_reading,
    set_climate_mode,
    touch_esp_seen,
    update_climate_schedule,
)
from server.db.models import User

esp_router = APIRouter(tags=["esp"])
climate_router = APIRouter(prefix="/api/climate", tags=["climate"])


class EspSensorsRequest(BaseModel):
    """Тело запроса POST /api/esp/sensors от ESP8266."""

    model_config = ConfigDict(extra="ignore")

    temperature: float
    humidity: float

    @field_validator("temperature", "humidity")
    @classmethod
    def validate_finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("Значение датчика должно быть конечным числом")
        return v


class EspCommandAckRequest(BaseModel):
    """Тело запроса POST /api/esp/command (подтверждение выполнения команды ESP)."""

    model_config = ConfigDict(extra="ignore")

    id: int = Field(ge=1)


class ClimateModeRequest(BaseModel):
    """Тело запроса POST /api/climate/mode."""

    model_config = ConfigDict(extra="ignore")

    mode: ClimateMode


class ClimateControlRequest(BaseModel):
    """Тело запроса POST /api/climate/control (только water и fan, без light)."""

    model_config = ConfigDict(extra="ignore")

    device: EspDevice
    action: EspAction


class ClimateScheduleRequest(BaseModel):
    """Тело запроса POST /api/climate/schedule."""

    model_config = ConfigDict(extra="ignore")

    water_interval_hours: int | None = Field(default=None, ge=1)
    water_duration_minutes: int | None = Field(default=None, ge=1)
    fan_interval_hours: int | None = Field(default=None, ge=1)
    fan_duration_minutes: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_any_field(self) -> "ClimateScheduleRequest":
        if (
            self.water_interval_hours is None
            and self.water_duration_minutes is None
            and self.fan_interval_hours is None
            and self.fan_duration_minutes is None
        ):
            raise ValueError("Необходимо указать хотя бы один параметр расписания")
        return self


class PhReadingRequest(BaseModel):
    """Тело запроса POST /api/climate/ph."""

    model_config = ConfigDict(extra="ignore")

    level: float = Field(ge=0.0, le=14.0)
    recorded_at: datetime | None = None

    @field_validator("level")
    @classmethod
    def validate_finite_level(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("Уровень pH должен быть конечным числом")
        return v


# ---------------------------------------------------------------------------
# ESP8266 HTTP API (совместимо с прошивкой GreenEyeAI)
# ---------------------------------------------------------------------------


@esp_router.get("/ping", response_model=dict[str, str])
def esp_ping(db: Annotated[Session, Depends(get_db)]) -> dict[str, str]:
    """Проверка доступности сервера от ESP и обновление метки esp_last_seen."""
    touch_esp_seen(db)
    db.commit()
    return {"status": "ok"}


@esp_router.post("/api/esp/sensors", response_model=dict[str, Any])
def esp_post_sensors(
    body: EspSensorsRequest,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Приём показаний температуры и влажности от ESP SHT31."""
    reading = record_sensor_reading(db, body.temperature, body.humidity)
    return {
        "status": "ok",
        "id": reading.id,
        "recorded_at": format_utc_iso(reading.recorded_at),
        "temperature": reading.temperature,
        "humidity": reading.humidity,
    }


@esp_router.get("/api/esp/command", response_model=dict[str, Any])
def esp_get_command(db: Annotated[Session, Depends(get_db)]) -> dict[str, Any]:
    """Отдаёт первую команду очереди в строгом формате прошивки ESP."""
    touch_esp_seen(db)
    db.commit()
    return esp_queue.peek_payload()


@esp_router.post("/api/esp/command", response_model=dict[str, Any])
def esp_ack_command(
    body: EspCommandAckRequest,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Подтверждает выполнение головы очереди команд ESP по id."""
    cmd = confirm_esp_command(db, body.id)
    if cmd is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Команда не совпадает с головой очереди или очередь пуста",
        )
    return {
        "status": "ok",
        "id": cmd.id,
        "device": cmd.device,
        "action": cmd.action,
    }


# ---------------------------------------------------------------------------
# Клиентский REST API (Web UI, Telegram-бот, CLI)
# ---------------------------------------------------------------------------


@climate_router.get("/summary", response_model=dict[str, Any])
def climate_summary(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[ClientUser, Depends(get_current_user_optional)],
) -> dict[str, Any]:
    """Сводка состояния ESP, датчиков, pH, полива, вентиляции и расписания."""
    return get_climate_summary(db)


@climate_router.get("/history", response_model=dict[str, Any])
def climate_history(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[ClientUser, Depends(get_current_user_optional)],
    period: Literal["24h", "7d"] = Query(default="24h"),
) -> dict[str, Any]:
    """История показаний климата и переключений периферии за 24h или 7d."""
    return get_climate_history(db, period=period)


@climate_router.post("/mode", response_model=dict[str, Any])
def climate_set_mode(
    body: ClimateModeRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_role("biologist", "developer"))],
) -> dict[str, Any]:
    """Переключение режима работы полива и вентиляции (auto / manual)."""
    set_climate_mode(db, body.mode)
    db.commit()
    return get_climate_summary(db)


@climate_router.post("/control", response_model=dict[str, Any])
def climate_manual_control(
    body: ClimateControlRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_role("biologist", "developer"))],
) -> dict[str, Any]:
    """Ручное переключение полива или вентиляции (запрещено в режиме auto -> 409)."""
    try:
        cmd = enqueue_manual_command(db, body.device, body.action)
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    return {
        "status": "queued",
        "command_id": cmd.id,
        "device": cmd.device,
        "action": cmd.action,
        "queue_size": esp_queue.size(),
    }


@climate_router.post("/schedule", response_model=dict[str, Any])
def climate_update_schedule(
    body: ClimateScheduleRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_role("biologist", "developer"))],
) -> dict[str, Any]:
    """Сохранение интервалов и длительности полива и вентиляции."""
    update_climate_schedule(
        db,
        water_interval_hours=body.water_interval_hours,
        water_duration_minutes=body.water_duration_minutes,
        fan_interval_hours=body.fan_interval_hours,
        fan_duration_minutes=body.fan_duration_minutes,
    )
    db.commit()
    return get_climate_summary(db)


@climate_router.post("/ph", response_model=dict[str, Any])
def climate_add_ph(
    body: PhReadingRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_role("biologist", "developer"))],
) -> dict[str, Any]:
    """Ручное добавление замера pH (0..14)."""
    reading = record_ph_reading(db, body.level, recorded_at=body.recorded_at)
    return {
        "status": "ok",
        "id": reading.id,
        "level": reading.level,
        "recorded_at": format_utc_iso(reading.recorded_at),
    }
