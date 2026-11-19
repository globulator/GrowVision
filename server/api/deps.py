import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from server.config import get_settings
from server.db.database import get_db
from server.db.models import User

__all__ = [
    "ClientUser",
    "format_utc_z",
    "get_current_user",
    "get_current_user_optional",
    "get_db",
    "require_role",
    "verify_client_key",
    "verify_edge_key",
]


def format_utc_z(dt: datetime | None) -> str | None:
    """Форматирует дату/время в ISO 8601 UTC с суффиксом Z."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    else:
        dt = dt.astimezone(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def verify_edge_key(
    x_edge_key: Annotated[str | None, Header(alias="X-Edge-Key")] = None,
) -> None:
    """Проверяет заголовок X-Edge-Key для запросов Edge API."""
    expected_key = get_settings().edge_api_key
    if not x_edge_key or not expected_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный или отсутствующий X-Edge-Key",
        )

    if not secrets.compare_digest(
        x_edge_key.encode("utf-8"), expected_key.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный или отсутствующий X-Edge-Key",
        )


def verify_client_key(
    x_client_key: Annotated[str | None, Header(alias="X-Client-Key")] = None,
) -> None:
    """Проверяет обязательный заголовок X-Client-Key."""
    expected_key = get_settings().client_api_key
    if not x_client_key or not expected_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный или отсутствующий X-Client-Key",
        )

    if not secrets.compare_digest(
        x_client_key.encode("utf-8"), expected_key.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный X-Client-Key",
        )


class ClientUser:
    """Представление пользователя для публичных эндпоинтов (включая роль Гость)."""

    def __init__(self, role: str, user: User | None = None) -> None:
        self.role = role
        self.user = user

    @property
    def is_guest(self) -> bool:
        return self.role == "guest"


def get_current_user_optional(
    db: Annotated[Session, Depends(get_db)],
    x_client_key: Annotated[str | None, Header(alias="X-Client-Key")] = None,
    x_client_type: Annotated[str | None, Header(alias="X-Client-Type")] = None,
    x_external_id: Annotated[str | None, Header(alias="X-External-Id")] = None,
) -> ClientUser:
    """Определяет пользователя для публичных эндпоинтов.

    Без заголовков или без сессии -> роль Гость. Неверный X-Client-Key -> 401.
    """
    if x_client_key is None:
        return ClientUser(role="guest", user=None)

    expected_key = get_settings().client_api_key
    if not expected_key or not secrets.compare_digest(
        x_client_key.encode("utf-8"), expected_key.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный X-Client-Key",
        )

    if not x_client_type or not x_external_id:
        return ClientUser(role="guest", user=None)

    user = (
        db.query(User)
        .filter(
            User.client_type == x_client_type,
            User.external_id == x_external_id,
        )
        .first()
    )
    if not user:
        return ClientUser(role="guest", user=None)

    user.last_active_at = datetime.now(UTC)
    db.commit()
    return ClientUser(role=user.role, user=user)


def get_current_user(
    db: Annotated[Session, Depends(get_db)],
    x_client_key: Annotated[str | None, Header(alias="X-Client-Key")] = None,
    x_client_type: Annotated[str | None, Header(alias="X-Client-Type")] = None,
    x_external_id: Annotated[str | None, Header(alias="X-External-Id")] = None,
) -> User:
    """Определяет пользователя для защищенных эндпоинтов (требует сессию)."""
    expected_key = get_settings().client_api_key
    if not x_client_key or not expected_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Отсутствует заголовок X-Client-Key",
        )

    if not secrets.compare_digest(
        x_client_key.encode("utf-8"), expected_key.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный X-Client-Key",
        )

    if not x_client_type or not x_external_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Отсутствуют заголовки сессии клиента",
        )

    user = (
        db.query(User)
        .filter(
            User.client_type == x_client_type,
            User.external_id == x_external_id,
        )
        .first()
    )
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Сессия не найдена или не авторизована",
        )

    user.last_active_at = datetime.now(UTC)
    db.commit()
    return user


def require_role(*allowed_roles: str) -> Callable[..., User]:
    """Зависимость для проверки прав роли."""

    def role_checker(user: Annotated[User, Depends(get_current_user)]) -> User:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Недостаточно прав роли",
            )
        return user

    return role_checker
