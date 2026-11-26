from pathlib import Path

import pytest

from clients.telegram_bot.config import BotConfig, load_config, normalize_proxy_url


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
    assert cfg.telegram_proxy is None
    assert cfg.debug_log is False


def test_load_config_with_proxy_and_debug_log(tmp_path: Path):
    """Проверка загрузки TELEGRAM_PROXY и BOT_DEBUG_LOG."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TELEGRAM_BOT_TOKEN=123456789:ABCdefGhI\n"
        "SERVER_URL=http://localhost:8000/\n"
        "CLIENT_API_KEY=my-super-secret-key\n"
        "TELEGRAM_PROXY=socks5://proxyuser:proxypass@127.0.0.1:1080\n"
        "BOT_DEBUG_LOG=true\n",
        encoding="utf-8",
    )

    cfg = load_config(env_file)
    assert cfg.telegram_proxy == "socks5://proxyuser:proxypass@127.0.0.1:1080"
    assert cfg.debug_log is True

    repr_str = repr(cfg)
    assert "proxyuser" not in repr_str
    assert "proxypass" not in repr_str
    assert "socks5://***:***@127.0.0.1:1080" in repr_str


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


@pytest.mark.parametrize(
    ("raw_proxy", "expected_proxy"),
    [
        ("socks://127.0.0.1:1080", "socks5://127.0.0.1:1080"),
        ("SOCKS://user:pass@127.0.0.1:1080", "socks5://user:pass@127.0.0.1:1080"),
        ("socks5://127.0.0.1:1080", "socks5://127.0.0.1:1080"),
        ("socks5h://127.0.0.1:1080", "socks5h://127.0.0.1:1080"),
        ("http://127.0.0.1:8080", "http://127.0.0.1:8080"),
        ("https://proxy.example.com:8443", "https://proxy.example.com:8443"),
        ("   ", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_proxy_url(raw_proxy: str | None, expected_proxy: str | None):
    """Проверка нормализации схемы прокси: socks:// -> socks5://, остальные без изменений."""
    assert normalize_proxy_url(raw_proxy) == expected_proxy


def test_load_config_normalizes_socks_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """load_config преобразует socks:// в socks5:// для совместимости с aiohttp-socks."""
    monkeypatch.delenv("TELEGRAM_PROXY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TELEGRAM_BOT_TOKEN=123456789:ABCdefGhI\n"
        "SERVER_URL=http://localhost:8000\n"
        "CLIENT_API_KEY=my-super-secret-key\n"
        "TELEGRAM_PROXY=socks://127.0.0.1:10808\n",
        encoding="utf-8",
    )

    cfg = load_config(env_file)
    assert cfg.telegram_proxy == "socks5://127.0.0.1:10808"
