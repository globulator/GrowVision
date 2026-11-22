import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from clients.telegram_bot.api_client import GrowVisionApiClient
from clients.telegram_bot.config import BotConfig, validate_and_get_config
from clients.telegram_bot.handlers import (
    cameras,
    common,
    control,
    installations,
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
    # 2. Бизнес-роутеры
    # 3. fallback_router (неизвестный текст вне ввода)
    routers = [
        common.bug_router,
        start_auth.router,
        installations.router,
        cameras.router,
        control.router,
        timelapse.router,
        system.router,
        common.fallback_router,
    ]
    for r in routers:
        r._parent_router = None
        dp.include_router(r)

    return dp


async def main() -> None:
    """Точка входа Telegram-бота GrowVision."""
    config = validate_and_get_config()
    logger.info("Конфигурация бота успешно загружена")

    session_kwargs: dict = {"timeout": 300.0}
    if config.telegram_proxy:
        logger.info(f"Используется прокси для Telegram API: {config.telegram_proxy}")
        session_kwargs["proxy"] = config.telegram_proxy

    session = AiohttpSession(**session_kwargs)
    bot = Bot(
        token=config.bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    api_client = GrowVisionApiClient(config)
    dp = build_dispatcher(config, api_client)

    # Проверка подключения и установка системных команд с повторами при сбое
    max_retries = 3
    retry_delay_sec = 2.0
    for attempt in range(1, max_retries + 1):
        try:
            bot_info = await bot.get_me()
            logger.info(
                f"Успешное подключение к Telegram API: @{bot_info.username} (id={bot_info.id})"
            )
            await setup_bot_commands(bot)
            logger.info("Системные команды Telegram успешно зарегистрированы")
            break
        except Exception as exc:
            logger.error(
                f"Ошибка подключения к Telegram API (попытка {attempt}/{max_retries}): {exc}. "
                f"Проверьте сетевое соединение, токен или TELEGRAM_PROXY={config.telegram_proxy}"
            )
            if attempt == max_retries:
                await api_client.close()
                await bot.session.close()
                raise
            await asyncio.sleep(retry_delay_sec)

    logger.info("Запуск long polling Telegram-бота GrowVision...")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Критическая ошибка при работе бота: {exc}")
    finally:
        logger.info("Остановка бота и закрытие сетевых соединений...")
        await api_client.close()
        await bot.session.close()
        logger.info("Бот успешно остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Процесс завершен")
        sys.exit(0)
