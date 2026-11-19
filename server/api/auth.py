import secrets
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, ValidationInfo, field_validator
from sqlalchemy.orm import Session

from server.api.deps import (
    format_utc_z,
    get_current_user,
    get_db,
    verify_client_key,
)
from server.config import get_settings
from server.core.rate_limit import login_limiter
from server.db.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    """Тело запроса POST /api/auth/login."""

    model_config = ConfigDict(extra="ignore")

    client_type: Literal["telegram", "cli"]
    external_id: str
    login: str
    password: str

    @field_validator("login", "external_id")
    @classmethod
    def validate_non_empty(cls, v: str, info: ValidationInfo) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"Поле {info.field_name} не может быть пустым")
        return v.strip()


class LoginResponse(BaseModel):
    """Ответ на успешный вход."""

    role: str
    login: str


class MeResponse(BaseModel):
    """Ответ на GET /api/auth/me."""

    login: str
    role: str
    authorized_at: str
    last_active_at: str


@router.post(
    "/login",
    response_model=LoginResponse,
    dependencies=[Depends(verify_client_key)],
)
def auth_login(
    body: LoginRequest,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> LoginResponse:
    """4.1. POST /api/auth/login.

    Вход по логину и паролю. Лимит: не более 5 попыток в минуту.
    """
    # 1. Проверка лимита попыток входа
    allowed, retry_after = login_limiter.is_allowed(body.client_type, body.external_id)
    if not allowed:
        response.headers["Retry-After"] = str(retry_after)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Превышен лимит попыток входа. Попробуйте позже.",
            headers={"Retry-After": str(retry_after)},
        )

    # 2. Проверка пароля через secrets.compare_digest
    settings = get_settings()
    pwd_bytes = body.password.encode("utf-8")

    role: str | None = None
    if settings.developer_password and secrets.compare_digest(
        pwd_bytes, settings.developer_password.encode("utf-8")
    ):
        role = "developer"
    elif settings.biologist_password and secrets.compare_digest(
        pwd_bytes, settings.biologist_password.encode("utf-8")
    ):
        role = "biologist"

    if not role:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный пароль",
        )

    # 3. Создание или обновление сессии в users
    now = datetime.now(UTC)
    user = (
        db.query(User)
        .filter(
            User.client_type == body.client_type,
            User.external_id == body.external_id,
        )
        .first()
    )
    if user:
        user.login = body.login
        user.role = role
        user.authorized_at = now
        user.last_active_at = now
    else:
        user = User(
            client_type=body.client_type,
            external_id=body.external_id,
            login=body.login,
            role=role,
            authorized_at=now,
            last_active_at=now,
        )
        db.add(user)

    db.commit()

    return LoginResponse(role=role, login=body.login)


@router.post("/logout", response_model=dict[str, bool])
def auth_logout(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, bool]:
    """4.1. POST /api/auth/logout.

    Выход из системы, удаление активной сессии пользователя.
    """
    db.delete(current_user)
    db.commit()
    return {"ok": True}


@router.get("/me", response_model=MeResponse)
def auth_me(
    current_user: Annotated[User, Depends(get_current_user)],
) -> MeResponse:
    """4.1. GET /api/auth/me.

    Информация о текущей сессии пользователя.
    """
    auth_at = format_utc_z(current_user.authorized_at) or ""
    last_act = format_utc_z(current_user.last_active_at) or ""
    return MeResponse(
        login=current_user.login,
        role=current_user.role,
        authorized_at=auth_at,
        last_active_at=last_act,
    )
