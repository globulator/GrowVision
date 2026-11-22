import argparse
import asyncio
import contextlib
import json
import logging
import sys
import time
from typing import Any

from aiohttp import web

# ID и имя фейкового бота
FAKE_BOT_ID = 99999999
FAKE_BOT_USERNAME = "GrowVisionBot"

logger = logging.getLogger("fake_telegram")


class FakeTelegramServer:
    """Легковесный HTTP-сервер фейкового Telegram Bot API на базе aiohttp.

    Эмулирует поведение реального Telegram Bot API:
    - В объектах callback_query поле callback.message.from_user — это бот (id=FAKE_BOT_ID, is_bot=True).
    - В поле callback.from_user — это реальный пользователь.
    - Очередь getUpdates для доставки сообщений боту в режиме polling.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self.host = host
        self.port = port
        self.app = web.Application()
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None

        self._upd_counter = 100
        self._msg_counter = 1000

        self.updates_queue: list[dict[str, Any]] = []
        self.sent_messages: list[dict[str, Any]] = []
        self.edited_messages: list[dict[str, Any]] = []
        self.all_responses: list[dict[str, Any]] = []
        self.deleted_messages: list[int] = []
        self.answered_callbacks: list[dict[str, Any]] = []
        self.registered_commands: list[dict[str, Any]] = []

        self._setup_routes()

    def _setup_routes(self) -> None:
        self.app.router.add_post("/bot{token}/getMe", self._handle_get_me)
        self.app.router.add_post(
            "/bot{token}/setMyCommands", self._handle_set_my_commands
        )
        self.app.router.add_post(
            "/bot{token}/getMyCommands", self._handle_get_my_commands
        )
        self.app.router.add_post("/bot{token}/getUpdates", self._handle_get_updates)
        self.app.router.add_post("/bot{token}/sendMessage", self._handle_send_message)
        self.app.router.add_post(
            "/bot{token}/editMessageText", self._handle_edit_message_text
        )
        self.app.router.add_post(
            "/bot{token}/deleteMessage", self._handle_delete_message
        )
        self.app.router.add_post(
            "/bot{token}/answerCallbackQuery", self._handle_answer_callback
        )
        self.app.router.add_post("/bot{token}/sendPhoto", self._handle_send_photo)
        self.app.router.add_post("/bot{token}/sendVideo", self._handle_send_video)
        self.app.router.add_post(
            "/bot{token}/sendMediaGroup", self._handle_send_media_group
        )

    def _next_upd_id(self) -> int:
        self._upd_counter += 1
        return self._upd_counter

    def _next_msg_id(self) -> int:
        self._msg_counter += 1
        return self._msg_counter

    async def _extract_data(self, request: web.Request) -> dict[str, Any]:
        """Универсально извлекает JSON или FormData из запроса."""
        if request.content_type == "application/json":
            try:
                return await request.json()
            except Exception:  # noqa: BLE001
                return {}
        elif request.content_type in (
            "application/x-www-form-urlencoded",
            "multipart/form-data",
        ):
            post_data = await request.post()
            res = {}
            for k, v in post_data.items():
                if isinstance(v, str):
                    try:
                        res[k] = json.loads(v)
                    except Exception:  # noqa: BLE001
                        res[k] = v
                else:
                    res[k] = v
            return res
        return {}

    async def _handle_get_me(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "ok": True,
                "result": {
                    "id": FAKE_BOT_ID,
                    "is_bot": True,
                    "first_name": "GrowVisionBot",
                    "username": FAKE_BOT_USERNAME,
                    "can_join_groups": False,
                    "can_read_all_group_messages": False,
                    "supports_inline_queries": False,
                },
            }
        )

    async def _handle_set_my_commands(self, request: web.Request) -> web.Response:
        data = await self._extract_data(request)
        cmds = data.get("commands", [])
        self.registered_commands = cmds
        return web.json_response({"ok": True, "result": True})

    async def _handle_get_my_commands(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "result": self.registered_commands})

    async def _handle_get_updates(self, request: web.Request) -> web.Response:
        data = await self._extract_data(request)
        offset = int(data.get("offset", 0))

        # Удаляем апдейты, подтвержденные оффсетом
        if offset > 0:
            self.updates_queue = [
                u for u in self.updates_queue if u["update_id"] >= offset
            ]

        res = list(self.updates_queue)
        return web.json_response({"ok": True, "result": res})

    async def _handle_send_message(self, request: web.Request) -> web.Response:
        data = await self._extract_data(request)
        msg_id = self._next_msg_id()
        chat_id = data.get("chat_id", 12345)
        text = data.get("text", "")
        reply_markup = data.get("reply_markup")

        # В объекте Message поле reply_markup может быть только InlineKeyboardMarkup
        msg_reply_markup = None
        if isinstance(reply_markup, dict) and "inline_keyboard" in reply_markup:
            msg_reply_markup = reply_markup

        msg_obj = {
            "message_id": msg_id,
            "date": int(time.time()),
            "chat": {"id": int(chat_id), "type": "private"},
            "from": {
                "id": FAKE_BOT_ID,
                "is_bot": True,
                "first_name": "GrowVisionBot",
                "username": FAKE_BOT_USERNAME,
            },
            "text": text,
            "reply_markup": msg_reply_markup,
        }
        self.sent_messages.append(msg_obj)
        self.all_responses.append(msg_obj)
        return web.json_response({"ok": True, "result": msg_obj})

    async def _handle_edit_message_text(self, request: web.Request) -> web.Response:
        data = await self._extract_data(request)
        msg_id = int(data.get("message_id", self._next_msg_id()))
        chat_id = data.get("chat_id", 12345)
        text = data.get("text", "")
        reply_markup = data.get("reply_markup")

        # В объекте Message поле reply_markup может быть только InlineKeyboardMarkup
        msg_reply_markup = None
        if isinstance(reply_markup, dict) and "inline_keyboard" in reply_markup:
            msg_reply_markup = reply_markup

        msg_obj = {
            "message_id": msg_id,
            "date": int(time.time()),
            "chat": {"id": int(chat_id), "type": "private"},
            "from": {
                "id": FAKE_BOT_ID,
                "is_bot": True,
                "first_name": "GrowVisionBot",
                "username": FAKE_BOT_USERNAME,
            },
            "text": text,
            "reply_markup": msg_reply_markup,
        }
        self.edited_messages.append(msg_obj)
        self.all_responses.append(msg_obj)
        return web.json_response({"ok": True, "result": msg_obj})

    async def _handle_delete_message(self, request: web.Request) -> web.Response:
        data = await self._extract_data(request)
        msg_id = int(data.get("message_id", 0))
        self.deleted_messages.append(msg_id)
        return web.json_response({"ok": True, "result": True})

    async def _handle_answer_callback(self, request: web.Request) -> web.Response:
        data = await self._extract_data(request)
        self.answered_callbacks.append(data)
        return web.json_response({"ok": True, "result": True})

    async def _handle_send_photo(self, request: web.Request) -> web.Response:
        data = await self._extract_data(request)
        msg_id = self._next_msg_id()
        msg_obj = {
            "message_id": msg_id,
            "date": int(time.time()),
            "chat": {
                "id": int(data.get("chat_id", 12345)),
                "type": "private",
            },
            "from": {
                "id": FAKE_BOT_ID,
                "is_bot": True,
                "first_name": "GrowVisionBot",
                "username": FAKE_BOT_USERNAME,
            },
            "caption": data.get("caption", ""),
            "photo": [{"file_id": "test_photo_id", "width": 100, "height": 100}],
        }
        self.sent_messages.append(msg_obj)
        return web.json_response({"ok": True, "result": msg_obj})

    async def _handle_send_video(self, request: web.Request) -> web.Response:
        data = await self._extract_data(request)
        msg_id = self._next_msg_id()
        msg_obj = {
            "message_id": msg_id,
            "date": int(time.time()),
            "chat": {
                "id": int(data.get("chat_id", 12345)),
                "type": "private",
            },
            "from": {
                "id": FAKE_BOT_ID,
                "is_bot": True,
                "first_name": "GrowVisionBot",
                "username": FAKE_BOT_USERNAME,
            },
            "caption": data.get("caption", ""),
            "video": {"file_id": "test_video_id", "duration": 10},
        }
        self.sent_messages.append(msg_obj)
        return web.json_response({"ok": True, "result": msg_obj})

    async def _handle_send_media_group(self, request: web.Request) -> web.Response:
        data = await self._extract_data(request)
        msg_id = self._next_msg_id()
        msg_obj = {
            "message_id": msg_id,
            "date": int(time.time()),
            "chat": {
                "id": int(data.get("chat_id", 12345)),
                "type": "private",
            },
            "from": {
                "id": FAKE_BOT_ID,
                "is_bot": True,
                "first_name": "GrowVisionBot",
                "username": FAKE_BOT_USERNAME,
            },
        }
        self.sent_messages.append(msg_obj)
        return web.json_response({"ok": True, "result": [msg_obj]})

    # ================== Методы эмуляции пользователя ==================

    def enqueue_user_message(
        self,
        user_id: int,
        text: str,
        first_name: str = "TestUser",
    ) -> int:
        """Ставит в очередь входящее текстовое сообщение от пользователя."""
        msg_id = self._next_msg_id()
        upd = {
            "update_id": self._next_upd_id(),
            "message": {
                "message_id": msg_id,
                "date": int(time.time()),
                "chat": {"id": user_id, "type": "private"},
                "from": {
                    "id": user_id,
                    "is_bot": False,
                    "first_name": first_name,
                    "username": f"user_{user_id}",
                },
                "text": text,
            },
        }
        self.updates_queue.append(upd)
        return msg_id

    def enqueue_callback_button(
        self,
        user_id: int,
        callback_data: str,
        target_message_id: int = 100,
        target_message_text: str = "",
        first_name: str = "TestUser",
    ) -> str:
        """Ставит в очередь нажатие inline-кнопки пользователем.

        ВАЖНО:
        В объекте callback_query:
        - callback_query.from = пользователь (id=user_id, is_bot=False)
        - callback_query.message.from = БОТ (id=FAKE_BOT_ID, is_bot=True)!
        """
        cq_id = f"cq_{self._next_upd_id()}"
        upd = {
            "update_id": self._next_upd_id(),
            "callback_query": {
                "id": cq_id,
                "chat_instance": f"ci_{user_id}",
                "from": {
                    "id": user_id,
                    "is_bot": False,
                    "first_name": first_name,
                    "username": f"user_{user_id}",
                },
                "message": {
                    "message_id": target_message_id,
                    "date": int(time.time()),
                    "chat": {"id": user_id, "type": "private"},
                    "from": {
                        "id": FAKE_BOT_ID,
                        "is_bot": True,
                        "first_name": "GrowVisionBot",
                        "username": FAKE_BOT_USERNAME,
                    },
                    "text": target_message_text,
                },
                "data": callback_data,
            },
        }
        self.updates_queue.append(upd)
        return cq_id

    async def wait_for_new_bot_response(
        self, previous_count: int, timeout: float = 3.0
    ) -> dict[str, Any]:
        """Ожидает появления нового ответа бота (sendMessage или editMessageText)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if len(self.all_responses) > previous_count:
                return self.all_responses[-1]
            await asyncio.sleep(0.02)
        raise TimeoutError(
            f"Бот не ответил за {timeout} секунд (ответов было: {previous_count}, стало: {len(self.all_responses)})"
        )

    def get_total_responses_count(self) -> int:
        return len(self.all_responses)

    def get_last_response(self) -> dict[str, Any] | None:
        return self.all_responses[-1] if self.all_responses else None

    # ================== Жизненный цикл ==================

    async def start(self) -> str:
        """Запускает HTTP-сервер фейкового Telegram Bot API и возвращает base_url."""
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()
        # Получаем фактически выделенный порт
        sockets = self.site._server.sockets
        if sockets:
            self.port = sockets[0].getsockname()[1]
        return f"http://{self.host}:{self.port}"

    async def stop(self) -> None:
        """Останавливает сервер."""
        if self.runner:
            await self.runner.cleanup()


# ================== Сценарии E2E тестирования ==================


async def _login_dev(fake_tg: FakeTelegramServer, user_id: int, dev_pass: str) -> None:
    """Двухэтапная авторизация разработчика в боте."""
    cnt = fake_tg.get_total_responses_count()
    fake_tg.enqueue_user_message(user_id, "/login")
    await fake_tg.wait_for_new_bot_response(cnt)

    # Шаг 1: ввод логина
    cnt = fake_tg.get_total_responses_count()
    fake_tg.enqueue_user_message(user_id, "DevUser")
    await fake_tg.wait_for_new_bot_response(cnt)

    # Шаг 2: ввод пароля
    cnt = fake_tg.get_total_responses_count()
    fake_tg.enqueue_user_message(user_id, dev_pass)
    resp = await fake_tg.wait_for_new_bot_response(cnt)
    assert "успешно вошли" in resp["text"]


async def run_scenario(
    scenario: str,
    server_url: str,
    client_key: str,
    dev_pass: str = "dev-pass-123",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Запускает фейковый Telegram-сервер и сценарий бота."""
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.client.session.aiohttp import AiohttpSession
    from aiogram.client.telegram import TelegramAPIServer
    from aiogram.enums import ParseMode

    from clients.telegram_bot.api_client import GrowVisionApiClient
    from clients.telegram_bot.config import BotConfig
    from clients.telegram_bot.main import build_dispatcher, setup_bot_commands

    fake_tg = FakeTelegramServer()
    base_url = await fake_tg.start()

    bot_token = "123456:FAKE_TOKEN"
    api_server = TelegramAPIServer.from_base(base_url)
    session = AiohttpSession(api=api_server, timeout=10.0)
    bot = Bot(
        token=bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    bot_config = BotConfig(
        bot_token=bot_token,
        server_url=server_url,
        client_api_key=client_key,
        web_public_url="http://127.0.0.1:8000",
        debug_log=True,
    )
    api_client = GrowVisionApiClient(bot_config)
    dp = build_dispatcher(bot_config, api_client)

    # Устанавливаем системные команды
    await setup_bot_commands(bot)
    assert len(fake_tg.registered_commands) == 5

    # Запускаем polling в фоновом таске
    polling_task = asyncio.create_task(dp.start_polling(bot, handle_as_tasks=False))

    user_id = 10001
    result: dict[str, Any] = {"ok": False}

    try:
        # Даем боту запуститься
        await asyncio.sleep(0.05)

        if scenario == "profile_and_dates":
            # 1. Логин Developer
            await _login_dev(fake_tg, user_id, dev_pass)

            # 2. Просмотр профиля
            cnt = fake_tg.get_total_responses_count()
            fake_tg.enqueue_user_message(user_id, "/profile")
            resp = await fake_tg.wait_for_new_bot_response(cnt)
            p_text = resp["text"]

            assert "Профиль пользователя" in p_text
            assert "Разработчик" in p_text
            assert "Время входа:</b>" in p_text
            assert "Последняя активность:</b>" in p_text
            assert "—" not in p_text
            result = {"ok": True, "profile_text": p_text}

        elif scenario == "installation_create_name_validation_and_aruco_default":
            # 1. Логин Developer
            await _login_dev(fake_tg, user_id, dev_pass)

            # 2. Нажимаем "➕ Создать установку"
            cnt = fake_tg.get_total_responses_count()
            fake_tg.enqueue_callback_button(
                user_id,
                "inst:create",
                target_message_id=200,
                target_message_text="Список",
            )
            resp = await fake_tg.wait_for_new_bot_response(cnt)
            assert "Шаг 1 из 5" in resp["text"]

            # 3. Шаг 1: ввод дубликата имени
            dup_name = (extra or {}).get("existing_name", "Установка 1")
            cnt = fake_tg.get_total_responses_count()
            fake_tg.enqueue_user_message(user_id, dup_name)
            resp = await fake_tg.wait_for_new_bot_response(cnt)
            assert "уже занято" in resp["text"]
            assert "Шаг 2" not in resp["text"]

            # 4. Шаг 1: ввод уникального имени
            cnt = fake_tg.get_total_responses_count()
            fake_tg.enqueue_user_message(user_id, "Уникальный Базилик E2E")
            resp = await fake_tg.wait_for_new_bot_response(cnt)
            assert "Шаг 2 из 5" in resp["text"]

            # 5. Шаг 2: культура
            cnt = fake_tg.get_total_responses_count()
            fake_tg.enqueue_user_message(user_id, "Базилик Изумрудный")
            resp = await fake_tg.wait_for_new_bot_response(cnt)
            assert "Шаг 3 из 5" in resp["text"]

            # 6. Шаг 3: пин света
            cnt = fake_tg.get_total_responses_count()
            fake_tg.enqueue_user_message(user_id, "17")
            resp = await fake_tg.wait_for_new_bot_response(cnt)
            assert "Шаг 4 из 5" in resp["text"]

            # 7. Шаг 4: пин вспышки
            cnt = fake_tg.get_total_responses_count()
            fake_tg.enqueue_user_message(user_id, "27")
            resp = await fake_tg.wait_for_new_bot_response(cnt)
            assert "Шаг 5 из 5" in resp["text"]
            assert "по умолчанию 50 мм" in resp["text"]

            # 8. Шаг 5: Пропустить ArUco (должно передать 50.0 мм)
            cnt = fake_tg.get_total_responses_count()
            fake_tg.enqueue_callback_button(
                user_id,
                "inst:skip_aruco",
                target_message_id=resp["message_id"],
                target_message_text=resp["text"],
            )
            final_resp = await fake_tg.wait_for_new_bot_response(cnt)
            f_text = final_resp["text"]
            assert "успешно создана" in f_text
            assert "Уникальный Базилик E2E" in f_text
            assert "50.0 мм" in f_text
            assert "Пин света (GPIO):</b> 17" in f_text
            assert "Пин вспышки (GPIO):</b> 27" in f_text
            result = {"ok": True, "card_text": f_text}

        elif scenario == "callback_user_id_system_and_installations":
            # 1. Логин Developer
            await _login_dev(fake_tg, user_id, dev_pass)

            inst_id = (extra or {}).get("installation_id", 1)

            # 2. Нажатие кнопки карточки установки inst:view:id
            # callback.message.from_user — это БОТ!
            cnt = fake_tg.get_total_responses_count()
            fake_tg.enqueue_callback_button(
                user_id,
                f"inst:view:{inst_id}",
                target_message_id=300,
                target_message_text="Список установок",
            )
            resp = await fake_tg.wait_for_new_bot_response(cnt)
            assert f"Установка #{inst_id}" in resp["text"]

            # 3. Нажатие кнопки быстрого интервала в меню системы sys:set_int:6
            cnt = fake_tg.get_total_responses_count()
            fake_tg.enqueue_callback_button(
                user_id,
                "sys:set_int:6",
                target_message_id=301,
                target_message_text="Меню системы",
            )
            resp = await fake_tg.wait_for_new_bot_response(cnt)
            assert "Интервал автосъёмки успешно изменён на <b>6 ч</b>" in resp["text"]
            result = {"ok": True}

        elif scenario == "unknown_message_fallback":
            cnt = fake_tg.get_total_responses_count()
            fake_tg.enqueue_user_message(user_id, "Непонятный запрос")
            resp = await fake_tg.wait_for_new_bot_response(cnt)
            assert resp["text"] == "Не понял. Список команд: /help"
            result = {"ok": True, "answer": resp["text"]}

        elif scenario == "bug_note_preserves_fsm":
            # 1. Логин Developer
            await _login_dev(fake_tg, user_id, dev_pass)

            # 2. Заходим в мастер создания (Шаг 1)
            cnt = fake_tg.get_total_responses_count()
            fake_tg.enqueue_callback_button(
                user_id,
                "inst:create",
                target_message_id=400,
                target_message_text="Список",
            )
            resp = await fake_tg.wait_for_new_bot_response(cnt)
            assert "Шаг 1 из 5" in resp["text"]

            # 3. Отправляем баг-заметку на *
            cnt = fake_tg.get_total_responses_count()
            fake_tg.enqueue_user_message(user_id, "*почему-то кнопка отмены серая")
            bug_resp = await fake_tg.wait_for_new_bot_response(cnt)
            assert bug_resp["text"].startswith("Записано: BUG-LOG-")

            # 4. Проверяем, что FSM остался на Шаге 1: отправляем имя
            cnt = fake_tg.get_total_responses_count()
            fake_tg.enqueue_user_message(user_id, "Стеллаж Без Сброса")
            step2_resp = await fake_tg.wait_for_new_bot_response(cnt)
            assert "Шаг 2 из 5" in step2_resp["text"]
            assert "Стеллаж Без Сброса" in step2_resp["text"]
            result = {
                "ok": True,
                "bug_tag": bug_resp["text"],
                "step2": step2_resp["text"],
            }

        else:
            raise ValueError(f"Неизвестный сценарий: {scenario}")

    finally:
        with contextlib.suppress(Exception):
            await dp.stop_polling()
        polling_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await polling_task
        await api_client.close()
        await bot.session.close()
        await fake_tg.stop()

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="E2E Runner фейкового Telegram Bot API"
    )
    parser.add_argument(
        "--scenario",
        required=True,
        choices=[
            "profile_and_dates",
            "installation_create_name_validation_and_aruco_default",
            "callback_user_id_system_and_installations",
            "unknown_message_fallback",
            "bug_note_preserves_fsm",
        ],
    )
    parser.add_argument("--server-url", required=True)
    parser.add_argument("--client-key", required=True)
    parser.add_argument("--dev-pass", default="dev-pass-123")
    parser.add_argument("--extra", default="{}")

    args = parser.parse_args()
    extra_data = json.loads(args.extra)

    try:
        res = asyncio.run(
            run_scenario(
                args.scenario,
                args.server_url,
                args.client_key,
                dev_pass=args.dev_pass,
                extra=extra_data,
            )
        )
        print(json.dumps(res, ensure_ascii=False))
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        print(f"ОШИБКА E2E СЦЕНАРИЯ: {exc}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
