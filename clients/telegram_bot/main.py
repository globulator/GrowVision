import asyncio
import contextlib
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from clients.telegram_bot.api_client import GrowVisionApiClient
from clients.telegram_bot.config import (
    BotConfig,
    mask_proxy_url,
    validate_and_get_config,
)
from clients.telegram_bot.handlers import (
    cameras,
    climate,
    common,
    control,
    installations,
    sessions,
    start_auth,
    system,
    timelapse,
)
from clients.telegram_bot.middlewares.logging_middleware import (
    DebugLoggingMiddleware,
)
from clients.telegram_bot.utils.debug_logger import get_debug_logger

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("growvision_bot")


async def setup_bot_commands(bot: Bot) -> None:
    """Устанавливает актуальный список системных команд в Telegram."""
    commands = [
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="help", description="Справка"),
        BotCommand(command="version", description="Версия системы"),
        BotCommand(command="login", description="Вход в систему"),
        BotCommand(command="cancel", description="Отмена текущего действия"),
    ]
    await bot.set_my_commands(commands)


def build_dispatcher(config: BotConfig, api_client: GrowVisionApiClient) -> Dispatcher:
    """Создает и настраивает диспетчер aiogram со всеми обработчиками."""
    dp = Dispatcher(storage=MemoryStorage())

    # Инициализация debug логгера
    get_debug_logger(enabled=config.debug_log)

    # Middleware логирования событий
    dp.message.middleware(DebugLoggingMiddleware())
    dp.callback_query.middleware(DebugLoggingMiddleware())

    # Внедрение зависимостей во все хэндлеры
    dp["api_client"] = api_client
    dp["config"] = config

    # Регистрация роутеров:
    # 1. bug_router (перехват заметок на * в любом состоянии)
    # 2. common.router (общие заглушки вроде pag:noop / frm:noop до бизнес-роутеров)
    # 3. Бизнес-роутеры
    # 4. fallback_router (неизвестный текст вне ввода)
    routers = [
        common.bug_router,
        common.router,
        start_auth.router,
        installations.router,
        cameras.router,
        control.router,
        climate.router,
        timelapse.router,
        system.router,
        sessions.router,
        common.fallback_router,
    ]
    for r in routers:
        r._parent_router = None
        dp.include_router(r)

    return dp


def get_retry_delay(attempt: int) -> float:
    """Возвращает ступенчатую паузу для повторного подключения: 5, 10, 20, 40, далее 60 с."""
    delays = [5.0, 10.0, 20.0, 40.0]
    if 1 <= attempt <= len(delays):
        return delays[attempt - 1]
    return 60.0


async def wait_for_telegram_connection(bot: Bot, config: BotConfig) -> None:
    """Подключение к Telegram API при старте с повторами по схеме 5, 10, 20, 40, 60... с."""
    attempt = 1
    masked_proxy = mask_proxy_url(config.telegram_proxy)
    while True:
        try:
            bot_info = await bot.get_me()
            logger.info(
                f"Успешное подключение к Telegram API: @{bot_info.username} (id={bot_info.id})"
            )
            await setup_bot_commands(bot)
            logger.info("Системные команды Telegram успешно зарегистрированы")
            return
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except TelegramUnauthorizedError as exc:
            logger.critical(
                f"Неверный токен Telegram-бота (BOT_TOKEN): {exc}. Завершение процесса."
            )
            sys.exit(1)
        except Exception as exc:  # noqa: BLE001
            delay = get_retry_delay(attempt)
            proxy_info = f" (TELEGRAM_PROXY={masked_proxy})" if masked_proxy else ""
            logger.warning(
                f"Нет доступа к api.telegram.org: проверьте VPN или TELEGRAM_PROXY{proxy_info}. "
                f"Ошибка: {exc}. Повторная попытка {attempt} через {int(delay)} с."
            )
            attempt += 1
            await asyncio.sleep(delay)


async def run_polling_with_reconnect(
    dp: Dispatcher, bot: Bot, config: BotConfig
) -> None:
    """Запуск long polling с автоматическим переподключением при сетевых сбоях."""
    attempt = 1
    masked_proxy = mask_proxy_url(config.telegram_proxy)
    while True:
        try:
            logger.info("Запуск long polling Telegram-бота GrowVision...")
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
            break
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except TelegramUnauthorizedError as exc:
            logger.critical(
                f"Неверный токен Telegram-бота (BOT_TOKEN): {exc}. Завершение процесса."
            )
            sys.exit(1)
        except Exception as exc:  # noqa: BLE001
            delay = get_retry_delay(attempt)
            proxy_info = f" (TELEGRAM_PROXY={masked_proxy})" if masked_proxy else ""
            logger.warning(
                f"Нет доступа к api.telegram.org: проверьте VPN или TELEGRAM_PROXY{proxy_info}. "
                f"Сетевой сбой в цикле polling: {exc}. Переподключение (попытка {attempt}) через {int(delay)} с."
            )
            attempt += 1
            await asyncio.sleep(delay)


async def poll_and_deliver_verification_codes(
    bot: Bot, api_client: GrowVisionApiClient
) -> None:
    """Фоновая доставка кодов подтверждения Web SPA в Telegram."""
    while True:
        try:
            codes = await api_client.get_pending_verification_codes()
            for item in codes:
                code_id = item.get("id")
                tg_id = item.get("telegram_id")
                code_val = item.get("code")
                if not (code_id and tg_id and code_val):
                    continue
                try:
                    text = (
                        f"🔐 Ваш код подтверждения GrowVision: <b>{code_val}</b>\n\n"
                        f"Код действителен 10 минут. Если вы не запрашивали регистрацию в веб-интерфейсе, "
                        f"проигнорируйте это сообщение."
                    )
                    await bot.send_message(chat_id=tg_id, text=text)
                    await api_client.ack_verification_code(code_id)
                    logger.info(
                        f"Код подтверждения #{code_id} успешно доставлен пользователю {tg_id}"
                    )
                except Exception as send_err:  # noqa: BLE001
                    logger.warning(
                        f"Не удалось отправить код подтверждения #{code_id} пользователю {tg_id}: {send_err}"
                    )
                    with contextlib.suppress(Exception):
                        await api_client.fail_verification_code(code_id, str(send_err))
        except asyncio.CancelledError:
            break
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"Ошибка при опросе очереди кодов подтверждения: {exc}")
        await asyncio.sleep(2.0)


async def main() -> None:
    """Точка входа Telegram-бота GrowVision."""
    config = validate_and_get_config()
    logger.info("Конфигурация бота успешно загружена")

    session_kwargs: dict = {"timeout": 300.0}
    if config.telegram_proxy:
        masked_proxy = mask_proxy_url(config.telegram_proxy)
        logger.info(f"Используется прокси для Telegram API: {masked_proxy}")
        session_kwargs["proxy"] = config.telegram_proxy

    session = AiohttpSession(**session_kwargs)
    bot = Bot(
        token=config.bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    api_client = GrowVisionApiClient(config)
    dp = build_dispatcher(config, api_client)

    code_worker: asyncio.Task | None = None
    try:
        await wait_for_telegram_connection(bot, config)
        code_worker = asyncio.create_task(
            poll_and_deliver_verification_codes(bot, api_client)
        )
        await run_polling_with_reconnect(dp, bot, config)
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Получен сигнал остановки бота")
    finally:
        if code_worker and not code_worker.done():
            code_worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await code_worker
        logger.info("Остановка бота и закрытие сетевых соединений...")
        with contextlib.suppress(Exception):
            await get_debug_logger().aflush()
        await api_client.close()
        await bot.session.close()
        logger.info("Бот успешно остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Процесс завершен")
        sys.exit(0)
