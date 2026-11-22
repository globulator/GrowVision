from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, Chat, Message, User

from clients.telegram_bot.api_client import GrowVisionApiClient
from clients.telegram_bot.config import BotConfig
from clients.telegram_bot.utils.debug_logger import DebugLogger, set_debug_logger


@pytest.fixture(autouse=True)
def isolate_debug_logger(tmp_path: Path):
    """Изолирует DebugLogger на время тестов во временную директорию."""
    test_log_dir = tmp_path / "bot_logs"
    test_log_dir.mkdir(parents=True, exist_ok=True)
    test_logger = DebugLogger(log_dir=test_log_dir, enabled=True)
    set_debug_logger(test_logger)
    yield test_logger
    set_debug_logger(None)


@pytest.fixture
def bot_config() -> BotConfig:
    """Тестовая конфигурация бота."""
    return BotConfig(
        bot_token="123456789:TEST_BOT_TOKEN_MOCK",
        server_url="http://testserver:8000",
        client_api_key="test-client-secret-key",
        web_public_url="http://public.web:8000",
    )


@pytest.fixture
def mock_bot() -> MagicMock:
    """Мок экземпляра Bot для фейковых апдейтов aiogram."""
    bot = MagicMock(spec=Bot)
    bot.token = "123456789:TEST_BOT_TOKEN_MOCK"
    bot.id = 123456789
    bot.parse_mode = ParseMode.HTML

    # Метод bot(...) в aiogram 3 выполняет вызов Telegram API метода
    mock_msg = Message(
        message_id=101,
        date=datetime.now(UTC),
        chat=Chat(id=12345, type="private"),
        text="Тестовое сообщение",
    ).as_(bot)

    async def _call(method, **kwargs):
        api_method = getattr(method, "__api_method__", "")
        if api_method == "sendMessage":
            return await bot.send_message(
                chat_id=method.chat_id,
                text=method.text,
                reply_markup=method.reply_markup,
            )
        elif api_method == "sendPhoto":
            return await bot.send_photo(
                chat_id=method.chat_id,
                photo=method.photo,
                caption=getattr(method, "caption", None),
            )
        elif api_method == "sendVideo":
            return await bot.send_video(
                chat_id=method.chat_id,
                video=method.video,
                caption=getattr(method, "caption", None),
                supports_streaming=getattr(method, "supports_streaming", None),
            )
        elif api_method == "deleteMessage":
            return await bot.delete_message(
                chat_id=method.chat_id,
                message_id=method.message_id,
            )
        elif api_method == "answerCallbackQuery":
            return await bot.answer_callback_query(
                callback_query_id=method.callback_query_id,
                text=method.text,
                show_alert=method.show_alert,
            )
        elif api_method == "editMessageText":
            return await bot.edit_message_text(
                chat_id=getattr(method, "chat_id", None),
                message_id=getattr(method, "message_id", None),
                text=method.text,
                reply_markup=getattr(method, "reply_markup", None),
            )
        elif api_method == "editMessageMedia":
            return await bot.edit_message_media(
                chat_id=getattr(method, "chat_id", None),
                message_id=getattr(method, "message_id", None),
                media=method.media,
                reply_markup=getattr(method, "reply_markup", None),
            )
        return mock_msg

    bot.side_effect = _call
    bot.send_message = AsyncMock(return_value=mock_msg)
    bot.send_photo = AsyncMock(return_value=mock_msg)
    bot.send_video = AsyncMock(return_value=mock_msg)
    bot.send_media_group = AsyncMock(return_value=[mock_msg])
    bot.edit_message_text = AsyncMock(return_value=mock_msg)
    bot.edit_message_media = AsyncMock(return_value=mock_msg)
    bot.delete_message = AsyncMock(return_value=True)
    bot.answer_callback_query = AsyncMock(return_value=True)
    bot.session = MagicMock()
    bot.session.close = AsyncMock()
    return bot


def create_mock_api_client(
    config: BotConfig,
    handler: callable,
) -> GrowVisionApiClient:
    """Создает GrowVisionApiClient с httpx.MockTransport."""
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url=config.server_url)
    return GrowVisionApiClient(config, client=client)


def create_test_message(
    text: str,
    user_id: int = 12345,
    chat_id: int = 12345,
    message_id: int = 1,
) -> Message:
    """Создает валидный pydantic экземпляр Message aiogram."""
    return Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="Тест"),
        text=text,
    )


def create_test_callback_query(
    data: str,
    user_id: int = 12345,
    chat_id: int = 12345,
    message_id: int = 99,
) -> CallbackQuery:
    """Создает валидный pydantic экземпляр CallbackQuery aiogram."""
    user = User(id=user_id, is_bot=False, first_name="Тест")
    chat = Chat(id=chat_id, type="private")
    msg = Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=chat,
        from_user=user,
        text="Контекст сообщения",
    )
    return CallbackQuery(
        id="query_100",
        from_user=user,
        chat_instance="instance_abc",
        message=msg,
        data=data,
    )
