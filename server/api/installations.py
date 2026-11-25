import shutil
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator
from sqlalchemy.orm import Session

from server.api import edge as edge_api
from server.api.deps import (
    ClientUser,
    format_utc_z,
    get_current_user_optional,
    get_db,
    require_role,
)
from server.core.area import calculate_area_display
from server.core.rgb import resolve_capture_display_rel_path
from server.core.state import app_state
from server.db.models import Capture, Installation, SystemSetting, User
from shared.schedule import compute_next_capture_at

router = APIRouter(prefix="/api/installations", tags=["installations"])


class InstallationCreate(BaseModel):
    """Тело запроса POST /api/installations."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., max_length=100)
    culture_name: str = Field(..., max_length=100)
    light_gpio_pin: int = Field(..., strict=True, ge=0, le=53)
    flash_gpio_pin: int = Field(..., strict=True, ge=0, le=53)
    aruco_size_mm: float | None = Field(default=None, gt=0)
    capture_delay_sec: float | None = Field(default=None, ge=0.0, le=30.0)

    @field_validator("name", "culture_name")
    @classmethod
    def validate_non_empty(cls, v: str, info: ValidationInfo) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"Поле {info.field_name} не может быть пустым")
        return v.strip()


class InstallationPatch(BaseModel):
    """Тело запроса PATCH /api/installations/{id}."""

    model_config = ConfigDict(extra="ignore")

    name: str | None = Field(default=None, max_length=100)
    culture_name: str | None = Field(default=None, max_length=100)
    light_gpio_pin: int | None = Field(default=None, strict=True, ge=0, le=53)
    flash_gpio_pin: int | None = Field(default=None, strict=True, ge=0, le=53)
    aruco_size_mm: float | None = Field(default=None, gt=0)
    capture_delay_sec: float | None = Field(default=None, ge=0.0, le=30.0)

    @field_validator("name", "culture_name", "light_gpio_pin", "flash_gpio_pin")
    @classmethod
    def validate_not_null(cls, v: Any, info: ValidationInfo) -> Any:
        if v is None:
            raise ValueError(f"Поле {info.field_name} не может быть null")
        if info.field_name in ("name", "culture_name"):
            if not isinstance(v, str) or not v.strip():
                raise ValueError(f"Поле {info.field_name} не может быть пустым")
            return v.strip()
        return v


def get_installation_last_capture_info(
    inst_id: int, aruco_size_mm: float | None, db: Session
) -> tuple[str | None, str | None]:
    """Вычисляет last_area_display и last_frame_raw_url по последнему плановому кадру."""
    last_cap = (
        db.query(Capture)
        .filter(
            Capture.installation_id == inst_id,
            Capture.is_manual.is_(False),
        )
        .order_by(Capture.captured_at.desc(), Capture.id.desc())
        .first()
    )
    if not last_cap:
        return None, None

    last_raw_url = (
        f"/{resolve_capture_display_rel_path(last_cap.raw_image_path, edge_api.get_media_dir())}"
        if last_cap.raw_image_path
        else None
    )

    meas = next(
        (m for m in last_cap.plant_measurements if m.plant_index == 0),
        None,
    )
    if not meas:
        return None, last_raw_url

    area_display = calculate_area_display(
        meas.area_px, last_cap.aruco_area_px, aruco_size_mm, area_cm2=meas.area_cm2
    )
    return area_display, last_raw_url


def serialize_installation(
    inst: Installation,
    role: str,
    db: Session,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Строит проекцию карточки установки в зависимости от роли клиента."""
    area_display, last_raw_url = get_installation_last_capture_info(
        inst.id, inst.aruco_size_mm, db
    )
    frames_count = db.query(Capture).filter(Capture.installation_id == inst.id).count()

    next_capture_at = None
    if not inst.is_archived:
        last_cap = (
            db.query(Capture)
            .filter(Capture.installation_id == inst.id)
            .order_by(Capture.captured_at.desc())
            .first()
        )
        last_time = last_cap.captured_at if last_cap else None
        setting = (
            db.query(SystemSetting)
            .filter(SystemSetting.key == "capture_interval_hours")
            .first()
        )
        interval_h = int(setting.value) if setting and setting.value else 1
        next_dt = compute_next_capture_at(last_time, interval_h)
        next_capture_at = format_utc_z(next_dt)

    data: dict[str, Any] = {
        "id": inst.id,
        "name": inst.name,
        "culture_name": inst.culture_name,
        "is_archived": inst.is_archived,
        "last_area_display": area_display,
        "last_frame_raw_url": last_raw_url,
        "last_frame_url": last_raw_url,
        "frames_count": frames_count,
        "next_capture_at": next_capture_at,
        "warnings": warnings if warnings is not None else [],
    }

    if role == "guest":
        return data

    cameras_list = []
    for c in inst.cameras:
        cam_info: dict[str, Any] = {
            "id": c.id,
            "auto_name": c.auto_name,
            "is_online": c.is_online,
        }
        if role == "developer":
            cam_info["hardware_id"] = c.hardware_id
        cameras_list.append(cam_info)

    data.update(
        {
            "aruco_size_mm": inst.aruco_size_mm,
            "created_at": format_utc_z(inst.created_at),
            "cameras": cameras_list,
            "capture_delay_sec": inst.capture_delay_sec,
        }
    )

    if role == "developer":
        light_st = app_state.get_pin_state(inst.light_gpio_pin, default=True)
        if inst.flash_gpio_pin == inst.light_gpio_pin:
            flash_st = light_st
        else:
            flash_st = app_state.get_pin_state(inst.flash_gpio_pin, default=False)

        data.update(
            {
                "light_gpio_pin": inst.light_gpio_pin,
                "flash_gpio_pin": inst.flash_gpio_pin,
                "light_state": light_st,
                "flash_state": flash_st,
            }
        )

    return data


def check_pin_conflicts_and_warnings(
    light_pin: int,
    flash_pin: int,
    db: Session,
    exclude_id: int | None = None,
) -> list[str]:
    """Проверяет пины внутри одной установки и собирает предупреждения о занятых пинах."""
    warnings: list[str] = []

    # Предупреждение о совпадении пинов света и вспышки
    if light_pin == flash_pin:
        warnings.append(
            f"⚠️ Пины света и вспышки совпадают (пин {light_pin}). "
            "Управляется только свет, при съёмке он не выключается."
        )

    query = db.query(Installation).filter(Installation.is_archived.is_(False))
    if exclude_id is not None:
        query = query.filter(Installation.id != exclude_id)
    active_installations = query.all()

    # Проверка занятости light_pin
    light_used_by = [
        inst.name
        for inst in active_installations
        if light_pin in (inst.light_gpio_pin, inst.flash_gpio_pin)
    ]
    if light_used_by:
        names_str = ", ".join(f"«{name}»" for name in light_used_by)
        warnings.append(
            f"⚠️ Пин {light_pin} уже занят ({names_str}). Реле будут общими."
        )

    # Проверка занятости flash_pin (только если отличается от light_pin)
    if flash_pin != light_pin:
        flash_used_by = [
            inst.name
            for inst in active_installations
            if flash_pin in (inst.light_gpio_pin, inst.flash_gpio_pin)
        ]
        if flash_used_by:
            names_str = ", ".join(f"«{name}»" for name in flash_used_by)
            warnings.append(
                f"⚠️ Пин {flash_pin} уже занят ({names_str}). Реле будут общими."
            )

    return warnings


# Для обратной совместимости вызовов
validate_pin_uniqueness = check_pin_conflicts_and_warnings


def validate_name_uniqueness(
    name: str, db: Session, exclude_id: int | None = None
) -> None:
    """Проверяет уникальность названия установки."""
    query = db.query(Installation).filter(Installation.name == name)
    if exclude_id is not None:
        query = query.filter(Installation.id != exclude_id)
    if query.first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Имя установки '{name}' уже занято",
        )


@router.get("", response_model=list[dict[str, Any]])
def list_installations(
    client_user: Annotated[ClientUser, Depends(get_current_user_optional)],
    db: Annotated[Session, Depends(get_db)],
    include_archived: bool = Query(default=False),
) -> list[dict[str, Any]]:
    """4.2. GET /api/installations.

    Список установок с проекцией по роли пользователя.
    Для роли Гость всегда возвращаются только активные установки.
    """
    if client_user.is_guest:
        include_archived = False

    query = db.query(Installation)
    if not include_archived:
        query = query.filter(Installation.is_archived.is_(False))

    installations = query.order_by(Installation.id.asc()).all()
    return [
        serialize_installation(inst, client_user.role, db) for inst in installations
    ]


@router.get("/{installation_id}", response_model=dict[str, Any])
def get_installation(
    installation_id: int,
    client_user: Annotated[ClientUser, Depends(get_current_user_optional)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """4.2. GET /api/installations/{id}.

    Карточка установки с проекцией по роли пользователя.
    """
    inst = db.query(Installation).filter(Installation.id == installation_id).first()
    if not inst:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Установка не найдена",
        )

    return serialize_installation(inst, client_user.role, db)


@router.post(
    "",
    response_model=dict[str, Any],
    status_code=status.HTTP_201_CREATED,
)
def create_installation(
    body: InstallationCreate,
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """4.2. POST /api/installations (Developer).

    Создание новой установки с валидацией имени и пинов.
    """
    validate_name_uniqueness(body.name, db)
    warnings = check_pin_conflicts_and_warnings(
        body.light_gpio_pin, body.flash_gpio_pin, db
    )

    inst = Installation(
        name=body.name,
        culture_name=body.culture_name,
        light_gpio_pin=body.light_gpio_pin,
        flash_gpio_pin=body.flash_gpio_pin,
        aruco_size_mm=body.aruco_size_mm,
        capture_delay_sec=body.capture_delay_sec,
        is_archived=False,
    )
    db.add(inst)
    db.commit()
    db.refresh(inst)

    # По умолчанию свет включен, вспышка выключена
    app_state.set_pin_state(inst.light_gpio_pin, True)
    if inst.flash_gpio_pin != inst.light_gpio_pin:
        app_state.set_pin_state(inst.flash_gpio_pin, False)

    return serialize_installation(inst, current_user.role, db, warnings=warnings)


@router.patch("/{installation_id}", response_model=dict[str, Any])
def patch_installation(
    installation_id: int,
    body: InstallationPatch,
    current_user: Annotated[User, Depends(require_role("biologist", "developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """4.2. PATCH /api/installations/{id}.

    Редактирование параметров установки с проверкой прав роли.
    """
    inst = db.query(Installation).filter(Installation.id == installation_id).first()
    if not inst:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Установка не найдена",
        )

    # Проверка прав: Biologist может менять name, culture_name и capture_delay_sec
    update_data = body.model_dump(exclude_unset=True)
    if current_user.role == "biologist":
        disallowed_fields = set(update_data.keys()) - {
            "name",
            "culture_name",
            "capture_delay_sec",
        }
        if disallowed_fields:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Недостаточно прав для изменения параметров оборудования",
            )

    # Валидация имени при изменении
    if "name" in update_data and update_data["name"] != inst.name:
        validate_name_uniqueness(update_data["name"], db, exclude_id=inst.id)
        inst.name = update_data["name"]

    if "culture_name" in update_data:
        inst.culture_name = update_data["culture_name"]

    warnings: list[str] = []
    # Валидация пинов (только Developer)
    if "light_gpio_pin" in update_data or "flash_gpio_pin" in update_data:
        if inst.is_archived:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Установка в архиве",
            )
        new_light = update_data.get("light_gpio_pin", inst.light_gpio_pin)
        new_flash = update_data.get("flash_gpio_pin", inst.flash_gpio_pin)
        warnings = check_pin_conflicts_and_warnings(
            new_light, new_flash, db, exclude_id=inst.id
        )
        inst.light_gpio_pin = new_light
        inst.flash_gpio_pin = new_flash

    if "aruco_size_mm" in update_data:
        inst.aruco_size_mm = update_data["aruco_size_mm"]

    if "capture_delay_sec" in update_data:
        inst.capture_delay_sec = update_data["capture_delay_sec"]

    db.commit()
    db.refresh(inst)

    return serialize_installation(inst, current_user.role, db, warnings=warnings)


@router.post("/{installation_id}/archive", response_model=dict[str, Any])
def archive_installation(
    installation_id: int,
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """4.2. POST /api/installations/{id}/archive (Developer).

    Архивация установки и освобождение привязанных камер.
    """
    inst = db.query(Installation).filter(Installation.id == installation_id).first()
    if not inst:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Установка не найдена",
        )

    inst.is_archived = True
    for cam in inst.cameras:
        cam.installation_id = None

    db.commit()
    db.refresh(inst)

    return serialize_installation(inst, current_user.role, db)


@router.post("/{installation_id}/unarchive", response_model=dict[str, Any])
def unarchive_installation(
    installation_id: int,
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """POST /api/installations/{id}/unarchive (Developer).

    Восстановление установки из архива:
    - перевод в is_archived = False;
    - свет по умолчанию ВКЛ (True), вспышка ВЫКЛ (False);
    - проверка пересечения пинов с другими активными установками и возврат warnings.
    """
    inst = db.query(Installation).filter(Installation.id == installation_id).first()
    if not inst:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Установка не найдена",
        )

    inst.is_archived = False
    db.commit()
    db.refresh(inst)

    app_state.set_pin_state(inst.light_gpio_pin, True)
    if inst.flash_gpio_pin != inst.light_gpio_pin:
        app_state.set_pin_state(inst.flash_gpio_pin, False)

    warnings = check_pin_conflicts_and_warnings(
        inst.light_gpio_pin, inst.flash_gpio_pin, db, exclude_id=inst.id
    )

    return serialize_installation(inst, current_user.role, db, warnings=warnings)


@router.delete("/{installation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_installation(
    installation_id: int,
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """4.2. DELETE /api/installations/{id} (Developer).

    Удаление установки без снимков (при наличии снимков -> 409, если не в архиве;
    если в архиве -> отвязка камер, каскадное удаление кадров и файлов).
    """
    inst = db.query(Installation).filter(Installation.id == installation_id).first()
    if not inst:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Установка не найдена",
        )

    captures_count = (
        db.query(Capture).filter(Capture.installation_id == installation_id).count()
    )
    if captures_count > 0:
        if not inst.is_archived:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="У установки есть снимки, используйте архивацию",
            )
        # Для архивной установки: отвязываем камеры, удаляем кадры из БД и файлы с диска
        for cam in inst.cameras:
            cam.installation_id = None

        db.query(Capture).filter(Capture.installation_id == installation_id).delete(
            synchronize_session="fetch"
        )

        media_inst_dir = edge_api.get_media_dir() / str(installation_id)
        if media_inst_dir.exists() and media_inst_dir.is_dir():
            shutil.rmtree(media_inst_dir, ignore_errors=True)
    else:
        for cam in inst.cameras:
            cam.installation_id = None

    db.delete(inst)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
