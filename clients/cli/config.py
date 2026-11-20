import contextlib
import json
import os
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

CONFIG_ENV_PATH = Path(__file__).resolve().parent / ".env"
SESSION_FILE_PATH = Path.home() / ".growvision_cli.json"


def get_session_file_path() -> Path:
    """Возвращает путь к файлу сессии (может быть переопределен в тестах через env)."""
    override = os.environ.get("GROWVISION_SESSION_FILE")
    if override:
        return Path(override)
    return SESSION_FILE_PATH


def load_or_create_external_id() -> str:
    """Загружает сохраненный external_id или генерирует новый."""
    session_file = get_session_file_path()
    if session_file.exists():
        with contextlib.suppress(json.JSONDecodeError, OSError, ValueError):
            data = json.loads(session_file.read_text(encoding="utf-8"))
            ext_id = data.get("external_id")
            if ext_id and isinstance(ext_id, str) and ext_id.startswith("cli_"):
                return ext_id

    # Создаем новый external_id
    new_ext_id = f"cli_{uuid.uuid4()}"
    with contextlib.suppress(OSError):
        session_file.parent.mkdir(parents=True, exist_ok=True)
        session_file.write_text(
            json.dumps({"external_id": new_ext_id}, indent=2),
            encoding="utf-8",
        )
    return new_ext_id


class Config:
    """Конфигурация CLI-клиента."""

    def __init__(
        self,
        server_url: str,
        client_api_key: str,
        cli_debug: bool = False,
        external_id: str | None = None,
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.client_api_key = client_api_key
        self.cli_debug = cli_debug
        self.external_id = external_id or load_or_create_external_id()


def load_config(env_path: Path | None = None) -> Config:
    """Загружает и валидирует конфигурацию CLI."""
    target_env = env_path or CONFIG_ENV_PATH
    if target_env.exists():
        load_dotenv(target_env)

    server_url = os.environ.get("SERVER_URL")
    client_api_key = os.environ.get("CLIENT_API_KEY")
    cli_debug_raw = os.environ.get("CLI_DEBUG", "0").lower()
    cli_debug = cli_debug_raw in ("1", "true", "yes")

    if not server_url or not client_api_key:
        sys.stderr.write(
            "Ошибка конфигурации CLI: не заданы SERVER_URL или CLIENT_API_KEY. "
            "Скопируйте clients/cli/.env.example в clients/cli/.env и заполните параметры.\n"
        )
        sys.exit(1)

    parsed = urlparse(server_url)
    if not parsed.scheme or parsed.scheme not in ("http", "https") or not parsed.netloc:
        sys.stderr.write(
            f"Ошибка конфигурации CLI: некорректный адрес SERVER_URL («{server_url}»). "
            "Укажите адрес вида http://127.0.0.1:8000.\n"
        )
        sys.exit(1)

    external_id = load_or_create_external_id()
    return Config(
        server_url=server_url,
        client_api_key=client_api_key,
        cli_debug=cli_debug,
        external_id=external_id,
    )
