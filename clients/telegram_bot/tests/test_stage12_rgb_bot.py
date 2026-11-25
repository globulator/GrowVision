"""Тесты этапа 12 для Telegram-бота: ввод 1/3 RGB пинов и подменю HEX-цвета."""

import json
from unittest.mock import MagicMock

import httpx
import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import Update

from clients.telegram_bot.config import BotConfig
from clients.telegram_bot.main import build_dispatcher
from clients.telegram_bot.states import ControlColorStates, InstallationCreateStates
from clients.telegram_bot.tests.conftest import (
    create_mock_api_client,
    create_test_callback_query,
    create_test_message,
)


@pytest.mark.asyncio
async def test_bot_create_rgb_pins_and_reject_invalid(
    bot_config: BotConfig, mock_bot: MagicMock
):
    """Мастер создания установки принимает 3 уникальных пина и отклоняет дубли/пересечения."""
    created_payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth/me":
            return httpx.Response(200, json={"role": "developer", "username": "Dev"})
        if path == "/api/installations" and request.method == "POST":
            body = json.loads(request.content.decode())
            created_payloads.append(body)
            return httpx.Response(
                201,
                json={
                    "id": 10,
                    "name": body["name"],
                    "culture_name": body["culture_name"],
                    "light_gpio_pin": body["light_gpio_pin"],
                    "flash_gpio_pin": body["flash_gpio_pin"],
                    "light_is_rgb": True,
                    "flash_is_rgb": False,
                    "light_color": "#FFFFFF",
                    "flash_color": "#FFFFFF",
                    "is_archived": False,
                },
            )
        return httpx.Response(404)

    api_client = create_mock_api_client(bot_config, handler)
    dp = build_dispatcher(bot_config, api_client)
    state = FSMContext(
        storage=dp.storage,
        key=StorageKey(bot_id=mock_bot.id, chat_id=12345, user_id=12345),
    )

    await state.set_state(InstallationCreateStates.waiting_light_pin)
    await state.update_data(name="RGB Бокс", crop_type="Салат")

    # 1. Дубликат внутри RGB -> остаёмся в waiting_light_pin
    await dp.feed_update(
        mock_bot, Update(update_id=1, message=create_test_message("17,17,22"))
    )
    assert await state.get_state() == InstallationCreateStates.waiting_light_pin

    # 2. 2 пина -> остаёмся в waiting_light_pin
    await dp.feed_update(
        mock_bot, Update(update_id=2, message=create_test_message("17,27"))
    )
    assert await state.get_state() == InstallationCreateStates.waiting_light_pin

    # 3. Корректные 3 уникальных пина
    await dp.feed_update(
        mock_bot, Update(update_id=3, message=create_test_message("17, 27, 22"))
    )
    assert await state.get_state() == InstallationCreateStates.waiting_flash_pin
    assert (await state.get_data())["light_gpio_pin"] == "17,27,22"

    # 4. Пересечение вспышки со светом -> остаёмся в waiting_flash_pin
    await dp.feed_update(
        mock_bot, Update(update_id=4, message=create_test_message("22"))
    )
    assert await state.get_state() == InstallationCreateStates.waiting_flash_pin

    # 5. Корректный пин вспышки
    await dp.feed_update(
        mock_bot, Update(update_id=5, message=create_test_message("26"))
    )
    assert await state.get_state() == InstallationCreateStates.waiting_aruco
    assert (await state.get_data())["flash_gpio_pin"] == 26


@pytest.mark.asyncio
async def test_bot_rgb_control_submenu_and_hex_fsm(
    bot_config: BotConfig, mock_bot: MagicMock
):
    """Для RGB-лампы открывается подменю с HEX, Вкл/Выкл и FSM задания HEX-цвета."""
    control_requests: list[dict] = []
    current_color = "#112233"
    current_light_state = True

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal current_color, current_light_state
        path = request.url.path
        if path == "/api/auth/me":
            return httpx.Response(200, json={"role": "developer", "username": "Dev"})
        if path == "/api/installations/5":
            return httpx.Response(
                200,
                json={
                    "id": 5,
                    "name": "RGB Установка",
                    "light_gpio_pin": "17,27,22",
                    "flash_gpio_pin": 26,
                    "light_pins": [17, 27, 22],
                    "flash_pins": [26],
                    "light_is_rgb": True,
                    "flash_is_rgb": False,
                    "light_color": current_color,
                    "flash_color": "#FFFFFF",
                    "light_state": current_light_state,
                    "flash_state": False,
                },
            )
        if path == "/api/control/light/main":
            body = json.loads(request.content.decode())
            control_requests.append(body)
            if "state" in body:
                current_light_state = bool(body["state"])
            if "color" in body:
                current_color = f"#{body['color'].lstrip('#').upper()}"
            return httpx.Response(
                200,
                json={
                    "status": "done",
                    "result_message": "OK",
                    "color": current_color,
                    "effective_state": current_light_state,
                    "pin_states": {"17": current_light_state},
                },
            )
        return httpx.Response(404)

    api_client = create_mock_api_client(bot_config, handler)
    dp = build_dispatcher(bot_config, api_client)

    # 1. Выбор цели -> кнопка света ведёт в подменю RGB, а кнопка вспышки (1-пиновая) — toggle
    await dp.feed_update(
        mock_bot,
        Update(
            update_id=10, callback_query=create_test_callback_query("ctrl:target:5")
        ),
    )
    kb = mock_bot.edit_message_text.call_args[1]["reply_markup"]
    light_btn = kb.inline_keyboard[0][0]
    flash_btn = kb.inline_keyboard[0][1]
    assert light_btn.callback_data == "ctrl:rgb:light:5"
    assert "#112233" in light_btn.text
    assert flash_btn.callback_data.startswith("ctrl:flash:")

    # 2. Открытие подменю RGB-света
    mock_bot.edit_message_text.reset_mock()
    await dp.feed_update(
        mock_bot,
        Update(
            update_id=11,
            callback_query=create_test_callback_query("ctrl:rgb:light:5"),
        ),
    )
    sub_text = mock_bot.edit_message_text.call_args[1]["text"]
    sub_kb = mock_bot.edit_message_text.call_args[1]["reply_markup"]
    assert "#112233" in sub_text
    assert sub_kb.inline_keyboard[0][0].callback_data == "ctrl:rgb_toggle:light:0:5"
    assert sub_kb.inline_keyboard[1][0].callback_data == "ctrl:rgb_hex:light:5"

    # 3. Переключение Вкл/Выкл в подменю RGB
    await dp.feed_update(
        mock_bot,
        Update(
            update_id=12,
            callback_query=create_test_callback_query("ctrl:rgb_toggle:light:0:5"),
        ),
    )
    assert control_requests[-1] == {"target": 5, "state": False}

    # 4. Запуск FSM задания HEX-цвета
    await dp.feed_update(
        mock_bot,
        Update(
            update_id=13,
            callback_query=create_test_callback_query("ctrl:rgb_hex:light:5"),
        ),
    )
    state = FSMContext(
        storage=dp.storage,
        key=StorageKey(bot_id=mock_bot.id, chat_id=12345, user_id=12345),
    )
    assert await state.get_state() == ControlColorStates.waiting_hex

    # Невалидный HEX -> остаёмся в waiting_hex
    await dp.feed_update(
        mock_bot, Update(update_id=14, message=create_test_message("ZZZZZZ"))
    )
    assert await state.get_state() == ControlColorStates.waiting_hex

    # Валидный HEX (без #) -> отправляется нормализованный #FF8800, FSM очищается
    await dp.feed_update(
        mock_bot, Update(update_id=15, message=create_test_message("ff8800"))
    )
    assert await state.get_state() is None
    assert control_requests[-1] == {"target": 5, "color": "#FF8800"}
    assert "#FF8800" in mock_bot.send_message.call_args[1]["text"]
