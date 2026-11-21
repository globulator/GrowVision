import argparse
import asyncio
import json
import logging
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

# Добавляем корень проекта в sys.path
root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import (
    CallbackQuery,
    Chat,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardMarkup,
    Update,
    User,
)

from clients.telegram_bot.api_client import GrowVisionApiClient
from clients.telegram_bot.config import BotConfig
from clients.telegram_bot.main import build_dispatcher

logging.basicConfig(level=logging.ERROR)


class MockBotHelper:
    """Хелпер мок-бота для фиксации исходящих действий aiogram."""

    def __init__(self, bot_token: str = "123456:TEST_TOKEN"):
        self.bot = MagicMock(spec=Bot)
        self.bot.token = bot_token
        self.bot.id = 123456
        self.bot.parse_mode = ParseMode.HTML

        self.sent_messages: list[dict[str, Any]] = []
        self.sent_photos: list[dict[str, Any]] = []
        self.sent_videos: list[dict[str, Any]] = []
        self.sent_media_groups: list[dict[str, Any]] = []
        self.edited_messages: list[dict[str, Any]] = []
        self.deleted_messages: list[int] = []
        self.answered_callbacks: list[dict[str, Any]] = []

        self._message_counter = 1000

        async def _call(method: Any, **kwargs: Any) -> Any:
            api_method = getattr(method, "__api_method__", "")
            self._message_counter += 1
            chat_id = getattr(method, "chat_id", 12345)
            created_msg = Message(
                message_id=self._message_counter,
                date=datetime.now(UTC),
                chat=Chat(id=chat_id, type="private"),
                text=getattr(method, "text", "") or "mock",
            ).as_(self.bot)

            if api_method == "sendMessage":
                res = await self.bot.send_message(
                    chat_id=method.chat_id,
                    text=method.text,
                    reply_markup=getattr(method, "reply_markup", None),
                )
                return res or created_msg
            elif api_method == "sendPhoto":
                res = await self.bot.send_photo(
                    chat_id=method.chat_id,
                    photo=method.photo,
                    caption=getattr(method, "caption", None),
                )
                return res or created_msg
            elif api_method == "sendVideo":
                res = await self.bot.send_video(
                    chat_id=method.chat_id,
                    video=method.video,
                    caption=getattr(method, "caption", None),
                    supports_streaming=getattr(method, "supports_streaming", None),
                )
                return res or created_msg
            elif api_method == "sendMediaGroup":
                res = await self.bot.send_media_group(
                    chat_id=method.chat_id,
                    media=method.media,
                )
                return res or [created_msg]
            elif api_method == "deleteMessage":
                return await self.bot.delete_message(
                    chat_id=method.chat_id,
                    message_id=method.message_id,
                )
            elif api_method == "answerCallbackQuery":
                return await self.bot.answer_callback_query(
                    callback_query_id=method.callback_query_id,
                    text=getattr(method, "text", None),
                    show_alert=getattr(method, "show_alert", None),
                )
            elif api_method == "editMessageText":
                res = await self.bot.edit_message_text(
                    chat_id=getattr(method, "chat_id", None),
                    message_id=getattr(method, "message_id", None),
                    text=method.text,
                    reply_markup=getattr(method, "reply_markup", None),
                )
                return res or created_msg
            elif api_method == "editMessageMedia":
                res = await self.bot.edit_message_media(
                    chat_id=getattr(method, "chat_id", None),
                    message_id=getattr(method, "message_id", None),
                    media=method.media,
                    reply_markup=getattr(method, "reply_markup", None),
                )
                return res or created_msg
            return created_msg

        self.bot.side_effect = _call

        async def _record_send_message(*args: Any, **kwargs: Any) -> Message:
            self.sent_messages.append(kwargs)
            return Message(
                message_id=self._message_counter,
                date=datetime.now(UTC),
                chat=Chat(id=kwargs.get("chat_id", 12345), type="private"),
                text=kwargs.get("text", ""),
            ).as_(self.bot)

        async def _record_send_photo(*args: Any, **kwargs: Any) -> Message:
            self.sent_photos.append(kwargs)
            return Message(
                message_id=self._message_counter,
                date=datetime.now(UTC),
                chat=Chat(id=kwargs.get("chat_id", 12345), type="private"),
                text=kwargs.get("caption", ""),
            ).as_(self.bot)

        async def _record_send_video(*args: Any, **kwargs: Any) -> Message:
            self.sent_videos.append(kwargs)
            return Message(
                message_id=self._message_counter,
                date=datetime.now(UTC),
                chat=Chat(id=kwargs.get("chat_id", 12345), type="private"),
                text=kwargs.get("caption", ""),
            ).as_(self.bot)

        async def _record_send_media_group(*args: Any, **kwargs: Any) -> list[Message]:
            self.sent_media_groups.append(kwargs)
            msg = Message(
                message_id=self._message_counter,
                date=datetime.now(UTC),
                chat=Chat(id=kwargs.get("chat_id", 12345), type="private"),
                text="",
            ).as_(self.bot)
            return [msg, msg]

        async def _record_edit_message(*args: Any, **kwargs: Any) -> Message:
            self.edited_messages.append(kwargs)
            return Message(
                message_id=kwargs.get("message_id", self._message_counter),
                date=datetime.now(UTC),
                chat=Chat(id=kwargs.get("chat_id", 12345), type="private"),
                text=kwargs.get("text", ""),
            ).as_(self.bot)

        async def _record_delete_message(*args: Any, **kwargs: Any) -> bool:
            self.deleted_messages.append(kwargs.get("message_id", 0))
            return True

        async def _record_answer_callback(*args: Any, **kwargs: Any) -> bool:
            self.answered_callbacks.append(kwargs)
            return True

        self.bot.send_message = AsyncMock(side_effect=_record_send_message)
        self.bot.send_photo = AsyncMock(side_effect=_record_send_photo)
        self.bot.send_video = AsyncMock(side_effect=_record_send_video)
        self.bot.send_media_group = AsyncMock(side_effect=_record_send_media_group)
        self.bot.edit_message_text = AsyncMock(side_effect=_record_edit_message)
        self.bot.edit_message_media = AsyncMock(side_effect=_record_edit_message)
        self.bot.delete_message = AsyncMock(side_effect=_record_delete_message)
        self.bot.answer_callback_query = AsyncMock(side_effect=_record_answer_callback)
        self.bot.session = MagicMock()
        self.bot.session.close = AsyncMock()


def make_message(
    text: str, user_id: int = 12345, chat_id: int = 12345, message_id: int = 1
) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="Tester"),
        text=text,
    )


def make_callback(
    data: str,
    user_id: int = 12345,
    chat_id: int = 12345,
    message_id: int = 200,
) -> CallbackQuery:
    user = User(id=user_id, is_bot=False, first_name="Tester")
    chat = Chat(id=chat_id, type="private")
    msg = Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=chat,
        from_user=user,
        text="Контекстное сообщение",
    )
    return CallbackQuery(
        id=f"cq_{message_id}",
        from_user=user,
        chat_instance="ci_test",
        message=msg,
        data=data,
    )


async def run_scenario_auth_lifecycle(
    config: BotConfig, dev_pass: str
) -> dict[str, Any]:
    """Сценарий проверки авторизации: /start -> Гость -> /login -> пароль -> Developer -> profile -> logout."""
    mock = MockBotHelper(config.bot_token)
    api_client = GrowVisionApiClient(config)
    dp = build_dispatcher(config, api_client)
    user_id = 9911

    try:
        # 1. /start до авторизации
        msg1 = make_message("/start", user_id=user_id, message_id=1)
        await dp.feed_update(mock.bot, Update(update_id=1, message=msg1))

        assert len(mock.sent_messages) >= 1
        m1_text = mock.sent_messages[-1]["text"]
        m1_kb = mock.sent_messages[-1]["reply_markup"]
        assert "Гость" in m1_text
        assert isinstance(m1_kb, ReplyKeyboardMarkup)
        buttons_guest = [b.text for row in m1_kb.keyboard for b in row]
        assert "🔑 Войти" in buttons_guest
        assert "🌱 Установки" in buttons_guest
        assert "💡 Свет и реле" not in buttons_guest

        # 2. Нажимаем /login
        msg2 = make_message("/login", user_id=user_id, message_id=2)
        await dp.feed_update(mock.bot, Update(update_id=2, message=msg2))
        m2_text = mock.sent_messages[-1]["text"]
        assert "Шаг 1 из 2" in m2_text

        # 3. Вводим имя пользователя
        msg3 = make_message("Иван Разработчик", user_id=user_id, message_id=3)
        await dp.feed_update(mock.bot, Update(update_id=3, message=msg3))
        m3_text = mock.sent_messages[-1]["text"]
        assert "Шаг 2 из 2" in m3_text

        # 4. Вводим пароль (проверяем удаление сообщения с паролем!)
        pwd_msg_id = 4
        msg4 = make_message(dev_pass, user_id=user_id, message_id=pwd_msg_id)
        await dp.feed_update(mock.bot, Update(update_id=4, message=msg4))

        # Сообщение с паролем должно быть удалено немедленно
        assert pwd_msg_id in mock.deleted_messages

        m4_text = mock.sent_messages[-1]["text"]
        m4_kb = mock.sent_messages[-1]["reply_markup"]
        assert "успешно вошли" in m4_text
        assert "Разработчик" in m4_text
        assert isinstance(m4_kb, ReplyKeyboardMarkup)
        buttons_dev = [b.text for row in m4_kb.keyboard for b in row]
        assert "💡 Свет и реле" in buttons_dev
        assert "📷 Камеры" in buttons_dev
        assert "⚙️ Система" in buttons_dev
        assert "📸 Сделать снимок" in buttons_dev

        # 5. /start после авторизации
        msg5 = make_message("/start", user_id=user_id, message_id=5)
        await dp.feed_update(mock.bot, Update(update_id=5, message=msg5))
        m5_text = mock.sent_messages[-1]["text"]
        assert "Разработчик" in m5_text

        # 6. Профиль
        msg6 = make_message("👤 Профиль / Выход", user_id=user_id, message_id=6)
        await dp.feed_update(mock.bot, Update(update_id=6, message=msg6))
        m6_text = mock.sent_messages[-1]["text"]
        m6_ikb = mock.sent_messages[-1]["reply_markup"]
        assert "Разработчик" in m6_text
        assert isinstance(m6_ikb, InlineKeyboardMarkup)
        assert any(
            btn.callback_data == "auth:logout"
            for row in m6_ikb.inline_keyboard
            for btn in row
        )

        # 7. Выход из аккаунта
        cb7 = make_callback("auth:logout", user_id=user_id, message_id=7)
        await dp.feed_update(mock.bot, Update(update_id=7, callback_query=cb7))
        m7_text = mock.sent_messages[-1]["text"]
        assert "успешно вышли" in m7_text

        # 8. Проверка, что после выхода пользователь снова Гость
        msg8 = make_message("/start", user_id=user_id, message_id=8)
        await dp.feed_update(mock.bot, Update(update_id=8, message=msg8))
        m8_text = mock.sent_messages[-1]["text"]
        assert "Гость" in m8_text

        return {
            "status": "ok",
            "password_deleted": pwd_msg_id in mock.deleted_messages,
            "roles_switched_properly": True,
        }
    finally:
        await api_client.close()


async def run_scenario_installations_and_cameras(
    config: BotConfig, dev_pass: str, extra: dict[str, Any]
) -> dict[str, Any]:
    """Сценарий проверки пагинатора, карточек, сканирования и привязки камер."""
    mock = MockBotHelper(config.bot_token)
    api_client = GrowVisionApiClient(config)
    dp = build_dispatcher(config, api_client)
    user_id = 9912

    try:
        # Авторизуемся под Developer
        await api_client.login(
            user_id=user_id, username="Dev CamTester", password=dev_pass
        )

        # 1. Запрос меню '📷 Камеры'
        msg1 = make_message("📷 Камеры", user_id=user_id, message_id=11)
        await dp.feed_update(mock.bot, Update(update_id=11, message=msg1))
        m1_kb = mock.sent_messages[-1]["reply_markup"]
        assert isinstance(m1_kb, InlineKeyboardMarkup)
        # Проверяем наличие кнопки сканирования
        has_scan_btn = any(
            b.callback_data == "cam:scan" for row in m1_kb.inline_keyboard for b in row
        )
        assert has_scan_btn

        # 2. Сканирование камер: нажимаем 'cam:scan'
        cb2 = make_callback("cam:scan", user_id=user_id, message_id=12)
        await dp.feed_update(mock.bot, Update(update_id=12, callback_query=cb2))

        # 3. Привязка камеры к установке: выбираем cam:bind_pick:cam-bot-01
        cam_id = extra.get("camera_hardware_id", "cam-bot-01")
        inst_id = extra.get("installation_id", 1)

        cb3 = make_callback(f"cam:bind_pick:{cam_id}", user_id=user_id, message_id=13)
        await dp.feed_update(mock.bot, Update(update_id=13, callback_query=cb3))

        # Выбираем привязку к установке: cam:do_bind:<cam_id>:<inst_id>
        cb4 = make_callback(
            f"cam:do_bind:{cam_id}:{inst_id}", user_id=user_id, message_id=14
        )
        await dp.feed_update(mock.bot, Update(update_id=14, callback_query=cb4))

        # 4. Просмотр установок '🌱 Установки'
        msg5 = make_message("🌱 Установки", user_id=user_id, message_id=15)
        await dp.feed_update(mock.bot, Update(update_id=15, message=msg5))
        m5_text = mock.sent_messages[-1]["text"]
        assert "Установки" in m5_text or "установок" in m5_text
        m5_kb = mock.sent_messages[-1]["reply_markup"]
        assert isinstance(m5_kb, InlineKeyboardMarkup)

        # Проверяем размер всех callback_data < 64 байт!
        for row in m5_kb.inline_keyboard:
            for b in row:
                assert len(b.callback_data.encode("utf-8")) < 64, (
                    f"Callback data too long: {b.callback_data}"
                )

        # 5. Просмотр карточки установки: inst:view:1
        cb6 = make_callback(f"inst:view:{inst_id}", user_id=user_id, message_id=16)
        await dp.feed_update(mock.bot, Update(update_id=16, callback_query=cb6))

        return {"status": "ok", "callbacks_validated": True}
    finally:
        await api_client.close()


async def run_scenario_control_and_snap(
    config: BotConfig, dev_pass: str, extra: dict[str, Any]
) -> dict[str, Any]:
    """Сценарий проверки управления светом и ручного снимка snap с альбомом из 2 фото."""
    mock = MockBotHelper(config.bot_token)
    api_client = GrowVisionApiClient(config)
    dp = build_dispatcher(config, api_client)
    user_id = 9913

    try:
        # Авторизуемся под Developer
        await api_client.login(
            user_id=user_id, username="Dev SnapTester", password=dev_pass
        )

        inst_id = extra.get("installation_id", 1)

        # 1. Меню '💡 Свет и реле'
        msg1 = make_message("💡 Свет и реле", user_id=user_id, message_id=21)
        await dp.feed_update(mock.bot, Update(update_id=21, message=msg1))

        # Выбираем целевую установку ctrl:target:1
        cb2 = make_callback(f"ctrl:target:{inst_id}", user_id=user_id, message_id=22)
        await dp.feed_update(mock.bot, Update(update_id=22, callback_query=cb2))

        # Включаем свет: ctrl:light:1:on
        cb3 = make_callback(f"ctrl:light:{inst_id}:on", user_id=user_id, message_id=23)
        await dp.feed_update(mock.bot, Update(update_id=23, callback_query=cb3))

        # 2. Ручной снимок '📸 Сделать снимок'
        msg4 = make_message("📸 Сделать снимок", user_id=user_id, message_id=24)
        await dp.feed_update(mock.bot, Update(update_id=24, message=msg4))

        # Выбираем установку для съёмки snap:target:1
        cb5 = make_callback(f"snap:target:{inst_id}", user_id=user_id, message_id=25)
        await dp.feed_update(mock.bot, Update(update_id=25, callback_query=cb5))

        # Проверяем отправку медиагруппы (альбома из 2 фото)
        assert len(mock.sent_media_groups) >= 1, (
            f"Media group was not sent: {len(mock.sent_media_groups)}"
        )
        mg_call = mock.sent_media_groups[-1]
        media_list = mg_call["media"]
        assert len(media_list) == 2, f"Expected 2 photos, got {len(media_list)}"

        # Проверяем подпись первого фото альбома
        caption = getattr(media_list[0], "caption", "") or ""
        assert "Установка" in caption, f"Missing 'Установка' in caption: {caption}"
        assert "Камера" in caption, f"Missing 'Камера' in caption: {caption}"
        assert "Площадь" in caption, f"Missing 'Площадь' in caption: {caption}"

        return {
            "status": "ok",
            "media_groups_sent": len(mock.sent_media_groups),
            "caption": caption,
        }
    finally:
        await api_client.close()


async def run_scenario_timelapse_and_gallery(
    config: BotConfig, dev_pass: str, extra: dict[str, Any]
) -> dict[str, Any]:
    """Сценарий проверки экспорта фото при 1 кадре, защиты от параллельного запуска и галереи."""
    mock = MockBotHelper(config.bot_token)
    api_client = GrowVisionApiClient(config)
    dp = build_dispatcher(config, api_client)
    user_id = 9914

    try:
        # Авторизуемся под Developer
        await api_client.login(
            user_id=user_id, username="Dev TimelapseTester", password=dev_pass
        )

        inst_id = extra.get("installation_id", 1)
        cam_id = extra.get("camera_hardware_id", "cam-bot-01")

        # 1. Меню '🎬 Таймлапс (видео)'
        msg1 = make_message("🎬 Таймлапс (видео)", user_id=user_id, message_id=31)
        await dp.feed_update(mock.bot, Update(update_id=31, message=msg1))

        # 2. Выбор установки inst:video:1
        cb2 = make_callback(f"inst:video:{inst_id}", user_id=user_id, message_id=32)
        await dp.feed_update(mock.bot, Update(update_id=32, callback_query=cb2))

        # 3. Запуск сборки таймлапса vid_cam:1:cam-bot-01
        # Так как кадр ровно 1, бот должен отправить фотографию с пояснением!
        cb3 = make_callback(
            f"vid_cam:{inst_id}:{cam_id}", user_id=user_id, message_id=33
        )
        await dp.feed_update(mock.bot, Update(update_id=33, callback_query=cb3))

        assert len(mock.sent_photos) >= 1, (
            f"Expected 1 photo sent, got {len(mock.sent_photos)}"
        )
        photo_call = mock.sent_photos[-1]
        p_caption = photo_call.get("caption", "")
        assert (
            "кадр пока один" in p_caption.lower()
            or "видео не собрано" in p_caption.lower()
        )

        # 4. Просмотр галереи кадров: inst:frames:1 -> frm_cam:1:cam-bot-01
        cb4 = make_callback(f"inst:frames:{inst_id}", user_id=user_id, message_id=34)
        await dp.feed_update(mock.bot, Update(update_id=34, callback_query=cb4))

        cb5 = make_callback(
            f"frm_cam:{inst_id}:{cam_id}", user_id=user_id, message_id=35
        )
        await dp.feed_update(mock.bot, Update(update_id=35, callback_query=cb5))

        return {
            "status": "ok",
            "one_frame_photo_sent": True,
            "photo_caption": p_caption,
        }
    finally:
        await api_client.close()


async def run_scenario_system_status(
    config: BotConfig, dev_pass: str
) -> dict[str, Any]:
    """Сценарий проверки телеметрии Raspberry Pi 5 и меню системы."""
    mock = MockBotHelper(config.bot_token)
    api_client = GrowVisionApiClient(config)
    dp = build_dispatcher(config, api_client)
    user_id = 9915

    try:
        # Авторизуемся под Developer
        await api_client.login(
            user_id=user_id, username="Dev SysTester", password=dev_pass
        )

        # Отправляем сообщение '⚙️ Система'
        msg1 = make_message("⚙️ Система", user_id=user_id, message_id=41)
        await dp.feed_update(mock.bot, Update(update_id=41, message=msg1))

        assert len(mock.sent_messages) >= 1
        m_text = mock.sent_messages[-1]["text"]
        m_kb = mock.sent_messages[-1]["reply_markup"]

        # Проверяем наличие информации о RPi и телеметрии
        assert "Raspberry Pi" in m_text or "RPi" in m_text or "Телеметрия" in m_text
        assert isinstance(m_kb, InlineKeyboardMarkup)
        # Проверяем кнопки настройки интервала и debug_mode
        cb_datas = [
            b.callback_data
            for row in m_kb.inline_keyboard
            for b in row
            if b.callback_data
        ]
        assert "sys:edit_interval" in cb_datas
        assert "sys:toggle_debug" in cb_datas

        return {"status": "ok", "system_text": m_text}
    finally:
        await api_client.close()


async def run_scenario_headers_and_error_handling(
    config: BotConfig,
) -> dict[str, Any]:
    """Сценарий проверки маппинга ошибок (401 при неверном пароле) и обязательных заголовков."""
    mock = MockBotHelper(config.bot_token)
    api_client = GrowVisionApiClient(config)
    dp = build_dispatcher(config, api_client)
    user_id = 9916

    try:
        # Проверяем заголовки HTTP-клиента
        headers = api_client._headers(user_id=user_id)
        assert headers.get("X-Client-Key") == config.client_api_key
        assert headers.get("X-Client-Type") == "telegram"
        assert headers.get("X-External-Id") == str(user_id)

        # Пытаемся залогиниться с неверным паролем
        msg1 = make_message("/login", user_id=user_id, message_id=51)
        await dp.feed_update(mock.bot, Update(update_id=51, message=msg1))

        msg2 = make_message("AnyUser", user_id=user_id, message_id=52)
        await dp.feed_update(mock.bot, Update(update_id=52, message=msg2))

        msg3 = make_message("WRONG_PASSWORD_XYZ", user_id=user_id, message_id=53)
        await dp.feed_update(mock.bot, Update(update_id=53, message=msg3))

        # Должен быть вызван delete_message для сообщения с неверным паролем
        assert 53 in mock.deleted_messages

        # Бот должен ответить сообщением о неверном пароле
        m3_text = mock.sent_messages[-1]["text"]
        assert "Неверный пароль" in m3_text

        return {
            "status": "ok",
            "headers_valid": True,
            "auth_error_handled": True,
        }
    finally:
        await api_client.close()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--server-url", required=True)
    parser.add_argument("--client-key", required=True)
    parser.add_argument("--dev-pass", default="dev-pass-123")
    parser.add_argument("--bio-pass", default="bio-pass-123")
    parser.add_argument("--extra", default="{}")
    args = parser.parse_args()

    config = BotConfig(
        bot_token="123456:FAKE_TOKEN_RUNNER",
        server_url=args.server_url,
        client_api_key=args.client_key,
        web_public_url=None,
    )
    extra_data = json.loads(args.extra)

    try:
        if args.scenario == "auth_lifecycle":
            res = await run_scenario_auth_lifecycle(config, args.dev_pass)
        elif args.scenario == "installations_and_cameras":
            res = await run_scenario_installations_and_cameras(
                config, args.dev_pass, extra_data
            )
        elif args.scenario == "control_and_snap":
            res = await run_scenario_control_and_snap(config, args.dev_pass, extra_data)
        elif args.scenario == "timelapse_and_gallery":
            res = await run_scenario_timelapse_and_gallery(
                config, args.dev_pass, extra_data
            )
        elif args.scenario == "system_status":
            res = await run_scenario_system_status(config, args.dev_pass)
        elif args.scenario == "headers_and_error_handling":
            res = await run_scenario_headers_and_error_handling(config)
        else:
            raise ValueError(f"Unknown scenario: {args.scenario}")

        print(json.dumps({"success": True, "result": res}))
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        print(
            json.dumps(
                {
                    "success": False,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }
            )
        )
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
