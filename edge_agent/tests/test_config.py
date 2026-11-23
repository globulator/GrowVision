from pathlib import Path

import pytest

from edge_agent.config import REPO_ROOT, EdgeConfig, load_config, resolve_path


def test_default_config():
    """Проверка значений по умолчанию в конфигурации."""
    cfg = EdgeConfig()
    assert cfg.server_url == "http://127.0.0.1:8000"
    assert cfg.edge_api_key == ""
    assert cfg.hardware_mode == "mock"
    assert cfg.heartbeat_interval_sec == 30.0
    assert cfg.command_poll_interval_sec == 2.0
    assert cfg.claim_poll_interval_sec == 2.0
    assert cfg.capture_interval_hours == 1
    assert cfg.debug_mode is False
    assert cfg.relay_active_high is True
    assert cfg.camera_warmup_frames == 5
    assert cfg.relay_switch_delay_ms == 200
    assert cfg.upload_timeout_sec == 60.0


def test_validate_api_key():
    """Проверка валидации API-ключа."""
    cfg = EdgeConfig(edge_api_key="")
    with pytest.raises(ValueError, match="EDGE_API_KEY не задан"):
        cfg.validate_api_key()

    cfg_valid = EdgeConfig(edge_api_key="valid-secret")
    cfg_valid.validate_api_key()


def test_invalid_hardware_mode_raises_error(tmp_path: Path):
    """Проверка падения при некорректном значении HARDWARE_MODE в файле."""
    bad_env = tmp_path / "bad.env"
    bad_env.write_text("HARDWARE_MODE=unsupported_mode\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Недопустимое значение HARDWARE_MODE"):
        load_config(env_path=bad_env)


def test_load_config_invalid_hardware_mode_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Проверка падения при недопустимом HARDWARE_MODE в env."""
    monkeypatch.setenv("HARDWARE_MODE", "arduino_sim")
    with pytest.raises(ValueError, match="Недопустимое значение HARDWARE_MODE"):
        load_config(env_path=tmp_path / "test.env")


def test_load_config_from_file(tmp_path: Path):
    """Проверка загрузки конфигурации из .env файла."""
    env_file = tmp_path / ".env.custom"
    env_file.write_text(
        "SERVER_URL=https://custom-server.local:8443/\n"
        "EDGE_API_KEY=custom-key-999\n"
        "HARDWARE_MODE=mock\n"
        "MODEL_PATH=/opt/models/custom_seg.onnx\n"
        "HEARTBEAT_INTERVAL_SEC=15\n"
        "COMMAND_POLL_INTERVAL_SEC=1.5\n"
        "RELAY_SWITCH_DELAY_MS=350\n"
        "CAMERA_WARMUP_FRAMES=7\n"
        "RELAY_ACTIVE_HIGH=false\n"
        "CAPTURE_INTERVAL_HOURS=2\n"
        "DEBUG_MODE=true\n",
        encoding="utf-8",
    )

    cfg = load_config(env_path=env_file)
    assert cfg.server_url == "https://custom-server.local:8443"
    assert cfg.edge_api_key == "custom-key-999"
    assert cfg.hardware_mode == "mock"
    assert cfg.model_path == Path("/opt/models/custom_seg.onnx")
    assert cfg.model_name == "custom_seg.onnx"
    assert cfg.heartbeat_interval_sec == 15.0
    assert cfg.command_poll_interval_sec == 1.5
    assert cfg.relay_switch_delay_ms == 350
    assert cfg.camera_warmup_frames == 7
    assert cfg.relay_active_high is False
    assert cfg.capture_interval_hours == 2
    assert cfg.debug_mode is True


def test_load_config_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Проверка переопределения значений через переменные окружения."""
    monkeypatch.setenv("SERVER_URL", "http://override-server:9000")
    monkeypatch.setenv("EDGE_API_KEY", "override-key")
    monkeypatch.setenv("HARDWARE_MODE", "mock")
    monkeypatch.setenv("COMMAND_POLL_INTERVAL_SEC", "3.5")
    monkeypatch.setenv("DEBUG_MODE", "1")

    cfg = load_config(env_path=tmp_path / "non_existent.env")
    assert cfg.server_url == "http://override-server:9000"
    assert cfg.edge_api_key == "override-key"
    assert cfg.hardware_mode == "mock"
    assert cfg.command_poll_interval_sec == 3.5
    assert cfg.debug_mode is True


def test_relative_paths_resolved_from_repo_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Проверка: относительные MODEL_PATH и OFFLINE_QUEUE_DIR резолвятся от REPO_ROOT."""
    # 1. Прямой вызов resolve_path
    rel_model = "edge_agent/models/lettuce_seg_v1.onnx"
    resolved_model = resolve_path(rel_model)
    assert resolved_model.is_absolute()
    assert resolved_model == (REPO_ROOT / rel_model).resolve()

    # 2. Загрузка через переменные окружения
    monkeypatch.setenv("MODEL_PATH", "edge_agent/models/lettuce_seg_v1.onnx")
    monkeypatch.setenv("OFFLINE_QUEUE_DIR", "edge_agent/offline_queue")

    cfg = load_config(env_path=tmp_path / "empty.env")
    assert cfg.model_path.is_absolute()
    assert cfg.model_path == REPO_ROOT / "edge_agent" / "models" / "lettuce_seg_v1.onnx"
    assert cfg.offline_queue_dir.is_absolute()
    assert cfg.offline_queue_dir == REPO_ROOT / "edge_agent" / "offline_queue"


def test_hardware_mode_auto(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Проверка автоопределения HARDWARE_MODE=auto (на ПК fallback в mock)."""
    monkeypatch.delenv("HARDWARE_MODE", raising=False)
    cfg = load_config(env_path=tmp_path / "empty.env")
    assert cfg.hardware_mode in ("rpi", "mock")
