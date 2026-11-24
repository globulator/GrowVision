import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationInfo,
    field_validator,
    model_validator,
)
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
from server.core.rate_limit import login_limiter
from server.db.models import InviteCode, TelegramContact, User, WebVerificationCode

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    """Тело запроса POST /api/auth/login."""

    model_config = ConfigDict(extra="ignore")

    client_type: Literal["telegram", "cli", "web"]
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


class TelegramContactSync(BaseModel):
    """Синхронизация связки username -> telegram_id от Telegram-бота."""

    model_config = ConfigDict(extra="ignore")

    telegram_id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None


class PendingCodeItem(BaseModel):
    """Элемент очереди кодов подтверждения для доставки ботом."""

    id: int
    telegram_id: int
    code: str


class WebRegisterStartRequest(BaseModel):
    """Тело запроса POST /api/auth/web/register/start."""

    model_config = ConfigDict(extra="ignore")

    telegram: str

    @field_validator("telegram")
    @classmethod
    def validate_telegram(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Поле telegram не может быть пустым")
        return v.strip()


class WebRegisterVerifyRequest(BaseModel):
    """Тело запроса POST /api/auth/web/register/verify."""

    model_config = ConfigDict(extra="ignore")

    telegram_id: int
    code: str

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Код подтверждения не может быть пустым")
        return v.strip()


class WebRegisterCompleteRequest(BaseModel):
    """Тело запроса POST /api/auth/web/register/complete."""

    model_config = ConfigDict(extra="ignore")

    telegram_id: int
    verification_code: str
    full_name: str
    access_code: str
    password: str | None = None

    @field_validator("full_name", "access_code", "verification_code")
    @classmethod
    def validate_non_empty(cls, v: str, info: ValidationInfo) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"Поле {info.field_name} не может быть пустым")
        return v.strip()


class WebLoginRequest(BaseModel):
    """Тело запроса POST /api/auth/web/login."""

    model_config = ConfigDict(extra="ignore")

    telegram: str
    code: str | None = None
    password: str | None = None

    @field_validator("telegram")
    @classmethod
    def validate_telegram(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Поле telegram не может быть пустым")
        return v.strip()

    @model_validator(mode="after")
    def validate_code(self) -> "WebLoginRequest":
        c = (self.code or self.password or "").strip()
        if not c:
            raise ValueError("Поле code не может быть пустым")
        self.code = c
        return self


class WebLoginResponse(BaseModel):
    """Ответ на веб-вход."""

    role: str
    login: str
    status: str = "ok"


class WebMeResponse(BaseModel):
    """Ответ на GET /api/auth/web/me."""

    authenticated: bool
    role: str
    login: str | None = None
    external_id: str | None = None
    bot_username: str | None = None


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
    norm_username = (
        body.username.lstrip("@").strip().lower()
        if (body.username and body.username.strip())
        else None
    )

    contact = (
        db.query(TelegramContact)
        .filter(TelegramContact.telegram_id == body.telegram_id)
        .first()
    )
    now = datetime.now(UTC)
    if contact:
        contact.username = norm_username
        contact.first_name = body.first_name
        contact.last_name = body.last_name
        contact.updated_at = now
    else:
        # Если username уже привязан к другой записи, освобождаем
        if norm_username:
            existing_user = (
                db.query(TelegramContact)
                .filter(TelegramContact.username == norm_username)
                .first()
            )
            if existing_user:
                existing_user.username = None

        contact = TelegramContact(
            telegram_id=body.telegram_id,
            username=norm_username,
            first_name=body.first_name,
            last_name=body.last_name,
            updated_at=now,
        )
        db.add(contact)

    db.commit()
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
    now = datetime.now(UTC)
    codes = (
        db.query(WebVerificationCode)
        .filter(
            WebVerificationCode.is_delivered.is_(False),
            WebVerificationCode.is_verified.is_(False),
            WebVerificationCode.expires_at > now,
        )
        .order_by(WebVerificationCode.created_at.asc())
        .all()
    )
    return [
        PendingCodeItem(id=c.id, telegram_id=c.telegram_id, code=c.code) for c in codes
    ]


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
    rec = (
        db.query(WebVerificationCode).filter(WebVerificationCode.id == code_id).first()
    )
    if rec:
        rec.is_delivered = True
        rec.delivered_at = datetime.now(UTC)
        db.commit()
    return {"ok": True}


# ================== Веб-регистрация и авторизация (Web SPA) ==================


def resolve_telegram_contact(
    telegram_input: str, db: Session
) -> tuple[int, TelegramContact | None]:
    """Резолвит ввод пользователя (@username или числовой ID) в числовой telegram_id и объект TelegramContact."""
    val = telegram_input.strip()
    if val.isdigit() or (val.startswith("-") and val[1:].isdigit()):
        tid = int(val)
        contact = (
            db.query(TelegramContact).filter(TelegramContact.telegram_id == tid).first()
        )
        return tid, contact

    norm_username = val.lstrip("@").strip().lower()
    contact = (
        db.query(TelegramContact)
        .filter(TelegramContact.username == norm_username)
        .first()
    )
    if not contact:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Пользователь с таким Telegram не найден. Пожалуйста, откройте Telegram-бота GrowVision, нажмите /start и повторите попытку.",
        )
    return contact.telegram_id, contact


def resolve_telegram_id(telegram_input: str, db: Session) -> int:
    """Резолвит ввод пользователя (@username или числовой ID) в числовой telegram_id."""
    tid, _ = resolve_telegram_contact(telegram_input, db)
    return tid


@router.post("/web/register/start")
def auth_web_register_start(
    body: WebRegisterStartRequest,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Шаг 1 регистрации: валидация Telegram пользователя и отправка 6-значного кода."""
    telegram_id, contact = resolve_telegram_contact(body.telegram, db)

    recommended_name: str | None = None
    if contact:
        parts = [p for p in (contact.first_name, contact.last_name) if p and p.strip()]
        if parts:
            recommended_name = " ".join(parts).strip()
        elif contact.username:
            recommended_name = f"@{contact.username}"

    now = datetime.now(UTC)
    expires = now + timedelta(minutes=10)
    code = f"{secrets.randbelow(1_000_000):06d}"

    ver_rec = WebVerificationCode(
        telegram_id=telegram_id,
        code=code,
        created_at=now,
        expires_at=expires,
        is_verified=False,
        is_delivered=False,
    )
    db.add(ver_rec)
    db.commit()

    return {
        "status": "code_sent",
        "telegram_id": telegram_id,
        "recommended_name": recommended_name,
        "expires_in": 600,
    }


@router.post("/web/register/verify")
def auth_web_register_verify(
    body: WebRegisterVerifyRequest,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Шаг 2 регистрации: проверка введённого 6-значного кода."""
    now = datetime.now(UTC)
    rec = (
        db.query(WebVerificationCode)
        .filter(
            WebVerificationCode.telegram_id == body.telegram_id,
            WebVerificationCode.code == body.code.strip(),
            WebVerificationCode.expires_at > now,
            WebVerificationCode.is_verified.is_(False),
        )
        .order_by(WebVerificationCode.created_at.desc())
        .first()
    )
    if not rec:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Неверный или просроченный код подтверждения",
        )

    rec.is_verified = True
    rec.verified_at = now
    db.commit()

    return {"status": "verified", "telegram_id": body.telegram_id}


@router.post("/web/register/complete", response_model=WebLoginResponse)
def auth_web_register_complete(
    body: WebRegisterCompleteRequest,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> WebLoginResponse:
    """Шаг 3 регистрации: профиль, инвайт/пароль разработчика, личный пароль."""
    now = datetime.now(UTC)
    ver = (
        db.query(WebVerificationCode)
        .filter(
            WebVerificationCode.telegram_id == body.telegram_id,
            WebVerificationCode.code == body.verification_code.strip(),
            WebVerificationCode.is_verified.is_(True),
            WebVerificationCode.expires_at > now,
        )
        .first()
    )
    if not ver:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Код подтверждения не верифицирован или истёк",
        )

    settings = get_settings()
    access_code = body.access_code.strip()
    role: str | None = None
    matched_invite: InviteCode | None = None

    if settings.developer_password and secrets.compare_digest(
        access_code.encode("utf-8"), settings.developer_password.encode("utf-8")
    ):
        role = "developer"
    else:
        matched_invite = (
            db.query(InviteCode)
            .filter(
                InviteCode.code == access_code.upper(),
                InviteCode.used_at.is_(None),
                InviteCode.is_revoked.is_(False),
                InviteCode.expires_at > now,
            )
            .first()
        )
        if matched_invite:
            role = matched_invite.role or "biologist"
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Неверный или просроченный код приглашения / пароль доступа",
            )

    pwd = body.password.strip()
    if len(pwd) < 4:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Пароль должен содержать не менее 4 символов",
        )

    full_name = body.full_name.strip()
    if not full_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Имя и Фамилия не могут быть пустыми",
        )

    ext_id = str(body.telegram_id)

    user = (
        db.query(User)
        .filter(
            User.client_type == "web",
            User.external_id == ext_id,
        )
        .first()
    )
    if not user:
        user = User(
            client_type="web",
            external_id=ext_id,
            login=full_name,
            role=role,
            is_active=True,
            authorized_at=now,
            last_active_at=now,
            logged_out_at=None,
        )
        db.add(user)
        db.flush()
    else:
        user.login = full_name
        user.role = role
        user.is_active = True
        user.authorized_at = now
        user.last_active_at = now
        user.logged_out_at = None

    if matched_invite:
        matched_invite.used_at = now
        matched_invite.used_by_user_id = user.id

    db.commit()

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

    # 1. Резолв пользователя Telegram
    telegram_input = body.telegram.strip()
    contact: TelegramContact | None = None
    if telegram_input.isdigit() or (
        telegram_input.startswith("-") and telegram_input[1:].isdigit()
    ):
        telegram_id = int(telegram_input)
        contact = (
            db.query(TelegramContact)
            .filter(TelegramContact.telegram_id == telegram_id)
            .first()
        )
    else:
        norm_username = telegram_input.lstrip("@").strip().lower()
        contact = (
            db.query(TelegramContact)
            .filter(TelegramContact.username == norm_username)
            .first()
        )
        if not contact:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Пользователь с таким Telegram не найден. Пожалуйста, откройте Telegram-бота GrowVision, нажмите /start и повторите попытку.",
            )
        telegram_id = contact.telegram_id

    # 2. Проверка кода доступа (DEVELOPER_PASSWORD или активный InviteCode биолога)
    access_code = (body.code or body.password or "").strip()
    settings = get_settings()
    now = datetime.now(UTC)
    role: str | None = None
    matched_invite: InviteCode | None = None

    if settings.developer_password and secrets.compare_digest(
        access_code.encode("utf-8"), settings.developer_password.encode("utf-8")
    ):
        role = "developer"
    else:
        matched_invite = (
            db.query(InviteCode)
            .filter(
                InviteCode.code == access_code.upper(),
                InviteCode.used_at.is_(None),
                InviteCode.is_revoked.is_(False),
                InviteCode.expires_at > now,
            )
            .first()
        )
        if matched_invite:
            role = matched_invite.role or "biologist"
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Неверный или просроченный код приглашения / пароль разработчика",
            )

    # 3. Синхронизация имени пользователя из TelegramContact
    ext_id = str(telegram_id)
    full_name = ""
    if contact:
        parts = [p for p in (contact.first_name, contact.last_name) if p and p.strip()]
        if parts:
            full_name = " ".join(parts).strip()
        elif contact.username:
            full_name = f"@{contact.username}"

    if not full_name:
        user_existing = (
            db.query(User)
            .filter(
                User.client_type.in_(["web", "telegram"]), User.external_id == ext_id
            )
            .first()
        )
        if user_existing and user_existing.login:
            full_name = user_existing.login
        else:
            full_name = f"User {telegram_id}"

    # 4. Проверка существования сессии пользователя в таблице users
    user = (
        db.query(User)
        .filter(
            User.client_type == "web",
            User.external_id == ext_id,
        )
        .first()
    )
    if not user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="telegram_verification_required",
        )

    if not user.login:
        user.login = full_name
    user.role = role
    user.is_active = True
    user.logged_out_at = None
    user.last_active_at = now

    if matched_invite:
        matched_invite.used_at = now
        matched_invite.used_by_user_id = user.id

    db.commit()

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
    settings = get_settings()
    bot_username = settings.bot_username or "GrowVisionBot"
    if gv_session:
        user = verify_session_cookie(gv_session, db)
        if user:
            return WebMeResponse(
                authenticated=True,
                role=user.role,
                login=user.login,
                external_id=user.external_id,
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
