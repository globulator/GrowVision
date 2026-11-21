from pathlib import Path

import pytest

from clients.telegram_bot.config import BotConfig, load_config


def test_load_valid_config(tmp_path: Path):
    """Проверка успешной загрузки полной конфигурации бота."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TELEGRAM_BOT_TOKEN=123456789:ABCdefGhI\n"
        "SERVER_URL=http://localhost:8000/\n"
        "CLIENT_API_KEY=my-super-secret-key\n"
        "WEB_PUBLIC_URL=http://192.168.1.50:8000/\n",
        encoding="utf-8",
    )

    cfg = load_config(env_file)
    assert cfg.bot_token == "123456789:ABCdefGhI"
    assert cfg.server_url == "http://localhost:8000"
    assert cfg.client_api_key == "my-super-secret-key"
    assert cfg.web_public_url == "http://192.168.1.50:8000"


def test_missing_required_variables_raises_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """При отсутствии обязательных переменных выбрасывается ValueError с их перечислением."""
    # Очищаем переменные окружения
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SERVER_URL", raising=False)
    monkeypatch.delenv("CLIENT_API_KEY", raising=False)

    empty_env = tmp_path / "empty.env"
    empty_env.write_text("", encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        load_config(empty_env)

    err_msg = str(excinfo.value)
    assert "TELEGRAM_BOT_TOKEN" in err_msg
    assert "SERVER_URL" in err_msg
    assert "CLIENT_API_KEY" in err_msg


def test_safe_repr_masks_secrets():
    """__repr__ скрывает токен бота и API-ключ."""
    cfg = BotConfig(
        bot_token="123456789:VerySecretTelegramBotToken987",
        server_url="http://127.0.0.1:8000",
        client_api_key="SecretClientKey12345",
    )
    repr_str = repr(cfg)
    assert "VerySecretTelegramBotToken987" not in repr_str
    assert "SecretClientKey12345" not in repr_str
    assert "1234...n987" in repr_str or "***" in repr_str
    assert "Se...45" in repr_str or "***" in repr_str
