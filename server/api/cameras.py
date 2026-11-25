import asyncio
import contextlib
import json
import re
import time
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from edge_agent.camera_controls import (
    apply_control_dependencies,
    get_default_mock_controls,
    get_default_mock_formats,
)
from server.api import edge as edge_api
from server.api.deps import format_utc_z, get_db, require_role
from server.core.live_manager import live_manager
from server.core.rgb import (
    apply_rgb_to_camera_captures,
    apply_rgb_to_jpeg_bytes,
    clamp_rgb_dict,
)
from server.core.state import app_state
from server.core.system_status import check_rpi_online, is_debug_mode, is_rpi_online
from server.db.models import Camera, EdgeCommand, Installation, SystemSetting, User
from shared.markers import generate_aruco_marker_svg, generate_color_marker_svg
from shared.schemas import SegmentationMode

router = APIRouter(prefix="/api/cameras", tags=["cameras"])


def _is_real_cameras_mode(db: Session) -> bool:
    """Определяет по телеметрии, работают ли камеры Edge-агента в реальном режиме (rpi или гибрид)."""
    telem = (
        db.query(SystemSetting)
        .filter(SystemSetting.key == "rpi_last_telemetry")
        .first()
    )
    if not telem or not telem.value:
        return False
    try:
        data = json.loads(telem.value)
    except Exception:  # noqa: BLE001
        return False
    if not isinstance(data, dict):
        return False
    hw = data.get("hardware") if isinstance(data.get("hardware"), dict) else {}
    if (
        data.get("hardware_mode") == "rpi"
        or data.get("is_raspberry_pi") is True
        or hw.get("gpio_mode") == "rpi"
    ):
        return True
    if "mock_cameras" in data:
        return not bool(data["mock_cameras"])
    if "mock_cameras" in hw:
        return not bool(hw["mock_cameras"])
    return False


_is_rpi_hardware_mode = _is_real_cameras_mode


def _resolve_camera_format_and_resolution(
    db: Session, hardware_id: str, formats: list[dict[str, Any]] | None = None
) -> tuple[str, str]:
    """Определяет актуальные формат и разрешение камеры из настроек, форматов или профиля."""
    fmt_setting = (
        db.query(SystemSetting)
        .filter(SystemSetting.key == f"camera_format_{hardware_id}")
        .first()
    )
    res_setting = (
        db.query(SystemSetting)
        .filter(SystemSetting.key == f"camera_res_{hardware_id}")
        .first()
    )

    avail_formats = formats
    if avail_formats is None:
        fmt_list_setting = (
            db.query(SystemSetting)
            .filter(SystemSetting.key == f"camera_formats_{hardware_id}")
            .first()
        )
        avail_formats = []
        if fmt_list_setting and fmt_list_setting.value:
            with contextlib.suppress(Exception):
                parsed = json.loads(fmt_list_setting.value)
                if isinstance(parsed, list):
                    avail_formats = parsed

    profile_fmt: str | None = None
    profile_res: str | None = None
    profile_setting = (
        db.query(SystemSetting)
        .filter(SystemSetting.key == f"camera_profile_{hardware_id}")
        .first()
    )
    if profile_setting and profile_setting.value:
        res_m = re.search(r"(\d+x\d+)", profile_setting.value)
        if res_m:
            profile_res = res_m.group(1)
        fmt_m = re.search(r"\(([A-Za-z0-9]+)\)", profile_setting.value)
        if fmt_m:
            profile_fmt = fmt_m.group(1).upper()

    if fmt_setting and fmt_setting.value:
        cur_fmt = fmt_setting.value
    elif avail_formats:
        mjpg_fmt = next(
            (f.get("format") for f in avail_formats if f.get("format") == "MJPG"),
            None,
        )
        cur_fmt = mjpg_fmt or avail_formats[0].get("format") or "MJPG"
    elif profile_fmt:
        cur_fmt = profile_fmt
    else:
        cur_fmt = "MJPG"

    if res_setting and res_setting.value:
        cur_res = res_setting.value
    else:
        matched_res: list[str] = []
        if avail_formats:
            fmt_entry = next(
                (f for f in avail_formats if f.get("format") == cur_fmt),
                avail_formats[0],
            )
            raw_res = (
                fmt_entry.get("resolutions") if isinstance(fmt_entry, dict) else []
            )
            if isinstance(raw_res, list):
                matched_res = [
                    str(r) for r in raw_res if isinstance(r, str) and "x" in r
                ]
        if matched_res:
            # Для Live-стрима при наличии 1280x720 предпочитаем его, иначе первое доступное у камеры
            cur_res = "1280x720" if "1280x720" in matched_res else matched_res[0]
        elif profile_res:
            cur_res = profile_res
        else:
            cur_res = "1280x720"

    return cur_fmt, cur_res


def _upsert_setting(db: Session, key: str, value: str, now: datetime) -> None:
    """Создаёт или обновляет SystemSetting по ключу."""
    existing = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if existing:
        existing.value = value
        existing.updated_at = now
    else:
        db.add(SystemSetting(key=key, value=value, updated_at=now))


class CameraBindRequest(BaseModel):
    """Тело запроса POST /api/cameras/{camera_id}/bind."""

    model_config = ConfigDict(extra="ignore")

    installation_id: int | None = None


def serialize_camera(cam: Camera, db: Session | None = None) -> dict[str, Any]:
    """Формирует представление камеры по API_CONTRACT.md п. 4.3."""
    res = {
        "id": cam.id,
        "hardware_id": cam.hardware_id,
        "auto_name": cam.auto_name,
        "is_online": cam.is_online,
        "last_seen_at": format_utc_z(cam.last_seen_at),
        "installation_id": cam.installation_id,
        "installation_name": cam.installation.name if cam.installation else None,
    }
    if db is not None:
        setting = (
            db.query(SystemSetting)
            .filter(SystemSetting.key == f"camera_profile_{cam.hardware_id}")
            .first()
        )
        if setting and setting.value:
            res["calibration_summary"] = setting.value
    return res


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
    scanned_set = set(scanned_hw_ids)
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

    # Камеры, которые не были обнаружены при сканировании, переводятся в offline
    for other_cam in db.query(Camera).all():
        if other_cam.hardware_id not in scanned_set:
            other_cam.is_online = False

    db.commit()

    # Сортировка по id asc
    result_cameras.sort(key=lambda c: c.id)
    resp["cameras"] = [serialize_camera(c) for c in result_cameras]
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
    cameras = db.query(Camera).order_by(Camera.id.asc()).all()
    return [serialize_camera(cam, db) for cam in cameras]


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

    return serialize_camera(cam, db)


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

    cam = db.query(Camera).filter(Camera.id == camera_id).first()
    if not cam:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Камера не найдена",
        )

    is_global = cam.installation_id is None
    cmd = EdgeCommand(
        command_type="AUTOCALIBRATE_CAMERA",
        installation_id=cam.installation_id,
        is_global=is_global,
        payload=json.dumps({"camera_hardware_id": cam.hardware_id}),
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

    if finished_cmd.status == "error":
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=finished_cmd.result_message or "Ошибка автокалибровки камеры",
        )

    summary = finished_cmd.result_message or "Калибровка выполнена успешно"

    # Сохраняем сводку профиля в SystemSetting
    setting = (
        db.query(SystemSetting)
        .filter(SystemSetting.key == f"camera_profile_{cam.hardware_id}")
        .first()
    )
    if setting:
        setting.value = summary
        setting.updated_at = datetime.now(UTC)
    else:
        db.add(
            SystemSetting(
                key=f"camera_profile_{cam.hardware_id}",
                value=summary,
                updated_at=datetime.now(UTC),
            )
        )
    db.commit()

    return {
        "status": "done",
        "camera_id": cam.id,
        "hardware_id": cam.hardware_id,
        "summary": summary,
    }


class CameraControlsPatch(BaseModel):
    """Тело запроса PATCH /api/cameras/{camera_id}/controls."""

    model_config = ConfigDict(extra="ignore")

    controls: dict[str, Any] | None = None
    format: str | None = None
    resolution: str | None = None
    rgb: dict[str, float] | None = None
    segmentation_mode: SegmentationMode | None = None


class CameraControlsSaveRequest(BaseModel):
    """Тело запроса POST /api/cameras/{camera_id}/controls/save."""

    model_config = ConfigDict(extra="ignore")

    scope: Literal["next_frame", "all_gallery"]
    controls: dict[str, Any] | None = None
    format: str | None = None
    resolution: str | None = None
    rgb: dict[str, float] | None = None
    segmentation_mode: SegmentationMode | None = None


class LiveModeRequest(BaseModel):
    """Тело запроса POST /api/cameras/{camera_id}/live/mode."""

    model_config = ConfigDict(extra="ignore")

    mode: Literal["raw", "annotated"]


@router.post("/{camera_id}/live/start", response_model=dict[str, Any])
def start_camera_live(
    camera_id: int,
    current_user: Annotated[User, Depends(require_role("developer", "biologist"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Запуск Live-сессии камеры (Developer, Biologist)."""
    check_rpi_online(db)
    cam = db.query(Camera).filter(Camera.id == camera_id).first()
    if not cam:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Камера не найдена",
        )

    session = live_manager.get_or_create(cam.hardware_id, camera_id=cam.id)
    session.is_active = True
    session.is_paused = False
    session.mode = "raw"
    session.device_online = True
    session.color_marker_detected = False
    session.stream_error = None
    session.stream_url = None
    session.latest_frame = None
    session.latest_raw_frame = None
    session.latest_annotated_frame = None
    session.last_frame_at = 0.0
    session.last_proxied_at = 0.0
    session._last_state_probe_at = 0.0
    session.fps = 0.0
    session.latency_ms = 0
    session._frame_times.clear()
    session.last_heartbeat = time.time()
    session.started_at = time.time()

    cur_fmt, cur_res = _resolve_camera_format_and_resolution(db, cam.hardware_id)
    session.resolution = cur_res

    saved_rgb = {"r": 1.0, "g": 1.0, "b": 1.0}
    rgb_setting = (
        db.query(SystemSetting)
        .filter(SystemSetting.key == f"camera_rgb_{cam.hardware_id}")
        .first()
    )
    if rgb_setting and rgb_setting.value:
        with contextlib.suppress(Exception):
            saved_rgb = clamp_rgb_dict(json.loads(rgb_setting.value))

    seg_setting = (
        db.query(SystemSetting)
        .filter(SystemSetting.key == f"camera_seg_mode_{cam.hardware_id}")
        .first()
    )
    saved_seg_mode = edge_api.normalize_segmentation_mode(
        seg_setting.value if seg_setting else None
    )
    session.set_segmentation_mode(saved_seg_mode)

    parts = cur_res.split("x")
    w = int(parts[0]) if len(parts) == 2 else 1280
    h = int(parts[1]) if len(parts) == 2 else 720

    cmd = EdgeCommand(
        command_type="START_LIVE",
        installation_id=cam.installation_id,
        is_global=cam.installation_id is None,
        payload=json.dumps(
            {
                "camera_hardware_id": cam.hardware_id,
                "width": w,
                "height": h,
                "pixel_format": cur_fmt,
                "rgb": saved_rgb,
                "segmentation_mode": saved_seg_mode,
            }
        ),
        status="pending",
    )
    db.add(cmd)
    db.commit()

    return {
        "status": "started",
        "camera_id": cam.id,
        "hardware_id": cam.hardware_id,
        "resolution": f"{w}x{h}",
        "format": cur_fmt,
        "segmentation_mode": saved_seg_mode,
    }


@router.post("/{camera_id}/live/stop", response_model=dict[str, Any])
def stop_camera_live(
    camera_id: int,
    current_user: Annotated[User, Depends(require_role("developer", "biologist"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Остановка Live-сессии камеры (Developer, Biologist)."""
    cam = db.query(Camera).filter(Camera.id == camera_id).first()
    if not cam:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Камера не найдена",
        )

    live_manager.stop(cam.hardware_id)

    cmd = EdgeCommand(
        command_type="STOP_LIVE",
        installation_id=cam.installation_id,
        is_global=cam.installation_id is None,
        payload=json.dumps({"camera_hardware_id": cam.hardware_id}),
        status="pending",
    )
    db.add(cmd)
    db.commit()

    return {
        "status": "stopped",
        "camera_id": cam.id,
        "hardware_id": cam.hardware_id,
    }


@router.post("/{camera_id}/live/mode", response_model=dict[str, Any])
async def set_camera_live_mode(
    camera_id: int,
    body: LiveModeRequest,
    current_user: Annotated[User, Depends(require_role("developer", "biologist"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Переключение режима Live-стрима ('raw' / 'annotated')."""
    cam = db.query(Camera).filter(Camera.id == camera_id).first()
    if not cam:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Камера не найдена",
        )
    session = live_manager.get_or_create(cam.hardware_id, camera_id=cam.id)
    session.set_mode(body.mode)
    async with session.condition:
        session.condition.notify_all()
    return {
        "status": "ok",
        "camera_id": cam.id,
        "hardware_id": cam.hardware_id,
        "mode": session.mode,
    }


async def _probe_ustreamer_online_state(session: Any) -> None:
    """Проверяет /state у ustreamer при прямом проксировании для выявления отключения камеры."""
    if not session.stream_url or not session.is_active or session.is_paused:
        return
    now_m = time.monotonic()
    if (now_m - session._last_state_probe_at) < 0.8:
        return
    session._last_state_probe_at = now_m
    state_url = f"{session.stream_url.rstrip('/')}/state"
    with contextlib.suppress(Exception):
        async with httpx.AsyncClient(timeout=0.6, trust_env=False) as http_client:
            resp = await http_client.get(state_url)
            if resp.status_code == 200:
                data = resp.json()
                res = (
                    data.get("result") if isinstance(data.get("result"), dict) else data
                )
                src = (
                    res.get("source")
                    if isinstance(res, dict) and isinstance(res.get("source"), dict)
                    else None
                )
                if src is not None and "online" in src:
                    is_on = bool(src["online"])
                    if not is_on and (
                        session.last_frame_at == 0.0
                        and (time.time() - getattr(session, "started_at", 0.0)) < 4.0
                    ):
                        # Камера прогревается при старте — не показываем ложный офлайн до первого кадра
                        return
                    session.device_online = is_on
                    session.stream_error = None if is_on else "Камера отключена"


@router.get("/{camera_id}/live/status", response_model=dict[str, Any])
async def get_camera_live_status(
    camera_id: int,
    current_user: Annotated[User, Depends(require_role("developer", "biologist"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Статус Live-сессии (FPS, задержка мс, пауза, разрешение, режим, online)."""
    cam = db.query(Camera).filter(Camera.id == camera_id).first()
    if not cam:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Камера не найдена",
        )
    session = live_manager.get(cam.hardware_id)
    if not session:
        _, cur_res = _resolve_camera_format_and_resolution(db, cam.hardware_id)
        seg_setting = (
            db.query(SystemSetting)
            .filter(SystemSetting.key == f"camera_seg_mode_{cam.hardware_id}")
            .first()
        )
        saved_seg_mode = edge_api.normalize_segmentation_mode(
            seg_setting.value if seg_setting else None
        )
        return {
            "hardware_id": cam.hardware_id,
            "camera_id": cam.id,
            "is_active": False,
            "is_paused": False,
            "mode": "raw",
            "segmentation_mode": saved_seg_mode,
            "device_online": True,
            "color_marker_detected": False,
            "stream_error": None,
            "fps": 0.0,
            "latency_ms": 0,
            "resolution": cur_res,
            "active_subscribers": 0,
        }
    await _probe_ustreamer_online_state(session)
    return session.to_status_dict()


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
    cam = db.query(Camera).filter(Camera.id == camera_id).first()
    if not cam:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Камера не найдена",
        )
    session = live_manager.get(cam.hardware_id)
    frame: bytes | None = None

    if session:
        is_fresh = session.latest_frame is not None and (
            session.is_paused or (time.time() - session.last_frame_at) <= 1.5
        )
        if is_fresh:
            frame = session.latest_frame
        elif session.stream_url and not session.is_paused:
            snap_url = f"{session.stream_url.rstrip('/')}/snapshot"
            with contextlib.suppress(Exception):
                async with httpx.AsyncClient(
                    timeout=3.0, trust_env=False
                ) as http_client:
                    resp = await http_client.get(snap_url)
                    if resp.status_code == 200 and resp.content:
                        frame = resp.content
                        session.latest_frame = frame
                        session.last_frame_at = time.time()

        if frame is None and session.latest_frame:
            frame = session.latest_frame

    if not frame:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Live-стрим недоступен или кадр ещё не получен",
        )

    if r is not None or g is not None or b is not None:
        frame = apply_rgb_to_jpeg_bytes(
            frame,
            r=r if r is not None else 1.0,
            g=g if g is not None else 1.0,
            b=b if b is not None else 1.0,
        )

    return Response(
        content=frame,
        media_type="image/jpeg",
        headers={
            "Content-Disposition": f'attachment; filename="snapshot_{cam.hardware_id}_{int(time.time())}.jpg"',
            "Cache-Control": "no-store",
        },
    )


async def _proxy_ustreamer_frames(
    session: Any,
    shutdown_event: asyncio.Event | None = None,
):
    """Читает MJPEG-поток напрямую из ustreamer и выдаёт отдельные JPEG-кадры."""
    shut_evt = shutdown_event or live_manager.shutdown_event
    if (
        shut_evt.is_set()
        or live_manager.is_shutting_down
        or not session.stream_url
        or session.mode != "raw"
        or not session.device_online
    ):
        return
    stream_url = f"{session.stream_url.rstrip('/')}/stream"
    timeout = httpx.Timeout(connect=2.0, read=6.0, write=5.0, pool=2.0)
    buf = bytearray()
    last_frame_t = time.perf_counter()

    async with (
        httpx.AsyncClient(timeout=timeout, trust_env=False) as http_client,
        http_client.stream("GET", stream_url) as resp,
    ):
        if resp.status_code != 200:
            return
        byte_iter = resp.aiter_bytes(chunk_size=32768).__aiter__()
        while (
            not shut_evt.is_set()
            and not live_manager.is_shutting_down
            and session.is_active
            and session.mode == "raw"
            and session.device_online
        ):
            if session.is_paused:
                buf.clear()
                return
            next_task = asyncio.create_task(anext(byte_iter))
            shut_task = asyncio.create_task(shut_evt.wait())
            try:
                done, pending = await asyncio.wait(
                    {next_task, shut_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task
            except BaseException:
                for task in (next_task, shut_task):
                    if not task.done():
                        task.cancel()
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await task
                raise

            if shut_task in done or shut_evt.is_set() or live_manager.is_shutting_down:
                break
            try:
                chunk = next_task.result()
            except StopAsyncIteration:
                break

            buf.extend(chunk)
            # Защита от разрастания буфера на некорректном потоке
            if len(buf) > 4 * 1024 * 1024:
                buf.clear()
                continue
            while True:
                if shut_evt.is_set() or live_manager.is_shutting_down:
                    return
                start = buf.find(b"\xff\xd8")
                if start == -1:
                    if len(buf) > 2:
                        del buf[:-2]
                    break
                if start > 0:
                    del buf[:start]
                end = buf.find(b"\xff\xd9", 2)
                if end == -1:
                    break
                frame = bytes(buf[: end + 2])
                del buf[: end + 2]
                now_t = time.perf_counter()
                lat_ms = max(1, min(500, int((now_t - last_frame_t) * 1000)))
                last_frame_t = now_t
                session.latest_raw_frame = frame
                session.latest_frame = frame
                session.record_proxied_frame(latency_ms=lat_ms)
                yield frame


async def _wait_for_frame_or_shutdown(
    session: Any,
    shut_evt: asyncio.Event,
    last_frame_id: int,
    timeout: float,
) -> None:
    """Ожидает новый кадр в condition или мгновенно просыпается при shutdown_event."""
    if shut_evt.is_set() or live_manager.is_shutting_down or not session.is_active:
        return

    async def _wait_cond() -> None:
        async with session.condition:
            if (
                session.frame_counter == last_frame_id
                and session.is_active
                and not shut_evt.is_set()
                and not live_manager.is_shutting_down
            ):
                await session.condition.wait()

    cond_task = asyncio.create_task(_wait_cond())
    shut_task = asyncio.create_task(shut_evt.wait())
    try:
        done, pending = await asyncio.wait(
            {cond_task, shut_task},
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if cond_task in done:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                cond_task.result()
    except BaseException:
        for task in (cond_task, shut_task):
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        raise


async def _mjpeg_generator(
    hardware_id: str,
    shutdown_event: asyncio.Event | None = None,
):
    """Низколатентный генератор потока MJPEG (прямое проксирование ustreamer или push-буфер)."""
    shut_evt = shutdown_event or live_manager.shutdown_event
    if shut_evt.is_set() or live_manager.is_shutting_down:
        return

    session = live_manager.get_or_create(hardware_id)
    session.active_subscribers += 1
    session.is_active = True
    last_frame_id = -1
    next_proxy_retry_at = 0.0
    proxy_backoff = 0.5

    try:
        while (
            session.is_active
            and not shut_evt.is_set()
            and not live_manager.is_shutting_down
        ):
            # 1. В режиме 'raw' пробуем прямое проксирование от ustreamer
            now_m = time.monotonic()
            if (
                session.mode == "raw"
                and session.device_online
                and session.stream_url
                and not session.is_paused
                and now_m >= next_proxy_retry_at
            ):
                got_any = False
                with contextlib.suppress(Exception):
                    proxy_stream = (
                        _proxy_ustreamer_frames(session)
                        if shut_evt is live_manager.shutdown_event
                        else _proxy_ustreamer_frames(session, shutdown_event=shut_evt)
                    )
                    async for frame in proxy_stream:
                        if shut_evt.is_set() or live_manager.is_shutting_down:
                            break
                        got_any = True
                        proxy_backoff = 0.5
                        next_proxy_retry_at = 0.0
                        yield (
                            b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                            + frame
                            + b"\r\n"
                        )
                if shut_evt.is_set() or live_manager.is_shutting_down:
                    break
                if (
                    not got_any
                    and session.is_active
                    and not session.is_paused
                    and session.mode == "raw"
                ):
                    next_proxy_retry_at = time.monotonic() + proxy_backoff
                    proxy_backoff = min(5.0, proxy_backoff * 2.0)

            # 2. Режим 'annotated' или fallback через push-буфер от Edge-агента
            if session.frame_counter == last_frame_id:
                wait_timeout = (
                    0.3 if (session.stream_url and session.mode == "raw") else 0.5
                )
                await _wait_for_frame_or_shutdown(
                    session, shut_evt, last_frame_id, wait_timeout
                )
            if (
                shut_evt.is_set()
                or live_manager.is_shutting_down
                or not session.is_active
            ):
                break

            async with session.condition:
                frame = (
                    session.latest_annotated_frame
                    if session.mode == "annotated"
                    else session.latest_frame
                )
                last_frame_id = session.frame_counter

            if frame is not None and session.device_online:
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(shut_evt.wait(), timeout=0.02)
    except asyncio.CancelledError:
        pass
    finally:
        session.active_subscribers = max(0, session.active_subscribers - 1)


@router.get("/{camera_id}/live/stream")
async def get_camera_live_stream(
    camera_id: int,
    current_user: Annotated[User, Depends(require_role("developer", "biologist"))],
    db: Annotated[Session, Depends(get_db)],
    mode: Annotated[str | None, Query()] = None,
) -> StreamingResponse:
    """Low-latency MJPEG-стрим камеры для прямого отображения в браузере."""
    cam = db.query(Camera).filter(Camera.id == camera_id).first()
    if not cam:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Камера не найдена",
        )
    if mode in ("raw", "annotated"):
        session = live_manager.get_or_create(cam.hardware_id, camera_id=cam.id)
        session.set_mode(mode)
        async with session.condition:
            session.condition.notify_all()
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
    cam = db.query(Camera).filter(Camera.id == camera_id).first()
    if not cam:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Камера не найдена",
        )

    is_real_cams = _is_real_cameras_mode(db)
    fmt_list_setting = (
        db.query(SystemSetting)
        .filter(SystemSetting.key == f"camera_formats_{cam.hardware_id}")
        .first()
    )
    ctrl_setting = (
        db.query(SystemSetting)
        .filter(SystemSetting.key == f"camera_ctrls_{cam.hardware_id}")
        .first()
    )

    has_cached_formats = bool(
        fmt_list_setting
        and fmt_list_setting.value
        and fmt_list_setting.value.strip() not in ("", "[]", "null")
    )
    has_cached_controls = bool(
        ctrl_setting
        and ctrl_setting.value
        and ctrl_setting.value.strip() not in ("", "[]", "null")
    )

    # В режиме реальных камер (rpi или гибрид) при отсутствии непустого кэша или ?refresh=true опрашиваем Edge-агента
    need_sync = is_real_cams and (
        refresh or not has_cached_formats or not has_cached_controls
    )
    if need_sync and is_rpi_online(db)[0]:
        cmd = EdgeCommand(
            command_type="GET_CAMERA_CONTROLS",
            installation_id=cam.installation_id,
            is_global=cam.installation_id is None,
            payload=json.dumps({"camera_hardware_id": cam.hardware_id}),
            status="pending",
        )
        db.add(cmd)
        db.commit()
        db.refresh(cmd)

        start_time = time.monotonic()
        while time.monotonic() - start_time < 8.0:
            await asyncio.sleep(0.05)
            db.expire_all()
            check_cmd = db.query(EdgeCommand).filter(EdgeCommand.id == cmd.id).first()
            if not check_cmd or check_cmd.status in ("done", "error", "timeout"):
                break

        fmt_list_setting = (
            db.query(SystemSetting)
            .filter(SystemSetting.key == f"camera_formats_{cam.hardware_id}")
            .first()
        )
        ctrl_setting = (
            db.query(SystemSetting)
            .filter(SystemSetting.key == f"camera_ctrls_{cam.hardware_id}")
            .first()
        )

    formats: list[dict[str, Any]] = []
    if fmt_list_setting and fmt_list_setting.value:
        with contextlib.suppress(Exception):
            parsed_fmt = json.loads(fmt_list_setting.value)
            if isinstance(parsed_fmt, list):
                formats = parsed_fmt
    if not formats and not is_real_cams:
        formats = get_default_mock_formats()

    controls: list[dict[str, Any]] = []
    if ctrl_setting and ctrl_setting.value:
        with contextlib.suppress(Exception):
            parsed_ctrls = json.loads(ctrl_setting.value)
            if isinstance(parsed_ctrls, list):
                controls = parsed_ctrls
    if not controls and not is_real_cams:
        controls = get_default_mock_controls()

    rgb_setting = (
        db.query(SystemSetting)
        .filter(SystemSetting.key == f"camera_rgb_{cam.hardware_id}")
        .first()
    )
    rgb = {"r": 1.0, "g": 1.0, "b": 1.0}
    if rgb_setting and rgb_setting.value:
        with contextlib.suppress(Exception):
            parsed_rgb = json.loads(rgb_setting.value)
            if isinstance(parsed_rgb, dict):
                rgb = clamp_rgb_dict(parsed_rgb)

    seg_setting = (
        db.query(SystemSetting)
        .filter(SystemSetting.key == f"camera_seg_mode_{cam.hardware_id}")
        .first()
    )
    segmentation_mode = edge_api.normalize_segmentation_mode(
        seg_setting.value if seg_setting else None
    )

    cur_fmt, cur_res = _resolve_camera_format_and_resolution(
        db, cam.hardware_id, formats=formats
    )

    return {
        "camera_id": cam.id,
        "hardware_id": cam.hardware_id,
        "formats": formats,
        "controls": controls,
        "rgb": rgb,
        "segmentation_mode": segmentation_mode,
        "current_format": cur_fmt,
        "current_resolution": cur_res,
    }


def _persist_camera_controls_profile(
    db: Session,
    cam: Camera,
    controls: dict[str, Any] | None,
    fmt: str | None,
    resolution: str | None,
    rgb: dict[str, float] | None,
    segmentation_mode: str | None = None,
    enqueue_edge_command: bool = True,
) -> tuple[list[dict[str, Any]] | None, dict[str, float], SegmentationMode]:
    """Сохраняет настройки формата, разрешения, V4L2-контролов, RGB и segmentation_mode в профиль камеры."""
    now = datetime.now(UTC)
    if fmt:
        _upsert_setting(db, f"camera_format_{cam.hardware_id}", fmt, now)
    if resolution:
        _upsert_setting(db, f"camera_res_{cam.hardware_id}", resolution, now)
        active_session = live_manager.get(cam.hardware_id)
        if active_session:
            active_session.resolution = resolution

    saved_seg_mode: SegmentationMode
    if segmentation_mode is not None:
        saved_seg_mode = edge_api.normalize_segmentation_mode(segmentation_mode)
        _upsert_setting(db, f"camera_seg_mode_{cam.hardware_id}", saved_seg_mode, now)
        active_session = live_manager.get(cam.hardware_id)
        if active_session:
            active_session.set_segmentation_mode(saved_seg_mode)
    else:
        existing_seg_setting = (
            db.query(SystemSetting)
            .filter(SystemSetting.key == f"camera_seg_mode_{cam.hardware_id}")
            .first()
        )
        saved_seg_mode = edge_api.normalize_segmentation_mode(
            existing_seg_setting.value if existing_seg_setting else None
        )

    clamped_rgb: dict[str, float]
    if rgb is not None:
        clamped_rgb = clamp_rgb_dict(rgb)
        _upsert_setting(
            db, f"camera_rgb_{cam.hardware_id}", json.dumps(clamped_rgb), now
        )
    else:
        existing_rgb_setting = (
            db.query(SystemSetting)
            .filter(SystemSetting.key == f"camera_rgb_{cam.hardware_id}")
            .first()
        )
        clamped_rgb = {"r": 1.0, "g": 1.0, "b": 1.0}
        if existing_rgb_setting and existing_rgb_setting.value:
            with contextlib.suppress(Exception):
                clamped_rgb = clamp_rgb_dict(json.loads(existing_rgb_setting.value))

    updated_controls: list[dict[str, Any]] | None = None
    if controls:
        ctrl_setting = (
            db.query(SystemSetting)
            .filter(SystemSetting.key == f"camera_ctrls_{cam.hardware_id}")
            .first()
        )
        if ctrl_setting and ctrl_setting.value:
            existing_controls = json.loads(ctrl_setting.value)
        elif not _is_real_cameras_mode(db):
            existing_controls = get_default_mock_controls()
        else:
            existing_controls = []

        if existing_controls:
            apply_control_dependencies(existing_controls, controls)
            _upsert_setting(
                db,
                f"camera_ctrls_{cam.hardware_id}",
                json.dumps(existing_controls),
                now,
            )
            updated_controls = existing_controls

    if enqueue_edge_command:
        cmd = EdgeCommand(
            command_type="SET_CAMERA_CONTROLS",
            installation_id=cam.installation_id,
            is_global=cam.installation_id is None,
            payload=json.dumps(
                {
                    "camera_hardware_id": cam.hardware_id,
                    "controls": controls or {},
                    "format": fmt,
                    "resolution": resolution,
                    "rgb": clamped_rgb if rgb is not None else None,
                    "segmentation_mode": saved_seg_mode
                    if segmentation_mode is not None
                    else None,
                }
            ),
            status="pending",
        )
        db.add(cmd)

    db.commit()
    return updated_controls, clamped_rgb, saved_seg_mode


@router.patch("/{camera_id}/controls", response_model=dict[str, Any])
def update_camera_controls(
    camera_id: int,
    body: CameraControlsPatch,
    current_user: Annotated[User, Depends(require_role("developer", "biologist"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Применение настроек V4L2, смена разрешения/формата, RGB-коррекции и segmentation_mode."""
    cam = db.query(Camera).filter(Camera.id == camera_id).first()
    if not cam:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Камера не найдена",
        )

    updated_controls, clamped_rgb, saved_seg_mode = _persist_camera_controls_profile(
        db=db,
        cam=cam,
        controls=body.controls,
        fmt=body.format,
        resolution=body.resolution,
        rgb=body.rgb,
        segmentation_mode=body.segmentation_mode,
        enqueue_edge_command=True,
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
    cam = db.query(Camera).filter(Camera.id == camera_id).first()
    if not cam:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Камера не найдена",
        )

    has_changes = bool(
        body.controls
        or body.format
        or body.resolution
        or body.rgb is not None
        or body.segmentation_mode is not None
    )
    updated_controls, saved_rgb, saved_seg_mode = _persist_camera_controls_profile(
        db=db,
        cam=cam,
        controls=body.controls,
        fmt=body.format,
        resolution=body.resolution,
        rgb=body.rgb,
        segmentation_mode=body.segmentation_mode,
        enqueue_edge_command=has_changes,
    )

    updated_captures = 0
    if body.scope == "all_gallery":
        updated_captures = apply_rgb_to_camera_captures(
            db=db,
            camera_id=cam.id,
            rgb=saved_rgb,
            base_media_dir=edge_api.get_media_dir(),
        )

    resp: dict[str, Any] = {
        "status": "saved",
        "scope": body.scope,
        "camera_id": cam.id,
        "hardware_id": cam.hardware_id,
        "rgb": saved_rgb,
        "segmentation_mode": saved_seg_mode,
        "updated_captures": updated_captures,
    }
    if updated_controls is not None:
        resp["controls"] = updated_controls
    return resp


class AutoColorCalibrateRequest(BaseModel):
    """Параметры запроса автоцветокоррекции (предпросмотр без применения к профилю)."""

    preview: bool = False


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

    cam = db.query(Camera).filter(Camera.id == camera_id).first()
    if not cam:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Камера не найдена",
        )

    is_preview = bool(preview or (body.preview if body is not None else False))
    cmd = EdgeCommand(
        command_type="AUTO_COLOR_CALIBRATE",
        installation_id=cam.installation_id,
        is_global=cam.installation_id is None,
        payload=json.dumps(
            {"camera_hardware_id": cam.hardware_id, "preview": is_preview}
        ),
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

    if finished_cmd.status == "error":
        app_state.pop_command_data(finished_cmd.id)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=finished_cmd.result_message
            or "Цветовой маркер не обнаружен в кадре",
        )

    cmd_data = app_state.pop_command_data(finished_cmd.id) or {}
    recommended_raw = cmd_data.get("recommended_rgb") or cmd_data.get("rgb")
    rgb = clamp_rgb_dict(recommended_raw) if isinstance(recommended_raw, dict) else None
    if rgb is None:
        rgb_setting = (
            db.query(SystemSetting)
            .filter(SystemSetting.key == f"camera_rgb_{cam.hardware_id}")
            .first()
        )
        rgb = {"r": 1.0, "g": 1.0, "b": 1.0}
        if rgb_setting and rgb_setting.value:
            with contextlib.suppress(Exception):
                rgb = clamp_rgb_dict(json.loads(rgb_setting.value))

    resp: dict[str, Any] = {
        "status": "done",
        "camera_id": cam.id,
        "hardware_id": cam.hardware_id,
        "preview": is_preview,
        "rgb": rgb,
        "recommended_rgb": rgb,
        "result_message": finished_cmd.result_message or "Автоцветокоррекция выполнена",
    }
    roi_b64 = cmd_data.get("roi_image_base64")
    if isinstance(roi_b64, str) and roi_b64:
        resp["roi_image_base64"] = roi_b64
    return resp
