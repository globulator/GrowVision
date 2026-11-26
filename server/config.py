import os
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field


class Settings(BaseModel):
    """Конфигурация сервера GrowVision."""

    model_config = ConfigDict(extra="ignore")

    server_host: str = Field(default="0.0.0.0", alias="SERVER_HOST")
    server_port: int = Field(default=8000, alias="SERVER_PORT")
    database_url: str = Field(default="sqlite:///./growvision.db", alias="DATABASE_URL")
    edge_api_key: str = Field(default="", alias="EDGE_API_KEY")
    client_api_key: str = Field(default="", alias="CLIENT_API_KEY")
    biologist_password: str = Field(default="", alias="BIOLOGIST_PASSWORD")
    developer_password: str = Field(default="", alias="DEVELOPER_PASSWORD")
    video_max_fps: int = Field(default=10, ge=1, alias="VIDEO_MAX_FPS")
    video_min_duration_sec: int = Field(default=2, ge=1, alias="VIDEO_MIN_DURATION_SEC")
    repo_url: str | None = Field(default=None, alias="REPO_URL")
    edge_server_url: str | None = Field(default=None, alias="EDGE_SERVER_URL")
    mock_mode: bool = Field(default=False, alias="MOCK_MODE")
    bot_username: str | None = Field(default=None, alias="BOT_USERNAME")
    telegram_bot_token: str | None = Field(default=None, alias="TELEGRAM_BOT_TOKEN")
    esp_adapter_enabled: bool = Field(default=False, alias="ESP_ADAPTER_ENABLED")
    esp_adapter_host: str = Field(default="0.0.0.0", alias="ESP_ADAPTER_HOST")
    esp_adapter_port: int = Field(
        default=8080, ge=1, le=65535, alias="ESP_ADAPTER_PORT"
    )


def load_settings(env_path: Path | str | None = None) -> Settings:
    """Загружает настройки из указанного .env файла и переменных окружения."""
    data: dict[str, str | None] = {}

    if env_path is None:
        default_env = Path(__file__).resolve().parent / ".env"
        if default_env.is_file():
            env_path = default_env

    if env_path is not None:
        p = Path(env_path)
        if p.is_file():
            data.update(dotenv_values(p))

    # Переменные окружения процесса имеют наивысший приоритет
    for field_name in Settings.model_fields.values():
        alias = field_name.alias
        if alias and alias in os.environ:
            data[alias] = os.environ[alias]

    settings = Settings.model_validate(data)
    if settings.mock_mode and settings.database_url == "sqlite:///./growvision.db":
        settings.database_url = "sqlite:///./growvision_mock.db"
    return settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Возвращает кэшированный экземпляр настроек сервера."""
    return load_settings()
