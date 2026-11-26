from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from server.api.deps import (
    create_session_cookie,
    format_utc_z,
    get_current_user,
    get_db,
    require_role,
    verify_client_key,
    verify_session_cookie,
)
from server.config import get_settings
from server.core.auth_service import (
    AuthService,
    auth_service,
    generate_invite_code,
)
from server.core.bot_info import get_telegram_bot_username
from server.core.rate_limit import login_limiter
from server.db.models import User
from server.schemas.auth import (
    InviteCreateResponse,
    LoginRequest,
    LoginResponse,
    MeResponse,
    SessionInfo,
    TelegramContactSync,
    TelegramLookupResponse,
    UpdateLoginRequest,
    WebLoginRequest,
    WebLoginResponse,
    WebMeResponse,
    WebRegisterCompleteRequest,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_extract_numeric_tg_id = AuthService.extract_numeric_tg_id
_get_telegram_username_map = AuthService.get_telegram_username_map
_get_telegram_username = AuthService.get_telegram_username
_build_me_response = AuthService.build_me_response
_build_session_info = AuthService.build_session_info

__all__ = [
    "InviteCreateResponse",
    "LoginRequest",
    "LoginResponse",
    "MeResponse",
    "SessionInfo",
    "TelegramContactSync",
    "TelegramLookupResponse",
    "UpdateLoginRequest",
    "WebLoginRequest",
    "WebLoginResponse",
    "WebMeResponse",
    "WebRegisterCompleteRequest",
    "_build_me_response",
    "_build_session_info",
    "_extract_numeric_tg_id",
    "_get_telegram_username",
    "_get_telegram_username_map",
    "generate_invite_code",
    "router",
]


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

    result = auth_service.authenticate_client_login(
        db=db,
        client_type=body.client_type,
        external_id=body.external_id,
        password=body.password,
        login=body.login,
        settings=get_settings(),
    )
    if result["needs_login"]:
        return JSONResponse(
            status_code=200,
            content={"status": "needs_login", "role": result["role"]},
        )
    return LoginResponse(role=result["role"], login=result["login"])


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
    db: Annotated[Session, Depends(get_db)],
) -> MeResponse:
    """4.1. GET /api/auth/me.

    Информация о текущей сессии пользователя.
    """
    return _build_me_response(current_user, db)


@router.patch("/me", response_model=MeResponse)
def update_me(
    body: UpdateLoginRequest,
    response: Response,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> MeResponse:
    """4.1. PATCH /api/auth/me.

    Изменение отображаемого имени и (опционально) Telegram текущего пользователя.
    """
    old_external_id = current_user.external_id
    updated_user = auth_service.update_me_profile(
        db=db,
        user=current_user,
        new_login=body.login or "",
        telegram_provided="telegram" in body.model_fields_set,
        telegram_raw=body.telegram,
    )
    if (
        updated_user.client_type == "web"
        and updated_user.external_id != old_external_id
    ):
        cookie_val = create_session_cookie(updated_user)
        response.set_cookie(
            key="gv_session",
            value=cookie_val,
            httponly=True,
            samesite="lax",
            path="/",
        )
    return _build_me_response(updated_user, db)


# ================== Telegram синхронизация контактов ==================


@router.post(
    "/telegram/contact",
    response_model=dict[str, bool],
    dependencies=[Depends(verify_client_key)],
)
def sync_telegram_contact(
    body: TelegramContactSync,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, bool]:
    """Синхронизация связки username -> telegram_id от Telegram-бота."""
    auth_service.sync_telegram_contact(
        db=db,
        telegram_id=body.telegram_id,
        username=body.username,
        first_name=body.first_name,
        last_name=body.last_name,
    )
    return {"ok": True}


# ================== Веб-регистрация и авторизация (Web SPA) ==================


@router.get("/web/telegram-lookup", response_model=TelegramLookupResponse)
def auth_web_telegram_lookup(
    telegram: str,
    db: Annotated[Session, Depends(get_db)],
) -> TelegramLookupResponse:
    """Поиск контакта Telegram в БД для автоподстановки имени и фамилии."""
    return auth_service.lookup_telegram_contact(db, telegram)


@router.post("/web/register/complete", response_model=WebLoginResponse)
@router.post("/web/register", response_model=WebLoginResponse)
def auth_web_register_complete(
    body: WebRegisterCompleteRequest,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> WebLoginResponse:
    """Регистрация web-пользователя в 1 шаг без подтверждения кодом через Telegram."""
    user, role, full_name = auth_service.complete_web_registration(
        db=db,
        telegram_raw=body.telegram,
        telegram_id=body.telegram_id,
        full_name_raw=body.full_name or "",
        access_code_raw=body.access_code,
        settings=get_settings(),
    )

    cookie_val = create_session_cookie(user)
    response.set_cookie(
        key="gv_session",
        value=cookie_val,
        httponly=True,
        samesite="lax",
        path="/",
    )

    return WebLoginResponse(role=role, login=full_name, status="ok")


@router.post("/web/login", response_model=WebLoginResponse)
def auth_web_login(
    body: WebLoginRequest,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> WebLoginResponse:
    """POST /api/auth/web/login.

    Веб-вход по Telegram (@username или ID) и коду доступа (пароль разработчика или инвайт биолога).
    Устанавливает HttpOnly-cookie gv_session (SameSite=Lax).
    """
    allowed, retry_after = login_limiter.is_allowed("web", body.telegram)
    if not allowed:
        response.headers["Retry-After"] = str(retry_after)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Превышен лимит попыток входа. Попробуйте позже.",
            headers={"Retry-After": str(retry_after)},
        )

    user = auth_service.authenticate_web_login(
        db=db,
        telegram_input_raw=body.telegram,
        access_code_raw=body.code or body.password or "",
        settings=get_settings(),
    )

    cookie_val = create_session_cookie(user)
    response.set_cookie(
        key="gv_session",
        value=cookie_val,
        httponly=True,
        samesite="lax",
        path="/",
    )

    return WebLoginResponse(role=user.role, login=user.login, status="ok")


@router.post("/web/logout")
def auth_web_logout(
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    gv_session: Annotated[str | None, Cookie(alias="gv_session")] = None,
) -> dict[str, bool]:
    """POST /api/auth/web/logout.

    Выход из системы для веб-клиента (очистка сессии и HttpOnly-cookie).
    """
    response.delete_cookie(key="gv_session", path="/")
    if gv_session:
        user = verify_session_cookie(gv_session, db)
        if user:
            user.is_active = False
            user.logged_out_at = datetime.now(UTC)
            db.commit()
    return {"ok": True}


@router.get("/web/me", response_model=WebMeResponse)
def auth_web_me(
    db: Annotated[Session, Depends(get_db)],
    gv_session: Annotated[str | None, Cookie(alias="gv_session")] = None,
) -> WebMeResponse:
    """GET /api/auth/web/me.

    Информация о текущем веб-пользователе или статус Гость.
    """
    bot_username = get_telegram_bot_username()
    if gv_session:
        user = verify_session_cookie(gv_session, db)
        if user:
            return WebMeResponse(
                authenticated=True,
                role=user.role,
                id=user.id,
                login=user.login,
                client_type=user.client_type,
                external_id=user.external_id,
                telegram_username=_get_telegram_username(user, db),
                telegram_url=auth_service.get_telegram_url(user, db),
                authorized_at=format_utc_z(user.authorized_at),
                last_active_at=format_utc_z(user.last_active_at),
                bot_username=bot_username,
            )
    return WebMeResponse(
        authenticated=False, role="guest", login=None, bot_username=bot_username
    )


# ================== Управление приглашениями (Developer) ==================


@router.post("/invites", response_model=InviteCreateResponse)
def create_invite_code(
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> InviteCreateResponse:
    """Генерация одноразового инвайт-кода для биолога (срок действия 10 минут)."""
    return auth_service.create_invite(db, current_user.id)


@router.get("/invites", response_model=list[InviteCreateResponse])
def list_active_invites(
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> list[InviteCreateResponse]:
    """Список активных неиспользованных инвайт-кодов."""
    return auth_service.list_active_invites(db)


@router.delete("/invites/{invite_id}", response_model=dict[str, bool])
def revoke_invite(
    invite_id: int,
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, bool]:
    """Отзыв неиспользованного инвайт-кода."""
    auth_service.revoke_invite(db, invite_id)
    return {"ok": True}


# ================== Управление сессиями (Developer) ==================


@router.get("/sessions", response_model=list[SessionInfo])
def list_sessions(
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> list[SessionInfo]:
    """Список всех сессий пользователей (активных и неактивных)."""
    return auth_service.list_sessions(db, current_user.id)


@router.post("/sessions/{session_id}/terminate", response_model=dict[str, bool])
def terminate_session(
    session_id: int,
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, bool]:
    """Принудительное завершение активной сессии пользователя (кик)."""
    auth_service.terminate_session(db, session_id)
    return {"ok": True}


@router.patch("/sessions/{session_id}", response_model=SessionInfo)
def rename_session_user(
    session_id: int,
    body: UpdateLoginRequest,
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> SessionInfo:
    """Изменение отображаемого имени пользователя любой сессии (Developer)."""
    return auth_service.rename_session_user(
        db=db,
        session_id=session_id,
        new_login=body.login or "",
        current_user_id=current_user.id,
    )
