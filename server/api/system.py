import contextlib
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from server.api.deps import (
    format_utc_z,
    get_current_user_optional,
    get_db,
    require_role,
)
from server.core.bot_info import get_telegram_bot_username
from server.core.system_status import is_rpi_online
from server.db.models import Camera, SystemSetting, User
from shared.version import get_app_version

router = APIRouter(tags=["system"])


class SystemSettingsPatch(BaseModel):
    """Тело запроса PATCH /api/system/settings."""

    model_config = ConfigDict(extra="ignore")

    capture_interval_hours: int | None = Field(default=None, strict=True, ge=1)
    debug_mode: bool | None = Field(default=None, strict=True)
    capture_delay_sec: float | None = Field(default=None, ge=0.0, le=30.0)


@router.get("/api/version", response_model=dict[str, Any])
def get_version(
    _client_user: Annotated[Any, Depends(get_current_user_optional)],
) -> dict[str, Any]:
    """4.6. GET /api/version.

    Публичный эндпоинт версии сервера для всех клиентов.
    """
    return {
        "server_version": get_app_version(),
        "bot_username": get_telegram_bot_username(),
    }


def is_raspberry_pi() -> bool:
    """Определяет, запущено ли приложение на физической плате Raspberry Pi."""
    has_model = False
    with contextlib.suppress(OSError):
        model_p = Path("/proc/device-tree/model")
        if model_p.exists():
            content = model_p.read_text(encoding="utf-8", errors="ignore").lower()
            if "raspberry pi" in content:
                has_model = True

    if not has_model:
        with contextlib.suppress(OSError):
            fw_p = Path("/sys/firmware/devicetree/base/model")
            if fw_p.exists():
                content = fw_p.read_text(encoding="utf-8", errors="ignore").lower()
                if "raspberry pi" in content:
                    has_model = True

    if not has_model:
        return False

    return importlib.util.find_spec("gpiozero") is not None


@router.get("/api/system/status", response_model=dict[str, Any])
def get_system_status(
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """4.6. GET /api/system/status (Developer).

    Версия сервера, статус подключения RPi, актуальные настройки и телеметрия.
    """
    # 1. Телеметрия RPi и статус подключения
    online, hb_dt = is_rpi_online(db)
    rpi_connection = "online" if online else "offline"
    rpi_last_heartbeat_at = format_utc_z(hb_dt) if hb_dt else None

    telem_setting = (
        db.query(SystemSetting)
        .filter(SystemSetting.key == "rpi_last_telemetry")
        .first()
    )
    rpi_telemetry = None
    if telem_setting and telem_setting.value:
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

    delay_setting = (
        db.query(SystemSetting).filter(SystemSetting.key == "capture_delay_sec").first()
    )
    capture_delay_sec = (
        float(delay_setting.value) if delay_setting and delay_setting.value else 1.5
    )

    hw_info = rpi_telemetry.get("hardware") if isinstance(rpi_telemetry, dict) else None
    hw_mode = (
        rpi_telemetry.get("hardware_mode") if isinstance(rpi_telemetry, dict) else None
    ) or (hw_info.get("gpio_mode") if isinstance(hw_info, dict) else None)

    real_rpi = False
    if online and (
        hw_mode == "rpi"
        or (
            isinstance(rpi_telemetry, dict)
            and bool(rpi_telemetry.get("is_raspberry_pi"))
        )
        or is_raspberry_pi()
    ):
        real_rpi = True

    if (
        rpi_telemetry
        and isinstance(rpi_telemetry, dict)
        and "hardware_mode" not in rpi_telemetry
    ):
        if hw_mode:
            rpi_telemetry["hardware_mode"] = hw_mode
        else:
            rpi_telemetry["hardware_mode"] = "rpi" if real_rpi else "mock"

    # 3. Сводка по камерам
    all_cameras = db.query(Camera).all()
    cam_total = len(all_cameras)
    cam_online = sum(1 for c in all_cameras if c.is_online)
    cam_bound = sum(1 for c in all_cameras if c.installation_id is not None)
    cameras_summary = {
        "total": cam_total,
        "online": cam_online,
        "offline": cam_total - cam_online,
        "bound": cam_bound,
        "unbound": cam_total - cam_bound,
    }

    return {
        "server_version": get_app_version(),
        "rpi_connection": rpi_connection,
        "rpi_last_heartbeat_at": rpi_last_heartbeat_at,
        "is_real_rpi": real_rpi,
        "cameras_summary": cameras_summary,
        "settings": {
            "capture_interval_hours": capture_interval_hours,
            "debug_mode": debug_mode,
            "capture_delay_sec": capture_delay_sec,
        },
        "rpi_telemetry": rpi_telemetry,
    }


@router.get("/api/system/settings", response_model=dict[str, Any])
def get_system_settings(
    current_user: Annotated[
        User, Depends(require_role("guest", "biologist", "developer"))
    ],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """4.6. GET /api/system/settings.

    Возвращает актуальные глобальные настройки системы.
    """
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

    delay_setting = (
        db.query(SystemSetting).filter(SystemSetting.key == "capture_delay_sec").first()
    )
    capture_delay_sec = (
        float(delay_setting.value) if delay_setting and delay_setting.value else 1.5
    )

    return {
        "capture_interval_hours": capture_interval_hours,
        "debug_mode": debug_mode,
        "capture_delay_sec": capture_delay_sec,
    }


@router.patch("/api/system/settings", response_model=dict[str, Any])
def patch_system_settings(
    body: SystemSettingsPatch,
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """4.6. PATCH /api/system/settings (Developer).

    Изменение интервала автосъёмки, режима отладки и задержки съёмки.
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

    # 3. Задержка съёмки
    delay_setting = (
        db.query(SystemSetting).filter(SystemSetting.key == "capture_delay_sec").first()
    )
    current_delay = (
        float(delay_setting.value) if delay_setting and delay_setting.value else 1.5
    )

    if body.capture_delay_sec is not None:
        current_delay = round(float(body.capture_delay_sec), 2)
        val_str = str(current_delay)
        if delay_setting:
            delay_setting.value = val_str
            delay_setting.updated_at = now
        else:
            db.add(
                SystemSetting(
                    key="capture_delay_sec",
                    value=val_str,
                    updated_at=now,
                )
            )

    resp["capture_delay_sec"] = current_delay

    db.commit()

    return resp
