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


class TelegramLookupResponse(BaseModel):
    """Ответ на GET /api/auth/web/telegram-lookup."""

    found: bool
    telegram_id: int | None = None
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    recommended_name: str | None = None
    telegram_url: str | None = None


class WebRegisterCompleteRequest(BaseModel):
    """Тело запроса POST /api/auth/web/register/complete (и /api/auth/web/register)."""

    model_config = ConfigDict(extra="ignore")

    telegram: str | None = None
    telegram_id: int | None = None
    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    access_code: str
    password: str | None = None

    @field_validator("access_code")
    @classmethod
    def validate_access_code(cls, v: str, info: ValidationInfo) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"Поле {info.field_name} не может быть пустым")
        return v.strip()

    @model_validator(mode="after")
    def validate_full_name_fields(self) -> "WebRegisterCompleteRequest":
        fn = (self.first_name or "").strip()
        ln = (self.last_name or "").strip()
        full = (self.full_name or "").strip()
        if not full:
            parts = [p for p in (fn, ln) if p]
            full = " ".join(parts)
        if not full:
            raise ValueError("Имя и Фамилия не могут быть пустыми")
        if len(full) > 64:
            raise ValueError("Длина имени не должна превышать 64 символа")
        self.full_name = full
        self.first_name = fn or None
        self.last_name = ln or None
        if self.telegram is not None:
            tg = self.telegram.strip()
            self.telegram = tg or None
        return self


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
    telegram_url: str | None = None
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
    telegram_url: str | None = None
    authorized_at: str
    last_active_at: str


class UpdateLoginRequest(BaseModel):
    """Тело запроса на изменение отображаемого имени и/или Telegram пользователя."""

    model_config = ConfigDict(extra="ignore")

    login: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    telegram: str | None = None

    @model_validator(mode="after")
    def validate_profile_fields(self) -> "UpdateLoginRequest":
        raw_login = (self.login or self.full_name or "").strip()
        if not raw_login:
            fn = (self.first_name or "").strip()
            ln = (self.last_name or "").strip()
            raw_login = " ".join(p for p in (fn, ln) if p)
        if not raw_login:
            raise ValueError("Имя пользователя не может быть пустым")
        if len(raw_login) > 64:
            raise ValueError("Длина имени не должна превышать 64 символа")
        self.login = raw_login
        if self.telegram is not None:
            self.telegram = self.telegram.strip()
        return self


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
    telegram_url: str | None = None
    is_current: bool = False


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
]
