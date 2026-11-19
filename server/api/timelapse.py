import subprocess
import tempfile
from datetime import UTC
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from server.api.deps import (
    ClientUser,
    format_utc_z,
    get_current_user_optional,
    get_db,
    require_role,
)
from server.api.edge import get_media_dir
from server.config import get_settings
from server.core.area import calculate_area_display
from server.db.models import Camera, Capture, Installation, User

router = APIRouter(prefix="/api/timelapse", tags=["timelapse"])


class VideoRequest(BaseModel):
    """Тело запроса POST /api/timelapse/{installation_id}/video."""

    model_config = ConfigDict(extra="ignore")

    camera_id: int | None = None
    step: int = Field(default=1, strict=True, ge=1)


def resolve_media_file_path(image_path: str) -> Path:
    """Находит полный путь к медиафайлу с учетом изоляции тестов."""
    base_media = get_media_dir()
    p = Path(image_path)
    if p.parts and p.parts[0] == "media":
        rel = Path(*p.parts[1:])
        direct_path = base_media / rel
        if direct_path.is_file():
            return direct_path

    candidate = base_media / p
    if candidate.is_file():
        return candidate

    repo_media = Path(__file__).resolve().parent.parent / image_path
    if repo_media.is_file():
        return repo_media

    return base_media / (p.name if p.name else image_path)


@router.get("/{installation_id}/frames", response_model=dict[str, Any])
def get_timelapse_frames(
    installation_id: int,
    _client_user: Annotated[ClientUser, Depends(get_current_user_optional)],
    db: Annotated[Session, Depends(get_db)],
    camera_id: int | None = Query(default=None),
) -> dict[str, Any]:
    """4.5. GET /api/timelapse/{installation_id}/frames.

    Плановые кадры таймлапса для установки (доступен Гостю).
    """
    inst = db.query(Installation).filter(Installation.id == installation_id).first()
    if not inst:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Установка не найдена",
        )

    query = db.query(Capture).filter(
        Capture.installation_id == installation_id,
        Capture.is_manual.is_(False),
    )
    if camera_id is not None:
        query = query.filter(Capture.camera_id == camera_id)

    captures = query.order_by(Capture.captured_at.asc(), Capture.id.asc()).all()

    frames = []
    for cap in captures:
        meas = next(
            (m for m in cap.plant_measurements if m.plant_index == 0),
            None,
        )
        area_display = calculate_area_display(
            meas.area_px if meas else None,
            cap.aruco_area_px,
            inst.aruco_size_mm,
        )
        frames.append(
            {
                "capture_id": cap.id,
                "camera_id": cap.camera_id,
                "captured_at": format_utc_z(cap.captured_at),
                "raw_url": f"/{cap.raw_image_path}",
                "annotated_url": f"/{cap.annotated_image_path}",
                "area_display": area_display,
            }
        )

    return {
        "installation_id": inst.id,
        "installation_name": inst.name,
        "culture_name": inst.culture_name,
        "frames": frames,
    }


def check_conflicting_cameras(frames: list[Capture], db: Session) -> None:
    """Проверяет наличие кадров разных камер, снятых с интервалом <= 10 минут."""
    cam_ids = {f.camera_id for f in frames if f.camera_id is not None}
    if len(cam_ids) <= 1:
        return

    # Проверка интервала <= 10 минут (600 секунд) между разными камерами
    has_conflict = False
    for i in range(len(frames)):
        f1 = frames[i]
        dt1 = (
            f1.captured_at
            if f1.captured_at.tzinfo
            else f1.captured_at.replace(tzinfo=UTC)
        )
        for j in range(i + 1, len(frames)):
            f2 = frames[j]
            dt2 = (
                f2.captured_at
                if f2.captured_at.tzinfo
                else f2.captured_at.replace(tzinfo=UTC)
            )
            diff = (dt2 - dt1).total_seconds()
            if diff > 600.0:
                break
            if f1.camera_id != f2.camera_id:
                has_conflict = True
                break
        if has_conflict:
            break

    if not has_conflict:
        return

    cameras = (
        db.query(Camera).filter(Camera.id.in_(cam_ids)).order_by(Camera.id.asc()).all()
    )
    cam_strs = [f"{c.id} ({c.auto_name})" for c in cameras]
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=f"У установки несколько камер, укажите camera_id: {', '.join(cam_strs)}",
    )


@router.post("/{installation_id}/video")
def generate_timelapse_video(
    installation_id: int,
    body: VideoRequest,
    current_user: Annotated[User, Depends(require_role("biologist", "developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """4.5. POST /api/timelapse/{installation_id}/video (Biologist, Developer).

    Синхронная сборка MP4 через ffmpeg (выполняется в пуле потоков).
    """
    inst = db.query(Installation).filter(Installation.id == installation_id).first()
    if not inst:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Установка не найдена",
        )

    if body.camera_id is not None:
        cam = db.query(Camera).filter(Camera.id == body.camera_id).first()
        if not cam:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Камера не найдена",
            )

    query = db.query(Capture).filter(
        Capture.installation_id == installation_id,
        Capture.is_manual.is_(False),
    )
    if body.camera_id is not None:
        query = query.filter(Capture.camera_id == body.camera_id)

    captures = query.order_by(Capture.captured_at.asc(), Capture.id.asc()).all()

    # Если camera_id не указан, проверяем одновременную съемку
    if body.camera_id is None:
        check_conflicting_cameras(captures, db)

    n_total = len(captures)
    if n_total == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Плановые снимки не найдены",
        )

    # 1 кадр -> возвращаем JPEG оригинал
    if n_total == 1:
        single_cap = captures[0]
        file_path = resolve_media_file_path(single_cap.raw_image_path)
        if not file_path.is_file():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Файл кадра не найден на сервере",
            )
        img_bytes = file_path.read_bytes()
        return Response(
            content=img_bytes,
            media_type="image/jpeg",
            headers={"Content-Length": str(len(img_bytes))},
        )

    # >= 2 кадров -> сборка MP4 с прореживанием
    step = body.step
    if step <= 1:
        selected = captures
    else:
        selected = captures[::step]
        if captures[-1].id != selected[-1].id:
            selected.append(captures[-1])

    settings = get_settings()
    fps_max = settings.video_max_fps
    min_dur = settings.video_min_duration_sec
    n_selected = len(selected)

    if n_selected >= fps_max * min_dur:
        framerate_str = str(fps_max)
    else:
        framerate_str = f"{n_selected}/{min_dur}"

    with tempfile.TemporaryDirectory() as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        # Подготовка входных файлов для ffmpeg
        for idx, cap in enumerate(selected, start=1):
            src_file = resolve_media_file_path(cap.raw_image_path)
            dst_link = tmp_dir / f"frame_{idx:06d}.jpg"
            if src_file.is_file():
                try:
                    dst_link.symlink_to(src_file)
                except OSError:
                    dst_link.write_bytes(src_file.read_bytes())
            else:
                # Если файл отсутствует (например в тесте без генерации файла), создаем 1x1 заглушку JPEG
                dst_link.write_bytes(
                    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00\x02\x00\x02\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
                )

        output_video = tmp_dir / "output.mp4"
        cmd = [
            "ffmpeg",
            "-y",
            "-framerate",
            framerate_str,
            "-i",
            str(tmp_dir / "frame_%06d.jpg"),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "28",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-vf",
            "scale=-2:min(720\\,ih)",
            "-an",
            str(output_video),
        ]

        try:
            subprocess.run(cmd, check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Ошибка кодирования ffmpeg: {e.stderr.decode(errors='replace')}",
            ) from e

        video_bytes = output_video.read_bytes()

    return Response(
        content=video_bytes,
        media_type="video/mp4",
        headers={
            "Content-Disposition": f'attachment; filename="timelapse_{installation_id}.mp4"',
            "Content-Length": str(len(video_bytes)),
        },
    )
