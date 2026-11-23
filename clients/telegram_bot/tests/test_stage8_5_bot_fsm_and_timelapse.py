# clients/telegram_bot/tests/test_stage8_5_bot_fsm_and_timelapse.py
from datetime import UTC, datetime

import httpx
import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from clients.telegram_bot.config import BotConfig
from clients.telegram_bot.main import build_dispatcher
from clients.telegram_bot.states import InstallationCreateStates
from clients.telegram_bot.tests.conftest import create_mock_api_client


def create_test_callback_query(
    data: str,
    user_id: int = 12345,
    chat_id: int = 12345,
    message_id: int = 99,
) -> CallbackQuery:
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


def create_test_message(
    text: str, user_id: int = 12345, chat_id: int = 12345
) -> Message:
    user = User(id=user_id, is_bot=False, first_name="Тест")
    chat = Chat(id=chat_id, type="private")
    return Message(
        message_id=101,
        date=datetime.now(UTC),
        chat=chat,
        from_user=user,
        text=text,
    )


@pytest.mark.asyncio
async def test_fsm_cancel_clears_state(bot_config: BotConfig, mock_bot):
    """Нажатие 'inst:cancel' или 'inst:list' очищает FSM контекст."""
    api_client = create_mock_api_client(
        bot_config, lambda r: httpx.Response(200, json=[])
    )
    dp = build_dispatcher(bot_config, api_client)

    storage = dp.storage
    state = FSMContext(
        storage=storage,
        key=StorageKey(bot_id=mock_bot.id, chat_id=12345, user_id=12345),
    )
    await state.set_state(InstallationCreateStates.waiting_name)
    assert await state.get_state() == InstallationCreateStates.waiting_name.state

    cb = create_test_callback_query("inst:cancel", user_id=12345, chat_id=12345)
    await dp.feed_update(mock_bot, Update(update_id=201, callback_query=cb))

    curr_state = await state.get_state()
    assert curr_state is None


@pytest.mark.asyncio
async def test_fsm_validation_error_preserves_state(bot_config: BotConfig, mock_bot):
    """При невалидном вводе пина шаг FSM сохраняется."""
    api_client = create_mock_api_client(
        bot_config, lambda r: httpx.Response(200, json=[])
    )
    dp = build_dispatcher(bot_config, api_client)

    storage = dp.storage
    state = FSMContext(
        storage=storage,
        key=StorageKey(bot_id=mock_bot.id, chat_id=12345, user_id=12345),
    )
    await state.set_state(InstallationCreateStates.waiting_light_pin)

    msg = create_test_message("не_число", user_id=12345, chat_id=12345)
    await dp.feed_update(mock_bot, Update(update_id=202, message=msg))

    curr_state = await state.get_state()
    assert curr_state == InstallationCreateStates.waiting_light_pin.state


@pytest.mark.asyncio
async def test_timelapse_calculated_strictly_for_30_fps(
    bot_config: BotConfig, mock_bot
):
    """Таймлапс: длительность рассчитывается строго под 30 FPS без пресетов 5/10/15/30."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/installations/1":
            return httpx.Response(200, json={"id": 1, "name": "Установка 30FPS"})
        if path == "/api/timelapse/1/frames":
            frames = [
                {"id": i, "captured_at": "2026-11-23T10:00:00Z"} for i in range(90)
            ]
            return httpx.Response(200, json={"frames": frames})
        return httpx.Response(404)

    api_client = create_mock_api_client(bot_config, handler)
    dp = build_dispatcher(bot_config, api_client)

    cb = create_test_callback_query("inst:video:1")
    await dp.feed_update(mock_bot, Update(update_id=203, callback_query=cb))

    assert mock_bot.edit_message_text.called or mock_bot.send_message.called
    call_kwargs = (
        mock_bot.edit_message_text.call_args[1]
        if mock_bot.edit_message_text.called
        else mock_bot.send_message.call_args[1]
    )
    text = call_kwargs["text"]
    kb = call_kwargs["reply_markup"]

    assert "30 FPS" in text
    assert "3.0 с" in text

    btn_texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("30 FPS" in t for t in btn_texts)
    assert not any(t == "10 сек" for t in btn_texts)
    assert not any(t == "15 сек" for t in btn_texts)
    assert not any(t == "30 сек" for t in btn_texts)
