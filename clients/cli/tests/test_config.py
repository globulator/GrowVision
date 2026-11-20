from pathlib import Path

import pytest

from clients.cli.config import (
    load_config,
    load_or_create_external_id,
)


def test_session_external_id_generation_and_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Проверка генерации cli_<uuid4> и сохранения в файл сессии."""
    session_file = tmp_path / "sub_dir" / "test_session.json"
    monkeypatch.setenv("GROWVISION_SESSION_FILE", str(session_file))

    assert not session_file.exists()
    ext_id = load_or_create_external_id()
    assert ext_id.startswith("cli_")
    assert session_file.exists()

    # Повторный вызов должен вернуть тот же ID
    ext_id_2 = load_or_create_external_id()
    assert ext_id_2 == ext_id

    # При повреждении файла создается новый
    session_file.write_text("corrupted json{", encoding="utf-8")
    ext_id_new = load_or_create_external_id()
    assert ext_id_new.startswith("cli_")
    assert ext_id_new != ext_id


def test_config_validation_missing_env(monkeypatch: pytest.MonkeyPatch, capsys):
    """Ошибка конфигурации при отсутствии SERVER_URL или CLIENT_API_KEY."""
    monkeypatch.delenv("SERVER_URL", raising=False)
    monkeypatch.delenv("CLIENT_API_KEY", raising=False)
    monkeypatch.setattr("clients.cli.config.CONFIG_ENV_PATH", Path("/nonexistent/.env"))

    with pytest.raises(SystemExit) as exc:
        load_config()
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert (
        "Ошибка конфигурации CLI: не заданы SERVER_URL или CLIENT_API_KEY"
        in captured.err
    )


def test_config_validation_invalid_url(monkeypatch: pytest.MonkeyPatch, capsys):
    """Ошибка конфигурации при некорректном SERVER_URL."""
    monkeypatch.setenv("SERVER_URL", "ftp://invalid-url")
    monkeypatch.setenv("CLIENT_API_KEY", "valid-key")

    with pytest.raises(SystemExit) as exc:
        load_config()
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert (
        "Ошибка конфигурации CLI: некорректный адрес SERVER_URL («ftp://invalid-url»)"
        in captured.err
    )
