from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from server.api.deps import format_utc_z, get_db, require_role
from server.db.models import Camera, Installation, User

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
