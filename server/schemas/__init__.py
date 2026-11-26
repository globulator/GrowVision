"""Пакет Pydantic-схем сервера GrowVision."""

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
    "FailVerificationCodeRequest",
    "InviteCreateResponse",
    "LiveModeRequest",
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
