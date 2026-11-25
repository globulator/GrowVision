"""Тесты этапа 12.1 для Telegram-бота: меню светового дня (все / установка), FSM HH:MM-HH:MM и блокировки."""

import json
from unittest.mock import MagicMock

import httpx
import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import Update

from clients.telegram_bot.config import BotConfig
from clients.telegram_bot.main import build_dispatcher
from clients.telegram_bot.states import ControlDayNightStates
from clients.telegram_bot.tests.conftest import (
    create_mock_api_client,
    create_test_callback_query,
    create_test_message,
)


@pytest.mark.asyncio
async def test_bot_global_day_night_menu_toggle_and_interval_fsm(
    bot_config: BotConfig, mock_bot: MagicMock
):
    """Меню 'Свет и реле' (target='all'): Световой день (все), Вкл/Выкл и FSM ввода HH:MM-HH:MM."""
    dn_requests: list[dict] = []
    global_state = {
        "global_day_night_enabled": False,
        "global_day_start": "08:00",
        "global_day_end": "22:00",
        "global_day_night_phase": "day",
        "global_light_locked": False,
        "global_light_mode": "default",
        "installations": [
            {
                "installation_id": 1,
                "name": "Бокс А",
                "light_gpio_pin": 17,
                "flash_gpio_pin": 27,
                "light_state": True,
                "flash_state": False,
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth/me":
            return httpx.Response(200, json={"role": "developer", "username": "Dev"})
        if path == "/api/installations":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 1,
                        "name": "Бокс А",
                        "light_gpio_pin": 17,
                        "flash_gpio_pin": 27,
                        "light_state": True,
                        "flash_state": False,
                        **global_state,
                    }
                ],
            )
        if path == "/api/control/relays":
            return httpx.Response(200, json=global_state)
        if path == "/api/control/day-night" and request.method == "POST":
            body = json.loads(request.content.decode())
            dn_requests.append(body)
            if "enabled" in body:
                global_state["global_day_night_enabled"] = bool(body["enabled"])
                global_state["global_light_locked"] = bool(body["enabled"])
            if "day_start" in body:
                global_state["global_day_start"] = body["day_start"]
            if "day_end" in body:
                global_state["global_day_end"] = body["day_end"]
            return httpx.Response(200, json={"status": "done", **global_state})
        return httpx.Response(404)

    api_client = create_mock_api_client(bot_config, handler)
    dp = build_dispatcher(bot_config, api_client)
    state = FSMContext(
        storage=dp.storage,
        key=StorageKey(bot_id=mock_bot.id, chat_id=12345, user_id=12345),
    )

    # 1. Панель 'all' содержит кнопку 'Световой день (все)'
    await dp.feed_update(
        mock_bot,
        Update(
            update_id=1, callback_query=create_test_callback_query("ctrl:target:all")
        ),
    )
    kb = mock_bot.edit_message_text.call_args[1]["reply_markup"]
    flat_btns = [btn for row in kb.inline_keyboard for btn in row]
    dn_btn = next((b for b in flat_btns if b.callback_data == "ctrl:dn:menu:all"), None)
    assert dn_btn is not None
    assert "Световой день (все)" in dn_btn.text

    # 2. Открытие подменю глобального светового дня
    mock_bot.edit_message_text.reset_mock()
    await dp.feed_update(
        mock_bot,
        Update(
            update_id=2, callback_query=create_test_callback_query("ctrl:dn:menu:all")
        ),
    )
    sub_text = mock_bot.edit_message_text.call_args[1]["text"]
    sub_kb = mock_bot.edit_message_text.call_args[1]["reply_markup"]
    assert "08:00–22:00" in sub_text
    assert "День" in sub_text
    assert sub_kb.inline_keyboard[0][0].callback_data == "ctrl:dn:toggle:1:all"
    assert sub_kb.inline_keyboard[1][0].callback_data == "ctrl:dn:interval:all"

    # 3. Переключение Вкл
    mock_bot.edit_message_text.reset_mock()
    await dp.feed_update(
        mock_bot,
        Update(
            update_id=3,
            callback_query=create_test_callback_query("ctrl:dn:toggle:1:all"),
        ),
    )
    assert dn_requests[-1] == {"target": "all", "enabled": True}
    assert "Вкл" in mock_bot.edit_message_text.call_args[1]["text"]

    # 4. Ввод интервала через FSM: отклоняет start == end и неверный формат, принимает 22:00-06:00
    await dp.feed_update(
        mock_bot,
        Update(
            update_id=4,
            callback_query=create_test_callback_query("ctrl:dn:interval:all"),
        ),
    )
    assert await state.get_state() == ControlDayNightStates.waiting_interval

    await dp.feed_update(
        mock_bot, Update(update_id=5, message=create_test_message("08:00-08:00"))
    )
    assert await state.get_state() == ControlDayNightStates.waiting_interval

    await dp.feed_update(
        mock_bot, Update(update_id=6, message=create_test_message("25:00-06:00"))
    )
    assert await state.get_state() == ControlDayNightStates.waiting_interval

    await dp.feed_update(
        mock_bot, Update(update_id=7, message=create_test_message("22:00-06:00"))
    )
    assert await state.get_state() is None
    assert dn_requests[-1] == {
        "target": "all",
        "day_start": "22:00",
        "day_end": "06:00",
    }
    last_msg_text = mock_bot.send_message.call_args[1]["text"]
    assert "22:00–06:00" in last_msg_text


@pytest.mark.asyncio
async def test_bot_installation_day_night_menu_shows_lock_reason(
    bot_config: BotConfig, mock_bot: MagicMock
):
    """Меню установки содержит 'Световой день' и отображает причину блокировки от общего цикла/света."""
    inst_payload = {
        "id": 3,
        "name": "Секция С",
        "light_gpio_pin": 17,
        "flash_gpio_pin": 27,
        "light_state": True,
        "flash_state": False,
        "day_night_enabled": False,
        "day_start": "08:00",
        "day_end": "22:00",
        "day_night_phase": "day",
        "day_night_locked": True,
        "light_locked": True,
        "light_lock_reason": "Заблокировано",
        "global_light_mode": "default",
        "global_day_night_enabled": True,
        "global_day_start": "09:00",
        "global_day_end": "21:00",
        "global_day_night_phase": "day",
        "global_light_locked": True,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth/me":
            return httpx.Response(200, json={"role": "developer", "username": "Dev"})
        if path == "/api/installations/3":
            return httpx.Response(200, json=inst_payload)
        return httpx.Response(404)

    api_client = create_mock_api_client(bot_config, handler)
    dp = build_dispatcher(bot_config, api_client)

    await dp.feed_update(
        mock_bot,
        Update(
            update_id=10, callback_query=create_test_callback_query("ctrl:target:3")
        ),
    )
    kb = mock_bot.edit_message_text.call_args[1]["reply_markup"]
    flat_btns = [btn for row in kb.inline_keyboard for btn in row]
    dn_btn = next((b for b in flat_btns if b.callback_data == "ctrl:dn:menu:3"), None)
    assert dn_btn is not None
    assert dn_btn.text == "☀️ Световой день"

    mock_bot.edit_message_text.reset_mock()
    await dp.feed_update(
        mock_bot,
        Update(
            update_id=11, callback_query=create_test_callback_query("ctrl:dn:menu:3")
        ),
    )
    text = mock_bot.edit_message_text.call_args[1]["text"]
    assert "Заблокировано" in text
    assert "общий цикл" in text
    assert "09:00–21:00" in text

    # Переключаем причину на общий ручной свет
    inst_payload["global_day_night_enabled"] = False
    inst_payload["global_light_mode"] = "off"
    mock_bot.edit_message_text.reset_mock()
    await dp.feed_update(
        mock_bot,
        Update(
            update_id=12, callback_query=create_test_callback_query("ctrl:dn:menu:3")
        ),
    )
    text2 = mock_bot.edit_message_text.call_args[1]["text"]
    assert "Заблокировано" in text2
    assert "общий ручной свет" in text2
