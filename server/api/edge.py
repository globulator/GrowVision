import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from server.api.deps import get_db, verify_edge_key
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
    payload_json = hb.model_dump_json()
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

    # 2. Обновление статуса известных камер
    detected_hw_ids = {c.hardware_id for c in hb.detected_cameras}
    known_cameras = db.query(Camera).all()
    for cam in known_cameras:
        if cam.hardware_id in detected_hw_ids:
            cam.is_online = True
            cam.last_seen_at = now
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

    # 4. Формирование списка активных установок
    active_installations = (
        db.query(Installation).filter(Installation.is_archived.is_(False)).all()
    )
    active_inst_data: list[HeartbeatActiveInstallation] = []
    for inst in active_installations:
        cam_ids = [c.hardware_id for c in inst.cameras]
        active_inst_data.append(
            HeartbeatActiveInstallation(
                id=inst.id,
                light_gpio_pin=inst.light_gpio_pin,
                flash_gpio_pin=inst.flash_gpio_pin,
                camera_hardware_ids=cam_ids,
                capture_delay_sec=inst.capture_delay_sec,
            )
        )

    db.commit()

    return HeartbeatResponse(
        capture_interval_hours=capture_interval_hours,
        debug_mode=debug_mode,
        active_installations=active_inst_data,
        capture_delay_sec=capture_delay_sec,
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

    if oldest_pending.is_global:
        active_installations = (
            db.query(Installation).filter(Installation.is_archived.is_(False)).all()
        )
        installations_list = [
            {
                "id": inst.id,
                "light_gpio_pin": inst.light_gpio_pin,
                "flash_gpio_pin": inst.flash_gpio_pin,
                "camera_hardware_ids": [c.hardware_id for c in inst.cameras],
                "capture_delay_sec": inst.capture_delay_sec,
            }
            for inst in active_installations
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
        inst_data = (
            {
                "id": inst.id,
                "light_gpio_pin": inst.light_gpio_pin,
                "flash_gpio_pin": inst.flash_gpio_pin,
                "camera_hardware_ids": [c.hardware_id for c in inst.cameras],
                "capture_delay_sec": inst.capture_delay_sec,
            }
            if inst
            else None
        )
        cmd_data = {
            "id": oldest_pending.id,
            "command_type": oldest_pending.command_type,
            "is_global": False,
            "installation": inst_data,
            "installations": None,
            "payload": payload,
        }

    return [cmd_data]


@router.post(
    "/commands/{command_id}/result",
    response_model=dict[str, bool],
    dependencies=[Depends(verify_edge_key)],
)
def edge_command_result(
    command_id: int,
    body: CommandResult,
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

    db.commit()

    return {"ok": True}


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

    # Сохранение замеров растений
    for m in meta.measurements:
        db.add(
            PlantMeasurement(
                capture_id=capture.id,
                plant_index=m.plant_index,
                area_px=m.area_px,
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
