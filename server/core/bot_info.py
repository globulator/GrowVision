import contextlib
import logging

import httpx

from server.config import get_settings

logger = logging.getLogger(__name__)

_cached_bot_username: str | None = None


def get_telegram_bot_username() -> str:
    """Возвращает username Telegram-бота с кешированием в памяти и безопасным fallback.

    1. Если в памяти уже есть закешированный username, возвращает его.
    2. Если задан TELEGRAM_BOT_TOKEN, запрашивает https://api.telegram.org/bot<TOKEN>/getMe.
    3. При успешном ответе кеширует результат.
    4. При отсутствии сети, ошибке или пустом ответе использует BOT_USERNAME из настроек или "GrowVisionBot".
    """
    global _cached_bot_username
    if _cached_bot_username:
        return _cached_bot_username

    settings = get_settings()
    fallback = settings.bot_username or "GrowVisionBot"

    token = getattr(settings, "telegram_bot_token", None)
    if not token:
        _cached_bot_username = fallback
        return _cached_bot_username

    with contextlib.suppress(Exception), httpx.Client(timeout=3.0) as client:
        resp = client.get(f"https://api.telegram.org/bot{token}/getMe")
        if resp.status_code == 200:
            data = resp.json()
            if data.get("ok") and data.get("result", {}).get("username"):
                _cached_bot_username = data["result"]["username"]
                return _cached_bot_username

    _cached_bot_username = fallback
    return _cached_bot_username


def set_cached_bot_username(username: str | None) -> None:
    """Устанавливает или сбрасывает закешированный username (для тестов)."""
    global _cached_bot_username
    _cached_bot_username = username
