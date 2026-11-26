import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

logger = logging.getLogger(__name__)

DEFAULT_ENV_FILE = Path(__file__).resolve().parent / ".env"


def mask_proxy_url(proxy_url: str | None) -> str | None:
    """Маскирует учетные данные в URL прокси до вида protocol://***:***@host:port."""
    if not proxy_url:
        return proxy_url
    res = re.sub(r"://([^:/@]+):([^/@]+)@", r"://***:***@", proxy_url)
    res = re.sub(r"://([^:/@]+)@", r"://***@", res)
    return res


def normalize_proxy_url(proxy_url: str | None) -> str | None:
    """Нормализует URL прокси: преобразует socks:// в socks5://, сохраняя остальные схемы."""
    if not proxy_url:
        return None
    cleaned = proxy_url.strip()
    if not cleaned:
        return None
    if cleaned.lower().startswith("socks://"):
        return "socks5://" + cleaned[len("socks://") :]
    return cleaned


@dataclass(frozen=True)
class BotConfig:
    """Конфигурация Telegram-бота GrowVision."""

    bot_token: str
    server_url: str
    client_api_key: str
    web_public_url: str | None = None
    telegram_proxy: str | None = None
    debug_log: bool = False

    def __repr__(self) -> str:
        """Безопасное строковое представление без раскрытия секретов."""
        masked_token = (
            f"{self.bot_token[:4]}...{self.bot_token[-4:]}"
            if len(self.bot_token) > 8
            else "***"
        )
        masked_key = (
            f"{self.client_api_key[:2]}...{self.client_api_key[-2:]}"
            if len(self.client_api_key) > 4
            else "***"
        )
        masked_proxy = mask_proxy_url(self.telegram_proxy)

        return (
            f"BotConfig(server_url='{self.server_url}', "
            f"bot_token='{masked_token}', client_api_key='{masked_key}', "
            f"web_public_url='{self.web_public_url}', "
            f"telegram_proxy='{masked_proxy}', debug_log={self.debug_log})"
        )


def load_config(env_path: Path | str | None = None) -> BotConfig:
    """Загружает и валидирует конфигурацию бота из файла .env и переменных окружения.

    Выбрасывает ValueError со списком всех недостающих обязательных переменных.
    """
    env_file = Path(env_path) if env_path else DEFAULT_ENV_FILE

    file_vars: dict[str, str] = {}
    if env_file.is_file():
        file_vars = {
            k: v
            for k, v in dotenv_values(dotenv_path=env_file).items()
            if v is not None
        }

    def get_val(key: str) -> str:
        if key in os.environ and os.environ[key].strip() != "":
            return os.environ[key].strip()
        if key in file_vars and file_vars[key].strip() != "":
            return file_vars[key].strip()
        return ""

    bot_token = get_val("TELEGRAM_BOT_TOKEN")
    server_url = get_val("SERVER_URL")
    client_api_key = get_val("CLIENT_API_KEY")
    web_public_url_raw = get_val("WEB_PUBLIC_URL")
    telegram_proxy_raw = get_val("TELEGRAM_PROXY")
    debug_log_raw = get_val("BOT_DEBUG_LOG").lower() in ("true", "1", "yes")

    missing = []
    if not bot_token:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not server_url:
        missing.append("SERVER_URL")
    if not client_api_key:
        missing.append("CLIENT_API_KEY")

    if missing:
        missing_str = ", ".join(missing)
        err_msg = (
            f"Не заданы обязательные переменные конфигурации бота: {missing_str}. "
            f"Укажите их в файле .env или переменных окружения."
        )
        logger.error(err_msg)
        raise ValueError(err_msg)

    # Нормализуем URL (без слэша на конце)
    server_url_clean = server_url.rstrip("/")
    web_public_clean = web_public_url_raw.rstrip("/") if web_public_url_raw else None
    telegram_proxy_clean = normalize_proxy_url(telegram_proxy_raw)

    return BotConfig(
        bot_token=bot_token,
        server_url=server_url_clean,
        client_api_key=client_api_key,
        web_public_url=web_public_clean,
        telegram_proxy=telegram_proxy_clean,
        debug_log=debug_log_raw,
    )


def validate_and_get_config(env_path: Path | str | None = None) -> BotConfig:
    """Загружает конфигурацию; при ошибке выводит понятное сообщение и завершает процесс."""
    try:
        return load_config(env_path)
    except ValueError as exc:
        print(f"ОШИБКА КОНФИГУРАЦИИ: {exc}", file=sys.stderr)
        sys.exit(1)
