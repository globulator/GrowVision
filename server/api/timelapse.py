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
from server.core.rgb import resolve_capture_display_rel_path
from server.db.models import Camera, Capture, Installation, User
from shared.logger import get_logger

logger = get_logger("server.timelapse")

router = APIRouter(prefix="/api/timelapse", tags=["timelapse"])


class VideoRequest(BaseModel):
    """Тело запроса POST /api/timelapse/{installation_id}/video."""

    model_config = ConfigDict(extra="ignore")

    camera_id: int | None = None
    step: int = Field(default=1, strict=True, ge=1)
    duration_sec: float | None = Field(default=None, ge=0.5, le=300.0)
    fps: float | None = Field(default=None, ge=1.0, le=60.0)


def get_frame_file_path(image_path: str) -> Path:
    """Строит абсолютный путь к файлу кадра строго через get_media_dir()."""
    base_media = get_media_dir()
    p = Path(image_path)
    if p.parts and p.parts[0] == "media":
        return base_media / Path(*p.parts[1:])
    return base_media / p


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
        measurements = list(cap.plant_measurements or [])
        plants_count = len(measurements)
        total_area_px = round(sum(float(m.area_px) for m in measurements), 1)
        avg_area_px = (
            round(total_area_px / plants_count, 1) if plants_count > 0 else 0.0
        )

        has_aruco_marker = (
            cap.aruco_area_px is not None and float(cap.aruco_area_px) > 0
        )
        has_persp_cm2 = plants_count > 0 and all(
            m.area_cm2 is not None for m in measurements
        )
        has_ratio_cal = inst.aruco_size_mm is not None and float(inst.aruco_size_mm) > 0
        has_aruco = bool(has_aruco_marker and (has_persp_cm2 or has_ratio_cal))

        total_area_cm2: float | None = None
        avg_area_cm2: float | None = None
        if has_aruco:
            if plants_count == 0:
                total_area_cm2 = 0.0
                avg_area_cm2 = 0.0
            elif has_persp_cm2:
                total_area_cm2 = round(
                    sum(
                        float(m.area_cm2)
                        for m in measurements
                        if m.area_cm2 is not None
                    ),
                    2,
                )
                avg_area_cm2 = round(total_area_cm2 / plants_count, 2)
            else:
                scale = (float(inst.aruco_size_mm) / 10.0) ** 2
                total_area_cm2 = round(
                    total_area_px * scale / float(cap.aruco_area_px), 2
                )
                avg_area_cm2 = round(total_area_cm2 / plants_count, 2)

        meas = next(
            (m for m in measurements if m.plant_index == 0),
            None,
        )
        area_display = calculate_area_display(
            meas.area_px if meas else None,
            cap.aruco_area_px,
            inst.aruco_size_mm,
            area_cm2=meas.area_cm2 if meas else None,
        )
        frames.append(
            {
                "capture_id": cap.id,
                "camera_id": cap.camera_id,
                "captured_at": format_utc_z(cap.captured_at),
                "raw_url": f"/{resolve_capture_display_rel_path(cap.raw_image_path, get_media_dir())}",
                "annotated_url": f"/{cap.annotated_image_path}",
                "area_display": area_display,
                "total_area_px": total_area_px,
                "avg_area_px": avg_area_px,
                "total_area_cm2": total_area_cm2,
                "avg_area_cm2": avg_area_cm2,
                "plants_count": plants_count,
                "has_aruco": has_aruco,
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

    # Кадры с отсутствующими файлами пропускаются
    media_dir = get_media_dir()
    valid_captures: list[tuple[Capture, Path]] = []
    for cap in captures:
        rel_path = resolve_capture_display_rel_path(cap.raw_image_path, media_dir)
        file_path = get_frame_file_path(rel_path)
        if file_path.is_file():
            valid_captures.append((cap, file_path))

    n_valid = len(valid_captures)
    if n_valid == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Плановые снимки не найдены",
        )

    # 1 кадр -> возвращаем JPEG оригинал
    if n_valid == 1:
        _, single_path = valid_captures[0]
        img_bytes = single_path.read_bytes()
        return Response(
            content=img_bytes,
            media_type="image/jpeg",
            headers={"Content-Length": str(len(img_bytes))},
        )

    # >= 2 кадров -> сборка MP4 с прореживанием
    step = body.step
    if step <= 1:
        selected = valid_captures
    else:
        selected = valid_captures[::step]
        if valid_captures[-1][0].id != selected[-1][0].id:
            selected.append(valid_captures[-1])

    settings = get_settings()
    fps_max = settings.video_max_fps
    min_dur = settings.video_min_duration_sec
    n_selected = len(selected)

    if body.duration_sec and body.duration_sec > 0:
        framerate_str = f"{n_selected}/{body.duration_sec}"
    elif body.fps and body.fps >= 1.0:
        framerate_str = str(body.fps)
    elif n_selected >= fps_max * min_dur:
        framerate_str = str(fps_max)
    else:
        framerate_str = f"{n_selected}/{min_dur}"

    with tempfile.TemporaryDirectory() as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        for idx, (_, src_file) in enumerate(selected, start=1):
            dst_link = tmp_dir / f"frame_{idx:06d}.jpg"
            try:
                dst_link.symlink_to(src_file)
            except OSError:
                dst_link.write_bytes(src_file.read_bytes())

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
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                timeout=600,
            )
        except subprocess.TimeoutExpired as e:
            logger.error("Таймаут сборки видео ffmpeg: %s", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Ошибка кодирования видео",
            ) from e
        except subprocess.CalledProcessError as e:
            logger.error(
                "Ошибка ffmpeg (код %s): %s",
                e.returncode,
                e.stderr.decode(errors="replace"),
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Ошибка кодирования видео",
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
