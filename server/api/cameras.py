import asyncio
import json
import time
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from edge_agent.camera_controls import (
    get_default_mock_controls,
    query_camera_formats_and_sizes,
)
from server.api.deps import format_utc_z, get_db, require_role
from server.core.live_manager import live_manager
from server.core.state import app_state
from server.core.system_status import check_rpi_online, is_debug_mode
from server.db.models import Camera, EdgeCommand, Installation, SystemSetting, User

router = APIRouter(prefix="/api/cameras", tags=["cameras"])


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
    session.last_heartbeat = time.time()

    fmt_setting = (
        db.query(SystemSetting)
        .filter(SystemSetting.key == f"camera_format_{cam.hardware_id}")
        .first()
    )
    res_setting = (
        db.query(SystemSetting)
        .filter(SystemSetting.key == f"camera_res_{cam.hardware_id}")
        .first()
    )
    cur_fmt = fmt_setting.value if fmt_setting and fmt_setting.value else "MJPG"
    cur_res = res_setting.value if res_setting and res_setting.value else "1280x720"

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


@router.get("/{camera_id}/live/status", response_model=dict[str, Any])
def get_camera_live_status(
    camera_id: int,
    current_user: Annotated[User, Depends(require_role("developer", "biologist"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Статус Live-сессии (FPS, задержка мс, пауза, разрешение)."""
    cam = db.query(Camera).filter(Camera.id == camera_id).first()
    if not cam:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Камера не найдена",
        )
    session = live_manager.get(cam.hardware_id)
    if not session:
        return {
            "hardware_id": cam.hardware_id,
            "camera_id": cam.id,
            "is_active": False,
            "is_paused": False,
            "fps": 0.0,
            "latency_ms": 0,
            "resolution": "1280x720",
            "active_subscribers": 0,
        }
    return session.to_status_dict()


@router.get("/{camera_id}/live/snapshot")
def get_camera_live_snapshot(
    camera_id: int,
    current_user: Annotated[User, Depends(require_role("developer", "biologist"))],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """Скачивание тестового JPEG-снимка напрямую без записи в БД и таймлапс."""
    cam = db.query(Camera).filter(Camera.id == camera_id).first()
    if not cam:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Камера не найдена",
        )
    session = live_manager.get(cam.hardware_id)
    frame = session.latest_frame if session else None
    if not frame:
        from edge_agent.synthetic import generate_synthetic_frame

        frame = generate_synthetic_frame(0, cam.hardware_id)

    return Response(
        content=frame,
        media_type="image/jpeg",
        headers={
            "Content-Disposition": f'attachment; filename="snapshot_{cam.hardware_id}_{int(time.time())}.jpg"',
        },
    )


async def _mjpeg_generator(hardware_id: str):
    """Низколатентный генератор потока MJPEG без накопления очереди."""
    session = live_manager.get_or_create(hardware_id)
    session.active_subscribers += 1
    session.is_active = True
    last_frame_id = -1

    try:
        while True:
            async with session.condition:
                if session.frame_counter == last_frame_id:
                    try:
                        await asyncio.wait_for(session.condition.wait(), timeout=0.5)
                    except TimeoutError:
                        pass
                frame = session.latest_frame
                last_frame_id = session.frame_counter

            if frame is None:
                from edge_agent.synthetic import generate_synthetic_frame

                frame = generate_synthetic_frame(0, hardware_id)

            yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")
            await asyncio.sleep(0.02)
    except asyncio.CancelledError:
        pass
    finally:
        session.active_subscribers = max(0, session.active_subscribers - 1)


@router.get("/{camera_id}/live/stream")
async def get_camera_live_stream(
    camera_id: int,
    current_user: Annotated[User, Depends(require_role("developer", "biologist"))],
    db: Annotated[Session, Depends(get_db)],
) -> StreamingResponse:
    """Low-latency MJPEG-стрим камеры для прямого отображения в браузере."""
    cam = db.query(Camera).filter(Camera.id == camera_id).first()
    if not cam:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Камера не найдена",
        )
    return StreamingResponse(
        _mjpeg_generator(cam.hardware_id),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@router.get("/{camera_id}/controls", response_model=dict[str, Any])
def get_camera_controls(
    camera_id: int,
    current_user: Annotated[User, Depends(require_role("developer", "biologist"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Получение поддерживаемых V4L2-контролов, форматов и RGB настроек."""
    cam = db.query(Camera).filter(Camera.id == camera_id).first()
    if not cam:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Камера не найдена",
        )

    formats = query_camera_formats_and_sizes(f"/dev/v4l/by-id/{cam.hardware_id}")

    ctrl_setting = (
        db.query(SystemSetting)
        .filter(SystemSetting.key == f"camera_ctrls_{cam.hardware_id}")
        .first()
    )
    if ctrl_setting and ctrl_setting.value:
        try:
            controls = json.loads(ctrl_setting.value)
        except Exception:  # noqa: BLE001
            controls = get_default_mock_controls()
    else:
        controls = get_default_mock_controls()

    rgb_setting = (
        db.query(SystemSetting)
        .filter(SystemSetting.key == f"camera_rgb_{cam.hardware_id}")
        .first()
    )
    if rgb_setting and rgb_setting.value:
        try:
            rgb = json.loads(rgb_setting.value)
        except Exception:  # noqa: BLE001
            rgb = {"r": 1.0, "g": 1.0, "b": 1.0}
    else:
        rgb = {"r": 1.0, "g": 1.0, "b": 1.0}

    cur_fmt_setting = (
        db.query(SystemSetting)
        .filter(SystemSetting.key == f"camera_format_{cam.hardware_id}")
        .first()
    )
    cur_res_setting = (
        db.query(SystemSetting)
        .filter(SystemSetting.key == f"camera_res_{cam.hardware_id}")
        .first()
    )

    return {
        "camera_id": cam.id,
        "hardware_id": cam.hardware_id,
        "formats": formats,
        "controls": controls,
        "rgb": rgb,
        "current_format": cur_fmt_setting.value if cur_fmt_setting else "MJPG",
        "current_resolution": cur_res_setting.value if cur_res_setting else "1280x720",
    }


@router.patch("/{camera_id}/controls", response_model=dict[str, Any])
def update_camera_controls(
    camera_id: int,
    body: CameraControlsPatch,
    current_user: Annotated[User, Depends(require_role("developer", "biologist"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Применение настроек V4L2, смена разрешения/формата и RGB-коррекции."""
    cam = db.query(Camera).filter(Camera.id == camera_id).first()
    if not cam:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Камера не найдена",
        )

    if body.format:
        db.merge(
            SystemSetting(
                key=f"camera_format_{cam.hardware_id}",
                value=body.format,
                updated_at=datetime.now(UTC),
            )
        )
    if body.resolution:
        db.merge(
            SystemSetting(
                key=f"camera_res_{cam.hardware_id}",
                value=body.resolution,
                updated_at=datetime.now(UTC),
            )
        )

    if body.rgb:
        db.merge(
            SystemSetting(
                key=f"camera_rgb_{cam.hardware_id}",
                value=json.dumps(body.rgb),
                updated_at=datetime.now(UTC),
            )
        )

    if body.controls:
        ctrl_setting = (
            db.query(SystemSetting)
            .filter(SystemSetting.key == f"camera_ctrls_{cam.hardware_id}")
            .first()
        )
        existing_controls = (
            json.loads(ctrl_setting.value)
            if ctrl_setting and ctrl_setting.value
            else get_default_mock_controls()
        )
        for ctrl in existing_controls:
            cname = ctrl.get("name")
            if cname in body.controls:
                ctrl["value"] = body.controls[cname]

        db.merge(
            SystemSetting(
                key=f"camera_ctrls_{cam.hardware_id}",
                value=json.dumps(existing_controls),
                updated_at=datetime.now(UTC),
            )
        )

    db.commit()

    cmd = EdgeCommand(
        command_type="SET_CAMERA_CONTROLS",
        installation_id=cam.installation_id,
        is_global=cam.installation_id is None,
        payload=json.dumps(
            {
                "camera_hardware_id": cam.hardware_id,
                "controls": body.controls or {},
                "format": body.format,
                "resolution": body.resolution,
                "rgb": body.rgb,
            }
        ),
        status="pending",
    )
    db.add(cmd)
    db.commit()

    return {
        "status": "updated",
        "camera_id": cam.id,
        "hardware_id": cam.hardware_id,
        "format": body.format,
        "resolution": body.resolution,
        "rgb": body.rgb,
    }
