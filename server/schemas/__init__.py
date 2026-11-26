"""Пакет Pydantic-схем сервера GrowVision."""

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
from server.schemas.cameras import (
    AutoColorCalibrateRequest,
    CameraBindRequest,
    CameraControlsPatch,
    CameraControlsSaveRequest,
    LiveModeRequest,
)

__all__ = [
    "AutoColorCalibrateRequest",
    "CameraBindRequest",
    "CameraControlsPatch",
    "CameraControlsSaveRequest",
    "InviteCreateResponse",
    "LiveModeRequest",
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
