import asyncio
import time
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from server.api.deps import format_utc_z, get_db, require_role
from server.core.state import app_state
from server.core.system_status import check_rpi_online, is_debug_mode
from server.db.models import Camera, EdgeCommand, Installation, User

router = APIRouter(prefix="/api/cameras", tags=["cameras"])


class CameraBindRequest(BaseModel):
    """Тело запроса POST /api/cameras/{camera_id}/bind."""

    model_config = ConfigDict(extra="ignore")

    installation_id: int | None = None


def serialize_camera(cam: Camera) -> dict[str, Any]:
    """Формирует представление камеры по API_CONTRACT.md п. 4.3."""
    return {
        "id": cam.id,
        "hardware_id": cam.hardware_id,
        "auto_name": cam.auto_name,
        "is_online": cam.is_online,
        "last_seen_at": format_utc_z(cam.last_seen_at),
        "installation_id": cam.installation_id,
        "installation_name": cam.installation.name if cam.installation else None,
    }


@router.post("/scan", response_model=dict[str, Any])
async def scan_cameras(
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """4.3. POST /api/cameras/scan (Developer).

    Запуск сканирования камер на Raspberry Pi (SCAN_CAMERAS) с ожиданием до 10 с.
    """
    check_rpi_online(db)

    cmd = EdgeCommand(
        command_type="SCAN_CAMERAS",
        installation_id=None,
        is_global=True,
        payload=None,
        status="pending",
    )
    db.add(cmd)
    db.commit()
    db.refresh(cmd)

    start_time = time.monotonic()
    finished_cmd = None
    while time.monotonic() - start_time < 10.0:
        await asyncio.sleep(0.05)
        db.expire_all()
        check_cmd = db.query(EdgeCommand).filter(EdgeCommand.id == cmd.id).first()
        if not check_cmd:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Команда не найдена",
            )
        if check_cmd.status == "timeout":
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="Превышено время ожидания ответа Raspberry Pi",
            )
        if check_cmd.status in ("done", "error"):
            finished_cmd = check_cmd
            break

    if not finished_cmd:
        db.expire_all()
        check_cmd = db.query(EdgeCommand).filter(EdgeCommand.id == cmd.id).first()
        if check_cmd and check_cmd.status == "pending":
            check_cmd.status = "timeout"
            db.commit()
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Превышено время ожидания ответа Raspberry Pi",
        )

    resp: dict[str, Any] = {
        "command_id": finished_cmd.id,
        "status": finished_cmd.status,
    }

    debug_info = None
    if is_debug_mode(db):
        debug_info = {
            "command": {
                "id": finished_cmd.id,
                "command_type": finished_cmd.command_type,
                "target": "all",
                "payload": None,
            },
            "log_line": finished_cmd.debug_log or "",
        }

    if finished_cmd.status == "error":
        app_state.pop_scanned_cameras(finished_cmd.id)
        resp["result_message"] = finished_cmd.result_message or "Ошибка сканирования"
        resp["cameras"] = []
        if debug_info:
            resp["debug_info"] = debug_info
        return resp

    # Извлечение результатов сканирования и очистка app_state
    scanned_hw_ids = app_state.pop_scanned_cameras(finished_cmd.id) or []
    now = datetime.now(UTC)

    # Регистрация новых камер и обновление существующих
    result_cameras: list[Camera] = []
    for hw_id in scanned_hw_ids:
        cam = db.query(Camera).filter(Camera.hardware_id == hw_id).first()
        if not cam:
            cam = Camera(
                hardware_id=hw_id,
                auto_name=hw_id,
                is_online=True,
                last_seen_at=now,
                installation_id=None,
            )
            db.add(cam)
            db.flush()
        else:
            cam.is_online = True
            cam.last_seen_at = now
        result_cameras.append(cam)

    db.commit()

    # Сортировка по id asc
    result_cameras.sort(key=lambda c: c.id)
    resp["cameras"] = [serialize_camera(c) for c in result_cameras]
    if debug_info:
        resp["debug_info"] = debug_info

    return resp


@router.get("", response_model=list[dict[str, Any]])
def list_cameras(
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> list[dict[str, Any]]:
    """4.3. GET /api/cameras (Developer).

    Список всех камер с информацией о подключении и привязке к установкам.
    """
    cameras = db.query(Camera).order_by(Camera.id.asc()).all()
    return [serialize_camera(cam) for cam in cameras]


@router.post("/{camera_id}/bind", response_model=dict[str, Any])
def bind_camera(
    camera_id: int,
    body: CameraBindRequest,
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """4.3. POST /api/cameras/{camera_id}/bind (Developer).

    Привязка камеры к установке или отвязка (installation_id: null).
    """
    cam = db.query(Camera).filter(Camera.id == camera_id).first()
    if not cam:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Камера не найдена",
        )

    if body.installation_id is not None:
        inst = (
            db.query(Installation)
            .filter(Installation.id == body.installation_id)
            .first()
        )
        if not inst:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Установка не найдена",
            )
        if inst.is_archived:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Нельзя привязать камеру к архивной установке",
            )

    cam.installation_id = body.installation_id
    db.commit()
    db.refresh(cam)

    return serialize_camera(cam)
