import asyncio
import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from aiogram import Bot, Dispatcher
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, Message, Update, User

from clients.telegram_bot.api_client import GrowVisionApiClient
from clients.telegram_bot.config import BotConfig, mask_proxy_url
from clients.telegram_bot.main import (
    build_dispatcher,
    get_retry_delay,
    run_polling_with_reconnect,
    wait_for_telegram_connection,
)
from clients.telegram_bot.states import AuthStates
from clients.telegram_bot.utils.debug_logger import DebugLogger, mask_sensitive_data
from clients.telegram_bot.utils.formatters import format_installation_card


def test_proxy_credentials_masking():
    """Логин и пароль в URL прокси маскируются во всех логах и представлении Config."""
    raw_socks = "socks5://proxy_user:proxy_pass123@127.0.0.1:1080"
    masked_socks = mask_proxy_url(raw_socks)
    assert masked_socks == "socks5://***:***@127.0.0.1:1080"
    assert "proxy_user" not in masked_socks
    assert "proxy_pass123" not in masked_socks

    raw_http = "http://admin:secret999@proxy.example.com:8080"
    masked_http = mask_proxy_url(raw_http)
    assert masked_http == "http://***:***@proxy.example.com:8080"
    assert "admin" not in masked_http
    assert "secret999" not in masked_http

    # Config __repr__
    cfg = BotConfig(
        bot_token="123456789:ABCDEF123456789",
        server_url="http://127.0.0.1:8000",
        client_api_key="client-key-secret",
        telegram_proxy=raw_socks,
    )
    repr_str = repr(cfg)
    assert "socks5://***:***@127.0.0.1:1080" in repr_str
    assert "proxy_user" not in repr_str
    assert "proxy_pass123" not in repr_str

    # mask_sensitive_data для произвольных строк
    masked_str = mask_sensitive_data(f"Connecting to {raw_socks} failed")
    assert "socks5://***:***@127.0.0.1:1080" in masked_str
    assert "proxy_user" not in masked_str


def test_retry_delays_progression():
    """Схема пауз подключения: 5, 10, 20, 40, далее 60 с."""
    assert get_retry_delay(1) == 5.0
    assert get_retry_delay(2) == 10.0
    assert get_retry_delay(3) == 20.0
    assert get_retry_delay(4) == 40.0
    assert get_retry_delay(5) == 60.0
    assert get_retry_delay(10) == 60.0


@pytest.mark.asyncio
async def test_wait_for_telegram_connection_retries_and_succeeds(
    bot_config: BotConfig,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    """При сетевой ошибке на старте бот повторяет попытки со ступенчатой паузой и предупреждением."""
    bot = MagicMock(spec=Bot)
    bot.get_me = AsyncMock()
    bot.set_my_commands = AsyncMock()

    # Первые 2 попытки выбрасывают ошибку сети, 3-я успешна
    bot.get_me.side_effect = [
        RuntimeError("Temporary DNS failure"),
        RuntimeError("Connection refused"),
        MagicMock(username="test_bot", id=12345),
    ]

    sleep_calls = []

    async def fake_sleep(sec: float):
        sleep_calls.append(sec)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    with caplog.at_level(logging.WARNING):
        await wait_for_telegram_connection(bot, bot_config)

    assert bot.get_me.call_count == 3
    assert sleep_calls == [5.0, 10.0]
    assert bot.set_my_commands.call_count == 1
    # Проверяем обязательный текст сообщения в логе
    assert (
        "Нет доступа к api.telegram.org: проверьте VPN или TELEGRAM_PROXY"
        in caplog.text
    )


@pytest.mark.asyncio
async def test_run_polling_reconnect_on_network_error(
    bot_config: BotConfig,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    """При сбое в цикле polling бот переподключается с паузами без падения процесса."""
    dp = MagicMock(spec=Dispatcher)
    dp.resolve_used_update_types = MagicMock(return_value=[])
    bot = MagicMock(spec=Bot)

    dp.start_polling = AsyncMock()
    # 1-й запуск polling падает с сетевым исключением, 2-й завершается штатно
    dp.start_polling.side_effect = [
        RuntimeError("Network tunnel closed"),
        None,
    ]

    sleep_calls = []

    async def fake_sleep(sec: float):
        sleep_calls.append(sec)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    with caplog.at_level(logging.WARNING):
        await run_polling_with_reconnect(dp, bot, bot_config)

    assert dp.start_polling.call_count == 2
    assert sleep_calls == [5.0]
    assert (
        "Нет доступа к api.telegram.org: проверьте VPN или TELEGRAM_PROXY"
        in caplog.text
    )


def test_html_escaping_in_formatters():
    """Пользовательские строки экранируются через html.escape в карточке установки."""
    dangerous_inst = {
        "id": 42,
        "name": "Тест <script>alert(1)</script> & Ромашка",
        "culture_name": "Томат <b>Черри</b> & Базилик",
        "is_archived": False,
        "aruco_size_mm": None,
        "cameras": [
            {
                "id": 1,
                "auto_name": "Камера <CSI-1>",
                "hardware_id": "cam<id>&1",
            }
        ],
    }

    card = format_installation_card(dangerous_inst, role="developer")
    # Проверяем, что сырые HTML-теги пользователя экранированы
    assert "<script>" not in card
    assert "&lt;script&gt;" in card
    assert "&amp; Ромашка" in card
    assert "&lt;CSI-1&gt;" in card
    assert "cam&lt;id&gt;&amp;1" in card
    assert "не задан (площадь в px)" in card


@pytest.mark.asyncio
async def test_password_never_leaks_to_jsonl(
    bot_config: BotConfig, mock_bot: MagicMock, tmp_path: Path
):
    """Текст сообщения шага ввода пароля не попадает в JSONL: ни входящим, ни ошибкой, ни заметкой *."""
    from clients.telegram_bot.utils.debug_logger import set_debug_logger

    test_log_dir = tmp_path / "leak_test_logs"
    debug_logger = DebugLogger(log_dir=test_log_dir, enabled=True)
    set_debug_logger(debug_logger)

    cfg = BotConfig(
        bot_token=bot_config.bot_token,
        server_url=bot_config.server_url,
        client_api_key=bot_config.client_api_key,
        web_public_url=bot_config.web_public_url,
        debug_log=True,
    )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(401, json={"detail": "Unauthorized"})
        ),
        base_url=cfg.server_url,
    )
    api_client = GrowVisionApiClient(cfg, client=client)
    dp = build_dispatcher(cfg, api_client)

    user = User(id=333, is_bot=False, first_name="User")
    chat = Chat(id=333, type="private")

    storage: MemoryStorage = dp.fsm.storage
    fsm_ctx = FSMContext(
        storage=storage,
        key=StorageKey(bot_id=mock_bot.id, chat_id=333, user_id=333),
    )

    # 1. Пользователь находится в состоянии waiting_password
    await fsm_ctx.set_state(AuthStates.waiting_password)
    secret_pass = "SuperSecret_P@ssw0rd_999"

    # Пользователь отправляет пароль, который начинается со звёздочки: *SuperSecret_P@ssw0rd_999
    star_pass_msg = Message(
        message_id=50,
        date=1700000000,
        chat=chat,
        from_user=user,
        text=f"*{secret_pass}",
    ).as_(mock_bot)

    await dp.feed_update(mock_bot, Update(update_id=50, message=star_pass_msg))

    # Пользователь отправляет чистый пароль в waiting_password
    plain_pass_msg = Message(
        message_id=51,
        date=1700000001,
        chat=chat,
        from_user=user,
        text=secret_pass,
    ).as_(mock_bot)

    await dp.feed_update(mock_bot, Update(update_id=51, message=plain_pass_msg))

    # Считываем все созданные JSONL файлы в test_log_dir
    log_files = list(test_log_dir.glob("*.jsonl"))
    assert len(log_files) > 0

    all_content = ""
    for lf in log_files:
        all_content += lf.read_text(encoding="utf-8")

    # Гарантируем: секретный пароль ни в каком виде не попал в лог!
    assert secret_pass not in all_content

    # Проверяем кольцевой буфер
    for ev in debug_logger.get_recent_events():
        ev_str = json.dumps(ev, ensure_ascii=False)
        assert secret_pass not in ev_str

    await api_client.close()
