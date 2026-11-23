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
async def test_sessions_menu_permissions_and_invite_flow(
    bot_config: BotConfig, mock_bot: MagicMock
):
    """Проверка раздела '👥 Сессии': отказ биологу, генерация и отзыв инвайт-кода для developer."""
    current_role = "biologist"
    invites_db = [
        {
            "id": 1,
            "code": "INV12345",
            "role": "biologist",
            "expires_at": "2026-11-22T22:00:00Z",
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth/me":
            return httpx.Response(200, json={"role": current_role, "login": "Tester"})
        if path == "/api/auth/invites" and request.method == "POST":
            new_inv = {
                "id": 2,
                "code": "NEWCODE9",
                "role": "biologist",
                "expires_at": "2026-11-22T22:10:00Z",
            }
            invites_db.append(new_inv)
            return httpx.Response(200, json=new_inv)
        if path == "/api/auth/invites" and request.method == "GET":
            return httpx.Response(200, json=invites_db)
        if path.startswith("/api/auth/invites/") and request.method == "DELETE":
            inv_id = int(path.split("/")[-1])
            invites_db[:] = [i for i in invites_db if i["id"] != inv_id]
            return httpx.Response(200, json={"status": "revoked"})
        return httpx.Response(404)

    api_client = create_mock_api_client(bot_config, handler)
    dp = build_dispatcher(bot_config, api_client)

    # 1. Биолог пробует открыть /sessions -> отказ
    msg_bio = create_test_message("/sessions", user_id=100)
    await dp.feed_update(mock_bot, Update(update_id=1, message=msg_bio))
    mock_bot.send_message.assert_called_once()
    assert (
        "доступен только для Разработчика" in mock_bot.send_message.call_args[1]["text"]
    )

    # 2. Разработчик открывает меню сессий
    current_role = "developer"
    mock_bot.send_message.reset_mock()
    msg_dev = create_test_message("👥 Сессии", user_id=200)
    await dp.feed_update(mock_bot, Update(update_id=2, message=msg_dev))
    mock_bot.send_message.assert_called_once()
    menu_text = mock_bot.send_message.call_args[1]["text"]
    assert "Управление сессиями и доступом" in menu_text

    # 3. Генерация инвайт-кода
    cb_create = create_test_callback_query("sess:invite_create", user_id=200)
    await dp.feed_update(mock_bot, Update(update_id=3, callback_query=cb_create))
    mock_bot.edit_message_text.assert_called_once()
    create_text = mock_bot.edit_message_text.call_args[1]["text"]
    assert "NEWCODE9" in create_text
    assert "10 минут" in create_text

    # 4. Список активных кодов
    mock_bot.edit_message_text.reset_mock()
    cb_list = create_test_callback_query("sess:invite_list", user_id=200)
    await dp.feed_update(mock_bot, Update(update_id=4, callback_query=cb_list))
    list_text = mock_bot.edit_message_text.call_args[1]["text"]
    assert "INV12345" in list_text
    assert "NEWCODE9" in list_text

    # 5. Отзыв кода
    mock_bot.send_message.reset_mock()
    cb_revoke = create_test_callback_query("sess:invite_revoke:1", user_id=200)
    await dp.feed_update(mock_bot, Update(update_id=5, callback_query=cb_revoke))
    mock_bot.send_message.assert_called_once()
    assert "Код доступа успешно отозван" in mock_bot.send_message.call_args[1]["text"]
    assert len(invites_db) == 1
    assert invites_db[0]["code"] == "NEWCODE9"


@pytest.mark.asyncio
async def test_sessions_list_and_terminate_flow(
    bot_config: BotConfig, mock_bot: MagicMock
):
    """Проверка просмотра списка сессий, карточки и принудительного завершения (кика)."""
    sessions_db = [
        {
            "id": 1,
            "client_type": "telegram",
            "external_id": "111222",
            "login": "Иван Биолог",
            "role": "biologist",
            "is_active": True,
            "authorized_at": "2026-11-22T10:00:00Z",
            "last_active_at": "2026-11-22T10:30:00Z",
            "logged_out_at": None,
        },
        {
            "id": 2,
            "client_type": "telegram",
            "external_id": "333444",
            "login": "Ольга Агроном",
            "role": "biologist",
            "is_active": False,
            "authorized_at": "2026-11-22T08:00:00Z",
            "last_active_at": "2026-11-22T08:20:00Z",
            "logged_out_at": "2026-11-22T08:25:00Z",
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth/me":
            return httpx.Response(200, json={"role": "developer", "login": "Dev"})
        if path == "/api/auth/sessions" and request.method == "GET":
            return httpx.Response(200, json=sessions_db)
        if path == "/api/auth/sessions/1/terminate" and request.method == "POST":
            sessions_db[0]["is_active"] = False
            sessions_db[0]["logged_out_at"] = "2026-11-22T10:35:00Z"
            return httpx.Response(200, json={"status": "terminated"})
        return httpx.Response(404)

    api_client = create_mock_api_client(bot_config, handler)
    dp = build_dispatcher(bot_config, api_client)

    # 1. Список сессий
    cb_list = create_test_callback_query("sess:list", user_id=200)
    await dp.feed_update(mock_bot, Update(update_id=10, callback_query=cb_list))
    mock_bot.edit_message_text.assert_called_once()
    list_text = mock_bot.edit_message_text.call_args[1]["text"]
    assert "🟢" in list_text
    assert "⚪️" in list_text
    assert '<a href="tg://user?id=111222">Иван Биолог</a>' in list_text

    # 2. Просмотр карточки сессии #1
    mock_bot.edit_message_text.reset_mock()
    cb_view = create_test_callback_query("sess:view:1", user_id=200)
    await dp.feed_update(mock_bot, Update(update_id=11, callback_query=cb_view))
    mock_bot.edit_message_text.assert_called_once()
    card_text = mock_bot.edit_message_text.call_args[1]["text"]
    assert "Карточка сессии #1" in card_text
    assert "🟢 <b>Активна</b>" in card_text
    kb = mock_bot.edit_message_text.call_args[1]["reply_markup"]
    assert "Завершить сессию" in kb.inline_keyboard[0][0].text

    # 3. Завершение сессии #1
    mock_bot.send_message.reset_mock()
    cb_term = create_test_callback_query("sess:terminate:1", user_id=200)
    await dp.feed_update(mock_bot, Update(update_id=12, callback_query=cb_term))
    mock_bot.send_message.assert_called_once()
    assert "Сессия #1 успешно завершена" in mock_bot.send_message.call_args[1]["text"]
    assert sessions_db[0]["is_active"] is False


@pytest.mark.asyncio
async def test_relay_toggle_switches_and_snap_layer_toggle(
    bot_config: BotConfig, mock_bot: MagicMock
):
    """Проверка переключателей реле (2 кнопки с отображением статуса) и переключения слоя кадра (raw/ann)."""
    from clients.telegram_bot.handlers.control import _relay_states

    _relay_states.clear()
    light_calls = []
    flash_calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth/me":
            return httpx.Response(200, json={"role": "developer", "login": "Dev"})
        if path == "/api/installations":
            return httpx.Response(200, json=[{"id": 1, "name": "Установка 1"}])
        if path == "/api/installations/1":
            return httpx.Response(200, json={"id": 1, "name": "Установка 1"})
        if path == "/api/system/status":
            return httpx.Response(200, json={"settings": {"debug_mode": False}})
        if path == "/api/control/light/main":
            body = json.loads(request.read().decode())
            light_calls.append(body)
            return httpx.Response(200, json={"result_message": "Свет изменён"})
        if path == "/api/control/light/flash":
            body = json.loads(request.read().decode())
            flash_calls.append(body)
            return httpx.Response(200, json={"result_message": "Вспышка изменена"})
        if path == "/api/control/captures/7":
            return httpx.Response(
                200,
                json={
                    "id": 7,
                    "installation_id": 1,
                    "raw_url": "/media/raw_7.jpg",
                    "annotated_url": "/media/ann_7.jpg",
                },
            )
        if path in ("/media/raw_7.jpg", "/media/ann_7.jpg"):
            return httpx.Response(200, content=b"\xff\xd8\xffimage\xff\xd9")
        return httpx.Response(404)

    api_client = create_mock_api_client(bot_config, handler)
    dp = build_dispatcher(bot_config, api_client)

    # 1. Открытие меню реле для установки 1
    cb_target = create_test_callback_query("ctrl:target:1")
    await dp.feed_update(mock_bot, Update(update_id=20, callback_query=cb_target))
    kb = mock_bot.edit_message_text.call_args[1]["reply_markup"]
    btn_light = kb.inline_keyboard[0][0]
    btn_flash = kb.inline_keyboard[0][1]
    assert "💡 Свет: ⚪️ ВЫКЛ" in btn_light.text
    assert "⚡ Вспышка: ⚪️ ВЫКЛ" in btn_flash.text

    # 2. Клик по кнопке включения света
    mock_bot.edit_message_text.reset_mock()
    cb_light = create_test_callback_query(btn_light.callback_data)
    await dp.feed_update(mock_bot, Update(update_id=21, callback_query=cb_light))
    assert len(light_calls) == 1
    assert light_calls[0]["state"] is True
    kb_after_light = mock_bot.edit_message_text.call_args[1]["reply_markup"]
    assert "💡 Свет: 🟢 ВКЛ" in kb_after_light.inline_keyboard[0][0].text

    # 3. Переключение слоя на ручном снимке (snap:layer:7:ann)
    cb_snap = create_test_callback_query("snap:layer:7:ann")
    await dp.feed_update(mock_bot, Update(update_id=22, callback_query=cb_snap))
    mock_bot.edit_message_media.assert_called_once()
    kb_snap = mock_bot.edit_message_media.call_args[1]["reply_markup"]
    assert "🖼 Исходник" in kb_snap.inline_keyboard[0][0].text


@pytest.mark.asyncio
async def test_timelapse_duration_preset_flow(
    bot_config: BotConfig, mock_bot: MagicMock
):
    """Проверка пошагового сценария таймлапса: выбор длительности видео и пресет."""
    video_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/installations/5":
            return httpx.Response(
                200,
                json={
                    "id": 5,
                    "name": "Тест Томаты",
                    "cameras": [{"id": 10, "name": "Cam"}],
                },
            )
        if path == "/api/timelapse/5/frames":
            # 20 кадров -> рекомендованная длительность 20 / 10 = 2 -> min 3 сек
            fake_frames = [{"capture_id": i, "camera_id": 10} for i in range(1, 21)]
            return httpx.Response(200, json={"frames": fake_frames})
        if path == "/api/timelapse/5/video":
            body = json.loads(request.read().decode())
            video_requests.append(body)
            fake_mp4 = b"\x00\x00\x00\x18ftypmp42" + b"X" * 1024
            return httpx.Response(
                200,
                headers={
                    "content-type": "video/mp4",
                    "content-length": str(len(fake_mp4)),
                },
                content=fake_mp4,
            )
        return httpx.Response(404)

    api_client = create_mock_api_client(bot_config, handler)
    dp = build_dispatcher(bot_config, api_client)

    # 1. Нажатие «Скачать видео» -> расчет под 30 FPS без пресетов
    cb_video = create_test_callback_query("inst:video:5")
    await dp.feed_update(mock_bot, Update(update_id=30, callback_query=cb_video))
    mock_bot.edit_message_text.assert_called_once()
    dur_text = mock_bot.edit_message_text.call_args[1]["text"]
    assert "Сборка таймлапса" in dur_text
    assert "Кадров для сборки: <b>20</b>" in dur_text
    assert "30 FPS" in dur_text
    kb = mock_bot.edit_message_text.call_args[1]["reply_markup"]
    assert "Собрать видео (30 FPS)" in kb.inline_keyboard[0][0].text

    # 2. Подтверждение сборки видео по кнопке с рассчитанной длительностью и 30 FPS
    cb_confirm = create_test_callback_query(kb.inline_keyboard[0][0].callback_data)
    await dp.feed_update(mock_bot, Update(update_id=33, callback_query=cb_confirm))

    assert len(video_requests) == 1
    assert video_requests[0]["fps"] == 30
    mock_bot.send_video.assert_called_once()
