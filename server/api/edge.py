import contextlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from server.api.deps import format_utc_z, get_db, verify_edge_key
from server.core.area import calculate_area_display
from server.core.light_state import (
    get_global_light_mode,
    get_installation_light_snapshot,
)
from server.core.live_manager import live_manager
from server.core.rgb import (
    apply_rgb_to_jpeg_bytes,
    clamp_rgb_dict,
    get_rgb_variant_rel_path,
    is_identity_rgb,
    resolve_media_abs_path,
)
from server.core.state import app_state
from server.core.timeouts import expire_stale_commands
from server.db.models import (
    Camera,
    Capture,
    EdgeCommand,
    Installation,
    PlantMeasurement,
    SystemSetting,
)
from shared.schemas import (
    CaptureMeta,
    CommandResult,
    HeartbeatActiveInstallation,
    HeartbeatRequest,
    HeartbeatResponse,
    SegmentationMode,
)

router = APIRouter(prefix="/api/edge", tags=["edge"])


def get_media_dir() -> Path:
    """Возвращает базовую директорию для хранения медиафайлов."""
    return Path(__file__).resolve().parent.parent / "media"


def _normalize_utc_datetime(dt_val: datetime | str) -> datetime:
    """Приводит дату/время к UTC и нормализует до целых секунд."""
    try:
        if isinstance(dt_val, str):
            dt = datetime.fromisoformat(dt_val)
        else:
            dt = dt_val

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        else:
            dt = dt.astimezone(UTC)

        return dt.replace(microsecond=0)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Невалидный формат даты/времени captured_at: {exc}",
        ) from exc


@router.post(
    "/heartbeat",
    response_model=HeartbeatResponse,
    dependencies=[Depends(verify_edge_key)],
)
def edge_heartbeat(
    hb: HeartbeatRequest,
    db: Annotated[Session, Depends(get_db)],
) -> HeartbeatResponse:
    """3.1. POST /api/edge/heartbeat.

    Сохраняет телеметрию RPi, обновляет статус известных камер и возвращает
    актуальный конфиг для работы агента.
    """
    now = datetime.now(UTC)

    # 1. Сохранение телеметрии в system_settings.rpi_last_telemetry
    telemetry_setting = (
        db.query(SystemSetting)
        .filter(SystemSetting.key == "rpi_last_telemetry")
        .first()
    )
    payload_dict = hb.model_dump()
    hw_mode = getattr(hb.hardware, "gpio_mode", "mock")
    payload_dict["hardware_mode"] = hw_mode
    payload_dict["is_raspberry_pi"] = hw_mode == "rpi"
    payload_dict["mock_cameras"] = bool(getattr(hb.hardware, "mock_cameras", False))
    payload_json = json.dumps(payload_dict)
    if telemetry_setting:
        telemetry_setting.value = payload_json
        telemetry_setting.updated_at = now
    else:
        db.add(
            SystemSetting(
                key="rpi_last_telemetry",
                value=payload_json,
                updated_at=now,
            )
        )

    # 2. Обновление статуса и человекочитаемого имени известных камер
    detected_by_hw_id = {c.hardware_id: c for c in hb.detected_cameras}
    known_cameras = db.query(Camera).all()
    for cam in known_cameras:
        detected_cam = detected_by_hw_id.get(cam.hardware_id)
        if detected_cam is not None:
            cam.is_online = True
            cam.last_seen_at = now
            if detected_cam.auto_name and detected_cam.auto_name.strip():
                cam.auto_name = detected_cam.auto_name.strip()
        else:
            cam.is_online = False

    # 3. Чтение глобальных настроек
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

    # 4. Формирование списка активных установок и целевых состояний света/вспышки
    global_light_mode = get_global_light_mode(db, is_flash=False)
    global_flash_mode = get_global_light_mode(db, is_flash=True)

    active_installations = (
        db.query(Installation).filter(Installation.is_archived.is_(False)).all()
    )
    active_inst_data: list[HeartbeatActiveInstallation] = []
    for inst in active_installations:
        cam_ids = [c.hardware_id for c in inst.cameras]
        light_snap = get_installation_light_snapshot(
            db,
            inst,
            global_light_mode=global_light_mode,
            global_flash_mode=global_flash_mode,
        )
        active_inst_data.append(
            HeartbeatActiveInstallation(
                id=inst.id,
                light_gpio_pin=inst.light_gpio_pin,
                flash_gpio_pin=inst.flash_gpio_pin,
                camera_hardware_ids=cam_ids,
                capture_delay_sec=inst.capture_delay_sec,
                name=inst.name,
                culture_name=inst.culture_name,
                aruco_size_mm=inst.aruco_size_mm,
                camera_rgb=_load_installation_camera_rgb(db, cam_ids),
                camera_segmentation_modes=_load_installation_camera_seg_modes(
                    db, cam_ids
                ),
                light_state=light_snap["light_state"],
                flash_state=light_snap["flash_state"],
            )
        )

    db.commit()

    return HeartbeatResponse(
        capture_interval_hours=capture_interval_hours,
        debug_mode=debug_mode,
        active_installations=active_inst_data,
        capture_delay_sec=capture_delay_sec,
        global_light_mode=global_light_mode,
        global_flash_mode=global_flash_mode,
    )


@router.post(
    "/commands/claim",
    response_model=list[dict[str, Any]],
    dependencies=[Depends(verify_edge_key)],
)
def edge_claim_command(
    db: Annotated[Session, Depends(get_db)],
) -> list[dict[str, Any]]:
    """3.2. POST /api/edge/commands/claim.

    Переводит просроченные команды pending и running в timeout, забирает
    одну самую старую команду pending, переводит в running и возвращает её агенту.
    """
    now = datetime.now(UTC)
    expire_stale_commands(db, now=now)
    db.flush()

    # 2. Выборка одной самой старой команды pending
    oldest_pending = (
        db.query(EdgeCommand)
        .filter(EdgeCommand.status == "pending")
        .order_by(EdgeCommand.created_at.asc())
        .first()
    )

    if not oldest_pending:
        db.commit()
        return []

    # 4. Перевод в running
    oldest_pending.status = "running"
    oldest_pending.started_at = now
    oldest_pending.attempts += 1
    db.commit()
    db.refresh(oldest_pending)

    # 5. Формирование структуры команды
    payload = json.loads(oldest_pending.payload) if oldest_pending.payload else None

    global_light_mode = get_global_light_mode(db, is_flash=False)
    global_flash_mode = get_global_light_mode(db, is_flash=True)

    def _serialize_cmd_inst(item: Installation) -> dict[str, Any]:
        cam_ids = [c.hardware_id for c in item.cameras]
        light_snap = get_installation_light_snapshot(
            db,
            item,
            global_light_mode=global_light_mode,
            global_flash_mode=global_flash_mode,
        )
        return {
            "id": item.id,
            "light_gpio_pin": item.light_gpio_pin,
            "flash_gpio_pin": item.flash_gpio_pin,
            "camera_hardware_ids": cam_ids,
            "capture_delay_sec": item.capture_delay_sec,
            "name": item.name,
            "culture_name": item.culture_name,
            "aruco_size_mm": item.aruco_size_mm,
            "camera_rgb": _load_installation_camera_rgb(db, cam_ids),
            "camera_segmentation_modes": _load_installation_camera_seg_modes(
                db, cam_ids
            ),
            "light_state": light_snap["light_state"],
            "flash_state": light_snap["flash_state"],
        }

    if oldest_pending.is_global:
        active_installations = (
            db.query(Installation).filter(Installation.is_archived.is_(False)).all()
        )
        installations_list = [
            _serialize_cmd_inst(inst) for inst in active_installations
        ]
        cmd_data = {
            "id": oldest_pending.id,
            "command_type": oldest_pending.command_type,
            "is_global": True,
            "installation": None,
            "installations": installations_list,
            "payload": payload,
        }
    else:
        inst = oldest_pending.installation
        inst_data = _serialize_cmd_inst(inst) if inst else None
        cmd_data = {
            "id": oldest_pending.id,
            "command_type": oldest_pending.command_type,
            "is_global": False,
            "installation": inst_data,
            "installations": None,
            "payload": payload,
        }

    return [cmd_data]


def _normalize_stream_url(raw_url: str | None, request: Request) -> str | None:
    """Подставляет IP клиента вместо loopback, если агент прислал 127.0.0.1."""
    if not raw_url:
        return None
    if "127.0.0.1" in raw_url and request.client and request.client.host:
        client_host = request.client.host
        if client_host not in ("127.0.0.1", "::1", "testclient"):
            return raw_url.replace("127.0.0.1", client_host, 1)
    return raw_url


@router.post(
    "/commands/{command_id}/result",
    response_model=dict[str, bool],
    dependencies=[Depends(verify_edge_key)],
)
def edge_command_result(
    command_id: int,
    body: CommandResult,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, bool]:
    """3.3. POST /api/edge/commands/{command_id}/result.

    Принимает отчёт о выполнении команды от Edge-агента.
    """
    cmd = db.query(EdgeCommand).filter(EdgeCommand.id == command_id).first()
    if not cmd:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Команда не найдена",
        )

    if cmd.status != "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Команда не находится в статусе running",
        )

    now = datetime.now(UTC)
    cmd.status = body.status
    cmd.result_message = body.result_message
    cmd.debug_log = body.debug_log_line
    cmd.completed_at = now

    # Для команды SCAN_CAMERAS регистрируем новые камеры и обновляем существующие
    if cmd.command_type == "SCAN_CAMERAS" and body.scanned_cameras is not None:
        scanned_hw_ids: list[str] = []
        for cam_info in body.scanned_cameras:
            scanned_hw_ids.append(cam_info.hardware_id)
            cam = (
                db.query(Camera)
                .filter(Camera.hardware_id == cam_info.hardware_id)
                .first()
            )
            if cam:
                cam.auto_name = cam_info.auto_name
                cam.is_online = True
                cam.last_seen_at = now
            else:
                new_cam = Camera(
                    hardware_id=cam_info.hardware_id,
                    auto_name=cam_info.auto_name,
                    is_online=True,
                    last_seen_at=now,
                )
                db.add(new_cam)

        # Сохраняем результат в памяти процесса (даже при пустом списке)
        app_state.save_scanned_cameras(command_id, scanned_hw_ids)

    # Обработка дополнительных данных результата команды (stream_url, controls, formats, auto_color, pin_states)
    if body.data and isinstance(body.data, dict):
        app_state.save_command_data(command_id, body.data)
        raw_pin_states = body.data.get("pin_states")
        if body.status == "done" and isinstance(raw_pin_states, dict):
            for raw_pin, raw_val in raw_pin_states.items():
                try:
                    app_state.set_pin_state(int(raw_pin), bool(raw_val))
                except (TypeError, ValueError):
                    continue
        cmd_payload = {}
        if cmd.payload:
            try:
                cmd_payload = json.loads(cmd.payload)
            except Exception:  # noqa: BLE001
                cmd_payload = {}
        hw_id = cmd_payload.get("camera_hardware_id")
        if hw_id:
            if cmd.command_type == "START_LIVE":
                session = live_manager.get_or_create(hw_id)
                if body.data.get("stream_url"):
                    session.stream_url = _normalize_stream_url(
                        str(body.data["stream_url"]), request
                    )
                if body.data.get("resolution"):
                    session.resolution = str(body.data["resolution"])
                session.is_active = True
            is_preview = bool(cmd_payload.get("preview") or body.data.get("preview"))
            if (
                cmd.command_type == "AUTO_COLOR_CALIBRATE"
                and not is_preview
                and isinstance(body.data.get("rgb"), dict)
            ):
                clamped_rgb = clamp_rgb_dict(body.data["rgb"])
                _upsert_setting(db, f"camera_rgb_{hw_id}", json.dumps(clamped_rgb), now)
            if "formats" in body.data or "controls" in body.data:
                _save_camera_controls_state(
                    db=db,
                    hardware_id=hw_id,
                    formats=body.data.get("formats"),
                    controls=body.data.get("controls"),
                    now=now,
                )

    db.commit()

    return {"ok": True}


def _upsert_setting(db: Session, key: str, value: str, now: datetime) -> None:
    """Создаёт или обновляет SystemSetting по ключу."""
    existing = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if existing:
        existing.value = value
        existing.updated_at = now
    else:
        db.add(SystemSetting(key=key, value=value, updated_at=now))


def _upsert_non_empty_json_list(
    db: Session, key: str, items: list[dict[str, Any]] | None, now: datetime
) -> None:
    """Сохраняет JSON-список в SystemSetting, не затирая существующие данные пустым списком."""
    if items is None:
        return
    if len(items) == 0:
        existing = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        if (
            existing
            and existing.value
            and existing.value.strip() not in ("", "[]", "null")
        ):
            return
        # Не записываем пустой [] как кэш, чтобы не блокировать повторный опрос камеры
        return
    _upsert_setting(db, key, json.dumps(items), now)


def _save_camera_controls_state(
    db: Session,
    hardware_id: str,
    formats: list[dict[str, Any]] | None = None,
    controls: list[dict[str, Any]] | None = None,
    current_format: str | None = None,
    current_resolution: str | None = None,
    now: datetime | None = None,
) -> None:
    """Сохраняет актуальные форматы и V4L2-контролы камеры в SystemSetting."""
    ts = now or datetime.now(UTC)
    _upsert_non_empty_json_list(db, f"camera_formats_{hardware_id}", formats, ts)
    _upsert_non_empty_json_list(db, f"camera_ctrls_{hardware_id}", controls, ts)
    if current_format:
        _upsert_setting(db, f"camera_format_{hardware_id}", current_format, ts)
    if current_resolution:
        _upsert_setting(db, f"camera_res_{hardware_id}", current_resolution, ts)


@router.post(
    "/cameras/{hardware_id}/controls-state",
    dependencies=[Depends(verify_edge_key)],
)
def receive_camera_controls_state(
    hardware_id: str,
    body: dict[str, Any],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, str]:
    """Принимает от Edge-агента реальные форматы и V4L2-контролы камеры."""
    _save_camera_controls_state(
        db=db,
        hardware_id=hardware_id,
        formats=body.get("formats"),
        controls=body.get("controls"),
        current_format=body.get("current_format"),
        current_resolution=body.get("current_resolution"),
    )
    db.commit()
    return {"status": "ok"}


def normalize_segmentation_mode(raw_val: Any) -> SegmentationMode:
    """Нормализует режим сегментации камеры к 'auto', 'onnx' или 'hsv'."""
    val = str(raw_val or "auto").strip().lower()
    if val == "onnx":
        return "onnx"
    if val == "hsv":
        return "hsv"
    return "auto"


def _load_installation_camera_seg_modes(
    db: Session, hardware_ids: list[str]
) -> dict[str, SegmentationMode]:
    """Загружает сохранённые режимы сегментации для списка камер установки."""
    modes_map: dict[str, SegmentationMode] = {}
    if not hardware_ids:
        return modes_map
    keys = [f"camera_seg_mode_{hw_id}" for hw_id in hardware_ids]
    rows = db.query(SystemSetting).filter(SystemSetting.key.in_(keys)).all()
    by_key = {row.key: row.value for row in rows}
    for hw_id in hardware_ids:
        raw_val = by_key.get(f"camera_seg_mode_{hw_id}")
        modes_map[hw_id] = normalize_segmentation_mode(raw_val)
    return modes_map


@router.get(
    "/camera-settings",
    dependencies=[Depends(verify_edge_key)],
)
def get_edge_camera_settings(
    db: Annotated[Session, Depends(get_db)],
    hardware_id: str | None = None,
) -> dict[str, Any]:
    """Возвращает сохранённые настройки камер (RGB-коэффициенты и segmentation_mode) для Edge-агента."""
    if hardware_id:
        hw_ids = [hardware_id]
    else:
        hw_ids = [c.hardware_id for c in db.query(Camera).all()]
        # Дополняем любыми камерами из SystemSetting, если ещё не привязаны
        rgb_prefix = "camera_rgb_"
        seg_prefix = "camera_seg_mode_"
        for row in (
            db.query(SystemSetting)
            .filter(
                (SystemSetting.key.like(f"{rgb_prefix}%"))
                | (SystemSetting.key.like(f"{seg_prefix}%"))
            )
            .all()
        ):
            if row.key.startswith(rgb_prefix):
                cand = row.key[len(rgb_prefix) :]
            elif row.key.startswith(seg_prefix):
                cand = row.key[len(seg_prefix) :]
            else:
                continue
            if cand and cand not in hw_ids:
                hw_ids.append(cand)

    rgb_map = _load_installation_camera_rgb(db, hw_ids)
    seg_map = _load_installation_camera_seg_modes(db, hw_ids)
    cameras_cfg: dict[str, dict[str, Any]] = {}
    for hw_id in hw_ids:
        cameras_cfg[hw_id] = {
            "rgb": rgb_map.get(hw_id, {"r": 1.0, "g": 1.0, "b": 1.0}),
            "segmentation_mode": seg_map.get(hw_id, "auto"),
        }

    resp: dict[str, Any] = {"cameras": cameras_cfg}
    if hardware_id:
        entry = cameras_cfg.get(
            hardware_id,
            {"rgb": {"r": 1.0, "g": 1.0, "b": 1.0}, "segmentation_mode": "auto"},
        )
        resp["hardware_id"] = hardware_id
        resp["rgb"] = entry["rgb"]
        resp["segmentation_mode"] = entry["segmentation_mode"]
    return resp


def _load_installation_camera_rgb(
    db: Session, hardware_ids: list[str]
) -> dict[str, dict[str, float]]:
    """Загружает сохранённые RGB-коэффициенты для списка камер установки."""
    rgb_map: dict[str, dict[str, float]] = {}
    if not hardware_ids:
        return rgb_map
    keys = [f"camera_rgb_{hw_id}" for hw_id in hardware_ids]
    rows = db.query(SystemSetting).filter(SystemSetting.key.in_(keys)).all()
    by_key = {row.key: row.value for row in rows}
    for hw_id in hardware_ids:
        raw_val = by_key.get(f"camera_rgb_{hw_id}")
        if not raw_val:
            continue
        with contextlib.suppress(Exception):
            parsed = json.loads(raw_val)
            if isinstance(parsed, dict):
                rgb_map[hw_id] = clamp_rgb_dict(parsed)
    return rgb_map


def _apply_saved_camera_rgb(
    db: Session, hardware_id: str, raw_bytes: bytes
) -> tuple[bytes, bool]:
    """Применяет сохранённый RGB-профиль камеры к JPEG-байтам в памяти."""
    rgb_setting = (
        db.query(SystemSetting)
        .filter(SystemSetting.key == f"camera_rgb_{hardware_id}")
        .first()
    )
    if rgb_setting and rgb_setting.value:
        with contextlib.suppress(Exception):
            rgb_vals = clamp_rgb_dict(json.loads(rgb_setting.value))
            if not is_identity_rgb(rgb_vals):
                filtered = apply_rgb_to_jpeg_bytes(
                    raw_bytes,
                    r=rgb_vals["r"],
                    g=rgb_vals["g"],
                    b=rgb_vals["b"],
                )
                return filtered, True
    return raw_bytes, False


@router.post(
    "/captures",
    dependencies=[Depends(verify_edge_key)],
)
async def edge_upload_capture(
    metadata: Annotated[str, Form(...)],
    raw_image: Annotated[UploadFile, File(...)],
    annotated_image: Annotated[UploadFile, File(...)],
    db: Annotated[Session, Depends(get_db)],
) -> JSONResponse:
    """3.4. POST /api/edge/captures.

    Загрузка исходного и размеченного кадров, создание записи кадра и замеров.
    Тестовые снимки (meta.is_test=True или команда SNAP) не пишутся в БД и на диск,
    а сохраняются в эфемерный in-memory кэш с TTL 10 минут.
    """
    # 1. Валидация метаданных
    try:
        raw_meta = json.loads(metadata)
        meta = CaptureMeta.model_validate(raw_meta)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Невалидные метаданные кадра: {exc}",
        ) from exc

    # 2. Нормализация captured_at
    normalized_captured_at = _normalize_utc_datetime(meta.captured_at)

    # 3. Проверка камеры
    camera = (
        db.query(Camera).filter(Camera.hardware_id == meta.camera_hardware_id).first()
    )
    if not camera:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Камера не зарегистрирована, выполните сканирование камер",
        )

    # 4. Проверка установки (архивные установки принимаются)
    installation = (
        db.query(Installation).filter(Installation.id == meta.installation_id).first()
    )
    if not installation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Установка не найдена",
        )

    # 4.5. Тестовые снимки (is_test=True или команда SNAP/CAPTURE_TEST) — только в памяти
    is_test_capture = bool(meta.is_test)
    if not is_test_capture and meta.command_id is not None:
        cmd = db.query(EdgeCommand).filter(EdgeCommand.id == meta.command_id).first()
        if cmd and cmd.command_type.upper() in ("SNAP", "CAPTURE_TEST"):
            is_test_capture = True

    if is_test_capture:
        raw_bytes = await raw_image.read()
        ann_bytes = await annotated_image.read()
        if meta.rgb_applied:
            raw_display_bytes = raw_bytes
        else:
            raw_display_bytes, _ = _apply_saved_camera_rgb(
                db, camera.hardware_id, raw_bytes
            )
        meas0 = next((m for m in meta.measurements if m.plant_index == 0), None)
        area_display = calculate_area_display(
            meas0.area_px if meas0 else None,
            meta.aruco_area_px,
            installation.aruco_size_mm,
            area_cm2=meas0.area_cm2 if meas0 else None,
        )
        snapshot = app_state.save_test_snapshot(
            installation_id=meta.installation_id,
            camera_id=camera.id,
            camera_hardware_id=camera.hardware_id,
            captured_at=format_utc_z(normalized_captured_at) or "",
            raw_bytes=raw_display_bytes,
            annotated_bytes=ann_bytes,
            area_display=area_display,
            measurements=[
                {
                    "plant_index": m.plant_index,
                    "area_px": m.area_px,
                    "width_px": m.width_px,
                    "height_px": m.height_px,
                    "width_mm": m.width_mm,
                    "height_mm": m.height_mm,
                    "area_cm2": m.area_cm2,
                }
                for m in meta.measurements
            ],
            aruco_area_px=meta.aruco_area_px,
            command_id=meta.command_id,
        )
        return JSONResponse(
            status_code=status.HTTP_201_CREATED,
            content={"ephemeral": True, "snapshot_id": snapshot.snapshot_id},
        )

    # 5. Проверка дубликата по UNIQUE(installation_id, camera_id, captured_at)
    existing_capture = (
        db.query(Capture)
        .filter(
            Capture.installation_id == meta.installation_id,
            Capture.camera_id == camera.id,
            Capture.captured_at == normalized_captured_at,
        )
        .first()
    )
    if existing_capture:
        if meta.command_id is not None:
            app_state.bind_command_capture(meta.command_id, existing_capture.id)
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"capture_id": existing_capture.id},
        )

    # 6. Создание нового кадра
    capture = Capture(
        installation_id=meta.installation_id,
        camera_id=camera.id,
        captured_at=normalized_captured_at,
        is_manual=meta.is_manual,
        aruco_area_px=meta.aruco_area_px,
        raw_image_path="",
        annotated_image_path="",
    )
    db.add(capture)
    db.flush()

    # Сохранение файлов на диск
    media_dir = get_media_dir() / str(meta.installation_id)
    media_dir.mkdir(parents=True, exist_ok=True)

    raw_filename = f"raw_{capture.id}.jpg"
    ann_filename = f"ann_{capture.id}.jpg"
    raw_path = media_dir / raw_filename
    ann_path = media_dir / ann_filename

    raw_bytes = await raw_image.read()
    raw_path.write_bytes(raw_bytes)

    ann_bytes = await annotated_image.read()
    ann_path.write_bytes(ann_bytes)

    capture.raw_image_path = f"media/{meta.installation_id}/{raw_filename}"
    capture.annotated_image_path = f"media/{meta.installation_id}/{ann_filename}"

    # При наличии сохранённого RGB-профиля камеры (и если edge_agent ещё не применял RGB) создаём отфильтрованную копию
    if not meta.rgb_applied:
        filtered_bytes, has_rgb = _apply_saved_camera_rgb(
            db, camera.hardware_id, raw_bytes
        )
        if has_rgb:
            rgb_rel = get_rgb_variant_rel_path(capture.raw_image_path)
            rgb_abs = resolve_media_abs_path(rgb_rel, get_media_dir())
            rgb_abs.write_bytes(filtered_bytes)

    # Сохранение замеров растений
    for m in meta.measurements:
        db.add(
            PlantMeasurement(
                capture_id=capture.id,
                plant_index=m.plant_index,
                area_px=m.area_px,
                width_px=m.width_px,
                height_px=m.height_px,
                width_mm=m.width_mm,
                height_mm=m.height_mm,
                area_cm2=m.area_cm2,
            )
        )

    db.commit()
    db.refresh(capture)

    # Связываем команду со снимком в памяти процесса
    if meta.command_id is not None:
        app_state.bind_command_capture(meta.command_id, capture.id)

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={"capture_id": capture.id},
    )


@router.post("/cameras/{hardware_id}/live-frame")
async def receive_live_frame(
    hardware_id: str,
    request: Request,
    _: Annotated[None, Depends(verify_edge_key)],
    x_timestamp: Annotated[str | None, Header(alias="X-Timestamp")] = None,
    x_frame_timestamp: Annotated[str | None, Header(alias="X-Frame-Timestamp")] = None,
    x_paused: Annotated[str | None, Header(alias="X-Paused")] = None,
    x_resolution: Annotated[str | None, Header(alias="X-Resolution")] = None,
    x_latency_ms: Annotated[str | None, Header(alias="X-Latency-Ms")] = None,
    x_stream_url: Annotated[str | None, Header(alias="X-Stream-Url")] = None,
    x_device_online: Annotated[str | None, Header(alias="X-Device-Online")] = None,
    x_frame_mode: Annotated[str | None, Header(alias="X-Frame-Mode")] = None,
    x_color_marker_detected: Annotated[
        str | None, Header(alias="X-Color-Marker-Detected")
    ] = None,
) -> dict[str, Any]:
    """Прием свежего кадра Live-стрима от Edge-агента."""
    session = live_manager.get_or_create(hardware_id)
    if x_color_marker_detected is not None:
        session.color_marker_detected = x_color_marker_detected in ("1", "true", "True")
    device_online: bool | None = None
    if x_device_online is not None:
        device_online = x_device_online in ("1", "true", "True")
        session.device_online = device_online
        session.stream_error = None if device_online else "Камера отключена"

    body_bytes = await request.body()
    if not body_bytes:
        if device_online is not None:
            session.is_active = True
            session.last_heartbeat = time.time()
            async with session.condition:
                session.condition.notify_all()
            return {
                "status": "ok",
                "active_subscribers": session.active_subscribers,
                "mode": session.mode,
                "segmentation_mode": session.segmentation_mode,
            }
        return {"status": "error", "message": "empty body"}

    raw_ts = x_timestamp or x_frame_timestamp
    try:
        ts = float(raw_ts) if raw_ts else time.time()
    except ValueError:
        ts = time.time()

    try:
        latency_ms = int(x_latency_ms) if x_latency_ms else None
    except ValueError:
        latency_ms = None

    is_paused = x_paused in ("1", "true", "True")
    res = x_resolution or "1280x720"
    frame_mode = x_frame_mode or "raw"

    stream_url = _normalize_stream_url(x_stream_url, request)

    session.push_frame(
        body_bytes,
        frame_timestamp=ts,
        is_paused=is_paused,
        resolution=res,
        latency_ms=latency_ms,
        stream_url=stream_url,
        frame_mode=frame_mode,
        device_online=device_online,
    )

    async with session.condition:
        session.condition.notify_all()

    return {
        "status": "ok",
        "active_subscribers": session.active_subscribers,
        "mode": session.mode,
        "segmentation_mode": session.segmentation_mode,
    }
