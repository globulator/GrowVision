import asyncio
import json
import time
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from server.api import edge as edge_api
from server.api.deps import format_utc_z, get_current_user, get_db, require_role
from server.core.area import calculate_area_display
from server.core.light_state import (
    LightMode,
    compute_active_pin_states,
    compute_effective_state,
    get_global_light_mode,
    get_individual_light_state,
    get_installation_light_snapshot,
    set_global_light_mode,
    set_individual_light_state,
    sync_app_state_pins,
)
from server.core.rgb import resolve_capture_display_rel_path
from server.core.state import EphemeralSnapshot, app_state
from server.core.system_status import (
    check_rpi_online,
    is_debug_mode,
    validate_target_value,
)
from server.db.models import Capture, EdgeCommand, Installation, SystemSetting, User

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
    state: bool | None = Field(default=None, strict=True)
    mode: LightMode | None = None

    @field_validator("target", mode="before")
    @classmethod
    def validate_target(cls, v: Any) -> int | Literal["all"]:
        return validate_target_value(v)

    @model_validator(mode="after")
    def validate_state_or_mode(self) -> "LightRequest":
        if self.state is None and self.mode is None:
            raise ValueError("Необходимо указать state или mode")
        if self.target != "all" and self.state is None:
            if self.mode in ("on", "off"):
                self.state = self.mode == "on"
            else:
                raise ValueError(
                    "Для конкретной установки необходимо указать state (true/false)"
                )
        return self


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


def _serialize_ephemeral_snapshot(snap: EphemeralSnapshot) -> dict[str, Any]:
    """Сериализует эфемерный тестовый снимок из памяти для ответа клиентам."""
    return {
        "id": snap.snapshot_id,
        "snapshot_id": snap.snapshot_id,
        "capture_id": snap.snapshot_id,
        "ephemeral": True,
        "installation_id": snap.installation_id,
        "camera_id": snap.camera_id,
        "camera_hardware_id": snap.camera_hardware_id,
        "captured_at": snap.captured_at,
        "raw_url": f"/api/control/test-snapshots/{snap.snapshot_id}/raw",
        "annotated_url": f"/api/control/test-snapshots/{snap.snapshot_id}/annotated",
        "area_display": snap.area_display,
        "measurements": snap.measurements,
        "aruco_area_px": snap.aruco_area_px,
    }


def _build_test_snapshot_image_response(
    snapshot_id: str,
    layer: Literal["raw", "annotated"],
    download: bool,
) -> Response:
    """Возвращает JPEG-байты эфемерного тестового снимка из оперативной памяти."""
    snap = app_state.get_test_snapshot(snapshot_id)
    if snap is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Тестовый снимок не найден или истёк",
        )
    content = snap.raw_bytes if layer == "raw" else snap.annotated_bytes
    disposition = "attachment" if download else "inline"
    filename = f"test_snap_{snapshot_id}_{layer}.jpg"
    return Response(
        content=content,
        media_type="image/jpeg",
        headers={
            "Content-Disposition": f'{disposition}; filename="{filename}"',
            "Cache-Control": "no-store, no-cache, must-revalidate",
        },
    )


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
        app_state.pop_test_snapshots_by_command(finished_cmd.id, remove_snapshots=True)
        debug_info = build_debug_info(finished_cmd, current_user, db)
        if debug_info:
            resp["debug_info"] = debug_info
        return resp

    # Получаем эфемерные снимки из оперативной памяти (без записи в БД и media/)
    ephemeral_snapshots = app_state.pop_test_snapshots_by_command(finished_cmd.id)
    legacy_capture_ids = app_state.pop_captures_by_command(finished_cmd.id)

    captures_data: list[dict[str, Any]] = []
    if ephemeral_snapshots:
        ephemeral_snapshots.sort(key=lambda s: (s.captured_at, s.created_at))
        captures_data = [
            _serialize_ephemeral_snapshot(snap) for snap in ephemeral_snapshots
        ]
    elif legacy_capture_ids:
        captures = (
            db.query(Capture)
            .filter(Capture.id.in_(legacy_capture_ids))
            .order_by(Capture.captured_at.asc(), Capture.id.asc())
            .all()
        )
        for cap in captures:
            meas = next(
                (m for m in cap.plant_measurements if m.plant_index == 0),
                None,
            )
            area_display = calculate_area_display(
                meas.area_px if meas else None,
                cap.aruco_area_px,
                cap.installation.aruco_size_mm if cap.installation else None,
                area_cm2=meas.area_cm2 if meas else None,
            )
            captures_data.append(
                {
                    "capture_id": cap.id,
                    "ephemeral": False,
                    "installation_id": cap.installation_id,
                    "camera_id": cap.camera_id,
                    "captured_at": format_utc_z(cap.captured_at),
                    "raw_url": f"/{resolve_capture_display_rel_path(cap.raw_image_path, edge_api.get_media_dir())}",
                    "annotated_url": f"/{cap.annotated_image_path}",
                    "area_display": area_display,
                }
            )

    resp["captures"] = captures_data
    debug_info = build_debug_info(finished_cmd, current_user, db)
    if debug_info:
        resp["debug_info"] = debug_info

    return resp


@router.get("/test-snapshots/latest/{installation_id}", response_model=dict[str, Any])
def get_latest_test_snapshot(
    installation_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Возвращает метаданные последнего эфемерного снимка установки."""
    snap = app_state.get_latest_test_snapshot_by_installation(installation_id)
    if snap is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Тестовый снимок не найден или истёк",
        )
    return _serialize_ephemeral_snapshot(snap)


@router.get("/test-snapshots/{snapshot_id}", response_model=dict[str, Any])
def get_test_snapshot_info(
    snapshot_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Возвращает метаданные эфемерного тестового снимка по snapshot_id."""
    snap = app_state.get_test_snapshot(snapshot_id)
    if snap is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Тестовый снимок не найден или истёк",
        )
    return _serialize_ephemeral_snapshot(snap)


@router.get("/test-snapshots/{snapshot_id}/raw")
def get_test_snapshot_raw(
    snapshot_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    download: bool = Query(default=False),
) -> Response:
    """Отдаёт оригинальный JPEG тестового снимка из памяти (поддерживает download=1)."""
    return _build_test_snapshot_image_response(snapshot_id, "raw", download)


@router.get("/test-snapshots/{snapshot_id}/annotated")
def get_test_snapshot_annotated(
    snapshot_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    download: bool = Query(default=False),
) -> Response:
    """Отдаёт размеченный JPEG тестового снимка из памяти (поддерживает download=1)."""
    return _build_test_snapshot_image_response(snapshot_id, "annotated", download)


@router.get("/captures/{capture_id}", response_model=dict[str, Any])
def get_capture_info(
    capture_id: str,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Возвращает информацию о снимке (для переключения слоев исходник/разметка)."""
    snap = app_state.get_test_snapshot(capture_id)
    if snap is not None:
        return _serialize_ephemeral_snapshot(snap)

    if not capture_id.isdigit():
        raise HTTPException(status_code=404, detail="Снимок не найден")

    cap = db.query(Capture).filter(Capture.id == int(capture_id)).first()
    if not cap:
        raise HTTPException(status_code=404, detail="Снимок не найден")
    return {
        "id": cap.id,
        "installation_id": cap.installation_id,
        "camera_id": cap.camera_id,
        "captured_at": format_utc_z(cap.captured_at),
        "raw_url": f"/{resolve_capture_display_rel_path(cap.raw_image_path, edge_api.get_media_dir())}",
        "annotated_url": f"/{cap.annotated_image_path}",
    }


async def _execute_light_control(
    body: LightRequest,
    current_user: User,
    db: Session,
    *,
    is_flash: bool,
) -> dict[str, Any]:
    """Общий обработчик управления основным светом и вспышкой с учётом иерархии режимов."""
    inst_id, is_global, _ = validate_target_and_resolve(body.target, db)
    check_rpi_online(db)

    command_type = "LIGHT_FLASH" if is_flash else "LIGHT_MAIN"

    if is_global:
        next_mode: LightMode = (
            body.mode
            if body.mode is not None
            else ("on" if bool(body.state) else "off")
        )
        active_insts = (
            db.query(Installation)
            .filter(Installation.is_archived.is_(False))
            .order_by(Installation.id.asc())
            .all()
        )
        other_mode = get_global_light_mode(db, is_flash=not is_flash)
        g_light = other_mode if is_flash else next_mode
        g_flash = next_mode if is_flash else other_mode

        target_pin_states: dict[int, bool] = {}
        inst_states_map: dict[str, dict[str, Any]] = {}
        for item in active_insts:
            ind_light = get_individual_light_state(db, item.id, is_flash=False)
            ind_flash = get_individual_light_state(db, item.id, is_flash=True)
            eff_light = compute_effective_state(g_light, ind_light)
            eff_flash = compute_effective_state(g_flash, ind_flash)
            inst_states_map[str(item.id)] = {
                "light_gpio_pin": item.light_gpio_pin,
                "flash_gpio_pin": item.flash_gpio_pin,
                "light_state": eff_light,
                "flash_state": eff_flash,
            }
            if not is_flash:
                target_pin_states[item.light_gpio_pin] = eff_light
            elif item.flash_gpio_pin != item.light_gpio_pin:
                target_pin_states[item.flash_gpio_pin] = eff_flash

        cmd_payload: dict[str, Any] = {
            "mode": next_mode,
            "state": (next_mode == "on") if next_mode != "default" else None,
            "pin_states": {str(p): v for p, v in target_pin_states.items()},
            "installation_states": inst_states_map,
        }
        cmd = EdgeCommand(
            command_type=command_type,
            installation_id=None,
            is_global=True,
            payload=json.dumps(cmd_payload),
            status="pending",
        )
        db.add(cmd)
        db.commit()
        db.refresh(cmd)

        finished_cmd = await wait_command_result(cmd.id, 10.0, db)
        if finished_cmd.status == "done":
            set_global_light_mode(db, next_mode, is_flash=is_flash)
            db.commit()
            sync_app_state_pins(target_pin_states)

        resp: dict[str, Any] = {
            "command_id": finished_cmd.id,
            "status": finished_cmd.status,
            "result_message": finished_cmd.result_message or "OK",
            "mode": next_mode,
            "global_light_mode": get_global_light_mode(db, is_flash=False),
            "global_flash_mode": get_global_light_mode(db, is_flash=True),
            "pin_states": {
                str(k): v for k, v in app_state.get_all_pin_states().items()
            },
        }
    else:
        inst = db.query(Installation).filter(Installation.id == inst_id).first()
        if not inst:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Установка не найдена",
            )
        next_state = bool(body.state)
        global_mode = get_global_light_mode(db, is_flash=is_flash)
        eff_state = compute_effective_state(global_mode, next_state)
        target_pin = inst.flash_gpio_pin if is_flash else inst.light_gpio_pin
        target_pin_states = {target_pin: eff_state}

        cmd_payload = {
            "state": eff_state,
            "individual_state": next_state,
            "mode": global_mode,
            "pin_states": {str(target_pin): eff_state},
        }
        cmd = EdgeCommand(
            command_type=command_type,
            installation_id=inst_id,
            is_global=False,
            payload=json.dumps(cmd_payload),
            status="pending",
        )
        db.add(cmd)
        db.commit()
        db.refresh(cmd)

        finished_cmd = await wait_command_result(cmd.id, 10.0, db)
        if finished_cmd.status == "done":
            set_individual_light_state(db, inst.id, next_state, is_flash=is_flash)
            # Синхронизируем установки, разделяющие тот же физический GPIO-пин
            shared_query = db.query(Installation).filter(
                Installation.is_archived.is_(False),
                Installation.id != inst.id,
            )
            if is_flash:
                shared_query = shared_query.filter(
                    Installation.flash_gpio_pin == target_pin
                )
            else:
                shared_query = shared_query.filter(
                    Installation.light_gpio_pin == target_pin
                )
            for other in shared_query.all():
                set_individual_light_state(db, other.id, next_state, is_flash=is_flash)
            db.commit()
            sync_app_state_pins(target_pin_states)

        resp = {
            "command_id": finished_cmd.id,
            "status": finished_cmd.status,
            "result_message": finished_cmd.result_message or "OK",
            "individual_state": next_state,
            "effective_state": eff_state,
            "locked": global_mode != "default",
            "pin_states": {
                str(k): v for k, v in app_state.get_all_pin_states().items()
            },
        }

    debug_info = build_debug_info(finished_cmd, current_user, db)
    if debug_info:
        resp["debug_info"] = debug_info
    return resp


@router.post("/light/main", response_model=dict[str, Any])
async def control_light_main(
    body: LightRequest,
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """4.4. POST /api/control/light/main (Developer)."""
    return await _execute_light_control(body, current_user, db, is_flash=False)


@router.post("/light/flash", response_model=dict[str, Any])
async def control_light_flash(
    body: LightRequest,
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """4.4. POST /api/control/light/flash (Developer)."""
    return await _execute_light_control(body, current_user, db, is_flash=True)


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

    # Вычисляем актуальные целевые состояния пинов с учётом иерархии global_*_mode + индивидуальные
    target_insts = (
        db.query(Installation).filter(Installation.is_archived.is_(False)).all()
        if is_global
        else db.query(Installation).filter(Installation.id == inst_id).all()
    )
    computed_pins = compute_active_pin_states(db, target_insts)
    initial_states: dict[str, bool] = {
        str(pin): val for pin, val in computed_pins.items()
    }

    cmd = EdgeCommand(
        command_type="RELAY_TEST",
        installation_id=inst_id,
        is_global=is_global,
        payload=json.dumps({"initial_states": initial_states}),
        status="pending",
    )
    db.add(cmd)
    db.commit()
    db.refresh(cmd)

    finished_cmd = await wait_command_result(cmd.id, 10.0, db)

    cmd_data = app_state.pop_command_data(finished_cmd.id)
    if finished_cmd.status == "done":
        raw_pins = (
            cmd_data.get("pin_states")
            if isinstance(cmd_data, dict)
            and isinstance(cmd_data.get("pin_states"), dict)
            else None
        )
        if raw_pins is not None:
            for raw_pin, raw_val in raw_pins.items():
                try:
                    app_state.set_pin_state(int(raw_pin), bool(raw_val))
                except (TypeError, ValueError):
                    continue
        sync_app_state_pins(computed_pins)

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


@router.get("/relays", response_model=dict[str, Any])
def get_relays_status(
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Возвращает текущие состояния GPIO-пинов, режимы света/вспышки, активные установки и capture_delay_sec."""
    pins = {str(k): v for k, v in app_state.get_all_pin_states().items()}
    global_light_mode = get_global_light_mode(db, is_flash=False)
    global_flash_mode = get_global_light_mode(db, is_flash=True)

    active_insts = (
        db.query(Installation)
        .filter(Installation.is_archived.is_(False))
        .order_by(Installation.id.asc())
        .all()
    )
    installations_relays: list[dict[str, Any]] = []
    for inst in active_insts:
        snap = get_installation_light_snapshot(
            db,
            inst,
            global_light_mode=global_light_mode,
            global_flash_mode=global_flash_mode,
        )
        pins[str(inst.light_gpio_pin)] = snap["light_state"]
        app_state.set_pin_state(inst.light_gpio_pin, snap["light_state"])
        if inst.flash_gpio_pin != inst.light_gpio_pin:
            pins[str(inst.flash_gpio_pin)] = snap["flash_state"]
            app_state.set_pin_state(inst.flash_gpio_pin, snap["flash_state"])

        installations_relays.append(
            {
                "installation_id": inst.id,
                "name": inst.name,
                "light_gpio_pin": inst.light_gpio_pin,
                "flash_gpio_pin": inst.flash_gpio_pin,
                "individual_light_state": snap["individual_light_state"],
                "individual_flash_state": snap["individual_flash_state"],
                "light_state": snap["light_state"],
                "flash_state": snap["flash_state"],
                "light_locked": snap["light_locked"],
                "flash_locked": snap["flash_locked"],
            }
        )

    delay_setting = (
        db.query(SystemSetting).filter(SystemSetting.key == "capture_delay_sec").first()
    )
    capture_delay_sec = (
        float(delay_setting.value) if delay_setting and delay_setting.value else 1.5
    )
    light_state = (
        any(item["light_state"] for item in installations_relays)
        if installations_relays
        else (global_light_mode != "off")
    )
    flash_pins_items = [
        item
        for item in installations_relays
        if item["flash_gpio_pin"] != item["light_gpio_pin"]
    ]
    flash_state = any(item["flash_state"] for item in flash_pins_items)

    return {
        "pins": pins,
        "installations": installations_relays,
        "global_light_mode": global_light_mode,
        "global_flash_mode": global_flash_mode,
        "light_state": light_state,
        "flash_state": flash_state,
        "capture_delay_sec": capture_delay_sec,
    }


@router.get("/relays/{installation_id}", response_model=dict[str, Any])
def get_installation_relays(
    installation_id: int,
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Возвращает состояния реле конкретной установки с учётом иерархии."""
    inst = db.query(Installation).filter(Installation.id == installation_id).first()
    if not inst:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Установка не найдена",
        )
    snap = get_installation_light_snapshot(db, inst)
    return {
        "installation_id": inst.id,
        "light_gpio_pin": inst.light_gpio_pin,
        "flash_gpio_pin": inst.flash_gpio_pin,
        **snap,
    }
