import json
from datetime import UTC, datetime

import httpx
import pytest
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from clients.telegram_bot.config import BotConfig
from clients.telegram_bot.handlers.timelapse import _user_video_locks
from clients.telegram_bot.main import build_dispatcher
from clients.telegram_bot.tests.conftest import create_mock_api_client


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


@pytest.mark.asyncio
async def test_single_frame_timelapse_sent_as_photo(bot_config: BotConfig, mock_bot):
    """Когда плановый кадр только один, сервер возвращает image/jpeg, бот шлет фото."""
    _user_video_locks.clear()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/installations/1":
            return httpx.Response(
                200,
                json={
                    "id": 1,
                    "name": "Тестовая",
                    "cameras": [{"id": 1, "name": "Cam 1"}],
                },
            )
        if path == "/api/timelapse/1/video":
            fake_jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xdb"
            return httpx.Response(
                200,
                headers={"content-type": "image/jpeg"},
                content=fake_jpeg,
            )
        return httpx.Response(404)

    api_client = create_mock_api_client(bot_config, handler)
    dp = build_dispatcher(bot_config, api_client)

    cb = create_test_callback_query("inst:video:1")
    await dp.feed_update(mock_bot, Update(update_id=100, callback_query=cb))

    mock_bot.send_photo.assert_called_once()
    caption = mock_bot.send_photo.call_args[1]["caption"]
    assert "Плановый кадр пока один, видео не собрано" in caption


@pytest.mark.asyncio
async def test_video_under_50mb_sent_successfully(bot_config: BotConfig, mock_bot):
    """Когда видео укладывается в 50 МБ, бот отправляет его сразу (step = 1)."""
    _user_video_locks.clear()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/installations/2":
            return httpx.Response(
                200,
                json={
                    "id": 2,
                    "name": "Базилик",
                    "cameras": [{"id": 5, "name": "C270"}],
                },
            )
        if path == "/api/timelapse/2/video":
            body = json.loads(request.read().decode())
            assert body.get("step") == 1
            fake_mp4 = b"\x00\x00\x00\x18ftypmp42" + b"A" * 1024
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

    cb = create_test_callback_query("inst:video:2")
    await dp.feed_update(mock_bot, Update(update_id=101, callback_query=cb))

    mock_bot.send_video.assert_called_once()
    caption = mock_bot.send_video.call_args[1]["caption"]
    assert "Базилик" in caption
    assert "Таймлапс" in caption
    assert mock_bot.send_video.call_args[1]["supports_streaming"] is True


@pytest.mark.asyncio
async def test_video_decimation_cycle_until_fits_limit(bot_config: BotConfig, mock_bot):
    """Проверка цикла прореживания: при превышении 50 МБ запускаются шаги step=2, 4,...

    Имитируем:
    - step=1: 80 МБ (превышает)
    - step=2: 60 МБ (превышает)
    - step=4: 30 МБ (уложился!)
    """
    _user_video_locks.clear()
    requested_steps = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/installations/3":
            return httpx.Response(
                200,
                json={"id": 3, "name": "Томаты", "cameras": [{"id": 1, "name": "Cam"}]},
            )
        if path == "/api/timelapse/3/video":
            body = json.loads(request.read().decode())
            step = body.get("step")
            requested_steps.append(step)

            if step == 1:
                return httpx.Response(
                    200,
                    headers={"content-type": "video/mp4", "content-length": "80000000"},
                    content=b"",
                )
            if step == 2:
                return httpx.Response(
                    200,
                    headers={"content-type": "video/mp4", "content-length": "60000000"},
                    content=b"",
                )
            if step == 4:
                fake_mp4 = b"\x00\x00\x00\x18ftypmp42" + b"X" * 2048
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

    cb = create_test_callback_query("inst:video:3")
    await dp.feed_update(mock_bot, Update(update_id=102, callback_query=cb))

    assert requested_steps == [1, 2, 4]
    mock_bot.send_video.assert_called_once()
    caption = mock_bot.send_video.call_args[1]["caption"]
    assert "Прореженная версия: каждый 4-й кадр" in caption
    assert "snap video 3" in caption


@pytest.mark.asyncio
async def test_video_decimation_exhausted_recommends_cli(
    bot_config: BotConfig, mock_bot
):
    """Если все 5 циклов прореживания (вплоть до step=32) превышают 50 МБ, бот сообщает об этом."""
    _user_video_locks.clear()
    requested_steps = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/installations/4":
            return httpx.Response(
                200,
                json={
                    "id": 4,
                    "name": "Огромный таймлапс",
                    "cameras": [{"id": 1, "name": "Cam"}],
                },
            )
        if path == "/api/timelapse/4/video":
            body = json.loads(request.read().decode())
            step = body.get("step")
            requested_steps.append(step)
            return httpx.Response(
                200,
                headers={"content-type": "video/mp4", "content-length": "99000000"},
                content=b"",
            )
        return httpx.Response(404)

    api_client = create_mock_api_client(bot_config, handler)
    dp = build_dispatcher(bot_config, api_client)

    cb = create_test_callback_query("inst:video:4")
    await dp.feed_update(mock_bot, Update(update_id=103, callback_query=cb))

    # Должно быть 1 (полный) + 5 циклов: [1, 2, 4, 8, 16, 32]
    assert requested_steps == [1, 2, 4, 8, 16, 32]
    mock_bot.send_video.assert_not_called()

    # Проверяем, что в сообщении есть рекомендация CLI
    assert mock_bot.edit_message_text.called or mock_bot.send_message.called
    call_text = ""
    if mock_bot.edit_message_text.called:
        call_text = mock_bot.edit_message_text.call_args[1]["text"]
    elif mock_bot.send_message.called:
        call_text = mock_bot.send_message.call_args[1]["text"]

    assert (
        "Не удалось уложить видео в лимит Telegram (50 МБ) даже при каждом 32-м кадре"
        in call_text
    )
    assert "snap video 4" in call_text


@pytest.mark.asyncio
async def test_parallel_video_generation_protection(bot_config: BotConfig, mock_bot):
    """Защита от параллельного запуска у одного пользователя."""
    user_id = 555
    _user_video_locks.add(user_id)

    api_client = create_mock_api_client(bot_config, lambda r: httpx.Response(200))
    dp = build_dispatcher(bot_config, api_client)

    cb = create_test_callback_query("inst:video:1", user_id=user_id)
    await dp.feed_update(mock_bot, Update(update_id=104, callback_query=cb))

    mock_bot.answer_callback_query.assert_called_with(
        callback_query_id="query_100",
        text="Видео уже собирается, подождите",
        show_alert=True,
    )
    _user_video_locks.clear()
