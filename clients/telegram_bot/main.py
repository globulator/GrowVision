import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from clients.telegram_bot.api_client import GrowVisionApiClient
from clients.telegram_bot.config import BotConfig, validate_and_get_config
from clients.telegram_bot.handlers import (
    cameras,
    control,
    installations,
    start_auth,
    system,
    timelapse,
)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("growvision_bot")


def build_dispatcher(config: BotConfig, api_client: GrowVisionApiClient) -> Dispatcher:
    """Создает и настраивает диспетчер aiogram со всеми обработчиками."""
    dp = Dispatcher(storage=MemoryStorage())

    # Внедрение зависимостей во все хэндлеры
    dp["api_client"] = api_client
    dp["config"] = config

    # Регистрация роутеров строго в логическом порядке
    routers = [
        start_auth.router,
        installations.router,
        cameras.router,
        control.router,
        timelapse.router,
        system.router,
    ]
    for r in routers:
        r._parent_router = None
        dp.include_router(r)

    return dp


async def main() -> None:
    """Точка входа Telegram-бота GrowVision."""
    config = validate_and_get_config()
    logger.info("Конфигурация бота успешно загружена")

    # Сессия бота с увеличенным таймаутом до 300 с для отправки больших видео (BOT_SPEC.md разд. 6.1)
    session = AiohttpSession(timeout=300.0)
    bot = Bot(
        token=config.bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    api_client = GrowVisionApiClient(config)
    dp = build_dispatcher(config, api_client)

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
