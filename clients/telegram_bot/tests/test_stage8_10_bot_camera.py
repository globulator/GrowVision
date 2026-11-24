from unittest.mock import MagicMock

import httpx
import pytest
from aiogram.types import Update

from clients.telegram_bot.config import BotConfig
from clients.telegram_bot.main import build_dispatcher
from clients.telegram_bot.tests.conftest import create_mock_api_client
from clients.telegram_bot.tests.test_handlers_installations_cameras_control import (
    create_test_callback_query,
)


@pytest.mark.asyncio
async def test_bot_camera_card_has_autocalibrate_button(
    bot_config: BotConfig, mock_bot: MagicMock
):
    """Карточка камеры содержит кнопку '⚙️ Автокалибровка' и профиль качества при наличии."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth/me":
            return httpx.Response(200, json={"role": "developer", "username": "Dev"})
        if path == "/api/auth/telegram/sync":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/api/cameras":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 10,
                        "auto_name": "Камера USB-1",
                        "hardware_id": "usb-cam-1",
                        "is_online": True,
                        "installation_id": 1,
                        "installation_name": "Бокс-1",
                        "calibration_summary": "1920x1080 (MJPG), автофокус: поддерживается",
                    }
                ],
            )
        return httpx.Response(404)

    api_client = create_mock_api_client(bot_config, handler)
    dp = build_dispatcher(bot_config, api_client)

    cb = create_test_callback_query("cam:view:10")
    update = Update(update_id=1, callback_query=cb)
    await dp.feed_update(mock_bot, update)

    mock_bot.edit_message_text.assert_called()
    text = mock_bot.edit_message_text.call_args[1]["text"]
    kb = mock_bot.edit_message_text.call_args[1]["reply_markup"]

    assert "Камера USB-1" in text
    assert "1920x1080 (MJPG)" in text

    # Проверяем наличие кнопки автокалибровки
    button_texts = [btn.text for row in kb.inline_keyboard for btn in row]
    callback_datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "⚙️ Автокалибровка" in button_texts
    assert "cam:calibrate:10" in callback_datas


@pytest.mark.asyncio
async def test_bot_camera_autocalibrate_action(
    bot_config: BotConfig, mock_bot: MagicMock
):
    """Нажатие 'cam:calibrate:10' вызывает POST /api/cameras/10/autocalibrate и выводит сводку."""
    calib_called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calib_called
        path = request.url.path
        if path == "/api/auth/me":
            return httpx.Response(200, json={"role": "developer", "username": "Dev"})
        if path == "/api/auth/telegram/sync":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/api/cameras/10/autocalibrate" and request.method == "POST":
            calib_called = True
            return httpx.Response(
                200,
                json={
                    "status": "done",
                    "camera_id": 10,
                    "hardware_id": "usb-cam-1",
                    "summary": "1920x1080 (MJPG), автофокус: поддерживается",
                },
            )
        if path == "/api/cameras":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 10,
                        "auto_name": "Камера USB-1",
                        "hardware_id": "usb-cam-1",
                        "is_online": True,
                    }
                ],
            )
        return httpx.Response(404)

    api_client = create_mock_api_client(bot_config, handler)
    dp = build_dispatcher(bot_config, api_client)

    cb = create_test_callback_query("cam:calibrate:10")
    update = Update(update_id=2, callback_query=cb)
    await dp.feed_update(mock_bot, update)

    assert calib_called is True
    mock_bot.edit_message_text.assert_called()
    final_text = mock_bot.edit_message_text.call_args[1]["text"]
    assert "Результаты автокалибровки камеры #10" in final_text
    assert "1920x1080 (MJPG)" in final_text


@pytest.mark.asyncio
async def test_bot_camera_bind_with_autopick(
    bot_config: BotConfig, mock_bot: MagicMock
):
    """При привязке камеры запускается автоподбор параметров и добавляется в сообщение."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth/me":
            return httpx.Response(200, json={"role": "developer", "username": "Dev"})
        if path == "/api/auth/telegram/sync":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/api/cameras/10/bind" and request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "id": 10,
                    "auto_name": "Камера USB-1",
                    "hardware_id": "usb-cam-1",
                    "installation_id": 1,
                    "is_online": True,
                },
            )
        if path == "/api/cameras/10/autocalibrate" and request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "status": "done",
                    "camera_id": 10,
                    "summary": "1920x1080 (MJPG), автофокус: поддерживается",
                },
            )
        if path == "/api/cameras":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 10,
                        "auto_name": "Камера USB-1",
                        "hardware_id": "usb-cam-1",
                        "is_online": True,
                    }
                ],
            )
        return httpx.Response(404)

    api_client = create_mock_api_client(bot_config, handler)
    dp = build_dispatcher(bot_config, api_client)

    cb = create_test_callback_query("cam:do_bind:10:1")
    update = Update(update_id=3, callback_query=cb)
    await dp.feed_update(mock_bot, update)

    mock_bot.edit_message_text.assert_called()
    text = mock_bot.edit_message_text.call_args[1]["text"]
    assert "успешно привязана к установке #1" in text
    assert "Автоподбор качества" in text
    assert "1920x1080 (MJPG)" in text
