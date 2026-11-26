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
from server.core.bot_info import get_telegram_bot_username
from server.core.rate_limit import login_limiter
from server.core.verification import (
    VerificationService,
    generate_invite_code,
    resolve_telegram_contact,
    resolve_telegram_id,
    verification_service,
)
from server.db.models import User
from server.schemas.auth import (
    FailVerificationCodeRequest,
    InviteCreateResponse,
    LoginRequest,
    LoginResponse,
    MeResponse,
    PendingCodeItem,
    SessionInfo,
    TelegramContactSync,
    UpdateLoginRequest,
    WebLoginRequest,
    WebLoginResponse,
    WebMeResponse,
    WebRegisterCompleteRequest,
    WebRegisterStartRequest,
    WebRegisterVerifyRequest,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_extract_numeric_tg_id = VerificationService.extract_numeric_tg_id
_get_telegram_username_map = VerificationService.get_telegram_username_map
_get_telegram_username = VerificationService.get_telegram_username
_build_me_response = VerificationService.build_me_response
_build_session_info = VerificationService.build_session_info

__all__ = [
    "FailVerificationCodeRequest",
    "InviteCreateResponse",
    "LoginRequest",
    "LoginResponse",
    "MeResponse",
    "PendingCodeItem",
    "SessionInfo",
    "TelegramContactSync",
    "UpdateLoginRequest",
    "WebLoginRequest",
    "WebLoginResponse",
    "WebMeResponse",
    "WebRegisterCompleteRequest",
    "WebRegisterStartRequest",
    "WebRegisterVerifyRequest",
    "_build_me_response",
    "_build_session_info",
    "_extract_numeric_tg_id",
    "_get_telegram_username",
    "_get_telegram_username_map",
    "generate_invite_code",
    "resolve_telegram_contact",
    "resolve_telegram_id",
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

    result = verification_service.authenticate_client_login(
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
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> MeResponse:
    """4.1. PATCH /api/auth/me.

    Изменение отображаемого имени текущего пользователя.
    """
    current_user.login = body.login
    db.commit()
    db.refresh(current_user)
    return _build_me_response(current_user, db)


# ================== Telegram синхронизация и доставка кодов ==================


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
    verification_service.sync_telegram_contact(
        db=db,
        telegram_id=body.telegram_id,
        username=body.username,
        first_name=body.first_name,
        last_name=body.last_name,
    )
    return {"ok": True}


@router.get(
    "/web/pending-codes",
    response_model=list[PendingCodeItem],
    dependencies=[Depends(verify_client_key)],
)
def get_pending_verification_codes(
    db: Annotated[Session, Depends(get_db)],
) -> list[PendingCodeItem]:
    """Очередь неотправленных кодов подтверждения регистрации для Telegram-бота."""
    return verification_service.get_pending_codes(db)


@router.post(
    "/web/pending-codes/{code_id}/ack",
    response_model=dict[str, bool],
    dependencies=[Depends(verify_client_key)],
)
def ack_verification_code(
    code_id: int,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, bool]:
    """Подтверждение доставки кода пользователю в Telegram."""
    verification_service.ack_code_delivery(db, code_id)
    return {"ok": True}


@router.post(
    "/web/pending-codes/{code_id}/fail",
    response_model=dict[str, bool],
    dependencies=[Depends(verify_client_key)],
)
def fail_verification_code(
    code_id: int,
    body: FailVerificationCodeRequest,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, bool]:
    """Подтверждение ошибки отправки кода подтверждения Telegram-ботом."""
    verification_service.fail_code_delivery(db, code_id, body.error)
    return {"ok": True}


# ================== Веб-регистрация и авторизация (Web SPA) ==================


@router.get("/web/register/code-status")
def get_verification_code_status(
    telegram_id: int,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Проверка статуса доставки кода подтверждения для указанного пользователя."""
    return verification_service.get_code_status(db, telegram_id)


@router.post("/web/register/start")
def auth_web_register_start(
    body: WebRegisterStartRequest,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Шаг 1 регистрации: валидация Telegram пользователя и отправка 6-значного кода."""
    return verification_service.start_web_registration(db, body.telegram)


@router.post("/web/register/verify")
def auth_web_register_verify(
    body: WebRegisterVerifyRequest,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Шаг 2 регистрации: проверка введённого 6-значного кода."""
    return verification_service.verify_web_registration_code(
        db, body.telegram_id, body.code
    )


@router.post("/web/register/complete", response_model=WebLoginResponse)
def auth_web_register_complete(
    body: WebRegisterCompleteRequest,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> WebLoginResponse:
    """Шаг 3 регистрации: профиль, инвайт/пароль разработчика, личный пароль."""
    user, role, full_name = verification_service.complete_web_registration(
        db=db,
        telegram_id=body.telegram_id,
        verification_code=body.verification_code,
        full_name_raw=body.full_name,
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

    user = verification_service.authenticate_web_login(
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
    return verification_service.create_invite(db, current_user.id)


@router.get("/invites", response_model=list[InviteCreateResponse])
def list_active_invites(
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> list[InviteCreateResponse]:
    """Список активных неиспользованных инвайт-кодов."""
    return verification_service.list_active_invites(db)


@router.delete("/invites/{invite_id}", response_model=dict[str, bool])
def revoke_invite(
    invite_id: int,
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, bool]:
    """Отзыв неиспользованного инвайт-кода."""
    verification_service.revoke_invite(db, invite_id)
    return {"ok": True}


# ================== Управление сессиями (Developer) ==================


@router.get("/sessions", response_model=list[SessionInfo])
def list_sessions(
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> list[SessionInfo]:
    """Список всех сессий пользователей (активных и неактивных)."""
    return verification_service.list_sessions(db, current_user.id)


@router.post("/sessions/{session_id}/terminate", response_model=dict[str, bool])
def terminate_session(
    session_id: int,
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, bool]:
    """Принудительное завершение активной сессии пользователя (кик)."""
    verification_service.terminate_session(db, session_id)
    return {"ok": True}


@router.patch("/sessions/{session_id}", response_model=SessionInfo)
def rename_session_user(
    session_id: int,
    body: UpdateLoginRequest,
    current_user: Annotated[User, Depends(require_role("developer"))],
    db: Annotated[Session, Depends(get_db)],
) -> SessionInfo:
    """Изменение отображаемого имени пользователя любой сессии (Developer)."""
    return verification_service.rename_session_user(
        db=db,
        session_id=session_id,
        new_login=body.login,
        current_user_id=current_user.id,
    )
