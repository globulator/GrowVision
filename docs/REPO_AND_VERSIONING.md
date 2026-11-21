# GrowVision — Структура репозитория и версионирование

Документ фиксирует раскладку монорепозитория, конфигурацию, правила деплоя, границы ответственности ИИ-агентов и систему версий на базе Git.

Опирается на `DB_SCHEMA.md` и `API_CONTRACT.md`.

---

## 1. Принципы

- **Монорепозиторий.** На любую машину клонируется весь репозиторий, а запускается только нужный сервис: `server`, `edge_agent` или клиент.
- **Изоляция сервисов.** У каждого сервиса свои зависимости (`requirements.txt`) и свой `.env`. Клиенты общаются с сервером только по REST (`API_CONTRACT.md`) и не импортируют код `server/`, `edge_agent/` и `shared/`.
- **Секреты разнесены.** На Raspberry Pi нет токена бота и паролей ролей; на сервере нет ничего от RPi, кроме общего ключа `EDGE_API_KEY`.
- **Без Docker для MVP.** Везде используется venv + systemd. `docker-compose.yml` в репозитории нет.

---

## 2. Структура репозитория

```text
GrowVision/
├── .gitignore                  # .env, venv, __pycache__, БД (*.db), медиа, логи, *.onnx
├── VERSION                     # Единый источник версии проекта (на старте 0.0.0)
├── README.md                   # Описание проекта, архитектура в 2 абзацах, навигация по docs/
│
├── docs/                       # Документация
│   ├── DB_SCHEMA.md            # Схема БД (защищён, см. раздел 5.3)
│   ├── API_CONTRACT.md         # Контракт Edge API и Client API (защищён, см. раздел 5.3)
│   ├── REPO_AND_VERSIONING.md  # Этот документ (защищён, см. раздел 5.3)
│   ├── CLI_SPEC.md             # Спецификация CLI-клиента (защищён, см. раздел 5.3)
│   ├── BOT_SPEC.md             # Спецификация Telegram-бота (защищён, см. раздел 5.3)
│   ├── WEB_SPEC.md             # Спецификация веб-плеера таймлапса (защищён, см. раздел 5.3)
│   ├── DEPLOYMENT_SERVER.md    # Развёртывание сервера и бота (venv + systemd)
│   ├── DEPLOYMENT_RPI.md       # Развёртывание edge_agent на Raspberry Pi 5, загрузка модели
│   ├── HARDWARE_WIRING.md      # GPIO-пины, маркировка кабелей, экшн-камеры
│   ├── USER_GUIDE.md           # Памятка для биологов и разработчика
│   ├── user_tasks.md           # Бэклог физических/sudo задач для пользователя от ИИ-агентов
│   ├── agent_questions.md      # Вопросы ИИ-агентов при неясности ТЗ
│   └── team_roles/             # Роли и системные инструкции ИИ-агентов
│
├── shared/                     # Код, общий для server и edge_agent (без тяжёлых зависимостей)
│   ├── __init__.py
│   ├── schemas.py              # Pydantic-модели Edge API: Heartbeat, Command, CaptureMeta (защищён)
│   ├── version.py              # get_app_version(): VERSION + короткий хэш Git
│   └── logger.py               # Единый форматтер логов с поддержкой DEBUG_MODE
│
├── edge_agent/                 # Приложение для Raspberry Pi 5
│   ├── .env.example
│   ├── requirements.txt        # httpx, pydantic, python-dotenv, opencv-headless, onnxruntime, gpiozero, psutil (python3-lgpio и v4l-utils ставятся из apt, см. 4.2)
│   ├── main.py                 # Запуск циклов: heartbeat, опрос команд, планировщик
│   ├── config.py               # Загрузка и валидация edge_agent/.env
│   ├── models/                 # .onnx-модель (в .gitignore, в git только .gitkeep)
│   ├── hardware/
│   │   ├── gpio_controller.py  # Реле (light=1, flash=0 по умолчанию), mock-режим для ПК
│   │   └── camera_manager.py   # Поиск через /dev/v4l/by-id и v4l2-ctl, прогрев, захват
│   ├── vision/
│   │   └── segmenter.py        # ONNX-инференс (маска → area_px) и детектор ArUco
│   ├── storage/
│   │   └── offline_queue.py    # Буфер снимков при потере связи и фоновая выгрузка; битые пакеты (422/404) уходят в offline_queue/failed/
│   ├── services/
│   │   ├── telemetry.py        # Метрики RPi для heartbeat
│   │   └── command_runner.py   # Исполнение команд: SNAP, LIGHT_*, RELAY_TEST, SCAN_CAMERAS; единый аппаратный мьютекс на GPIO и камеры, плановая съёмка в приоритете
│   └── tests/                  # Юнит-тесты (mock-режим на ПК)
│
├── server/                     # Центральный бэкенд (ПК/сервер)
│   ├── .env.example
│   ├── requirements.txt        # fastapi, uvicorn, sqlalchemy, python-multipart, python-dotenv (без aiogram, typer и opencv: сервер только сохраняет готовые файлы и вызывает ffmpeg)
│   ├── main.py                 # Точка входа FastAPI, раздача clients/web (StaticFiles) и server/media по /media, очистка таймаутов
│   ├── config.py               # Загрузка и валидация server/.env
│   ├── db/
│   │   ├── database.py         # Подключение SQLite (PRAGMA foreign_keys=ON, journal_mode=WAL, busy_timeout=5000), сессии
│   │   └── models.py           # Модели 7 таблиц строго по DB_SCHEMA.md
│   ├── api/
│   │   ├── deps.py             # X-Edge-Key, X-Client-Key, сессии users, права ролей
│   │   ├── edge.py             # /api/edge/*
│   │   ├── auth.py             # /api/auth/* (с лимитом попыток входа)
│   │   ├── installations.py    # /api/installations/*
│   │   ├── cameras.py          # /api/cameras/*
│   │   ├── control.py          # /api/control/*
│   │   ├── timelapse.py        # /api/timelapse/* (кадры, сборка MP4)
│   │   └── system.py           # /api/system/*
│   ├── core/                   # Бизнес-логика: расчёт площади в см², сборка MP4 через ffmpeg (libx264), резолвер целей
│   ├── media/                  # Хранилище кадров (в .gitignore, в git только .gitkeep)
│   └── tests/                  # Тесты API и бизнес-логики
│
└── clients/                    # Тонкие REST-клиенты (каждый со своими зависимостями)
    ├── cli/                    # CLI (Typer, httpx, rich, python-dotenv)
    │   ├── .env.example
    │   └── requirements.txt
    ├── telegram_bot/           # Telegram-бот (Aiogram 3, httpx, python-dotenv) с универсальным пагинатором
    │   ├── .env.example
    │   └── requirements.txt
    └── web/                    # Статический фронтенд таймлапса (index.html, app.js, style.css)
```

---

## 3. Конфигурация (`.env`)

Реальные `.env` в git не попадают, в репозитории лежат только `.env.example`.

### 3.1. `server/.env` (только на сервере)

```ini
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
DATABASE_URL=sqlite:///./growvision.db
EDGE_API_KEY=...          # секрет приёма данных от RPi (совпадает с RPi)
CLIENT_API_KEY=...        # секрет клиентов: CLI и бота (заголовок X-Client-Key)
BIOLOGIST_PASSWORD=...
DEVELOPER_PASSWORD=...
VIDEO_MAX_FPS=10          # максимальная частота кадров MP4 для /api/timelapse/{id}/video
VIDEO_MIN_DURATION_SEC=2  # минимальная длительность видео, целое ≥ 1 с (расчёт частоты кадров — в API_CONTRACT.md, раздел 4.5)
```

### 3.2. `edge_agent/.env` (только на Raspberry Pi 5)

```ini
SERVER_URL=http://192.168.1.10:8000   # локальный IP сервера на этапе разработки; удалённый доступ настраивается перед v1.0.0
EDGE_API_KEY=...                      # совпадает с ключом на сервере
HARDWARE_MODE=rpi                     # на ПК разработчика: mock (без реле и камер)
MODEL_PATH=edge_agent/models/lettuce_seg_v1.onnx
HEARTBEAT_INTERVAL_SEC=30
COMMAND_POLL_INTERVAL_SEC=2
RELAY_SWITCH_DELAY_MS=200
CAMERA_WARMUP_FRAMES=5
```

### 3.3. `clients/telegram_bot/.env` (рядом с сервером)

```ini
TELEGRAM_BOT_TOKEN=...
SERVER_URL=http://127.0.0.1:8000
CLIENT_API_KEY=...        # совпадает с ключом на сервере
WEB_PUBLIC_URL=http://192.168.1.10:8000   # необязательный: адрес веб-плеера для пользователей (127.0.0.1 с телефона недоступен); без него ссылка на веб-плеер скрыта
```

### 3.4. `clients/cli/.env` (на сервере или локально на ноутбуке)

```ini
SERVER_URL=http://127.0.0.1:8000
CLIENT_API_KEY=...        # совпадает с ключом на сервере
```

Пароли ролей (`BIOLOGIST_PASSWORD`, `DEVELOPER_PASSWORD`) знает только сервер: клиенты передают введённый пароль в `POST /api/auth/login`.

`clients/web` секретов не имеет: его эндпоинты публичные (роль Гость).

---

## 4. Запуск и деплой

| Компонент | Где работает | Окружение и способ раздачи |
|---|---|---|
| `server` | ПК/сервер | venv + systemd |
| `clients/telegram_bot` | рядом с сервером (на том же ПК/сервере) | venv + systemd |
| `clients/cli` | на сервере и/или локально на ноутбуке (`SERVER_URL` + `CLIENT_API_KEY` из локального `clients/cli/.env`) | venv |
| `edge_agent` | Raspberry Pi 5 | venv + systemd |
| `clients/web` | раздаётся самим сервером FastAPI через `StaticFiles` (без отдельного веб-сервера и без CORS) | часть процесса `server` |

Правила:

- На каждую машину клонируется весь репозиторий; у каждого запускаемого сервиса свой venv и свой `requirements.txt`.
- `shared/` подключается запуском из корня репозитория (`python -m server.main`, `python -m edge_agent.main`). Точные команды и unit-файлы systemd — в `DEPLOYMENT_SERVER.md` и `DEPLOYMENT_RPI.md`.
- Версия сервера и RPi берётся из `shared/version.py` (раздел 6.1), поэтому на сервере и на RPi репозиторий должен быть клонирован с `.git` (иначе версия показывается без хэша). CLI, Telegram-бот и веб получают версию сервера через публичный `GET /api/version` и `shared/` не импортируют.
- На этапе разработки (`0.1.0`–`0.5.0`) связь RPi и сервера тестируется в одной локальной сети. Настройка и проверка удалённого подключения ПК ↔ RPi в лицее вынесена в финальный этап приёмки перед присвоением тега `v1.0.0`.

### 4.1. Доставка ONNX-модели

- Модель не хранится в git (`*.onnx` в `.gitignore`); папка `edge_agent/models/` в репозитории содержит только `.gitkeep`.
- До выхода релиза кода `v1.0.0` файл модели хранится в отдельном техническом pre-release теге `model-v1` (GitHub Releases) и скачивается при деплое на RPi в `edge_agent/models/`. Имя файла совпадает с `MODEL_PATH` (например, `lettuce_seg_v1.onnx`) и попадает в heartbeat как `model_name`.
- Шаги скачивания описываются в `DEPLOYMENT_RPI.md`.

### 4.2. Обязательные пункты для DEPLOYMENT-документов

Принятые решения, которые должны попасть в инструкции развёртывания:

- `DEPLOYMENT_SERVER.md`: установка `ffmpeg` из apt (сборка MP4 через `libx264`); запуск сервера в один воркер (`uvicorn ... --workers 1`), так как связь `command_id → capture_id` и результат сканирования камер хранятся в памяти процесса; сборка MP4 выполняется синхронным эндпоинтом (в пуле потоков), чтобы `ffmpeg` не блокировал обработку heartbeat и команд; `python-multipart` входит в `server/requirements.txt`.
- `DEPLOYMENT_RPI.md`: установка `python3-lgpio` и `v4l-utils` из apt; venv создаётся с `--system-site-packages`.

---

## 5. Границы ответственности ИИ-агентов

### 5.1. Матрица владения путями

Scope совпадает со scope коммитов (раздел 6.3).

| Путь | Кто правит |
|---|---|
| `server/` | агент `server` |
| `edge_agent/` | агент `edge` |
| `clients/cli/` | агент `cli` |
| `clients/telegram_bot/` | агент `bot` |
| `clients/web/` | агент `web` |
| `shared/` (кроме `schemas.py`) | агент `shared`; изменения проверяются тестами `server` и `edge_agent` |
| `shared/schemas.py` | **только с согласия пользователя** |
| `docs/DB_SCHEMA.md`, `docs/API_CONTRACT.md`, `docs/REPO_AND_VERSIONING.md`, `docs/CLI_SPEC.md`, `docs/BOT_SPEC.md`, `docs/WEB_SPEC.md` | **только с согласия пользователя** |
| `docs/DEPLOYMENT_SERVER.md` | агент `server` |
| `docs/DEPLOYMENT_RPI.md`, `docs/HARDWARE_WIRING.md` | агент `edge` |
| `docs/USER_GUIDE.md` | агенты `cli` и `bot` (каждый — свои разделы) |
| `docs/user_tasks.md`, `docs/agent_questions.md` | любой агент дописывает; закрывает и отвечает пользователь |
| `docs/team_roles/` | вне матрицы; содержимое задаёт пользователь |
| `VERSION` | агенты в своих коммитах не меняют; PATCH поднимается централизованно при слиянии задачи (правило 6.3), MINOR и 1.0.0 — только по указанию пользователя |
| `README.md`, `.gitignore` | любой агент, только в части своей папки (навигация, запуск, правила игнорирования) |

### 5.2. Правила границ

1. Агент правит только свой путь из матрицы. Если нужна правка чужого пути или неясно ТЗ, агент записывает вопрос в `docs/agent_questions.md` и не правит чужие файлы.
2. `clients/*` не импортируют код `server/`, `edge_agent/` и `shared/`. Формат запросов и ответов клиенты берут из `API_CONTRACT.md`.
3. `shared/` используют только `server` и `edge_agent`; он не получает тяжёлых зависимостей.
4. `server/db/models.py` строго следует `DB_SCHEMA.md`. Изменение структуры БД начинается с изменения `DB_SCHEMA.md` (с согласия пользователя), а не с кода.
5. Любое изменение Edge API или Client API начинается с изменения `API_CONTRACT.md` (с согласия пользователя) и `shared/schemas.py`.
6. Физические действия и действия с `sudo` агент не выполняет, а записывает в `docs/user_tasks.md`.

### 5.3. Защищённые файлы

Без явного согласия пользователя запрещено изменять:

- `docs/DB_SCHEMA.md`
- `docs/API_CONTRACT.md`
- `docs/REPO_AND_VERSIONING.md`
- `docs/CLI_SPEC.md`
- `docs/BOT_SPEC.md`
- `docs/WEB_SPEC.md`
- `shared/schemas.py`

---

## 6. Версионирование

### 6.1. Строка версии (`shared/version.py`)

При запуске `server` и `edge_agent` функция `get_app_version()` читает `VERSION` из корня репозитория и добавляет короткий хэш коммита (`git rev-parse --short HEAD`):

| Условие | Результат |
|---|---|
| Git доступен | `0.2.0-a3f9c12` (формат из `API_CONTRACT.md`: `edge_version`, `server_version`) |
| Нет папки `.git` (архив) | значение из `VERSION`, например `0.2.0` |

### 6.2. Шкала версий (`0.MINOR.PATCH` → `1.0.0`)

| Версия | Этап готовности |
|---|---|
| 0.1.0 | Инициализация репозитория, каркас `shared/`, модели БД по `DB_SCHEMA.md`, базовый `server` с серверными роутами `/api/edge/*`. |
| 0.2.0 | Реализованы Client API и CLI, пройдены тесты управления и авторизации с mock-агентом (эмуляция запросов RPi к `/api/edge/*`). |
| 0.3.0 | Реализован `edge_agent` (реле, камеры, офлайн-очередь, ONNX-сегментация), протестирован на RPi 5. |
| 0.4.0 | Реализован Telegram-бот (роли, пагинация, управление, сборка MP4 с автоматическим прореживанием при превышении лимита Telegram в 50 МБ). |
| 0.5.0 | Реализован Web-плеер таймлапса (покадровый просмотр, загрузка с блокировкой, переключатель слоя). |
| 1.0.0 | Первый релиз MVP: настройка и тестирование удалённого подключения ПК ↔ RPi в лицее, тег `v1.0.0` и GitHub Release после полевой проверки. |

### 6.3. Правила коммитов для ИИ-агентов

Формат коммитов — Conventional Commits: `тип(scope): описание`.

- **Типы:** `feat`, `fix`, `docs`, `test`, `chore`, `refactor`.
- **Scope:** `server`, `edge`, `bot`, `cli`, `web`, `shared`, `repo`. Scope `repo` — общие конфиги, корневые файлы и документы в `docs/`, не принадлежащие одному сервису.
- **Файл `VERSION`:** в обычных коммитах агентов (любого типа) не трогается, это исключает конфликты слияния. **PATCH** (например, `0.1.0` → `0.1.1`) поднимается централизованно, отдельным коммитом `chore(repo): bump version to X.Y.Z` при слиянии задачи в основную ветку.
- **MINOR и `1.0.0`:** только по явному указанию пользователя после приёмки этапа.