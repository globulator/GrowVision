# GrowVision

Система автоматизированного наблюдения и фенотипирования растений в гидропонных установках. Raspberry Pi 5 по расписанию включает свет и импульсную вспышку, выполняет съёмку с экшн-камер через USB (UVC), рассчитывает проективную площадь листовой поверхности с помощью нейросетевой сегментации (ONNX) и передаёт кадры с метриками на сервер. Сервер сохраняет данные в SQLite, склеивает таймлапсы в MP4 и предоставляет API для веб-плеера, Telegram-бота и CLI.

## Архитектура системы

GrowVision организован как монорепозиторий, разделяющий компоненты по изолированным окружениям (`venv`). Серверный бэкенд на базе FastAPI и SQLAlchemy обеспечивает безопасную запись данных через транзакционный журнал SQLite (WAL) и строгие ограничения целостности (индексы, внешние ключи, CHECK-ограничения). Общий модуль `shared` содержит логику сквозного версионирования, форматтер логов и Pydantic-контракты обмена данными с Edge-устройствами.

Edge-агент для Raspberry Pi 5 управляет GPIO-реле и камерами, локально выполняет инференс нейросети и передаёт результаты через Edge API. Пользовательские интерфейсы включают CLI для терминального управления, Telegram-бота для уведомлений и оперативных команд, а также легковесный статический веб-плеер таймлапсов без внешних сборок и CDN.

## Структура репозитория

```text
GrowVision/
├── docs/                       # Архитектурная документация, контракты и регламенты
├── shared/                     # Общий код сервера и edge-агента (версионирование, схемы, логгер)
├── server/                     # Бэкенд FastAPI, SQLite ORM-модели, хранилище медиа и тесты
├── edge_agent/                 # Агент управления периферией и съёмкой для Raspberry Pi 5
├── clients/
│   ├── cli/                    # Консольный интерфейс управления (Typer, rich)
│   ├── telegram_bot/           # Telegram-бот для биологов и разработчиков (aiogram 3)
│   └── web/                    # Веб-плеер таймлапса (чистый HTML/CSS/JS)
├── requirements-dev.txt        # Общие зависимости для тестов и линтинга (pytest, ruff)
└── VERSION                     # Источник версии проекта
```

## Быстрый старт: сервер и тесты

### 1. Подготовка окружения

```bash
python3 -m venv server/.venv
server/.venv/bin/pip install -r server/requirements.txt -r requirements-dev.txt
```

### 2. Запуск тестов

```bash
server/.venv/bin/pytest server/tests -q
```

### 3. Проверка качества кода

```bash
server/.venv/bin/ruff check .
server/.venv/bin/ruff format --check .
```

## Документация

- [docs/PROJECT.md](file:///home/mcperchik/study/GrowVision/docs/PROJECT.md) — паспорт проекта и правила разработки
- [docs/REPO_AND_VERSIONING.md](file:///home/mcperchik/study/GrowVision/docs/REPO_AND_VERSIONING.md) — структура монорепозитория и версионирование
- [docs/DB_SCHEMA.md](file:///home/mcperchik/study/GrowVision/docs/DB_SCHEMA.md) — схема базы данных SQLite
- [docs/API_CONTRACT.md](file:///home/mcperchik/study/GrowVision/docs/API_CONTRACT.md) — контракты Edge API и Client API
- [docs/CLI_SPEC.md](file:///home/mcperchik/study/GrowVision/docs/CLI_SPEC.md) — спецификация консольного клиента
- [docs/BOT_SPEC.md](file:///home/mcperchik/study/GrowVision/docs/BOT_SPEC.md) — спецификация Telegram-бота
- [docs/WEB_SPEC.md](file:///home/mcperchik/study/GrowVision/docs/WEB_SPEC.md) — спецификация веб-плеера
- [docs/stage_reports/stage-1.md](file:///home/mcperchik/study/GrowVision/docs/stage_reports/stage-1.md) — отчёт по этапу 1
