import httpx
import pytest
from aiogram.types import Update

from clients.telegram_bot.config import BotConfig
from clients.telegram_bot.keyboards.inline import get_installation_card_keyboard
from clients.telegram_bot.main import build_dispatcher
from clients.telegram_bot.tests.conftest import (
    create_mock_api_client,
)
from clients.telegram_bot.tests.test_timelapse_and_video import (
    create_test_callback_query,
)


def test_archived_card_keyboard_has_unarchive_button():
    """Проверка наличия кнопки разархивации в карточке архивной установки для разработчика."""
    kb = get_installation_card_keyboard(inst_id=1, role="developer", is_archived=True)
    all_buttons = [btn for row in kb.inline_keyboard for btn in row]
    unarchive_btn = next(
        (b for b in all_buttons if b.callback_data == "inst:unarchive:1"), None
    )
    assert unarchive_btn is not None
    assert "Восстановить из архива" in unarchive_btn.text


@pytest.mark.asyncio
async def test_bot_unarchive_flow(bot_config: BotConfig, mock_bot):
    """Сценарий подтверждения и разархивации установки в Telegram-боте."""
    unarchive_called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal unarchive_called
        path = request.url.path
        if path == "/api/auth/me":
            return httpx.Response(200, json={"role": "developer", "username": "Dev"})
        if path == "/api/installations/1/unarchive" and request.method == "POST":
            unarchive_called = True
            return httpx.Response(
                200,
                json={
                    "id": 1,
                    "name": "Восстановленная",
                    "culture_name": "Томат",
                    "is_archived": False,
                    "light_gpio_pin": 17,
                    "flash_gpio_pin": 27,
                    "warnings": [
                        "⚠️ Пин 17 уже занят («Установка 2»). Реле будут общими."
                    ],
                },
            )
        return httpx.Response(404)

    api_client = create_mock_api_client(bot_config, handler)
    dp = build_dispatcher(bot_config, api_client)

    # 1. Запрос подтверждения
    cb_prompt = create_test_callback_query("inst:unarchive:1", user_id=123)
    await dp.feed_update(mock_bot, Update(update_id=1, callback_query=cb_prompt))
    mock_bot.edit_message_text.assert_called()
    prompt_text = mock_bot.edit_message_text.call_args[1]["text"]
    assert "Восстановление установки из архива" in prompt_text

    # 2. Подтверждение 'yes'
    cb_confirm = create_test_callback_query("cnf:unarchive:1:yes", user_id=123)
    await dp.feed_update(mock_bot, Update(update_id=2, callback_query=cb_confirm))
    assert unarchive_called is True
    res_text = mock_bot.edit_message_text.call_args[1]["text"]
    assert "Установка успешно восстановлена из архива" in res_text
    assert "⚠️ Пин 17 уже занят" in res_text
