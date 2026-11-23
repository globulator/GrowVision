import logging
import sys
import time


class UTCFormatter(logging.Formatter):
    """Форматтер логов со временем в UTC в формате [YYYY-MM-DDTHH:MM:SSZ]."""

    converter = time.gmtime

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        ct = self.converter(record.created)
        if datefmt:
            return time.strftime(datefmt, ct)
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", ct)


def setup_logger(
    name: str = "growvision",
    debug_mode: bool = False,
) -> logging.Logger:
    """Создаёт и настраивает логгер с единым форматом и поддержкой DEBUG_MODE."""
    logger = logging.getLogger(name)
    level = logging.DEBUG if debug_mode else logging.INFO
    logger.setLevel(level)

    # Не дублируем обработчики при повторных вызовах
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = UTCFormatter(
            fmt="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%SZ",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    else:
        for handler in logger.handlers:
            handler.setLevel(level)

    logger.propagate = False
    return logger


def get_logger(name: str = "growvision", debug_mode: bool = False) -> logging.Logger:
    """Возвращает настроенный логгер."""
    return setup_logger(name=name, debug_mode=debug_mode)
