import asyncio
import time
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, joinedload

from server.api import edge as edge_api
from server.api.deps import get_db, require_role
from server.core.camera_calibration import (
    camera_calibration_service,
    serialize_camera,
)
from server.core.camera_calibration import (
    is_real_cameras_mode as _is_real_cameras_mode,
)
from server.core.camera_calibration import (
    load_camera_profiles_map as _load_camera_profiles_map,
)
from server.core.camera_calibration import (
    persist_camera_controls_profile as _persist_camera_controls_profile,
)
from server.core.camera_calibration import (
    resolve_camera_format_and_resolution as _resolve_camera_format_and_resolution,
)
from server.core.camera_live import (
    camera_live_service,
)
from server.core.camera_live import (
    probe_ustreamer_online_state as _probe_ustreamer_online_state,
)
from server.core.camera_live import (
    proxy_ustreamer_frames as _proxy_ustreamer_frames,
)
from server.core.camera_live import (
    wait_for_frame_or_shutdown as _wait_for_frame_or_shutdown,
)
from server.core.commands import wait_command_result
from server.core.state import app_state
from server.core.system_status import build_debug_info, check_rpi_online
from server.db.models import Camera, EdgeCommand, User
from server.schemas.cameras import (
    AutoColorCalibrateRequest,
    CameraBindRequest,
    CameraControlsPatch,
    CameraControlsSaveRequest,
    LiveModeRequest,
)
from shared.markers import generate_aruco_marker_svg, generate_color_marker_svg

router = APIRouter(prefix="/api/cameras", tags=["cameras"])

__all__ = [
    "AutoColorCalibrateRequest",
    "CameraBindRequest",
    "CameraControlsPatch",
    "CameraControlsSaveRequest",
    "LiveModeRequest",
    "_is_real_cameras_mode",
    "_load_camera_profiles_map",
    "_mjpeg_generator",
    "_persist_camera_controls_profile",
    "_probe_ustreamer_online_state",
    "_proxy_ustreamer_frames",
    "_resolve_camera_format_and_resolution",
    "_wait_for_frame_or_shutdown",
    "router",
    "serialize_camera",
]


async def _mjpeg_generator(
    hardware_id: str,
    shutdown_event: asyncio.Event | None = None,
):
    """Низколатентный генератор потока MJPEG (прямое проксирование ustreamer или push-буфер)."""
    async for chunk in camera_live_service.mjpeg_generator(
        hardware_id,
        shutdown_event=shutdown_event,
        proxy_frames_fn=_proxy_ustreamer_frames,
    ):
        yield chunk


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

    finished_cmd = await wait_command_result(cmd.id, 10.0, db)

    resp: dict[str, Any] = {
        "command_id": finished_cmd.id,
        "status": finished_cmd.status,
    }

    debug_info = build_debug_info(finished_cmd, current_user, db)

    if finished_cmd.status == "error":
        app_state.pop_scanned_cameras(finished_cmd.id)
        resp["result_message"] = finished_cmd.result_message or "Ошибка сканирования"
        resp["cameras"] = []
        if debug_info:
            resp["debug_info"] = debug_info
        return resp

    # Извлечение результатов сканирования и очистка app_state
    scanned_hw_ids = app_state.pop_scanned_cameras(finished_cmd.id) or []
    resp["cameras"] = camera_calibration_service.reconcile_scanned_cameras(
        db, scanned_hw_ids
    )
    if debug_info:
        resp["debug_info"] = debug_info

    return resp


@router.get("", response_model=list[dict[str, Any]])
def list_cameras(
    current_user: Annotated[User, Depends(require_role("developer", "biologist"))],
    db: Annotated[Session, Depends(get_db)],
) -> list[dict[str, Any]]:
    """4.3. GET /api/cameras (Developer, Biologist).

    Список всех камер с информацией о подключении и привязке к установкам.
    """
    cameras = (
        db.query(Camera)
        .options(joinedload(Camera.installation))
        .order_by(Camera.id.asc())
        .all()
    )
    profiles_by_hw = _load_camera_profiles_map(db, [cam.hardware_id for cam in cameras])
    return [serialize_camera(cam, db, profiles_by_hw=profiles_by_hw) for cam in cameras]


@router.get("/markers/aruco.svg")
def download_aruco_marker_svg(
    current_user: Annotated[User, Depends(require_role("developer", "biologist"))],
    size_mm: Annotated[float, Query(ge=10.0, le=300.0)] = 50.0,
) -> Response:
    """Генерирует и отдаёт готовый к печати SVG ArUco-маркер DICT_4X4_50 (ID=0)."""
    svg_content = generate_aruco_marker_svg(size_mm=size_mm)
    filename = f"growvision_aruco_4x4_id0_{size_mm:g}mm.svg"
    return Response(
        content=svg_content,
        media_type="image/svg+xml; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/markers/color.svg")
def download_color_marker_svg(
    current_user: Annotated[User, Depends(require_role("developer", "biologist"))],
    size_mm: Annotated[float, Query(ge=10.0, le=300.0)] = 50.0,
) -> Response:
    """Генерирует и отдаёт готовый к печати SVG-шаблон эталонного цветового маркера."""
    svg_content = generate_color_marker_svg(size_mm=size_mm)
    filename = f"growvision_color_marker_{size_mm:g}mm.svg"
    return Response(
        content=svg_content,
        media_type="image/svg+xml; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


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
    return camera_calibration_service.bind_camera_to_installation(
        db, camera_id, body.installation_id
    )


@router.post("/{camera_id}/autocalibrate", response_model=dict[str, Any])
async def autocalibrate_camera(
    camera_id: int,
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """4.3. POST /api/cameras/{camera_id}/autocalibrate (Developer).

    Запуск автокалибровки камеры на Raspberry Pi с ожиданием ответа до 10 с.
    """
    check_rpi_online(db)
    cam = camera_calibration_service.get_camera_or_404(db, camera_id)
    cmd = camera_calibration_service.enqueue_autocalibrate_command(db, cam)
    finished_cmd = await wait_command_result(cmd.id, 10.0, db)
    return camera_calibration_service.finalize_autocalibrate_result(
        db, cam, finished_cmd
    )


@router.post("/{camera_id}/live/start", response_model=dict[str, Any])
def start_camera_live(
    camera_id: int,
    current_user: Annotated[User, Depends(require_role("developer", "biologist"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Запуск Live-сессии камеры (Developer, Biologist)."""
    check_rpi_online(db)
    cam = camera_calibration_service.get_camera_or_404(db, camera_id)
    cur_fmt, cur_res = _resolve_camera_format_and_resolution(db, cam.hardware_id)
    return camera_live_service.start_session(
        db,
        cam,
        cur_fmt=cur_fmt,
        cur_res=cur_res,
        normalize_seg_fn=edge_api.normalize_segmentation_mode,
    )


@router.post("/{camera_id}/live/stop", response_model=dict[str, Any])
def stop_camera_live(
    camera_id: int,
    current_user: Annotated[User, Depends(require_role("developer", "biologist"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Остановка Live-сессии камеры (Developer, Biologist)."""
    cam = camera_calibration_service.get_camera_or_404(db, camera_id)
    return camera_live_service.stop_session(db, cam)


@router.post("/{camera_id}/live/mode", response_model=dict[str, Any])
async def set_camera_live_mode(
    camera_id: int,
    body: LiveModeRequest,
    current_user: Annotated[User, Depends(require_role("developer", "biologist"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Переключение режима Live-стрима ('raw' / 'annotated')."""
    cam = camera_calibration_service.get_camera_or_404(db, camera_id)
    return await camera_live_service.set_mode(cam, body.mode)


@router.get("/{camera_id}/live/status", response_model=dict[str, Any])
async def get_camera_live_status(
    camera_id: int,
    current_user: Annotated[User, Depends(require_role("developer", "biologist"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Статус Live-сессии (FPS, задержка мс, пауза, разрешение, режим, online)."""
    cam = camera_calibration_service.get_camera_or_404(db, camera_id)
    _, cur_res = _resolve_camera_format_and_resolution(db, cam.hardware_id)
    return await camera_live_service.get_status_payload(
        db,
        cam,
        cur_res=cur_res,
        normalize_seg_fn=edge_api.normalize_segmentation_mode,
        probe_fn=_probe_ustreamer_online_state,
    )


@router.get("/{camera_id}/live/snapshot")
async def get_camera_live_snapshot(
    camera_id: int,
    current_user: Annotated[User, Depends(require_role("developer", "biologist"))],
    db: Annotated[Session, Depends(get_db)],
    r: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
    g: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
    b: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
) -> Response:
    """Скачивание тестового JPEG-снимка напрямую без записи в БД и таймлапс (с RGB-фильтром)."""
    cam = camera_calibration_service.get_camera_or_404(db, camera_id)
    frame = await camera_live_service.capture_snapshot_bytes(cam, r=r, g=g, b=b)
    return Response(
        content=frame,
        media_type="image/jpeg",
        headers={
            "Content-Disposition": f'attachment; filename="snapshot_{cam.hardware_id}_{int(time.time())}.jpg"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/{camera_id}/live/stream")
async def get_camera_live_stream(
    camera_id: int,
    current_user: Annotated[User, Depends(require_role("developer", "biologist"))],
    db: Annotated[Session, Depends(get_db)],
    mode: Annotated[str | None, Query()] = None,
) -> StreamingResponse:
    """Low-latency MJPEG-стрим камеры для прямого отображения в браузере."""
    cam = camera_calibration_service.get_camera_or_404(db, camera_id)
    if mode in ("raw", "annotated"):
        await camera_live_service.set_mode(cam, mode)
    return StreamingResponse(
        _mjpeg_generator(cam.hardware_id),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@router.get("/{camera_id}/controls", response_model=dict[str, Any])
async def get_camera_controls(
    camera_id: int,
    current_user: Annotated[User, Depends(require_role("developer", "biologist"))],
    db: Annotated[Session, Depends(get_db)],
    refresh: Annotated[bool, Query()] = False,
) -> dict[str, Any]:
    """Получение поддерживаемых V4L2-контролов, форматов и RGB настроек."""
    cam = camera_calibration_service.get_camera_or_404(db, camera_id)
    return await camera_calibration_service.get_controls_payload(
        db,
        cam,
        refresh=refresh,
        normalize_seg_fn=edge_api.normalize_segmentation_mode,
    )


@router.patch("/{camera_id}/controls", response_model=dict[str, Any])
def update_camera_controls(
    camera_id: int,
    body: CameraControlsPatch,
    current_user: Annotated[User, Depends(require_role("developer", "biologist"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Применение настроек V4L2, смена разрешения/формата, RGB-коррекции и segmentation_mode."""
    cam = camera_calibration_service.get_camera_or_404(db, camera_id)

    updated_controls, clamped_rgb, saved_seg_mode = _persist_camera_controls_profile(
        db=db,
        cam=cam,
        controls=body.controls,
        fmt=body.format,
        resolution=body.resolution,
        rgb=body.rgb,
        segmentation_mode=body.segmentation_mode,
        enqueue_edge_command=True,
        normalize_seg_fn=edge_api.normalize_segmentation_mode,
    )

    resp: dict[str, Any] = {
        "status": "updated",
        "camera_id": cam.id,
        "hardware_id": cam.hardware_id,
        "format": body.format,
        "resolution": body.resolution,
        "rgb": clamped_rgb if body.rgb is not None else None,
        "segmentation_mode": saved_seg_mode,
    }
    if updated_controls is not None:
        resp["controls"] = updated_controls
    return resp


@router.post("/{camera_id}/controls/save", response_model=dict[str, Any])
def save_camera_controls(
    camera_id: int,
    body: CameraControlsSaveRequest,
    current_user: Annotated[User, Depends(require_role("developer", "biologist"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Сохраняет настройки профиля камеры ('next_frame' или 'all_gallery' без разрушения raw_...jpg)."""
    cam = camera_calibration_service.get_camera_or_404(db, camera_id)
    return camera_calibration_service.save_controls_and_apply_rgb(
        db=db,
        cam=cam,
        scope=body.scope,
        controls=body.controls,
        fmt=body.format,
        resolution=body.resolution,
        rgb=body.rgb,
        segmentation_mode=body.segmentation_mode,
        base_media_dir=edge_api.get_media_dir(),
        normalize_seg_fn=edge_api.normalize_segmentation_mode,
    )


@router.post("/{camera_id}/auto-color-calibrate", response_model=dict[str, Any])
async def auto_color_calibrate_camera(
    camera_id: int,
    current_user: Annotated[User, Depends(require_role("developer", "biologist"))],
    db: Annotated[Session, Depends(get_db)],
    preview: bool = Query(default=False),
    body: Annotated[AutoColorCalibrateRequest | None, Body()] = None,
) -> dict[str, Any]:
    """Запуск автоцветокоррекции по эталонному цветовому маркеру в кадре."""
    check_rpi_online(db)
    cam = camera_calibration_service.get_camera_or_404(db, camera_id)
    is_preview = bool(preview or (body.preview if body is not None else False))
    cmd = camera_calibration_service.enqueue_auto_color_calibrate_command(
        db, cam, is_preview
    )
    finished_cmd = await wait_command_result(cmd.id, 10.0, db)
    return camera_calibration_service.finalize_auto_color_calibrate_result(
        db, cam, finished_cmd, is_preview
    )
