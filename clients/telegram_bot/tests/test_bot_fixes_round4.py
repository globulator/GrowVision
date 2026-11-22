from unittest.mock import MagicMock

import httpx
import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Update

from clients.telegram_bot.config import BotConfig
from clients.telegram_bot.main import build_dispatcher
from clients.telegram_bot.states import (
    CaptureDelayStates,
    InstallationCreateStates,
)
from clients.telegram_bot.tests.test_handlers_installations_cameras_control import (
    create_mock_api_client,
    create_test_callback_query,
    create_test_message,
)
from clients.telegram_bot.utils.formatters import format_debug_block


@pytest.mark.asyncio
async def test_bot_installation_cameras_button(
    bot_config: BotConfig, mock_bot: MagicMock
):
    """Кнопка inst:cameras:{id} выводит список привязанных онлайн/оффлайн камер и кнопку возврата."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth/me":
            return httpx.Response(200, json={"role": "developer", "username": "Dev"})
        if path == "/api/installations/1":
            return httpx.Response(
                200,
                json={
                    "id": 1,
                    "name": "Бокс-1",
                    "culture_name": "Базилик",
                    "cameras": [
                        {
                            "id": 10,
                            "auto_name": "Камера USB-1",
                            "hardware_id": "cam-1",
                            "is_online": True,
                        },
                        {
                            "id": 11,
                            "auto_name": "Камера CSI-2",
                            "hardware_id": "cam-2",
                            "is_online": False,
                        },
                    ],
                },
            )
        return httpx.Response(404)

    api_client = create_mock_api_client(bot_config, handler)
    dp = build_dispatcher(bot_config, api_client)

    cb = create_test_callback_query("inst:cameras:1")
    await dp.feed_update(mock_bot, Update(update_id=101, callback_query=cb))

    mock_bot.edit_message_text.assert_called_once()
    text = mock_bot.edit_message_text.call_args[1]["text"]
    assert "Бокс-1" in text
    assert "🟢" in text
    assert "Камера USB-1" in text
    assert "🔴" in text
    assert "Камера CSI-2" in text
    assert "оффлайн" in text

    # Проверяем наличие кнопок возврата к установке и перехода к камерам
    kb = mock_bot.edit_message_text.call_args[1]["reply_markup"]
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "cam:view:10" in callbacks
    assert "cam:view:11" in callbacks
    assert "inst:view:1" in callbacks


@pytest.mark.asyncio
async def test_bot_noop_stubs(bot_config: BotConfig, mock_bot: MagicMock):
    """Заглушки frm:noop, pag:noop отвечают callback.answer() без ошибок."""
    api_client = create_mock_api_client(bot_config, lambda req: httpx.Response(200))
    dp = build_dispatcher(bot_config, api_client)

    for cb_data in ["pag:noop", "frm:noop"]:
        cb = create_test_callback_query(cb_data)
        await dp.feed_update(mock_bot, Update(update_id=102, callback_query=cb))
        mock_bot.answer_callback_query.assert_called()


@pytest.mark.asyncio
async def test_bot_frames_gallery_roles(bot_config: BotConfig, mock_bot: MagicMock):
    """Гость видит только последний кадр без листания; биолог видит галерею с пагинатором."""
    current_role = "guest"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth/me":
            return httpx.Response(
                200, json={"role": current_role, "username": "Tester"}
            )
        if path == "/api/installations/1":
            return httpx.Response(200, json={"id": 1, "name": "Установка Ромашка"})
        if path == "/api/timelapse/1/frames":
            # Сервер возвращает {"installation_id": 1, "frames": [...]}
            return httpx.Response(
                200,
                json={
                    "installation_id": 1,
                    "frames": [
                        {
                            "id": 100,
                            "camera_id": 1,
                            "captured_at": "2026-11-20T10:00:00",
                            "raw_url": "/media/frame1.jpg",
                            "total_area_cm2": 15.0,
                        },
                        {
                            "id": 101,
                            "camera_id": 1,
                            "captured_at": "2026-11-20T12:00:00",
                            "raw_url": "/media/frame2.jpg",
                            "total_area_cm2": 25.0,
                        },
                    ],
                },
            )
        if path in ("/media/frame1.jpg", "/media/frame2.jpg"):
            return httpx.Response(200, content=b"\xff\xd8\xfffake_jpeg\xff\xd9")
        return httpx.Response(404)

    api_client = create_mock_api_client(bot_config, handler)
    dp = build_dispatcher(bot_config, api_client)

    # 1. Проверяем guest
    cb_guest = create_test_callback_query("inst:frames:1")
    await dp.feed_update(mock_bot, Update(update_id=103, callback_query=cb_guest))

    mock_bot.send_photo.assert_called_once()
    caption_guest = mock_bot.send_photo.call_args[1]["caption"]
    assert "Последний плановый кадр" in caption_guest
    assert "Полная галерея доступна биологам и разработчикам" in caption_guest
    kb_guest = mock_bot.send_photo.call_args[1]["reply_markup"]
    callbacks_guest = [
        btn.callback_data for row in kb_guest.inline_keyboard for btn in row
    ]
    assert callbacks_guest == ["frm:close"]

    # 2. Проверяем biologist
    current_role = "biologist"
    mock_bot.send_photo.reset_mock()

    cb_bio = create_test_callback_query("inst:frames:1")
    await dp.feed_update(mock_bot, Update(update_id=104, callback_query=cb_bio))

    mock_bot.send_photo.assert_called_once()
    caption_bio = mock_bot.send_photo.call_args[1]["caption"]
    assert "Кадр 2 из 2" in caption_bio
    kb_bio = mock_bot.send_photo.call_args[1]["reply_markup"]
    callbacks_bio = [btn.callback_data for row in kb_bio.inline_keyboard for btn in row]
    assert "frm:0:r" in callbacks_bio
    assert "frm:close" in callbacks_bio


def test_format_debug_block_with_log_line():
    """format_debug_block форматирует команду и строку лога с html-экранированием."""
    debug_data = {
        "command": "relay_on <pin 17>",
        "log_line": "[DEBUG] Pin 17 set HIGH & verified",
    }
    block = format_debug_block(debug_data.get("command"), debug_data.get("log_line"))
    assert "🐞 <b>DEBUG:</b>" in block
    assert "Команда:" in block
    assert "relay_on &lt;pin 17&gt;" in block
    assert "Лог:" in block
    assert "[DEBUG] Pin 17 set HIGH &amp; verified" in block


@pytest.mark.asyncio
async def test_gpio_pin_validation_wizard_and_edit(
    bot_config: BotConfig, mock_bot: MagicMock
):
    """Валидация GPIO 0..53 в мастере создания установки: некорректный ввод не сбрасывает FSM."""
    api_client = create_mock_api_client(bot_config, lambda req: httpx.Response(200))
    storage = MemoryStorage()
    dp = build_dispatcher(bot_config, api_client)
    dp.fsm.storage = storage

    state = FSMContext(
        storage=storage,
        key=StorageKey(bot_id=mock_bot.id, chat_id=12345, user_id=12345),
    )

    # 1. Шаг waiting_light_pin
    await state.set_state(InstallationCreateStates.waiting_light_pin)

    # Некорректный ввод: число > 53
    msg_bad = create_test_message("54")
    await dp.feed_update(mock_bot, Update(update_id=105, message=msg_bad))
    assert await state.get_state() == InstallationCreateStates.waiting_light_pin
    mock_bot.send_message.assert_called()
    assert "от 0 до 53" in mock_bot.send_message.call_args[1]["text"]

    # Корректный ввод: 17
    mock_bot.send_message.reset_mock()
    msg_good = create_test_message("17")
    await dp.feed_update(mock_bot, Update(update_id=106, message=msg_good))
    assert await state.get_state() == InstallationCreateStates.waiting_flash_pin
    data = await state.get_data()
    assert data["light_gpio_pin"] == 17

    # 2. Шаг waiting_flash_pin
    msg_bad_flash = create_test_message("-1")
    await dp.feed_update(mock_bot, Update(update_id=107, message=msg_bad_flash))
    assert await state.get_state() == InstallationCreateStates.waiting_flash_pin
    assert "от 0 до 53" in mock_bot.send_message.call_args[1]["text"]

    msg_good_flash = create_test_message("27")
    await dp.feed_update(mock_bot, Update(update_id=108, message=msg_good_flash))
    assert await state.get_state() == InstallationCreateStates.waiting_aruco
    data = await state.get_data()
    assert data["flash_gpio_pin"] == 27


@pytest.mark.asyncio
async def test_archived_installations_filtering(
    bot_config: BotConfig, mock_bot: MagicMock
):
    """Архив установок показывает только элементы с is_archived=True."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth/me":
            return httpx.Response(200, json={"role": "developer", "username": "Dev"})
        if path == "/api/installations":
            return httpx.Response(
                200,
                json=[
                    {"id": 1, "name": "Активная 1", "is_archived": False},
                    {"id": 2, "name": "Архивная 2", "is_archived": True},
                ],
            )
        return httpx.Response(404)

    api_client = create_mock_api_client(bot_config, handler)
    dp = build_dispatcher(bot_config, api_client)

    cb = create_test_callback_query("pag:inst:0:arc")
    await dp.feed_update(mock_bot, Update(update_id=109, callback_query=cb))

    mock_bot.edit_message_text.assert_called_once()
    kb = mock_bot.edit_message_text.call_args[1]["reply_markup"]
    button_texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("Архивная 2" in t for t in button_texts)
    assert not any("Активная 1" in t for t in button_texts)


@pytest.mark.asyncio
async def test_capture_delay_keyboard_and_validation(
    bot_config: BotConfig, mock_bot: MagicMock
):
    """Настройка задержки съёмки: отображение клавиатуры и валидация 0.0..30.0 с без сброса FSM."""
    patch_payload = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth/me":
            return httpx.Response(200, json={"role": "developer", "username": "Dev"})
        if path == "/api/installations/1":
            if request.method == "PATCH":
                nonlocal patch_payload
                patch_payload = request.read().decode()
                return httpx.Response(
                    200, json={"id": 1, "name": "Бокс-1", "capture_delay_sec": 2.5}
                )
            return httpx.Response(
                200, json={"id": 1, "name": "Бокс-1", "capture_delay_sec": 1.5}
            )
        return httpx.Response(404)

    api_client = create_mock_api_client(bot_config, handler)
    storage = MemoryStorage()
    dp = build_dispatcher(bot_config, api_client)
    dp.fsm.storage = storage

    state = FSMContext(
        storage=storage,
        key=StorageKey(bot_id=mock_bot.id, chat_id=12345, user_id=12345),
    )

    # 1. Открываем клавиатуру задержки для установки
    cb_delay = create_test_callback_query("inst:delay:1")
    await dp.feed_update(mock_bot, Update(update_id=110, callback_query=cb_delay))

    mock_bot.edit_message_text.assert_called()
    kb = mock_bot.edit_message_text.call_args[1]["reply_markup"]
    button_texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("0.5 с" in t for t in button_texts)
    assert any("1.5 с" in t for t in button_texts)
    assert any("3.0 с" in t for t in button_texts)
    assert any("5.0 с" in t for t in button_texts)
    assert any("Использовать общую" in t for t in button_texts)

    # 2. Проверяем валидацию FSM при ручном вводе
    await state.set_state(CaptureDelayStates.waiting_delay)
    await state.update_data(target_type="installation", target_id=1)

    # Некорректный ввод: отрицательное число
    msg_bad = create_test_message("-1.0")
    await dp.feed_update(mock_bot, Update(update_id=111, message=msg_bad))
    assert await state.get_state() == CaptureDelayStates.waiting_delay
    assert "от 0.0 до 30.0" in mock_bot.send_message.call_args[1]["text"]

    # Некорректный ввод: больше 30
    msg_bad2 = create_test_message("31.5")
    await dp.feed_update(mock_bot, Update(update_id=112, message=msg_bad2))
    assert await state.get_state() == CaptureDelayStates.waiting_delay

    # Корректный ввод: 2.5
    msg_good = create_test_message("2.5")
    await dp.feed_update(mock_bot, Update(update_id=113, message=msg_good))
    assert await state.get_state() is None
    assert '"capture_delay_sec":2.5' in patch_payload.replace(" ", "")
