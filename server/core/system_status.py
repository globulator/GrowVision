import json
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from server.db.models import EdgeCommand, SystemSetting, User


def is_rpi_online(db: Session) -> tuple[bool, datetime | None]:
    """Проверяет подключение Raspberry Pi по updated_at записи rpi_last_telemetry.

    RPi считается онлайн, если updated_at не старше 90 секунд (в UTC).
    Возвращает кортеж: (is_online, last_heartbeat_datetime_utc).
    """
    telem_setting = (
        db.query(SystemSetting)
        .filter(SystemSetting.key == "rpi_last_telemetry")
        .first()
    )
    if not telem_setting or not telem_setting.updated_at:
        return False, None

    updated_dt = telem_setting.updated_at
    if updated_dt.tzinfo is None:
        updated_dt = updated_dt.replace(tzinfo=UTC)
    else:
        updated_dt = updated_dt.astimezone(UTC)

    diff_sec = (datetime.now(UTC) - updated_dt).total_seconds()
    online = diff_sec <= 35.0
    return online, updated_dt


def check_rpi_online(db: Session) -> None:
    """Выбрасывает 503, если Raspberry Pi офлайн."""
    online, _ = is_rpi_online(db)
    if not online:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Raspberry Pi офлайн",
        )


def is_debug_mode(db: Session) -> bool:
    """Возвращает признак включения debug_mode в system_settings."""
    setting = db.query(SystemSetting).filter(SystemSetting.key == "debug_mode").first()
    return setting.value.lower() == "true" if setting and setting.value else False


def validate_target_value(v: Any) -> int | Literal["all"]:
    """Валидатор поля target для запросов управления."""
    if isinstance(v, bool):
        raise ValueError("target не может быть boolean")  # noqa: TRY004
    if isinstance(v, int):
        if v <= 0:
            raise ValueError("ID установки должен быть положительным числом")
        return v
    if v == "all":
        return "all"
    raise ValueError("target должен быть целым числом или 'all'")


def build_debug_info(
    cmd: EdgeCommand, user: User, db: Session
) -> dict[str, Any] | None:
    """Формирует debug_info для Developer при debug_mode=True."""
    if user.role != "developer" or not is_debug_mode(db):
        return None

    payload_data = None
    if cmd.payload:
        try:
            payload_data = json.loads(cmd.payload)
        except (json.JSONDecodeError, TypeError):
            payload_data = cmd.payload

    return {
        "command": {
            "id": cmd.id,
            "command_type": cmd.command_type,
            "target": "all" if cmd.is_global else cmd.installation_id,
            "payload": payload_data,
        },
        "log_line": cmd.debug_log or "",
    }
