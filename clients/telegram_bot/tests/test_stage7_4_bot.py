import json
from unittest.mock import MagicMock

import httpx
import pytest
from aiogram.types import Update

from clients.telegram_bot.config import BotConfig
from clients.telegram_bot.main import build_dispatcher
from clients.telegram_bot.tests.conftest import (
    create_mock_api_client,
    create_test_callback_query,
    create_test_message,
)


@pytest.mark.asyncio
async def test_bot_profile_rename_me(bot_config: BotConfig, mock_bot: MagicMock):
    """Критерий 4: Переименование себя биологом через кнопку в профиле."""
    current_login = "Старое Имя"

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal current_login
        path = request.url.path
        if path == "/api/auth/me":
            if request.method == "GET":
                return httpx.Response(
                    200,
                    json={
                        "login": current_login,
                        "role": "biologist",
                        "authorized_at": "2026-11-22T10:00:00Z",
                        "last_active_at": "2026-11-22T10:30:00Z",
                    },
                )
            if request.method == "PATCH":
                body = json.loads(request.content.decode())
                current_login = body["login"]
                return httpx.Response(
                    200,
                    json={
                        "login": current_login,
                        "role": "biologist",
                        "authorized_at": "2026-11-22T10:00:00Z",
                        "last_active_at": "2026-11-22T10:30:00Z",
                    },
                )
        return httpx.Response(404)

    api_client = create_mock_api_client(bot_config, handler)
    dp = build_dispatcher(bot_config, api_client)

    # 1. Открываем /profile
    msg = create_test_message("/profile", user_id=100)
    await dp.feed_update(mock_bot, Update(update_id=1, message=msg))
    mock_bot.send_message.assert_called_once()
    kb = mock_bot.send_message.call_args[1]["reply_markup"]
    btn_rename = any(
        btn.callback_data == "auth:rename_me"
        for row in kb.inline_keyboard
        for btn in row
    )
    assert btn_rename is True

    # 2. Нажимаем «✏️ Изменить имя»
    mock_bot.send_message.reset_mock()
    cb = create_test_callback_query("auth:rename_me", user_id=100)
    await dp.feed_update(mock_bot, Update(update_id=2, callback_query=cb))
    mock_bot.send_message.assert_called_once()
    assert (
        "Введите ваше новое отображаемое имя"
        in mock_bot.send_message.call_args[1]["text"]
    )

    # 3. Вводим новое имя
    mock_bot.send_message.reset_mock()
    msg_name = create_test_message("Мария Петрова", user_id=100)
    await dp.feed_update(mock_bot, Update(update_id=3, message=msg_name))
    assert mock_bot.send_message.call_count >= 1
    # Проверяем подтверждение
    first_resp = mock_bot.send_message.call_args_list[0][1]["text"]
    assert "Имя успешно изменено на: <b>Мария Петрова</b>" in first_resp
    assert current_login == "Мария Петрова"


@pytest.mark.asyncio
async def test_bot_sessions_rename_user(bot_config: BotConfig, mock_bot: MagicMock):
    """Критерий 4: Переименование любого пользователя разработчиком в разделе сессий."""
    session_login = "Иван Биолог"

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal session_login
        path = request.url.path
        if path == "/api/auth/me":
            return httpx.Response(200, json={"role": "developer", "login": "Dev"})
        if path == "/api/auth/sessions":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 5,
                        "client_type": "telegram",
                        "external_id": "555",
                        "login": session_login,
                        "role": "biologist",
                        "is_active": True,
                        "authorized_at": "2026-11-22T10:00:00Z",
                        "last_active_at": "2026-11-22T10:30:00Z",
                        "logged_out_at": None,
                    }
                ],
            )
        if path == "/api/auth/sessions/5" and request.method == "PATCH":
            body = json.loads(request.content.decode())
            session_login = body["login"]
            return httpx.Response(
                200,
                json={
                    "id": 5,
                    "client_type": "telegram",
                    "external_id": "555",
                    "login": session_login,
                    "role": "biologist",
                    "is_active": True,
                    "authorized_at": "2026-11-22T10:00:00Z",
                    "last_active_at": "2026-11-22T10:30:00Z",
                    "logged_out_at": None,
                },
            )
        return httpx.Response(404)

    api_client = create_mock_api_client(bot_config, handler)
    dp = build_dispatcher(bot_config, api_client)

    # 1. Открываем карточку сессии sess:view:5
    cb_view = create_test_callback_query("sess:view:5", user_id=200)
    await dp.feed_update(mock_bot, Update(update_id=10, callback_query=cb_view))
    kb = mock_bot.edit_message_text.call_args[1]["reply_markup"]
    btn_rename = any(
        btn.callback_data == "sess:rename:5"
        for row in kb.inline_keyboard
        for btn in row
    )
    assert btn_rename is True

    # 2. Нажимаем «✏️ Переименовать»
    mock_bot.send_message.reset_mock()
    cb_rename = create_test_callback_query("sess:rename:5", user_id=200)
    await dp.feed_update(mock_bot, Update(update_id=11, callback_query=cb_rename))
    mock_bot.send_message.assert_called_once()
    assert (
        "Введите новое отображаемое имя для пользователя сессии #5"
        in mock_bot.send_message.call_args[1]["text"]
    )

    # 3. Вводим новое имя
    mock_bot.send_message.reset_mock()
    msg_name = create_test_message("Василий Агроном", user_id=200)
    await dp.feed_update(mock_bot, Update(update_id=12, message=msg_name))
    mock_bot.send_message.assert_called_once()
    assert (
        "Пользователь сессии #5 успешно переименован в: <b>Василий Агроном</b>"
        in mock_bot.send_message.call_args[1]["text"]
    )
    assert session_login == "Василий Агроном"


@pytest.mark.asyncio
async def test_bot_relay_sync_by_pin(bot_config: BotConfig, mock_bot: MagicMock):
    """Критерий 2: Программная синхронизация состояния реле по пину в боте."""
    installations_db = [
        {
            "id": 1,
            "name": "Установка 1",
            "culture_name": "Томат",
            "light_gpio_pin": 4,
            "flash_gpio_pin": 5,
            "light_state": False,
            "flash_state": False,
        },
        {
            "id": 2,
            "name": "Установка 2",
            "culture_name": "Перец",
            "light_gpio_pin": 4,  # общий пин 4
            "flash_gpio_pin": 6,
            "light_state": False,
            "flash_state": False,
        },
    ]

    pin_states = {"4": False, "5": False, "6": False}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth/me":
            return httpx.Response(200, json={"role": "developer", "login": "Dev"})
        if path == "/api/installations/1":
            data = dict(installations_db[0])
            data["light_state"] = pin_states["4"]
            return httpx.Response(200, json=data)
        if path == "/api/installations/2":
            data = dict(installations_db[1])
            data["light_state"] = pin_states["4"]
            return httpx.Response(200, json=data)
        if path == "/api/installations":
            return httpx.Response(200, json=installations_db)
        if path == "/api/control/light/main" and request.method == "POST":
            body = json.loads(request.content.decode())
            target = str(body["target"])
            state = body["state"]
            if target in ("1", "2"):
                pin_states["4"] = state
            return httpx.Response(
                200,
                json={
                    "status": "done",
                    "result_message": "OK",
                    "pin_states": pin_states,
                },
            )
        if path == "/api/system/status":
            return httpx.Response(200, json={"settings": {"debug_mode": False}})
        return httpx.Response(404)

    api_client = create_mock_api_client(bot_config, handler)
    dp = build_dispatcher(bot_config, api_client)

    # 1. Включаем свет на Установке 1 (ctrl:light:1:1 -> state=1, target=1)
    cb_on = create_test_callback_query("ctrl:light:1:1", user_id=200)
    await dp.feed_update(mock_bot, Update(update_id=20, callback_query=cb_on))
    kb1 = mock_bot.edit_message_text.call_args[1]["reply_markup"]
    # Кнопка света Установки 1 включена (зеленый кружок)
    btn_light1 = kb1.inline_keyboard[0][0].text
    assert "🟢" in btn_light1

    # 2. Теперь открываем панель Установки 2 (target=2), у которой тот же пин 4
    mock_bot.edit_message_text.reset_mock()
    cb_target2 = create_test_callback_query("ctrl:target:2", user_id=200)
    await dp.feed_update(mock_bot, Update(update_id=21, callback_query=cb_target2))
    kb2 = mock_bot.edit_message_text.call_args[1]["reply_markup"]
    # Кнопка света Установки 2 должна автоматически быть ВКЛЮЧЕНА (🟢), так как пин 4 включен!
    btn_light2 = kb2.inline_keyboard[0][0].text
    assert "🟢" in btn_light2, (
        f"Ожидался включенный свет на Установке 2 с общим пином, получено: {btn_light2}"
    )


@pytest.mark.asyncio
async def test_bot_installation_pin_warnings(
    bot_config: BotConfig, mock_bot: MagicMock
):
    """Критерий 3: Предупреждения о занятых пинах при редактировании установки."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth/me":
            return httpx.Response(200, json={"role": "developer", "login": "Dev"})
        if path == "/api/installations/1" and request.method == "PATCH":
            return httpx.Response(
                200,
                json={
                    "id": 1,
                    "name": "Установка 1",
                    "culture_name": "Томат",
                    "light_gpio_pin": 4,
                    "flash_gpio_pin": 5,
                    "is_archived": False,
                    "warnings": [
                        "⚠️ Пин 4 уже занят («Установка 2»). Реле будут общими."
                    ],
                },
            )
        return httpx.Response(404)

    api_client = create_mock_api_client(bot_config, handler)
    dp = build_dispatcher(bot_config, api_client)

    # 1. Запускаем редактирование пина света: edit:fld:light_pin:1
    cb = create_test_callback_query("edit:fld:light_pin:1", user_id=200)
    await dp.feed_update(mock_bot, Update(update_id=30, callback_query=cb))

    # 2. Вводим пин 4
    msg = create_test_message("4", user_id=200)
    await dp.feed_update(mock_bot, Update(update_id=31, message=msg))

    mock_bot.send_message.assert_called_once()
    sent_text = mock_bot.send_message.call_args[1]["text"]
    assert "⚠️ Пин 4 уже занят («Установка 2»). Реле будут общими." in sent_text
    assert "Изменения успешно сохранены!" in sent_text
