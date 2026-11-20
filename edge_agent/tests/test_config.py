from pathlib import Path

import pytest

from edge_agent.config import EdgeConfig, load_config


def test_default_config(tmp_path: Path):
    """Проверка значений по умолчанию в конфигурации."""
    cfg = EdgeConfig()
    assert cfg.server_url == "http://127.0.0.1:8000"
    assert cfg.edge_api_key == ""
    assert cfg.hardware_mode == "mock"
    assert cfg.heartbeat_interval_sec == 30.0
    assert cfg.claim_poll_interval_sec == 2.0
    assert cfg.capture_interval_hours == 1
    assert cfg.debug_mode is False


def test_validate_api_key():
    """Проверка валидации API-ключа."""
    cfg = EdgeConfig(edge_api_key="")
    with pytest.raises(ValueError, match="EDGE_API_KEY не задан"):
        cfg.validate_api_key()

    cfg_valid = EdgeConfig(edge_api_key="valid-secret")
    cfg_valid.validate_api_key()  # Не должно вызывать исключений


def test_load_config_from_file(tmp_path: Path):
    """Проверка загрузки конфигурации из .env файла."""
    env_file = tmp_path / ".env.custom"
    env_file.write_text(
        "SERVER_URL=https://custom-server.local:8443/\n"
        "EDGE_API_KEY=custom-key-999\n"
        "HARDWARE_MODE=mock\n"
        "HEARTBEAT_INTERVAL_SEC=15\n"
        "CLAIM_POLL_INTERVAL_SEC=1.5\n"
        "CAPTURE_INTERVAL_HOURS=2\n"
        "DEBUG_MODE=true\n",
        encoding="utf-8",
    )

    cfg = load_config(env_path=env_file)
    assert cfg.server_url == "https://custom-server.local:8443"
    assert cfg.edge_api_key == "custom-key-999"
    assert cfg.hardware_mode == "mock"
    assert cfg.heartbeat_interval_sec == 15.0
    assert cfg.claim_poll_interval_sec == 1.5
    assert cfg.capture_interval_hours == 2
    assert cfg.debug_mode is True


def test_load_config_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Проверка переопределения значений через переменные окружения."""
    monkeypatch.setenv("SERVER_URL", "http://override-server:9000")
    monkeypatch.setenv("EDGE_API_KEY", "override-key")
    monkeypatch.setenv("HARDWARE_MODE", "rpi")
    monkeypatch.setenv("DEBUG_MODE", "1")

    cfg = load_config(env_path=tmp_path / "non_existent.env")
    assert cfg.server_url == "http://override-server:9000"
    assert cfg.edge_api_key == "override-key"
    assert cfg.hardware_mode == "rpi"
    assert cfg.debug_mode is True
