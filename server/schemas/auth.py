"""Pydantic-схемы запросов и ответов для API авторизации и сессий."""

from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationInfo,
    field_validator,
    model_validator,
)


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


class FailVerificationCodeRequest(BaseModel):
    """Тело запроса POST /api/auth/web/pending-codes/{code_id}/fail."""

    model_config = ConfigDict(extra="ignore")

    error: str | None = None


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
    id: int | None = None
    login: str | None = None
    client_type: str | None = None
    external_id: str | None = None
    telegram_username: str | None = None
    authorized_at: str | None = None
    last_active_at: str | None = None
    bot_username: str | None = None


class MeResponse(BaseModel):
    """Ответ на GET /api/auth/me."""

    id: int
    login: str
    role: str
    client_type: str
    external_id: str
    telegram_username: str | None = None
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
    telegram_username: str | None = None
    is_current: bool = False


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
]
