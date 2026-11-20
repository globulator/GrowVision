# PROJECT.md — паспорт проекта (ЗАЩИЩЁННЫЙ ФАЙЛ)

Здесь собрано всё, что зависит от конкретного проекта. Роли агентов универсальны и берут отсюда детали.
Пока в разделах 1–6 есть `TODO:`, Coordinator работает в режиме «Этап 0» и задаёт вопросы пользователю.
Менять файл можно только с согласия пользователя.

Все команды выполняются **из корня репозитория** `GrowVision/` (Linux/macOS; под Windows — `.venv\Scripts\python`).

---

## 1. О проекте
- Название: GrowVision
- Цель (2–3 предложения): Система наблюдения за ростом растений в гидропонных установках. Raspberry Pi 5 по расписанию включает свет и вспышку, снимает кадры с камер, считает площадь растения нейросетью (ONNX) и отправляет данные на сервер. Сервер хранит кадры и замеры, собирает таймлапс (MP4) и отдаёт данные клиентам.
- Для кого / кто пользователи: Гость (публичный веб-плеер, только чтение), Biologist (просмотр, ручной снимок, правка названия и культуры), Developer (полное управление: установки, камеры, свет, реле, настройки).
- Тип проекта: многокомпонентная система: серверный бэкенд (FastAPI + SQLite), edge-агент на Raspberry Pi 5, клиенты (CLI, Telegram-бот, веб-плеер).
- Источник правды по архитектуре: `docs/REPO_AND_VERSIONING.md`, `docs/DB_SCHEMA.md`, `docs/API_CONTRACT.md`.

## 2. Стек и команды
- Языки и фреймворки: Python 3.11+. Сервер: FastAPI, uvicorn, SQLAlchemy, SQLite (WAL), python-multipart, python-dotenv, ffmpeg (libx264, из apt). Edge: httpx, pydantic, opencv-headless, onnxruntime, gpiozero, psutil (`python3-lgpio` и `v4l-utils` из apt). CLI: Typer, httpx, rich. Бот: aiogram 3, httpx. Веб: чистые HTML/CSS/JS без сборщиков и CDN. Docker не используется (venv + systemd).
- Установка зависимостей (у каждого сервиса свой venv):
  - `python3 -m venv server/.venv && server/.venv/bin/pip install -r server/requirements.txt -r requirements-dev.txt`
  - `python3 -m venv edge_agent/.venv && edge_agent/.venv/bin/pip install -r edge_agent/requirements.txt -r requirements-dev.txt` (на RPi venv создаётся с `--system-site-packages`)
  - `python3 -m venv clients/cli/.venv && clients/cli/.venv/bin/pip install -r clients/cli/requirements.txt -r requirements-dev.txt`
  - `python3 -m venv clients/telegram_bot/.venv && clients/telegram_bot/.venv/bin/pip install -r clients/telegram_bot/requirements.txt -r requirements-dev.txt`
  - `requirements-dev.txt` (корень репозитория): `pytest`, `pytest-asyncio`, `httpx`, `ruff`. Нужен только для проверки, на боевые машины не ставится.
- Запуск тестов:
  - `server/.venv/bin/python -m pytest server/tests -q` (тесты `shared/` лежат в `server/tests/`)
  - `edge_agent/.venv/bin/python -m pytest edge_agent/tests -q`
  - `clients/cli/.venv/bin/python -m pytest clients/cli/tests -q`
  - `clients/telegram_bot/.venv/bin/python -m pytest clients/telegram_bot/tests -q`
  - QA (сквозные): `server/.venv/bin/python -m pytest tests/e2e -q`
  - у веб-клиента автотестов нет; проверка — сценарии из критериев приёмки этапа 8
- Линтер / форматтер: `ruff check .` и `ruff format --check .` (запускать из любого venv, где стоит ruff)
- Сборка: не требуется (Python без сборки, веб без сборщика)
- Запуск для ручной проверки:
  - сервер: `server/.venv/bin/python -m server.main` (один воркер; веб-плеер открывается на `http://127.0.0.1:8000/`)
  - edge на ПК: `HARDWARE_MODE=mock edge_agent/.venv/bin/python -m edge_agent.main`
  - CLI: `clients/cli/.venv/bin/python -m clients.cli.main shell`
  - бот: `clients/telegram_bot/.venv/bin/python -m clients.telegram_bot.main`
- Тестовое окружение для QA:
  - БД: временный SQLite-файл в `tmp_path` или `sqlite:///:memory:`; рабочую `growvision.db` и `server/media/` не трогать.
  - RPi эмулируется тестовым клиентом (запросы к `/api/edge/*` с `X-Edge-Key`); для edge — `HARDWARE_MODE=mock`.
  - Ключи и пароли в тестах — только тестовые значения (`EDGE_API_KEY=test-edge`, `CLIENT_API_KEY=test-client` и т. п.).
  - Telegram недоступен: тесты бота идут на подменённом HTTP-транспорте (`httpx.MockTransport`) и без обращения к серверам Telegram.
  - Для проверок MP4 нужен `ffmpeg`; если его нет — это блокер (`T-NNN`), а не повод пропускать проверку.
- `qa_tests_dir`: `tests/e2e/`

## 3. Компоненты и владение путями

| Компонент | Allowed Paths (кто пишет) | Тесты компонента | Зависит от |
|---|---|---|---|
| `shared` | `shared/` (кроме `schemas.py`) | `server/tests/`, `edge_agent/tests/` | — |
| `shared-schemas` | `shared/schemas.py` — только по разовой карточке с одобрением пользователя (создание файла входит в этап 1 и одобряется «ОК» на этот план) | `server/tests/` | `docs/API_CONTRACT.md` |
| `server` | `server/`, `docs/DEPLOYMENT_SERVER.md` | `server/tests/` | `shared` |
| `edge` | `edge_agent/`, `docs/DEPLOYMENT_RPI.md`, `docs/HARDWARE_WIRING.md` | `edge_agent/tests/` | `shared`, Edge API (`API_CONTRACT.md`, раздел 3) |
| `cli` | `clients/cli/`, свой раздел в `docs/USER_GUIDE.md` | `clients/cli/tests/` | Client API (`API_CONTRACT.md`, раздел 4) |
| `bot` | `clients/telegram_bot/`, свой раздел в `docs/USER_GUIDE.md` | `clients/telegram_bot/tests/` | Client API |
| `web` | `clients/web/` | — (проверка сценариями QA) | Client API, раздача через `server` |
| `repo` | `.gitignore`, `requirements-dev.txt`, корневой `README.md` (в части запуска), `VERSION` (только создание со значением `0.0.0`, один раз на этапе 1), каталоги каркаса из `REPO_AND_VERSIONING.md`, разд. 2, с файлами `.gitkeep` | — | — |

Исходное состояние репозитория (до этапа 1): готовы только каталог `docs/` с `PROJECT.md`, `STATE.md`, шестью спецификациями и ролями агентов в `docs/team_roles/`. Всё остальное создают агенты: каталоги и корневые файлы — на этапе 1 (компонент `repo`), служебные каталоги `docs/tasks/`, `docs/handoff/`, `docs/stage_reports/` и файлы реестров — Coordinator, Builder, QA и Scribe по ходу работы. В Allowed Paths каждой карточки входит файл `docs/handoff/<компонент>.md`.

Общие файлы: `server/requirements.txt`, `server/.env.example`, `server/main.py`, `requirements-dev.txt`, `.gitignore`, `README.md` в один момент времени правит только один Builder. Владелец общих файлов: Builder текущего этапа по `server`; на этапах клиентов — Builder этого клиента (только в своей папке).

Разделение документации: Builder создаёт черновики `DEPLOYMENT_*.md`, `HARDWARE_WIRING.md` и своих разделов `USER_GUIDE.md` (по матрице `REPO_AND_VERSIONING.md`, п. 5.1). После `PASS` Scribe приводит их и `README.md` в соответствие с кодом (событие `STAGE_PASS`). Факты о железе (пины, маркировка кабелей), которых нет в спецификациях, Scribe не выдумывает: он задаёт вопрос `Q-NNN`.

## 4. Защищённые файлы (менять только с согласия пользователя)
- `docs/PROJECT.md` (этот файл)
- `docs/DB_SCHEMA.md`
- `docs/API_CONTRACT.md`
- `docs/REPO_AND_VERSIONING.md`
- `docs/CLI_SPEC.md`
- `docs/BOT_SPEC.md`
- `docs/WEB_SPEC.md`
- `shared/schemas.py` (см. раздел 3)
- `VERSION` (начальное значение `0.0.0` создаётся на этапе 1 по разрешению пользователя через «ОК» на этот план)
- `docs/team_roles/` (роли агентов; содержимое задаёт пользователь)

Файлы спецификаций, которые читают агенты:
- `docs/DB_SCHEMA.md`, `docs/API_CONTRACT.md`, `docs/REPO_AND_VERSIONING.md` — все агенты
- `docs/CLI_SPEC.md` — `cli`; `docs/BOT_SPEC.md` — `bot`; `docs/WEB_SPEC.md` — `web`

Если в спецификациях найдено противоречие (например, список зависимостей сервера без `httpx`, нужного для тестов), Builder возвращает `SPEC_ISSUE` и не правит файл сам.

## 5. План этапов

Версии `VERSION` по шкале из `REPO_AND_VERSIONING.md`, п. 6.2. MINOR поднимает только пользователь после приёмки (см. раздел 8).

| № | Этап | Компоненты | Критерии приёмки (проверяемые командой или сценарием) | Зависит от |
|---|---|---|---|---|
| 1 | Каркас репозитория: каталоги, `VERSION`, `.gitignore`, `shared/`, конфиг сервера, модели БД | `repo`, `shared`, `shared-schemas`, `server` (только `config.py`, `db/`) | 0) Созданы все каталоги из `REPO_AND_VERSIONING.md`, разд. 2 (с `.gitkeep` в пустых), `.gitignore` по тому же документу, `VERSION` с содержимым ровно `0.0.0`, `requirements-dev.txt`. 1) `pytest server/tests` проходит. 2) Создание БД на пустом SQLite даёт ровно 7 таблиц из `DB_SCHEMA.md`; `PRAGMA foreign_keys` = 1, `journal_mode` = wal, `busy_timeout` = 5000. 3) Две активные установки с одинаковым GPIO-пином нарушают ограничение, а архивная с тем же пином — нет (`DB_SCHEMA.md`, п. 4.3). 4) `get_app_version()` возвращает `0.0.0-<хэш>` в git и `0.0.0` без `.git`. 5) `shared/schemas.py` содержит модели Heartbeat, Command, CaptureMeta по `API_CONTRACT.md`. 6) `ruff check .` без ошибок. | — |
| 2 | Edge API сервера (после приёмки: версия 0.1.0) | `server` (`main.py`, `api/deps.py`, `api/edge.py`, `core/`, `media/.gitkeep`) | 1) Четыре эндпоинта `/api/edge/*` отвечают по `API_CONTRACT.md`, разд. 3 (тесты на каждый). 2) Неверный или отсутствующий `X-Edge-Key` → 401, формат ошибки `{"detail": "..."}`. 3) Повторная загрузка кадра с теми же `(installation_id, camera_id, captured_at)` → 200 с прежним `capture_id`. 4) Кадр от неизвестной камеры → 409. 5) Команда `pending` старше 30 с получает статус `timeout`. 6) `command_id` из метаданных кадра связывается с результатом команды. 7) `ruff` и все тесты проходят. | 1 |
| 3 | Client API, часть 1: вход, установки, камеры, система | `server` (`api/auth.py`, `installations.py`, `cameras.py`, `system.py`) | 1) Эндпоинты из `API_CONTRACT.md`, разд. 4.1–4.3 и 4.6 работают, включая `GET /api/version`. 2) Гость без заголовков видит только `id`, `name`, `culture_name`, `is_archived`, `last_area_display`. 3) Шестая попытка `POST /api/auth/login` за минуту → 429 с `Retry-After`. 4) Права ролей: Biologist правит только `name`, `culture_name`; Developer — все поля; нет прав → 403. 5) Удаление установки с кадрами → 409; архивация отвязывает камеры. 6) Все тесты и `ruff` проходят. | 2 |
| 4 | Client API, часть 2: управление, таймлапс, видео; раздача веба | `server` (`api/control.py`, `api/timelapse.py`, `core/` сборка MP4, раздача `clients/web` и `/media`) | 1) `/api/control/*` работают через очередь команд; при офлайне RPi (heartbeat старше 90 с) → 503; при отсутствии ответа → 504. 2) `GET /api/timelapse/{id}/frames` отдаёт кадры по `captured_at ASC` без пагинации. 3) `POST .../video` собирает MP4 через ffmpeg; 2 кадра → ровно 2 с, 100 кадров → 10 fps (`API_CONTRACT.md`, п. 4.5); один плановый кадр → JPEG; несколько камер без `camera_id` → 422. 4) `debug_info` видит только Developer. 5) Сервер раздаёт каталог `clients/web/` и `/media/...`. 6) Сервер обрабатывает heartbeat во время сборки видео (эндпоинт синхронный). 7) Все тесты и `ruff` проходят. | 3 |
| 5 | CLI-клиент (после приёмки: версия 0.2.0) | `cli` | 1) `clients/cli/.venv/bin/python -m clients.cli.main help` и `<раздел> info` для всех 6 разделов (`auth`, `unit`, `camera`, `light`, `snap`, `system`) работают. 2) Команды и права по `CLI_SPEC.md`, п. 3. 3) Сквозной сценарий на тестовом сервере: `auth login` → `unit list` → `snap take 1` (с mock-агентом) → экспорт MP4. 4) Ошибки сервера и сети выводятся понятным текстом по `CLI_SPEC.md`, разд. 4. 5) Нет импортов из `server/`, `edge_agent/`, `shared/` (проверка командой `grep -rn "from server\|from shared\|from edge_agent" clients/cli`). 6) Тесты и `ruff` проходят. | 4 |
| 6 | Edge-агент (после приёмки: версия 0.3.0; проверка на настоящей RPi — задача пользователя `T-NNN`) | `edge` | 1) `HARDWARE_MODE=mock` на ПК: агент шлёт heartbeat, забирает и исполняет `SNAP`, `LIGHT_*`, `RELAY_TEST`, `SCAN_CAMERAS` против тестового сервера. 2) Плановая съёмка загружает кадры с `area_px` и `command_id` по контракту. 3) При недоступном сервере кадры копятся в `offline_queue`, после восстановления выгружаются; битые пакеты (422/404) уходят в `offline_queue/failed/`. 4) Единый мьютекс: плановая съёмка не прерывается ручной командой. 5) Тесты и `ruff` проходят. 6) `docs/DEPLOYMENT_RPI.md` и `docs/HARDWARE_WIRING.md` созданы. | 2 |
| 7 | Telegram-бот (после приёмки: версия 0.4.0) | `bot` | 1) Тесты на подменённом транспорте покрывают меню трёх ролей, двухэтапный вход, универсальный пагинатор и маппер ошибок. 2) Все сценарии `BOT_SPEC.md`, разд. 5 реализованы в файлах из структуры `BOT_SPEC.md`, разд. 2 (монолитного `bot.py` нет). 3) Без обязательных переменных в `.env` бот не запускается и называет недостающие. 4) Нет импортов из `server/`, `edge_agent/`, `shared/`. 5) Тесты и `ruff` проходят. | 4 |
| 8 | Веб-плеер (после приёмки: версия 0.5.0) | `web` | 1) `GET /` на запущенном сервере отдаёт `index.html`; в `clients/web/` ровно `index.html`, `style.css`, `app.js`. 2) В коде нет внешних URL (CDN, шрифты), `grep -rn "http" clients/web` не находит внешних адресов. 3) Сценарии `WEB_SPEC.md`, разд. 3–5: список с флажком архива, предзагрузка с блокировкой управления, Play/пауза/шаги/скорость 1x–4x/зацикливание, слой «Оригинал/Разметка», выбор камеры по соседним кадрам, пустые состояния и ошибки. 4) Заголовки `X-Client-Key`, `X-Client-Type`, `X-External-Id` веб не отправляет. 5) Проверка в браузере на ширине 360 px — задача пользователя `T-NNN`. | 4 |

Ещё не входит в этапы: приёмка `v1.0.0` (настройка и проверка удалённой связи ПК ↔ RPi) — отдельная работа пользователя.

Автопродолжение после `PASS` (запускать следующий этап без команды пользователя): нет

## 6. Правила git
- Основная ветка: `main`
- Ветка этапа: `stage-<N>/<компонент>`
- Параллельные Builder-ы работают в отдельных ветках или git worktrees.
- Формат коммитов: Conventional Commits — `тип(scope): описание`. Типы: `feat`, `fix`, `docs`, `test`, `chore`, `refactor`. Scope: `server`, `edge`, `bot`, `cli`, `web`, `shared`, `repo`.
- Слияние: после `PASS`, выполняет Coordinator (`git merge`). Конфликты слияния решает человек.

## 7. Документация и реестры
- Журнал QA: `docs/qa_log.md`
- Вопросы к пользователю: `docs/agent_questions.md`
- Задачи для пользователя: `docs/user_tasks.md`
- Состояние этапа: `docs/STATE.md`
- Карточки задач: `docs/tasks/`
- Заметки Builder-ов: `docs/handoff/`
- Итоги этапов: `docs/stage_reports/`
- Роли агентов: `docs/team_roles/` (вместо `agents/` из шаблона)

Реестры `docs/agent_questions.md` и `docs/user_tasks.md` ведёт только Scribe. Builder вместо записи в реестр возвращает Coordinator-у `BLOCKED` или `SPEC_ISSUE`, дальше цепочка идёт по `coordinator.md`.

## 8. Версии
- Политика версий: файл `VERSION` создаёт Builder этапа 1 со значением `0.0.0` (пустой репозиторий без работающего кода). PATCH (например, `0.1.0` → `0.1.1`) поднимается отдельным коммитом `chore(repo): bump version to X.Y.Z` при слиянии задачи в `main`: Coordinator ставит мини-задачу Builder-у с Allowed Paths `VERSION` (только для `feat` и `fix`, не для `docs`). PATCH на этапе 1 не поднимается (остаётся `0.0.0`). MINOR (0.1.0 после этапа 2, затем 0.2.0, 0.3.0, 0.4.0, 0.5.0) и `1.0.0` — только по прямому указанию пользователя после приёмки этапа.
- Может ли Scribe менять `VERSION`: нет

## 9. Правила скорости и тестов (обязательны для Builder и QA)

Coordinator включает этот раздел в «Прочитать перед началом» карточек Builder и QA.

1. Тесты быстрые: один тест не дольше 3 с (тест с настоящим ffmpeg до 10 с), весь набор не дольше 60 с. Настоящие ожидания (sleep, ответ RPi 10/15 с, пороги 30/60/90 с) в тестах запрещены: константы времени подменяются через monkeypatch.
2. Состояние для тестов готовится через публичные эндпоинты (RPi онлайн только через POST /api/edge/heartbeat). Прямая запись в БД допустима только для данных, у которых нет эндпоинта.
3. QA выносит общие фикстуры (валидный heartbeat, эмуляция RPi через claim и result) в tests/e2e/conftest.py и использует их повторно.
4. Любой запуск тестов выполняется как `timeout 300 ... pytest`, у вызовов ffmpeg стоит timeout.
5. Две подряд неудачные попытки починить один и тот же тест (Builder или QA): стоп, вернуть статус BLOCKED с описанием причины, не делать третью попытку наугад.
6. QA не дублирует unit-тесты Builder-а: только сквозные сценарии через публичный API и негативная матрица (qa_tester.md, п. 4.3).
7. Если карточка выходит за 10 файлов или 6 критериев, Coordinator предлагает пользователю разбить этап до запуска.
8. Перед каждым действием Coordinator смотрит `git log -1 --format=%cr`. Если агент 20 минут не делал коммитов и не обновлял handoff, Coordinator перезапускает его по coordinator.md, п. I, и записывает это в STATE.md.
9. Мелкие правки (до 3 файлов, без контрактов) всегда в режиме LIGHT.
