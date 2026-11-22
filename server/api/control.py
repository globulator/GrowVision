import asyncio
import json
import time
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from server.api.deps import format_utc_z, get_current_user, get_db, require_role
from server.core.area import calculate_area_display
from server.core.state import app_state
from server.core.system_status import (
    check_rpi_online,
    is_debug_mode,
    validate_target_value,
)
from server.db.models import Capture, EdgeCommand, Installation, User

router = APIRouter(prefix="/api/control", tags=["control"])


class SnapRequest(BaseModel):
    """Тело запроса POST /api/control/snap."""

    model_config = ConfigDict(extra="ignore")

    target: int | Literal["all"]

    @field_validator("target", mode="before")
    @classmethod
    def validate_target(cls, v: Any) -> int | Literal["all"]:
        return validate_target_value(v)


class LightRequest(BaseModel):
    """Тело запроса POST /api/control/light/main и /flash."""

    model_config = ConfigDict(extra="ignore")

    target: int | Literal["all"]
    state: bool = Field(..., strict=True)

    @field_validator("target", mode="before")
    @classmethod
    def validate_target(cls, v: Any) -> int | Literal["all"]:
        return validate_target_value(v)


class RelayTestRequest(BaseModel):
    """Тело запроса POST /api/control/relay-test."""

    model_config = ConfigDict(extra="ignore")

    target: int | Literal["all"]

    @field_validator("target", mode="before")
    @classmethod
    def validate_target(cls, v: Any) -> int | Literal["all"]:
        return validate_target_value(v)


def validate_target_and_resolve(
    target: int | Literal["all"], db: Session
) -> tuple[int | None, bool, int]:
    """Проверяет target и возвращает (installation_id, is_global, active_count)."""
    active_count = (
        db.query(Installation).filter(Installation.is_archived.is_(False)).count()
    )

    if target == "all":
        if active_count == 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Нет активных установок",
            )
        return None, True, active_count

    inst = db.query(Installation).filter(Installation.id == target).first()
    if not inst:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Установка не найдена",
        )
    if inst.is_archived:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Установка в архиве",
        )
    return inst.id, False, active_count


async def wait_command_result(
    cmd_id: int, timeout_sec: float, db: Session
) -> EdgeCommand:
    """Ожидает завершения команды RPi с таймаутом. Сразу 504 при timeout."""
    start_time = time.monotonic()
    while time.monotonic() - start_time < timeout_sec:
        await asyncio.sleep(0.05)
        db.expire_all()
        cmd = db.query(EdgeCommand).filter(EdgeCommand.id == cmd_id).first()
        if not cmd:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Команда не найдена",
            )
        if cmd.status == "timeout":
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="Превышено время ожидания ответа Raspberry Pi",
            )
        if cmd.status in ("done", "error"):
            return cmd

    # По истечении ожидания
    db.expire_all()
    cmd = db.query(EdgeCommand).filter(EdgeCommand.id == cmd_id).first()
    if cmd and cmd.status == "pending":
        cmd.status = "timeout"
        db.commit()

    raise HTTPException(
        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        detail="Превышено время ожидания ответа Raspberry Pi",
    )


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


@router.post("/snap", response_model=dict[str, Any])
async def control_snap(
    body: SnapRequest,
    current_user: Annotated[User, Depends(require_role("biologist", "developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """4.4. POST /api/control/snap (Biologist, Developer)."""
    # 1. Проверка target (404/409)
    inst_id, is_global, active_count = validate_target_and_resolve(body.target, db)

    # 2. Проверка онлайна RPi (503)
    check_rpi_online(db)

    # Ожидание: 15 с для одиночной, 15 + 10 * N для "all"
    timeout_sec = 15.0 + 10.0 * active_count if is_global else 15.0

    cmd = EdgeCommand(
        command_type="SNAP",
        installation_id=inst_id,
        is_global=is_global,
        payload=None,
        status="pending",
    )
    db.add(cmd)
    db.commit()
    db.refresh(cmd)

    finished_cmd = await wait_command_result(cmd.id, timeout_sec, db)

    resp: dict[str, Any] = {
        "command_id": finished_cmd.id,
        "status": finished_cmd.status,
        "result_message": finished_cmd.result_message or "",
        "captures": [],
    }

    if finished_cmd.status == "error":
        app_state.pop_captures_by_command(finished_cmd.id)
        debug_info = build_debug_info(finished_cmd, current_user, db)
        if debug_info:
            resp["debug_info"] = debug_info
        return resp

    # Получение ID созданных снимков и очистка app_state
    capture_ids = app_state.pop_captures_by_command(finished_cmd.id)
    captures = (
        db.query(Capture)
        .filter(Capture.id.in_(capture_ids))
        .order_by(Capture.captured_at.asc(), Capture.id.asc())
        .all()
        if capture_ids
        else []
    )

    captures_data = []
    for cap in captures:
        meas = next(
            (m for m in cap.plant_measurements if m.plant_index == 0),
            None,
        )
        area_display = calculate_area_display(
            meas.area_px if meas else None,
            cap.aruco_area_px,
            cap.installation.aruco_size_mm if cap.installation else None,
        )
        captures_data.append(
            {
                "capture_id": cap.id,
                "installation_id": cap.installation_id,
                "camera_id": cap.camera_id,
                "captured_at": format_utc_z(cap.captured_at),
                "raw_url": f"/{cap.raw_image_path}",
                "annotated_url": f"/{cap.annotated_image_path}",
                "area_display": area_display,
            }
        )

    resp["captures"] = captures_data
    debug_info = build_debug_info(finished_cmd, current_user, db)
    if debug_info:
        resp["debug_info"] = debug_info

    return resp


@router.get("/captures/{capture_id}", response_model=dict[str, Any])
def get_capture_info(
    capture_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Возвращает информацию о снимке (для переключения слоев исходник/разметка)."""
    cap = db.query(Capture).filter(Capture.id == capture_id).first()
    if not cap:
        raise HTTPException(status_code=404, detail="Снимок не найден")
    return {
        "id": cap.id,
        "installation_id": cap.installation_id,
        "camera_id": cap.camera_id,
        "captured_at": format_utc_z(cap.captured_at),
        "raw_url": f"/{cap.raw_image_path}",
        "annotated_url": f"/{cap.annotated_image_path}",
    }


@router.post("/light/main", response_model=dict[str, Any])
async def control_light_main(
    body: LightRequest,
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """4.4. POST /api/control/light/main (Developer)."""
    # 1. Проверка target
    inst_id, is_global, _ = validate_target_and_resolve(body.target, db)

    # 2. Проверка онлайна RPi
    check_rpi_online(db)

    cmd = EdgeCommand(
        command_type="LIGHT_MAIN",
        installation_id=inst_id,
        is_global=is_global,
        payload=json.dumps({"state": body.state}),
        status="pending",
    )
    db.add(cmd)
    db.commit()
    db.refresh(cmd)

    finished_cmd = await wait_command_result(cmd.id, 10.0, db)

    if finished_cmd.status == "done":
        if is_global:
            all_insts = (
                db.query(Installation).filter(Installation.is_archived.is_(False)).all()
            )
            for item in all_insts:
                app_state.set_pin_state(item.light_gpio_pin, body.state)
        elif inst_id:
            inst = db.query(Installation).filter(Installation.id == inst_id).first()
            if inst:
                app_state.set_pin_state(inst.light_gpio_pin, body.state)

    resp: dict[str, Any] = {
        "command_id": finished_cmd.id,
        "status": finished_cmd.status,
        "result_message": finished_cmd.result_message or "OK",
        "pin_states": {str(k): v for k, v in app_state.get_all_pin_states().items()},
    }
    debug_info = build_debug_info(finished_cmd, current_user, db)
    if debug_info:
        resp["debug_info"] = debug_info
    return resp


@router.post("/light/flash", response_model=dict[str, Any])
async def control_light_flash(
    body: LightRequest,
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """4.4. POST /api/control/light/flash (Developer)."""
    # 1. Проверка target
    inst_id, is_global, _ = validate_target_and_resolve(body.target, db)

    # 2. Проверка онлайна RPi
    check_rpi_online(db)

    cmd = EdgeCommand(
        command_type="LIGHT_FLASH",
        installation_id=inst_id,
        is_global=is_global,
        payload=json.dumps({"state": body.state}),
        status="pending",
    )
    db.add(cmd)
    db.commit()
    db.refresh(cmd)

    finished_cmd = await wait_command_result(cmd.id, 10.0, db)

    if finished_cmd.status == "done":
        if is_global:
            all_insts = (
                db.query(Installation).filter(Installation.is_archived.is_(False)).all()
            )
            for item in all_insts:
                app_state.set_pin_state(item.flash_gpio_pin, body.state)
        elif inst_id:
            inst = db.query(Installation).filter(Installation.id == inst_id).first()
            if inst:
                app_state.set_pin_state(inst.flash_gpio_pin, body.state)

    resp: dict[str, Any] = {
        "command_id": finished_cmd.id,
        "status": finished_cmd.status,
        "result_message": finished_cmd.result_message or "OK",
        "pin_states": {str(k): v for k, v in app_state.get_all_pin_states().items()},
    }
    debug_info = build_debug_info(finished_cmd, current_user, db)
    if debug_info:
        resp["debug_info"] = debug_info
    return resp


@router.post("/relay-test", response_model=dict[str, Any])
async def control_relay_test(
    body: RelayTestRequest,
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """4.4. POST /api/control/relay-test (Developer)."""
    # 1. Проверка target
    inst_id, is_global, _ = validate_target_and_resolve(body.target, db)

    # 2. Проверка онлайна RPi
    check_rpi_online(db)

    cmd = EdgeCommand(
        command_type="RELAY_TEST",
        installation_id=inst_id,
        is_global=is_global,
        payload=None,
        status="pending",
    )
    db.add(cmd)
    db.commit()
    db.refresh(cmd)

    finished_cmd = await wait_command_result(cmd.id, 10.0, db)

    resp: dict[str, Any] = {
        "command_id": finished_cmd.id,
        "status": finished_cmd.status,
        "result_message": finished_cmd.result_message or "OK",
    }
    debug_info = build_debug_info(finished_cmd, current_user, db)
    if debug_info:
        resp["debug_info"] = debug_info
    return resp


@router.get("/relays", response_model=dict[str, Any])
def get_relays_status(
    current_user: Annotated[User, Depends(require_role("developer"))],
) -> dict[str, Any]:
    """Возвращает текущие программные состояния всех GPIO-пинов."""
    return {"pins": {str(k): v for k, v in app_state.get_all_pin_states().items()}}


@router.get("/relays/{installation_id}", response_model=dict[str, Any])
def get_installation_relays(
    installation_id: int,
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Возвращает программные состояния реле конкретной установки."""
    inst = db.query(Installation).filter(Installation.id == installation_id).first()
    if not inst:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Установка не найдена",
        )
    return {
        "installation_id": inst.id,
        "light_gpio_pin": inst.light_gpio_pin,
        "light_state": app_state.get_pin_state(inst.light_gpio_pin),
        "flash_gpio_pin": inst.flash_gpio_pin,
        "flash_state": app_state.get_pin_state(inst.flash_gpio_pin),
    }
