import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationInfo, field_validator
from sqlalchemy.orm import Session

from server.api.deps import (
    format_utc_z,
    get_current_user,
    get_db,
    require_role,
    verify_client_key,
)
from server.config import get_settings
from server.core.rate_limit import login_limiter
from server.db.models import InviteCode, User

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    """Тело запроса POST /api/auth/login."""

    model_config = ConfigDict(extra="ignore")

    client_type: Literal["telegram", "cli"]
    external_id: str
    password: str
    login: str | None = None

    @field_validator("external_id")
    @classmethod
    def validate_external_id(cls, v: str, info: ValidationInfo) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"Поле {info.field_name} не может быть пустым")
        return v.strip()


class LoginResponse(BaseModel):
    """Ответ на успешный вход или запрос ввода логина."""

    role: str
    login: str | None = None
    status: str | None = None


class MeResponse(BaseModel):
    """Ответ на GET /api/auth/me."""

    login: str
    role: str
    authorized_at: str
    last_active_at: str


class UpdateLoginRequest(BaseModel):
    """Тело запроса на изменение отображаемого имени пользователя."""

    model_config = ConfigDict(extra="ignore")

    login: str

    @field_validator("login")
    @classmethod
    def validate_login(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Имя пользователя не может быть пустым")
        val = v.strip()
        if len(val) > 64:
            raise ValueError("Длина имени не должна превышать 64 символа")
        return val


class InviteCreateResponse(BaseModel):
    """Ответ на создание одноразового кода."""

    id: int
    code: str
    role: str
    created_at: str
    expires_at: str


class SessionInfo(BaseModel):
    """Информация о сессии пользователя."""

    id: int
    client_type: str
    external_id: str
    login: str
    role: str
    is_active: bool
    authorized_at: str
    last_active_at: str
    logged_out_at: str | None = None


def generate_invite_code(length: int = 6) -> str:
    """Генерирует читаемый уникальный код из букв и цифр без похожих символов."""
    chars = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
    return "".join(secrets.choice(chars) for _ in range(length))


@router.post(
    "/login",
    response_model=LoginResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(verify_client_key)],
)
def auth_login(
    body: LoginRequest,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> Any:
    """4.1. POST /api/auth/login.

    Вход по логину/паролю или одноразовому коду. Лимит: не более 5 попыток в минуту.
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

    settings = get_settings()
    pwd_bytes = body.password.strip().encode("utf-8")
    now = datetime.now(UTC)

    role: str | None = None
    matched_invite: InviteCode | None = None

    # 2. Проверка прав в зависимости от типа клиента
    if body.client_type == "cli":
        # В CLI принимается ТОЛЬКО пароль разработчика
        if settings.developer_password and secrets.compare_digest(
            pwd_bytes, settings.developer_password.encode("utf-8")
        ):
            role = "developer"
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Неверный пароль разработчика",
            )
        if not body.login or not body.login.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Для входа через CLI необходимо указать login",
            )
    else:
        # Для Telegram: проверяем пароль разработчика или одноразовый код
        if settings.developer_password and secrets.compare_digest(
            pwd_bytes, settings.developer_password.encode("utf-8")
        ):
            role = "developer"
        else:
            # Ищем активный неиспользованный одноразовый код
            invite = (
                db.query(InviteCode)
                .filter(
                    InviteCode.code == body.password.strip().upper(),
                    InviteCode.used_at.is_(None),
                    InviteCode.is_revoked.is_(False),
                    InviteCode.expires_at > now,
                )
                .first()
            )
            if invite:
                role = invite.role
                matched_invite = invite
            elif settings.biologist_password and secrets.compare_digest(
                pwd_bytes, settings.biologist_password.encode("utf-8")
            ):
                # Поддержка постоянного пароля биолога (legacy/fallback)
                role = "biologist"
            else:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Неверный пароль или одноразовый код",
                )

    # 3. Поиск существующего пользователя по (client_type, external_id)
    user = (
        db.query(User)
        .filter(
            User.client_type == body.client_type,
            User.external_id == body.external_id,
        )
        .first()
    )

    resolved_login = body.login.strip() if (body.login and body.login.strip()) else None

    if not user:
        # Новый пользователь: если login не передан, НЕ сжигаем код и запрашиваем имя
        if not resolved_login:
            return JSONResponse(
                status_code=200,
                content={"status": "needs_login", "role": role},
            )
        user = User(
            client_type=body.client_type,
            external_id=body.external_id,
            login=resolved_login,
            role=role,
            is_active=True,
            authorized_at=now,
            last_active_at=now,
            logged_out_at=None,
        )
        db.add(user)
        db.flush()
    else:
        # Пользователь уже существует: подтягиваем сохранённый login, если не передан новый
        if not resolved_login:
            resolved_login = user.login
        user.login = resolved_login
        user.role = role
        user.is_active = True
        user.authorized_at = now
        user.last_active_at = now
        user.logged_out_at = None

    # Если использован одноразовый код — помечаем его
    if matched_invite:
        matched_invite.used_at = now
        matched_invite.used_by_user_id = user.id

    db.commit()

    return LoginResponse(role=role, login=resolved_login)


@router.post("/logout", response_model=dict[str, bool])
def auth_logout(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, bool]:
    """4.1. POST /api/auth/logout.

    Выход из системы. Запись пользователя сохраняется с is_active=False и logged_out_at.
    """
    current_user.is_active = False
    current_user.logged_out_at = datetime.now(UTC)
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


@router.patch("/me", response_model=MeResponse)
def update_me(
    body: UpdateLoginRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> MeResponse:
    """4.1. PATCH /api/auth/me.

    Изменение отображаемого имени текущего пользователя.
    """
    current_user.login = body.login
    db.commit()
    db.refresh(current_user)
    return MeResponse(
        login=current_user.login,
        role=current_user.role,
        authorized_at=format_utc_z(current_user.authorized_at) or "",
        last_active_at=format_utc_z(current_user.last_active_at) or "",
    )


# ================== Управление приглашениями (Developer) ==================


@router.post("/invites", response_model=InviteCreateResponse)
def create_invite_code(
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> InviteCreateResponse:
    """Генерация одноразового инвайт-кода для биолога (срок действия 10 минут)."""
    now = datetime.now(UTC)
    expires = now + timedelta(minutes=10)

    # Генерируем уникальный код
    code = generate_invite_code(6)
    while db.query(InviteCode).filter(InviteCode.code == code).first():
        code = generate_invite_code(6)

    invite = InviteCode(
        code=code,
        role="biologist",
        created_by_user_id=current_user.id,
        created_at=now,
        expires_at=expires,
        is_revoked=False,
    )
    db.add(invite)
    db.commit()

    return InviteCreateResponse(
        id=invite.id,
        code=invite.code,
        role=invite.role,
        created_at=format_utc_z(invite.created_at) or "",
        expires_at=format_utc_z(invite.expires_at) or "",
    )


@router.get("/invites", response_model=list[InviteCreateResponse])
def list_active_invites(
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> list[InviteCreateResponse]:
    """Список активных неиспользованных инвайт-кодов."""
    now = datetime.now(UTC)
    invites = (
        db.query(InviteCode)
        .filter(
            InviteCode.used_at.is_(None),
            InviteCode.is_revoked.is_(False),
            InviteCode.expires_at > now,
        )
        .order_by(InviteCode.created_at.desc())
        .all()
    )
    return [
        InviteCreateResponse(
            id=i.id,
            code=i.code,
            role=i.role,
            created_at=format_utc_z(i.created_at) or "",
            expires_at=format_utc_z(i.expires_at) or "",
        )
        for i in invites
    ]


@router.delete("/invites/{invite_id}", response_model=dict[str, bool])
def revoke_invite(
    invite_id: int,
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, bool]:
    """Отзыв неиспользованного инвайт-кода."""
    invite = db.query(InviteCode).filter(InviteCode.id == invite_id).first()
    if not invite:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Инвайт-код #{invite_id} не найден",
        )
    invite.is_revoked = True
    db.commit()
    return {"ok": True}


# ================== Управление сессиями (Developer) ==================


@router.get("/sessions", response_model=list[SessionInfo])
def list_sessions(
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> list[SessionInfo]:
    """Список всех сессий пользователей (активных и неактивных)."""
    users = db.query(User).order_by(User.last_active_at.desc()).all()
    return [
        SessionInfo(
            id=u.id,
            client_type=u.client_type,
            external_id=u.external_id,
            login=u.login,
            role=u.role,
            is_active=u.is_active,
            authorized_at=format_utc_z(u.authorized_at) or "",
            last_active_at=format_utc_z(u.last_active_at) or "",
            logged_out_at=format_utc_z(u.logged_out_at),
        )
        for u in users
    ]


@router.post("/sessions/{session_id}/terminate", response_model=dict[str, bool])
def terminate_session(
    session_id: int,
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, bool]:
    """Принудительное завершение активной сессии пользователя (кик)."""
    target_user = db.query(User).filter(User.id == session_id).first()
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Сессия #{session_id} не найдена",
        )
    target_user.is_active = False
    target_user.logged_out_at = datetime.now(UTC)
    db.commit()
    return {"ok": True}


@router.patch("/sessions/{session_id}", response_model=SessionInfo)
def rename_session_user(
    session_id: int,
    body: UpdateLoginRequest,
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> SessionInfo:
    """Изменение отображаемого имени пользователя любой сессии (Developer)."""
    target_user = db.query(User).filter(User.id == session_id).first()
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Сессия #{session_id} не найдена",
        )
    target_user.login = body.login
    db.commit()
    db.refresh(target_user)
    return SessionInfo(
        id=target_user.id,
        client_type=target_user.client_type,
        external_id=target_user.external_id,
        login=target_user.login,
        role=target_user.role,
        is_active=target_user.is_active,
        authorized_at=format_utc_z(target_user.authorized_at) or "",
        last_active_at=format_utc_z(target_user.last_active_at) or "",
        logged_out_at=format_utc_z(target_user.logged_out_at),
    )
