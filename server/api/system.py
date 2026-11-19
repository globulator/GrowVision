import json
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from server.api.deps import format_utc_z, get_db, require_role
from server.db.models import SystemSetting, User
from shared.version import get_app_version

router = APIRouter(tags=["system"])


class SystemSettingsPatch(BaseModel):
    """Тело запроса PATCH /api/system/settings."""

    model_config = ConfigDict(extra="ignore")

    capture_interval_hours: int | None = Field(default=None, strict=True, ge=1)
    debug_mode: bool | None = Field(default=None, strict=True)


@router.get("/api/version", response_model=dict[str, str])
def get_version() -> dict[str, str]:
    """4.6. GET /api/version.

    Публичный эндпоинт версии сервера для всех клиентов.
    """
    return {"server_version": get_app_version()}


@router.get("/api/system/status", response_model=dict[str, Any])
def get_system_status(
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """4.6. GET /api/system/status (Developer).

    Версия сервера, статус подключения RPi, актуальные настройки и телеметрия.
    """
    now = datetime.now(UTC)

    # 1. Телеметрия RPi и статус подключения
    telem_setting = (
        db.query(SystemSetting)
        .filter(SystemSetting.key == "rpi_last_telemetry")
        .first()
    )

    rpi_connection = "offline"
    rpi_last_heartbeat_at = None
    rpi_telemetry = None

    if telem_setting:
        hb_dt = (
            telem_setting.updated_at
            if telem_setting.updated_at.tzinfo
            else telem_setting.updated_at.replace(tzinfo=UTC)
        )
        if (now - hb_dt).total_seconds() <= 90.0:
            rpi_connection = "online"

        rpi_last_heartbeat_at = format_utc_z(telem_setting.updated_at)
        try:
            rpi_telemetry = json.loads(telem_setting.value)
        except json.JSONDecodeError:
            rpi_telemetry = None

    # 2. Настройки
    interval_setting = (
        db.query(SystemSetting)
        .filter(SystemSetting.key == "capture_interval_hours")
        .first()
    )
    capture_interval_hours = (
        int(interval_setting.value)
        if interval_setting and interval_setting.value.isdigit()
        else 1
    )

    debug_setting = (
        db.query(SystemSetting).filter(SystemSetting.key == "debug_mode").first()
    )
    debug_mode = debug_setting.value.lower() == "true" if debug_setting else False

    return {
        "server_version": get_app_version(),
        "rpi_connection": rpi_connection,
        "rpi_last_heartbeat_at": rpi_last_heartbeat_at,
        "settings": {
            "capture_interval_hours": capture_interval_hours,
            "debug_mode": debug_mode,
        },
        "rpi_telemetry": rpi_telemetry,
    }


@router.patch("/api/system/settings", response_model=dict[str, Any])
def patch_system_settings(
    body: SystemSettingsPatch,
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """4.6. PATCH /api/system/settings (Developer).

    Изменение интервала автосъёмки и режима отладки.
    """
    now = datetime.now(UTC)
    resp: dict[str, Any] = {}

    # 1. Интервал съёмки
    interval_setting = (
        db.query(SystemSetting)
        .filter(SystemSetting.key == "capture_interval_hours")
        .first()
    )
    current_interval = (
        int(interval_setting.value)
        if interval_setting and interval_setting.value.isdigit()
        else 1
    )

    if body.capture_interval_hours is not None:
        if body.capture_interval_hours != current_interval:
            resp["warning"] = (
                f"Интервал съёмки изменён с {current_interval} на {body.capture_interval_hours} ч: "
                "шаг таймлапса станет неравномерным."
            )
        current_interval = body.capture_interval_hours
        if interval_setting:
            interval_setting.value = str(current_interval)
            interval_setting.updated_at = now
        else:
            db.add(
                SystemSetting(
                    key="capture_interval_hours",
                    value=str(current_interval),
                    updated_at=now,
                )
            )

    resp["capture_interval_hours"] = current_interval

    # 2. Режим отладки
    debug_setting = (
        db.query(SystemSetting).filter(SystemSetting.key == "debug_mode").first()
    )
    current_debug = debug_setting.value.lower() == "true" if debug_setting else False

    if body.debug_mode is not None:
        current_debug = body.debug_mode
        val_str = "true" if current_debug else "false"
        if debug_setting:
            debug_setting.value = val_str
            debug_setting.updated_at = now
        else:
            db.add(
                SystemSetting(
                    key="debug_mode",
                    value=val_str,
                    updated_at=now,
                )
            )

    resp["debug_mode"] = current_debug

    db.commit()

    return resp
