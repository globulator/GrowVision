import secrets
from typing import Annotated

from fastapi import Header, HTTPException, status

from server.config import get_settings
from server.db.database import get_db

__all__ = ["get_db", "verify_edge_key"]


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

    if not secrets.compare_digest(x_edge_key, expected_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный или отсутствующий X-Edge-Key",
        )
