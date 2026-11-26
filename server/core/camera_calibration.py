"""Сервис калибровки камер (ArUco, автоцветокоррекция), профилей и V4L2-контролов."""

import contextlib
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session, joinedload

from edge_agent.camera_controls import (
    apply_control_dependencies,
    get_default_mock_controls,
    get_default_mock_formats,
)
from server.api.deps import format_utc_z
from server.core.commands import wait_command_result
from server.core.live_manager import live_manager
from server.core.rgb import apply_rgb_to_camera_captures, clamp_rgb_dict
from server.core.state import app_state
from server.core.system_status import is_rpi_online
from server.db.database import upsert_system_setting
from server.db.models import Camera, EdgeCommand, Installation, SystemSetting


def _default_normalize_seg_mode(raw_val: Any) -> str:
    """Нормализует режим сегментации по умолчанию к 'auto', 'onnx' или 'hsv'."""
    val = str(raw_val or "auto").strip().lower()
    if val in ("onnx", "hsv"):
        return val
    return "auto"


class CameraCalibrationService:
    """Управляет профилями калибровки, форматами, V4L2-контролами и калибровочными командами."""

    @staticmethod
    def is_real_cameras_mode(db: Session) -> bool:
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

    @staticmethod
    def resolve_camera_format_and_resolution(
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

    @staticmethod
    def load_camera_profiles_map(
        db: Session, hardware_ids: list[str]
    ) -> dict[str, str]:
        """Загружает сводки профилей калибровки камер одним IN-запросом."""
        if not hardware_ids:
            return {}
        prefix = "camera_profile_"
        keys = [f"{prefix}{hw_id}" for hw_id in hardware_ids]
        rows = db.query(SystemSetting).filter(SystemSetting.key.in_(keys)).all()
        return {row.key[len(prefix) :]: row.value for row in rows if row.value}

    @staticmethod
    def serialize_camera(
        cam: Camera,
        db: Session | None = None,
        profiles_by_hw: dict[str, str] | None = None,
    ) -> dict[str, Any]:
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
        if profiles_by_hw is not None:
            summary = profiles_by_hw.get(cam.hardware_id)
            if summary:
                res["calibration_summary"] = summary
        elif db is not None:
            setting = (
                db.query(SystemSetting)
                .filter(SystemSetting.key == f"camera_profile_{cam.hardware_id}")
                .first()
            )
            if setting and setting.value:
                res["calibration_summary"] = setting.value
        return res

    @classmethod
    def get_camera_or_404(cls, db: Session, camera_id: int) -> Camera:
        """Возвращает камеру по ID или выбрасывает 404."""
        cam = db.query(Camera).filter(Camera.id == camera_id).first()
        if not cam:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Камера не найдена",
            )
        return cam

    @classmethod
    def bind_camera_to_installation(
        cls, db: Session, camera_id: int, installation_id: int | None
    ) -> dict[str, Any]:
        """Привязывает камеру к установке или отвязывает (installation_id=None)."""
        cam = cls.get_camera_or_404(db, camera_id)

        if installation_id is not None:
            inst = (
                db.query(Installation)
                .filter(Installation.id == installation_id)
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

        cam.installation_id = installation_id
        db.commit()
        db.refresh(cam)
        return cls.serialize_camera(cam, db)

    @classmethod
    def reconcile_scanned_cameras(
        cls, db: Session, scanned_hw_ids: list[str]
    ) -> list[dict[str, Any]]:
        """Регистрирует новые камеры после сканирования, обновляет существующие и переводит отсутствующие в offline."""
        now = datetime.now(UTC)

        # Предзагрузка всех известных камер для устранения N+1 запросов в цикле
        existing_cameras = (
            db.query(Camera).options(joinedload(Camera.installation)).all()
        )
        cameras_by_hw: dict[str, Camera] = {c.hardware_id: c for c in existing_cameras}

        # Регистрация новых камер и обновление существующих
        result_cameras: list[Camera] = []
        scanned_set = set(scanned_hw_ids)
        for hw_id in scanned_hw_ids:
            cam = cameras_by_hw.get(hw_id)
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
                cameras_by_hw[hw_id] = cam
            else:
                cam.is_online = True
                cam.last_seen_at = now
            result_cameras.append(cam)

        # Камеры, которые не были обнаружены при сканировании, переводятся в offline
        for other_cam in existing_cameras:
            if other_cam.hardware_id not in scanned_set:
                other_cam.is_online = False

        db.commit()

        # Сортировка по id asc
        result_cameras.sort(key=lambda c: c.id)
        profiles_by_hw = cls.load_camera_profiles_map(
            db, [c.hardware_id for c in result_cameras]
        )
        return [
            cls.serialize_camera(c, db=db, profiles_by_hw=profiles_by_hw)
            for c in result_cameras
        ]

    @staticmethod
    def enqueue_autocalibrate_command(db: Session, cam: Camera) -> EdgeCommand:
        """Создаёт Edge-команду AUTOCALIBRATE_CAMERA для автокалибровки ArUco/профиля."""
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
        return cmd

    @staticmethod
    def finalize_autocalibrate_result(
        db: Session, cam: Camera, finished_cmd: EdgeCommand
    ) -> dict[str, Any]:
        """Обрабатывает результат команды автокалибровки и сохраняет профиль камеры."""
        if finished_cmd.status == "error":
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=finished_cmd.result_message or "Ошибка автокалибровки камеры",
            )

        summary = finished_cmd.result_message or "Калибровка выполнена успешно"

        # Сохраняем сводку профиля в SystemSetting
        upsert_system_setting(db, f"camera_profile_{cam.hardware_id}", summary)
        db.commit()

        return {
            "status": "done",
            "camera_id": cam.id,
            "hardware_id": cam.hardware_id,
            "summary": summary,
        }

    @staticmethod
    def enqueue_auto_color_calibrate_command(
        db: Session, cam: Camera, is_preview: bool
    ) -> EdgeCommand:
        """Создаёт Edge-команду AUTO_COLOR_CALIBRATE."""
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
        return cmd

    @staticmethod
    def finalize_auto_color_calibrate_result(
        db: Session, cam: Camera, finished_cmd: EdgeCommand, is_preview: bool
    ) -> dict[str, Any]:
        """Обрабатывает результат AUTO_COLOR_CALIBRATE и формирует ответ с RGB и ROI."""
        if finished_cmd.status == "error":
            app_state.pop_command_data(finished_cmd.id)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=finished_cmd.result_message
                or "Цветовой маркер не обнаружен в кадре",
            )

        cmd_data = app_state.pop_command_data(finished_cmd.id) or {}
        recommended_raw = cmd_data.get("recommended_rgb") or cmd_data.get("rgb")
        rgb = (
            clamp_rgb_dict(recommended_raw)
            if isinstance(recommended_raw, dict)
            else None
        )
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
            "result_message": (
                finished_cmd.result_message or "Автоцветокоррекция выполнена"
            ),
        }
        roi_b64 = cmd_data.get("roi_image_base64")
        if isinstance(roi_b64, str) and roi_b64:
            resp["roi_image_base64"] = roi_b64
        return resp

    @classmethod
    async def get_controls_payload(
        cls,
        db: Session,
        cam: Camera,
        refresh: bool = False,
        normalize_seg_fn: Callable[[Any], str] = _default_normalize_seg_mode,
    ) -> dict[str, Any]:
        """Возвращает поддерживаемые V4L2-контролы, форматы, RGB и segmentation_mode камеры."""
        is_real_cams = cls.is_real_cameras_mode(db)
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

            with contextlib.suppress(HTTPException):
                await wait_command_result(cmd.id, 8.0, db)

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
        segmentation_mode = normalize_seg_fn(seg_setting.value if seg_setting else None)

        cur_fmt, cur_res = cls.resolve_camera_format_and_resolution(
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

    @classmethod
    def persist_controls_profile(
        cls,
        db: Session,
        cam: Camera,
        controls: dict[str, Any] | None,
        fmt: str | None,
        resolution: str | None,
        rgb: dict[str, float] | None,
        segmentation_mode: str | None = None,
        enqueue_edge_command: bool = True,
        normalize_seg_fn: Callable[[Any], str] = _default_normalize_seg_mode,
    ) -> tuple[list[dict[str, Any]] | None, dict[str, float], str]:
        """Сохраняет настройки формата, разрешения, V4L2-контролов, RGB и segmentation_mode в профиль камеры."""
        now = datetime.now(UTC)
        if fmt:
            upsert_system_setting(db, f"camera_format_{cam.hardware_id}", fmt, now)
        if resolution:
            upsert_system_setting(db, f"camera_res_{cam.hardware_id}", resolution, now)
            active_session = live_manager.get(cam.hardware_id)
            if active_session:
                active_session.resolution = resolution

        saved_seg_mode: str
        if segmentation_mode is not None:
            saved_seg_mode = normalize_seg_fn(segmentation_mode)
            upsert_system_setting(
                db, f"camera_seg_mode_{cam.hardware_id}", saved_seg_mode, now
            )
            active_session = live_manager.get(cam.hardware_id)
            if active_session:
                active_session.set_segmentation_mode(saved_seg_mode)
        else:
            existing_seg_setting = (
                db.query(SystemSetting)
                .filter(SystemSetting.key == f"camera_seg_mode_{cam.hardware_id}")
                .first()
            )
            saved_seg_mode = normalize_seg_fn(
                existing_seg_setting.value if existing_seg_setting else None
            )

        clamped_rgb: dict[str, float]
        if rgb is not None:
            clamped_rgb = clamp_rgb_dict(rgb)
            upsert_system_setting(
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
            elif not cls.is_real_cameras_mode(db):
                existing_controls = get_default_mock_controls()
            else:
                existing_controls = []

            if existing_controls:
                apply_control_dependencies(existing_controls, controls)
                upsert_system_setting(
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

    @classmethod
    def save_controls_and_apply_rgb(
        cls,
        db: Session,
        cam: Camera,
        scope: str,
        controls: dict[str, Any] | None,
        fmt: str | None,
        resolution: str | None,
        rgb: dict[str, float] | None,
        segmentation_mode: str | None,
        base_media_dir: Path,
        normalize_seg_fn: Callable[[Any], str] = _default_normalize_seg_mode,
    ) -> dict[str, Any]:
        """Сохраняет профиль камеры и при scope='all_gallery' применяет RGB ко всем снимкам."""
        has_changes = bool(
            controls
            or fmt
            or resolution
            or rgb is not None
            or segmentation_mode is not None
        )
        updated_controls, saved_rgb, saved_seg_mode = cls.persist_controls_profile(
            db=db,
            cam=cam,
            controls=controls,
            fmt=fmt,
            resolution=resolution,
            rgb=rgb,
            segmentation_mode=segmentation_mode,
            enqueue_edge_command=has_changes,
            normalize_seg_fn=normalize_seg_fn,
        )

        updated_captures = 0
        if scope == "all_gallery":
            updated_captures = apply_rgb_to_camera_captures(
                db=db,
                camera_id=cam.id,
                rgb=saved_rgb,
                base_media_dir=base_media_dir,
            )

        resp: dict[str, Any] = {
            "status": "saved",
            "scope": scope,
            "camera_id": cam.id,
            "hardware_id": cam.hardware_id,
            "rgb": saved_rgb,
            "segmentation_mode": saved_seg_mode,
            "updated_captures": updated_captures,
        }
        if updated_controls is not None:
            resp["controls"] = updated_controls
        return resp


camera_calibration_service = CameraCalibrationService()
is_real_cameras_mode = camera_calibration_service.is_real_cameras_mode
resolve_camera_format_and_resolution = (
    camera_calibration_service.resolve_camera_format_and_resolution
)
load_camera_profiles_map = camera_calibration_service.load_camera_profiles_map
serialize_camera = camera_calibration_service.serialize_camera
persist_camera_controls_profile = camera_calibration_service.persist_controls_profile

__all__ = [
    "CameraCalibrationService",
    "camera_calibration_service",
    "is_real_cameras_mode",
    "load_camera_profiles_map",
    "persist_camera_controls_profile",
    "resolve_camera_format_and_resolution",
    "serialize_camera",
]
