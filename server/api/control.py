import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from server.api import edge as edge_api
from server.api.deps import format_utc_z, get_current_user, get_db, require_role
from server.core.area import calculate_area_display
from server.core.commands import wait_command_result
from server.core.light_state import (
    LightMode,
    build_light_main_command_payload,
    build_single_installation_light_payload,
    compute_active_pin_states,
    compute_effective_main_light_state,
    compute_effective_state,
    get_global_day_night_settings,
    get_global_light_mode,
    get_installation_channel_pins,
    get_installation_light_snapshot,
    set_global_day_night_settings,
    set_global_light_mode,
    set_individual_light_state,
    set_installation_channel_color,
    set_installation_day_night_settings,
    sync_app_state_pins,
)
from server.core.rgb import resolve_capture_display_rel_path
from server.core.state import EphemeralSnapshot, app_state
from server.core.system_status import (
    build_debug_info,
    check_rpi_online,
    validate_target_value,
)
from server.db.models import Capture, EdgeCommand, Installation, SystemSetting, User
from shared.light_cycle import normalize_hhmm, validate_day_night_window
from shared.pins import format_gpio_pins, normalize_hex_color

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
    color: str | None = None

    @field_validator("target", mode="before")
    @classmethod
    def validate_target(cls, v: Any) -> int | Literal["all"]:
        return validate_target_value(v)

    @field_validator("color")
    @classmethod
    def validate_color(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return normalize_hex_color(v)

    @model_validator(mode="after")
    def validate_state_or_mode(self) -> "LightRequest":
        if self.target == "all" and self.color is not None:
            raise ValueError("Выбор HEX-цвета доступен только для конкретной установки")
        if self.state is None and self.mode is None and self.color is None:
            raise ValueError("Необходимо указать state, mode или color")
        if self.target != "all" and self.state is None:
            if self.mode in ("on", "off"):
                self.state = self.mode == "on"
            elif self.color is None:
                raise ValueError(
                    "Для конкретной установки необходимо указать state (true/false) или color (#RRGGBB)"
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


class DayNightRequest(BaseModel):
    """Тело запроса POST /api/control/day-night."""

    model_config = ConfigDict(extra="ignore")

    target: int | Literal["all"]
    enabled: bool | None = Field(default=None, strict=True)
    day_start: str | None = None
    day_end: str | None = None

    @field_validator("target", mode="before")
    @classmethod
    def validate_target(cls, v: Any) -> int | Literal["all"]:
        return validate_target_value(v)

    @field_validator("day_start")
    @classmethod
    def validate_day_start(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return normalize_hhmm(v, field_label="day_start")

    @field_validator("day_end")
    @classmethod
    def validate_day_end(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return normalize_hhmm(v, field_label="day_end")

    @model_validator(mode="after")
    def validate_fields(self) -> "DayNightRequest":
        if self.enabled is None and self.day_start is None and self.day_end is None:
            raise ValueError("Необходимо указать enabled, day_start или day_end")
        if self.day_start is not None and self.day_end is not None:
            validate_day_night_window(self.day_start, self.day_end)
        return self


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
    """Общий обработчик управления основным светом и вспышкой с учётом иерархии режимов и HEX-цвета."""
    inst_id, is_global, _ = validate_target_and_resolve(body.target, db)

    command_type = "LIGHT_FLASH" if is_flash else "LIGHT_MAIN"

    if is_global:
        check_rpi_online(db)
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
            snap = get_installation_light_snapshot(
                db,
                item,
                global_light_mode=g_light,
                global_flash_mode=g_flash,
            )
            eff_light = snap["light_state"]
            eff_flash = snap["flash_state"]
            inst_states_map[str(item.id)] = {
                "light_gpio_pin": format_gpio_pins(snap["light_pins"]),
                "flash_gpio_pin": format_gpio_pins(snap["flash_pins"]),
                "light_pins": snap["light_pins"],
                "flash_pins": snap["flash_pins"],
                "light_color": snap["light_color"],
                "flash_color": snap["flash_color"],
                "light_state": eff_light,
                "flash_state": eff_flash,
            }
            if not is_flash:
                for p in snap["light_pins"]:
                    target_pin_states[p] = eff_light
            else:
                for p in snap["flash_pins"]:
                    if p not in snap["light_pins"]:
                        target_pin_states[p] = eff_flash

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
        debug_info = build_debug_info(finished_cmd, current_user, db)
        if debug_info:
            resp["debug_info"] = debug_info
        return resp

    inst = db.query(Installation).filter(Installation.id == inst_id).first()
    if not inst:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Установка не найдена",
        )

    snap = get_installation_light_snapshot(db, inst)
    channel_pins = snap["flash_pins"] if is_flash else snap["light_pins"]
    saved_color = snap["flash_color"] if is_flash else snap["light_color"]
    next_color = body.color if body.color is not None else saved_color
    current_ind_state = (
        snap["individual_flash_state"] if is_flash else snap["individual_light_state"]
    )
    next_ind_state = bool(body.state) if body.state is not None else current_ind_state
    global_mode = get_global_light_mode(db, is_flash=is_flash)
    if is_flash:
        eff_state = compute_effective_state(global_mode, next_ind_state)
        is_locked = bool(snap["flash_locked"])
        lock_reason: str | None = "Заблокировано" if is_locked else None
    else:
        eff_state = compute_effective_main_light_state(
            global_day_night_enabled=snap["global_day_night_enabled"],
            global_is_day=(snap["global_day_night_phase"] == "day"),
            global_light_mode=global_mode,
            inst_day_night_enabled=snap["day_night_enabled"],
            inst_is_day=(snap["day_night_phase"] == "day"),
            individual_light_state=next_ind_state,
        )
        is_locked = bool(snap["light_locked"])
        lock_reason = snap["light_lock_reason"]

    # Изменение только цвета при эффективно выключенном канале:
    # сохраняем цвет в БД, не включая лампу!
    if body.state is None and not eff_state:
        if body.color is not None:
            set_installation_channel_color(db, inst.id, next_color, is_flash=is_flash)
            for other in (
                db.query(Installation)
                .filter(Installation.is_archived.is_(False), Installation.id != inst.id)
                .all()
            ):
                other_pins = get_installation_channel_pins(db, other, is_flash=is_flash)
                if other_pins == channel_pins:
                    set_installation_channel_color(
                        db, other.id, next_color, is_flash=is_flash
                    )
            db.commit()
        return {
            "command_id": None,
            "status": "done",
            "result_message": f"Цвет сохранён ({next_color}), канал выключен",
            "individual_state": next_ind_state,
            "effective_state": eff_state,
            "locked": is_locked,
            "lock_reason": lock_reason,
            "color": next_color,
            "pins": channel_pins,
            "is_rgb": len(channel_pins) == 3,
            "pin_states": {
                str(k): v for k, v in app_state.get_all_pin_states().items()
            },
        }

    check_rpi_online(db)
    target_pin_states = {p: eff_state for p in channel_pins}

    cmd_payload = build_single_installation_light_payload(
        state=eff_state,
        individual_state=next_ind_state,
        mode=global_mode,
        pins=channel_pins,
        color=next_color,
    )
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
        if body.state is not None:
            set_individual_light_state(db, inst.id, next_ind_state, is_flash=is_flash)
        if body.color is not None:
            set_installation_channel_color(db, inst.id, next_color, is_flash=is_flash)
        # Синхронизируем установки, разделяющие те же физические GPIO-пины
        channel_set = set(channel_pins)
        other_insts = (
            db.query(Installation)
            .filter(Installation.is_archived.is_(False), Installation.id != inst.id)
            .all()
        )
        for other in other_insts:
            other_pins = get_installation_channel_pins(db, other, is_flash=is_flash)
            if body.state is not None and (set(other_pins) & channel_set):
                set_individual_light_state(
                    db, other.id, next_ind_state, is_flash=is_flash
                )
            if body.color is not None and other_pins == channel_pins:
                set_installation_channel_color(
                    db, other.id, next_color, is_flash=is_flash
                )
        db.commit()
        sync_app_state_pins(target_pin_states)

    resp = {
        "command_id": finished_cmd.id,
        "status": finished_cmd.status,
        "result_message": finished_cmd.result_message or "OK",
        "individual_state": next_ind_state,
        "effective_state": eff_state,
        "locked": is_locked,
        "lock_reason": lock_reason,
        "color": next_color,
        "pins": channel_pins,
        "is_rgb": len(channel_pins) == 3,
        "pin_states": {str(k): v for k, v in app_state.get_all_pin_states().items()},
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
    channels_payload: list[dict[str, Any]] = []
    for t_inst in target_insts:
        t_snap = get_installation_light_snapshot(db, t_inst)
        channels_payload.append(
            {
                "installation_id": t_inst.id,
                "role": "light",
                "pins": t_snap["light_pins"],
                "state": t_snap["light_state"],
                "color": t_snap["light_color"],
            }
        )
        channels_payload.append(
            {
                "installation_id": t_inst.id,
                "role": "flash",
                "pins": t_snap["flash_pins"],
                "state": t_snap["flash_state"],
                "color": t_snap["flash_color"],
            }
        )

    cmd = EdgeCommand(
        command_type="RELAY_TEST",
        installation_id=inst_id,
        is_global=is_global,
        payload=json.dumps(
            {
                "initial_states": initial_states,
                "channels": channels_payload,
            }
        ),
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


def _is_control_rpi_online(db: Session) -> bool:
    """Проверяет онлайн RPi через check_rpi_online без падения в 503."""
    try:
        check_rpi_online(db)
        return True
    except HTTPException:
        return False


def apply_day_night_settings(
    db: Session,
    target: int | Literal["all"],
    *,
    enabled: bool | None = None,
    day_start: str | None = None,
    day_end: str | None = None,
) -> dict[str, Any]:
    """Сохраняет настройки цикла День/Ночь, обновляет app_state и ставит LIGHT_MAIN при изменении."""
    active_insts = (
        db.query(Installation)
        .filter(Installation.is_archived.is_(False))
        .order_by(Installation.id.asc())
        .all()
    )
    before_pins = compute_active_pin_states(db, active_insts)

    if target == "all":
        try:
            g_dn = set_global_day_night_settings(
                db, enabled=enabled, day_start=day_start, day_end=day_end
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc
        db.commit()

        after_pins = compute_active_pin_states(db, active_insts)
        sync_app_state_pins(after_pins)
        light_pin_states, cmd_payload = build_light_main_command_payload(
            db, active_insts
        )
        changed = any(before_pins.get(p) != val for p, val in light_pin_states.items())
        cmd_id: int | None = None
        delivery_status = "unchanged"
        if changed and active_insts:
            if _is_control_rpi_online(db):
                cmd = EdgeCommand(
                    command_type="LIGHT_MAIN",
                    installation_id=None,
                    is_global=True,
                    payload=json.dumps(cmd_payload),
                    status="pending",
                )
                db.add(cmd)
                db.commit()
                db.refresh(cmd)
                cmd_id = cmd.id
                delivery_status = "queued"
            else:
                delivery_status = "offline"

        relays_snapshot = _build_relays_snapshot(db)
        return {
            "status": "done",
            "target": "all",
            "command_id": cmd_id,
            "delivery_status": delivery_status,
            "changed": changed,
            "global_day_night_enabled": g_dn["enabled"],
            "global_day_start": g_dn["day_start"],
            "global_day_end": g_dn["day_end"],
            "global_day_night_phase": g_dn["phase"],
            "global_light_locked": bool(g_dn["enabled"]),
            "pin_states": {
                str(k): v for k, v in app_state.get_all_pin_states().items()
            },
            **relays_snapshot,
        }

    inst_id, _, _ = validate_target_and_resolve(target, db)
    inst = db.query(Installation).filter(Installation.id == inst_id).first()
    assert inst is not None

    try:
        set_installation_day_night_settings(
            db, inst.id, enabled=enabled, day_start=day_start, day_end=day_end
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    db.commit()

    after_pins = compute_active_pin_states(db, active_insts)
    sync_app_state_pins(after_pins)
    snap = get_installation_light_snapshot(db, inst)
    changed = any(before_pins.get(p) != snap["light_state"] for p in snap["light_pins"])
    cmd_id = None
    delivery_status = "unchanged"
    if changed:
        if _is_control_rpi_online(db):
            cmd_payload = build_single_installation_light_payload(snap)
            cmd = EdgeCommand(
                command_type="LIGHT_MAIN",
                installation_id=inst.id,
                is_global=False,
                payload=json.dumps(cmd_payload),
                status="pending",
            )
            db.add(cmd)
            db.commit()
            db.refresh(cmd)
            cmd_id = cmd.id
            delivery_status = "queued"
        else:
            delivery_status = "offline"

    return {
        "status": "done",
        "target": inst.id,
        "installation_id": inst.id,
        "command_id": cmd_id,
        "delivery_status": delivery_status,
        "changed": changed,
        "light_gpio_pin": format_gpio_pins(snap["light_pins"]),
        "flash_gpio_pin": format_gpio_pins(snap["flash_pins"]),
        **snap,
        "pin_states": {str(k): v for k, v in app_state.get_all_pin_states().items()},
    }


@router.post("/day-night", response_model=dict[str, Any])
def control_day_night(
    body: DayNightRequest,
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """POST /api/control/day-night (Developer): настройка глобального или индивидуального цикла дня и ночи."""
    return apply_day_night_settings(
        db,
        body.target,
        enabled=body.enabled,
        day_start=body.day_start,
        day_end=body.day_end,
    )


def _build_relays_snapshot(db: Session) -> dict[str, Any]:
    """Собирает актуальный срез состояния реле, глобальных режимов и цикла дня/ночи."""
    pins = {str(k): v for k, v in app_state.get_all_pin_states().items()}
    global_light_mode = get_global_light_mode(db, is_flash=False)
    global_flash_mode = get_global_light_mode(db, is_flash=True)
    global_day_night = get_global_day_night_settings(db)

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
            global_day_night=global_day_night,
        )
        for p in snap["light_pins"]:
            pins[str(p)] = snap["light_state"]
            app_state.set_pin_state(p, snap["light_state"])
        for p in snap["flash_pins"]:
            if p not in snap["light_pins"]:
                pins[str(p)] = snap["flash_state"]
                app_state.set_pin_state(p, snap["flash_state"])

        installations_relays.append(
            {
                "installation_id": inst.id,
                "name": inst.name,
                "light_gpio_pin": format_gpio_pins(snap["light_pins"]),
                "flash_gpio_pin": format_gpio_pins(snap["flash_pins"]),
                "light_pins": snap["light_pins"],
                "flash_pins": snap["flash_pins"],
                "light_is_rgb": snap["light_is_rgb"],
                "flash_is_rgb": snap["flash_is_rgb"],
                "light_color": snap["light_color"],
                "flash_color": snap["flash_color"],
                "individual_light_state": snap["individual_light_state"],
                "individual_flash_state": snap["individual_flash_state"],
                "light_state": snap["light_state"],
                "flash_state": snap["flash_state"],
                "light_locked": snap["light_locked"],
                "light_lock_reason": snap["light_lock_reason"],
                "flash_locked": snap["flash_locked"],
                "day_night_enabled": snap["day_night_enabled"],
                "day_start": snap["day_start"],
                "day_end": snap["day_end"],
                "day_night_phase": snap["day_night_phase"],
                "day_night_locked": snap["day_night_locked"],
                "global_day_night_enabled": snap["global_day_night_enabled"],
                "global_day_start": snap["global_day_start"],
                "global_day_end": snap["global_day_end"],
                "global_day_night_phase": snap["global_day_night_phase"],
                "global_light_locked": snap["global_light_locked"],
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
        else (
            global_day_night["is_day"]
            if global_day_night["enabled"]
            else (global_light_mode != "off")
        )
    )
    flash_pins_items = [
        item
        for item in installations_relays
        if set(item["flash_pins"]) != set(item["light_pins"])
    ]
    flash_state = any(item["flash_state"] for item in flash_pins_items)

    return {
        "pins": pins,
        "installations": installations_relays,
        "global_light_mode": global_light_mode,
        "global_flash_mode": global_flash_mode,
        "global_day_night_enabled": global_day_night["enabled"],
        "global_day_start": global_day_night["day_start"],
        "global_day_end": global_day_night["day_end"],
        "global_day_night_phase": global_day_night["phase"],
        "global_light_locked": bool(global_day_night["enabled"]),
        "global_light_lock_reason": (
            "Цикл дня/ночи" if global_day_night["enabled"] else None
        ),
        "light_state": light_state,
        "flash_state": flash_state,
        "capture_delay_sec": capture_delay_sec,
    }


@router.get("/relays", response_model=dict[str, Any])
def get_relays_status(
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Возвращает текущие состояния GPIO-пинов, режимы света/вспышки, цикл дня/ночи и capture_delay_sec."""
    return _build_relays_snapshot(db)


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
        "light_gpio_pin": format_gpio_pins(snap["light_pins"]),
        "flash_gpio_pin": format_gpio_pins(snap["flash_pins"]),
        **snap,
    }
