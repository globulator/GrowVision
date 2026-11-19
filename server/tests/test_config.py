from pathlib import Path

from server.config import Settings, load_settings


def test_load_settings_defaults():
    """Проверяет настройки по умолчанию."""
    settings = load_settings(env_path=None)
    assert settings.server_host == "0.0.0.0"
    assert settings.server_port == 8000
    assert settings.database_url == "sqlite:///./growvision.db"
    assert settings.video_max_fps == 10
    assert settings.video_min_duration_sec == 2


def test_load_settings_from_env_file(tmp_path: Path):
    """Проверяет загрузку настроек из кастомного .env файла."""
    env_file = tmp_path / "server.env"
    env_file.write_text(
        "SERVER_HOST=127.0.0.1\n"
        "SERVER_PORT=9000\n"
        "DATABASE_URL=sqlite:///./custom.db\n"
        "EDGE_API_KEY=test-edge-secret\n"
        "CLIENT_API_KEY=test-client-secret\n"
        "BIOLOGIST_PASSWORD=bio-pass\n"
        "DEVELOPER_PASSWORD=dev-pass\n"
        "VIDEO_MAX_FPS=15\n"
        "VIDEO_MIN_DURATION_SEC=5\n",
        encoding="utf-8",
    )
    settings = load_settings(env_path=env_file)
    assert settings.server_host == "127.0.0.1"
    assert settings.server_port == 9000
    assert settings.database_url == "sqlite:///./custom.db"
    assert settings.edge_api_key == "test-edge-secret"
    assert settings.client_api_key == "test-client-secret"
    assert settings.biologist_password == "bio-pass"
    assert settings.developer_password == "dev-pass"
    assert settings.video_max_fps == 15
    assert settings.video_min_duration_sec == 5


def test_settings_validation_errors():
    """Проверяет валидацию некорректных значений."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings.model_validate({"VIDEO_MAX_FPS": 0})

    with pytest.raises(ValidationError):
        Settings.model_validate({"VIDEO_MIN_DURATION_SEC": 0})
