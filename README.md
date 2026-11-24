# GrowVision

Система автоматизированного наблюдения и фенотипирования растений в гидропонных установках. Raspberry Pi 5 по расписанию включает свет и импульсную вспышку, выполняет съёмку с экшн-камер через USB (UVC), рассчитывает проективную площадь листовой поверхности с помощью нейросетевой сегментации (ONNX) и передаёт кадры с метриками на сервер. Сервер сохраняет данные в SQLite, склеивает таймлапсы в MP4 и предоставляет API для веб-плеера, Telegram-бота и CLI.

## Архитектура системы

GrowVision организован как монорепозиторий, разделяющий компоненты по изолированным окружениям (`venv`). Серверный бэкенд на базе FastAPI и SQLAlchemy обеспечивает безопасную запись данных через транзакционный журнал SQLite (WAL) и строгие ограничения целостности (индексы, внешние ключи, CHECK-ограничения). Общий модуль `shared` содержит логику сквозного версионирования, форматтер логов и Pydantic-контракты обмена данными с Edge-устройствами.

Edge-агент для Raspberry Pi 5 управляет GPIO-реле и камерами, локально выполняет инференс нейросети и передаёт результаты через Edge API. Пользовательские интерфейсы включают CLI для терминального управления, Telegram-бота для мобильного мониторинга и оперативных команд, а также автономный Web SPA-клиент (просмотр таймлапсов, клиентский GIF-экспорт на Canvas, тестовые снимки, регистрация и вход через Telegram, 0 эмодзи / векторные SVG-иконки, без внешних сборок и CDN).

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
│   └── web/                    # Веб-клиент Web SPA (чистый HTML5/CSS3/ES6, плеер, GIF-экспорт)
├── tests/                      # Сквозные интеграционные тесты (E2E)
├── requirements-dev.txt        # Общие зависимости для тестов и линтинга (pytest, ruff)
└── VERSION                     # Источник версии проекта (0.6.5)
```

## Быстрый старт

### Автоматическое развёртывание и обновление (Linux)

- **Первичная установка сервера:** `sudo bash scripts/install_server.sh`
- **Универсальное обновление (сервер / RPi):** `gv-update` (или `./scripts/gv-update.sh`)

### Сервер (ручной запуск)

#### 1. Подготовка окружения сервера

```bash
python3 -m venv server/.venv
server/.venv/bin/pip install -r server/requirements.txt -r requirements-dev.txt
cp server/.env.example server/.env
```

#### 2. Запуск сервера

```bash
server/.venv/bin/python -m server.main
```

- Веб-интерфейс (Web SPA) с плеером таймлапсов, GIF-экспортом, тестовой съёмкой и помощником подключения Raspberry Pi доступен по адресу: `http://127.0.0.1:8000/`
- Документация OpenAPI (Swagger UI) доступна по адресу: `http://127.0.0.1:8000/docs`

#### 3. Запуск тестов сервера и E2E

```bash
server/.venv/bin/pytest server/tests tests/e2e -q
```

---

### Консольный клиент (CLI)

#### 1. Подготовка окружения CLI

```bash
python3 -m venv clients/cli/.venv
clients/cli/.venv/bin/pip install -r clients/cli/requirements.txt -r requirements-dev.txt
cp clients/cli/.env.example clients/cli/.env
```

#### 2. Запуск интерактивной оболочки (shell)

```bash
clients/cli/.venv/bin/python -m clients.cli.main shell
```

Внутри оболочки доступны все команды без префиксов (`unit list`, `snap take 1`, `system status`, `help` и др.).

#### 3. Запуск одиночных команд

```bash
clients/cli/.venv/bin/python -m clients.cli.main unit list
clients/cli/.venv/bin/python -m clients.cli.main version
```

#### 4. Запуск тестов CLI

```bash
clients/cli/.venv/bin/pytest clients/cli/tests -q
```

---

### Telegram-бот (`clients/telegram_bot`)

Мобильный интерфейс для мониторинга установок, просмотра галереи плановых снимков, видео-таймлапсов и оперативного управления гидропоникой.

#### 1. Подготовка окружения бота

```bash
python3 -m venv clients/telegram_bot/.venv
clients/telegram_bot/.venv/bin/pip install -r clients/telegram_bot/requirements.txt -r requirements-dev.txt
cp clients/telegram_bot/.env.example clients/telegram_bot/.env
```

Настройте переменные в `clients/telegram_bot/.env`:
- `TELEGRAM_BOT_TOKEN` — токен бота от `@BotFather`
- `SERVER_URL` — адрес сервера (по умолчанию `http://127.0.0.1:8000`)
- `CLIENT_API_KEY` — ключ клиента (должен совпадать с `server/.env`)
- `TELEGRAM_PROXY` — опциональный HTTP/SOCKS5 прокси для Telegram API
- `BOT_DEBUG_LOG` — включение структурированного JSONL-журнала событий (true/false)

#### 2. Запуск Telegram-бота

```bash
clients/telegram_bot/.venv/bin/python -m clients.telegram_bot.main
```

Для оперативного сбора баг-репортов пользователи могут отправить боту сообщение с `*` (например, `*ошибка в карточке`), а просмотреть журнал дефектов можно через встроенную утилиту:
```bash
clients/telegram_bot/.venv/bin/python -m clients.telegram_bot.tools.bug_log list
```

#### 3. Запуск тестов бота

```bash
clients/telegram_bot/.venv/bin/pytest clients/telegram_bot/tests -q
```

---

### Edge-агент (Raspberry Pi 5 / Mock)

#### 1. Подготовка окружения Edge-агента

```bash
python3 -m venv edge_agent/.venv
edge_agent/.venv/bin/pip install -r edge_agent/requirements.txt -r requirements-dev.txt
cp edge_agent/.env.example edge_agent/.env
```

#### 2. Запуск Edge-агента на ПК (mock-режим)

```bash
HARDWARE_MODE=mock edge_agent/.venv/bin/python -m edge_agent.main
```

Для развертывания на реальной плате Raspberry Pi 5 см. руководство [docs/DEPLOYMENT_RPI.md](file:///home/mcperchik/study/GrowVision/docs/DEPLOYMENT_RPI.md), схему подключения [docs/HARDWARE_WIRING.md](file:///home/mcperchik/study/GrowVision/docs/HARDWARE_WIRING.md) и задачу проверки [docs/user_tasks.md](file:///home/mcperchik/study/GrowVision/docs/user_tasks.md) (T-001).

#### 3. Посев тестовых кадров (симулятор)

Для мгновенного наполнения истории установки синтетическими или реальными снимками без ожидания плановой съемки:
```bash
edge_agent/.venv/bin/python -m edge_agent.simulator seed --installation 1 --frames 12
```

#### 4. Запуск тестов Edge-агента

```bash
edge_agent/.venv/bin/pytest edge_agent/tests -q
```

---

### Проверка качества кода (линтер и форматирование)

```bash
server/.venv/bin/ruff check .
server/.venv/bin/ruff format --check .
```

## Документация

- [docs/PROJECT.md](file:///home/mcperchik/study/GrowVision/docs/PROJECT.md) — паспорт проекта и правила разработки
- [docs/USER_GUIDE.md](file:///home/mcperchik/study/GrowVision/docs/USER_GUIDE.md) — руководство пользователя по работе с CLI и Telegram-ботом
- [docs/DEPLOYMENT_SERVER.md](file:///home/mcperchik/study/GrowVision/docs/DEPLOYMENT_SERVER.md) — развертывание сервера и Telegram-бота (systemd)
- [docs/DEPLOYMENT_RPI.md](file:///home/mcperchik/study/GrowVision/docs/DEPLOYMENT_RPI.md) — развертывание Edge-агента на Raspberry Pi 5
- [docs/HARDWARE_WIRING.md](file:///home/mcperchik/study/GrowVision/docs/HARDWARE_WIRING.md) — схема аппаратного подключения GPIO и камер
- [docs/REPO_AND_VERSIONING.md](file:///home/mcperchik/study/GrowVision/docs/REPO_AND_VERSIONING.md) — структура монорепозитория и версионирование
- [docs/DB_SCHEMA.md](file:///home/mcperchik/study/GrowVision/docs/DB_SCHEMA.md) — схема базы данных SQLite
- [docs/API_CONTRACT.md](file:///home/mcperchik/study/GrowVision/docs/API_CONTRACT.md) — контракты Edge API и Client API
- [docs/CLI_SPEC.md](file:///home/mcperchik/study/GrowVision/docs/CLI_SPEC.md) — спецификация консольного клиента
- [docs/BOT_SPEC.md](file:///home/mcperchik/study/GrowVision/docs/BOT_SPEC.md) — спецификация Telegram-бота
- [docs/WEB_SPEC.md](file:///home/mcperchik/study/GrowVision/docs/WEB_SPEC.md) — спецификация веб-плеера
- [docs/user_tasks.md](file:///home/mcperchik/study/GrowVision/docs/user_tasks.md) — реестр задач пользователя (включая T-001)
- [docs/stage_reports/stage-1.md](file:///home/mcperchik/study/GrowVision/docs/stage_reports/stage-1.md) — отчёт по этапу 1
- [docs/stage_reports/stage-2.md](file:///home/mcperchik/study/GrowVision/docs/stage_reports/stage-2.md) — отчёт по этапу 2
- [docs/stage_reports/stage-3.md](file:///home/mcperchik/study/GrowVision/docs/stage_reports/stage-3.md) — отчёт по этапу 3
- [docs/stage_reports/stage-4.md](file:///home/mcperchik/study/GrowVision/docs/stage_reports/stage-4.md) — отчёт по этапу 4
- [docs/stage_reports/stage-5.md](file:///home/mcperchik/study/GrowVision/docs/stage_reports/stage-5.md) — отчёт по этапу 5
- [docs/stage_reports/stage-6.md](file:///home/mcperchik/study/GrowVision/docs/stage_reports/stage-6.md) — отчёт по этапу 6
- [docs/stage_reports/stage-6.1.md](file:///home/mcperchik/study/GrowVision/docs/stage_reports/stage-6.1.md) — отчёт по этапу 6.1
- [docs/stage_reports/stage-7.md](file:///home/mcperchik/study/GrowVision/docs/stage_reports/stage-7.md) — отчёт по этапу 7
- [docs/stage_reports/stage-7.2.md](file:///home/mcperchik/study/GrowVision/docs/stage_reports/stage-7.2.md) — отчёт по этапу 7.2 (исправления раунд 4)
- [docs/stage_reports/stage-7.3.md](file:///home/mcperchik/study/GrowVision/docs/stage_reports/stage-7.3.md) — отчёт по этапу 7.3
- [docs/stage_reports/stage-7.4.md](file:///home/mcperchik/study/GrowVision/docs/stage_reports/stage-7.4.md) — отчёт по этапу 7.4
- [docs/stage_reports/stage-8.md](file:///home/mcperchik/study/GrowVision/docs/stage_reports/stage-8.md) — отчёт по этапу 8
- [docs/stage_reports/stage-8.1.md](file:///home/mcperchik/study/GrowVision/docs/stage_reports/stage-8.1.md) — отчёт по этапу 8.1
- [docs/stage_reports/stage-8.2.md](file:///home/mcperchik/study/GrowVision/docs/stage_reports/stage-8.2.md) — отчёт по этапу 8.2
- [docs/stage_reports/stage-8.3.md](file:///home/mcperchik/study/GrowVision/docs/stage_reports/stage-8.3.md) — отчёт по этапу 8.3
- [docs/stage_reports/stage-8.4.md](file:///home/mcperchik/study/GrowVision/docs/stage_reports/stage-8.4.md) — отчёт по этапу 8.4
- [docs/stage_reports/stage-8.4.1.md](file:///home/mcperchik/study/GrowVision/docs/stage_reports/stage-8.4.1.md) — отчёт по этапу 8.4.1
- [docs/stage_reports/stage-8.5.md](file:///home/mcperchik/study/GrowVision/docs/stage_reports/stage-8.5.md) — отчёт по этапу 8.5
- [docs/stage_reports/stage-8.6.md](file:///home/mcperchik/study/GrowVision/docs/stage_reports/stage-8.6.md) — отчёт по этапу 8.6
- [docs/stage_reports/stage-8.7.md](file:///home/mcperchik/study/GrowVision/docs/stage_reports/stage-8.7.md) — отчёт по этапу 8.7
- [docs/stage_reports/stage-8.8.md](file:///home/mcperchik/study/GrowVision/docs/stage_reports/stage-8.8.md) — отчёт по этапу 8.8
- [docs/stage_reports/stage-8.9.md](file:///home/mcperchik/study/GrowVision/docs/stage_reports/stage-8.9.md) — отчёт по этапу 8.9
- [docs/stage_reports/stage-8.10.md](file:///home/mcperchik/study/GrowVision/docs/stage_reports/stage-8.10.md) — отчёт по этапу 8.10
