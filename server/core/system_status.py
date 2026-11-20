from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from server.db.models import SystemSetting


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
    online = diff_sec <= 90.0
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
