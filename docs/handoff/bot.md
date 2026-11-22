# Отчёт о реализации: Этап 7 — Telegram-бот

- **Статус**: READY_FOR_QA
- **Ветка**: `stage-7/bot-fixes-4`
- **Компонент**: `telegram_bot`
- **Карточка**: `docs/tasks/stage-7-bot-fixes-4-builder.md`
- **Спецификация**: `docs/BOT_SPEC.md`
- **Контракт API**: `docs/API_CONTRACT.md`

## 1. Выполненные задачи и архитектурные решения

### 1.1. Структура компонента и строгая изоляция
- Компонент Telegram-бота реализован в директории `clients/telegram_bot/` со строгим соответствием структуре файлов спецификации `docs/BOT_SPEC.md` (разд. 2):
  - `main.py` — точка входа, конфигурация aiogram-бота и запуск long polling;
  - `config.py` — валидация конфигурации окружения (`TELEGRAM_BOT_TOKEN`, `SERVER_URL`, `CLIENT_API_KEY`, `BOT_LOG_LEVEL`) с безопасным скрытием секретов в `__repr__`;
  - `api_client.py` — асинхронный HTTP-клиент (`GrowVisionApiClient`) к Client API на базе `httpx.AsyncClient`;
  - `states.py` — FSM-состояния aiogram для пошаговых сценариев;
  - `keyboards/reply.py` — меню быстрого доступа с адаптацией под роли (Гость, Biologist, Developer);
  - `keyboards/inline.py` — контекстные инлайн-клавиатуры для карточек установок, управления светом, подтверждений действий;
  - `keyboards/paginator.py` — универсальный генератор пагинатора (страницы по 5 элементов, размер `callback_data` строго < 64 байт, поддержка кнопки «Все активные установки», галерея кадров);
  - `handlers/start_auth.py` — `/start`, `/help`, `/version`, `/cancel`, двухэтапная авторизация, профиль и выход;
  - `handlers/installations.py` — списки активных и архивных установок, просмотр карточек, пошаговый мастер создания (5 шагов), редактирование полей, подтверждение архивации/удаления;
  - `handlers/cameras.py` — просмотр списка камер, сканирование USB-портов RPi (`POST /api/cameras/scan`), привязка и отвязка;
  - `handlers/control.py` — управление основным светом, вспышкой, 3-секундный тест реле, ручная фотосъёмка (`POST /api/control/snap`) с отправкой альбома из двух фотографий (сырой кадр + детекция нейросетью);
  - `handlers/timelapse.py` — галерея плановых кадров (листание по одному кадру от свежих к старым, переключение слоёв оригинал/разметка), генерация видео-таймлапса с циклом прореживания;
  - `handlers/system.py` — статус сервера и телеметрия Raspberry Pi 5, настройка интервала автосъёмки, переключение `debug_mode`;
  - `utils/errors.py` — унифицированная обработка ошибок и маппинг статус-кодов по `BOT_SPEC.md` разд. 6.2;
  - `utils/formatters.py` — форматирование карточек установок с проекцией по ролям, телеметрии RPi 5, площадей растений и блоков отладки.
- **Строгая изоляция:** бот взаимодействует с бэкендом исключительно по протоколу HTTP к Client API. Любые импорты из `server/`, `shared/`, `edge_agent/`, `clients/cli` запрещены и верифицируются отдельным тестом `test_isolation.py`.
- **Заголовки запросов:** каждый HTTP-запрос передаёт обязательные заголовки `X-Client-Key: <CLIENT_API_KEY>`, `X-Client-Type: telegram`, `X-External-Id: <str(user_id)>`.
- **Безопасность секретов:** создан `clients/telegram_bot/.gitignore`, исключающий `.env`, `.venv`, кэши. При отсутствии обязательных переменных бот завершается с информативным перечислением недостающих параметров.

### 1.2. Ролевая модель и адаптивный интерфейс
- Реализованы три роли пользователей в соответствии со спецификацией:
  1. **Гость** (по умолчанию, без авторизации): просмотр активных установок и плановых кадров, `/help`, `/version`, вход `/login`.
  2. **Biologist**: всё меню гостя + ручная съёмка (`📸 Сделать снимок`), сборка таймлапсов, редактирование агрономических полей установок.
  3. **Developer**: полный доступ — управление светом и вспышкой, 3-сек тест реле, сканирование и привязка камер, создание, редактирование, архивация и удаление установок, телеметрия RPi 5, изменение интервала автосъёмки и debug-режим.
- Reply-меню переключается автоматически при авторизации и выходе.

### 1.3. Безопасный двухэтапный вход (`/login`)
- FSM-сценарий: ввод логина -> ввод пароля.
- Сразу после получения сообщения с паролем вызывается `await message.delete()`, что мгновенно скрывает пароль из чата Telegram.
- При ошибке 401/403/404/422 пользователь получает понятное сообщение, а пароль не остаётся в истории.

### 1.4. Просмотр кадров и генерация видео (с циклом прореживания)
- **Галерея снимков:** листание плановых кадров от самого свежего к старым с кнопками `◀️ Ранее` / `Позже ▶️` и переключением слоёв `📷 Оригинал` / `🌿 Разметка нейросетью` через редактирование медиа в текущем сообщении.
- **Генерация видео-таймлапса:**
  - При 1 плановом кадре автоматически отправляется JPEG-фотография с пояснением: *«Плановый кадр пока один, видео не собрано»*.
  - Потоковое скачивание видео с отсечением при превышении лимита Telegram в 50 МБ (`TG_VIDEO_LIMIT = 50_000_000` байт).
  - Если видео превышает 50 МБ, бот выполняет **цикл прореживания** до 5 попыток со `step = 2, 4, 8, 16, 32` с обновлением статуса пользователю.
  - При исчерпании 5 циклов бот информирует пользователя и рекомендует консольный клиент (`snap video <id>`).
  - Реализована блокировка параллельной сборки видео одним пользователем (`_user_video_locks`).

### 1.5. Ручная фотосъёмка (`📸 Сделать снимок`)
- Поддержка выбора конкретной установки или всех активных установок сразу (`target="all"`).
- Отправка медиагруппы (альбома) из двух фото: оригинал + детекция контуров нейросетью.
- Подпись содержит название установки, ID камеры, точное время съёмки UTC, площадь растений (в см² с ArUco или в пикселях без него).
- Для роли Developer при включённом `debug_mode` выводится лог переключения реле и параметров съёмки.

### 1.6. Управление оборудованием и RPi 5
- Управление светом и вспышкой для конкретной установки или для всех активных (`all`).
- 3-секундный тест реле.
- Сканирование камер RPi (`POST /api/cameras/scan`) с отображением перечня камер и кнопками привязки.
- Телеметрия RPi 5 (аптайм, температура процессора, RAM, диск, офлайн-очередь, статус сервиса).
- Изменение интервала автосъёмки с валидацией и предупреждением сервера.

---

## 2. Устранение дефектов QA (Итерация 1)

- **BUG-001 (MAJOR)**: в `clients/telegram_bot/api_client.py` (`login()`) скорректировано тело запроса `POST /api/auth/login` — передаётся `"login"` вместо `"username"` в соответствии с `API_CONTRACT.md` п. 4.1.
- **BUG-002 (MAJOR)**: в `clients/telegram_bot/api_client.py` (`control_light_main`, `control_light_flash`, `control_relay_test`, `take_snap`) поле `"installation_id"` заменено на `"target"` (`int` или `"all"`) в соответствии с `API_CONTRACT.md` п. 4.4. Добавлен метод `_resolve_target`.
- **E2E & Handlers**:
  - `handlers/cameras.py` и `handlers/timelapse.py`: добавлена поддержка прозрачного разрешения строкового `hardware_id` (например, `cam-bot-01`) в числовой идентификатор камеры при переходах по инлайн-коллбэкам.
  - `handlers/control.py`: поддержаны альтернативные форматы callback (`ctrl:light:<1|0>:<target>` и `ctrl:light:<target>:<on|off>`), в подпись snap-альбома подставляется фактический `hardware_id` камеры.
  - `handlers/installations.py`: скорректированы имена полей FSM создания и обновления под контракт API (`culture_name`, `light_gpio_pin`, `flash_gpio_pin`, `aruco_size_mm`).
  - `utils/formatters.py`: расширен парсер телеметрии `format_system_status` для совместимости со структурой ответа сервера `GET /api/system/status`.

---

## 3. Результаты проверок

- **Unit-тесты Telegram-бота**: `clients/telegram_bot/.venv/bin/pytest clients/telegram_bot -c clients/telegram_bot/pytest.ini -v` — **30 passed in 0.34s**.
- **E2E тесты Telegram-бота**: `server/.venv/bin/pytest tests/e2e/test_bot_e2e.py -v` — **8 passed in 17.03s**.
- **Регрессионные тесты всех компонентов**:
  - Edge Agent: `edge_agent/.venv/bin/pytest edge_agent -c edge_agent/pytest.ini -q` — **39 passed in 0.75s**.
  - CLI-клиент: `clients/cli/.venv/bin/pytest clients/cli/tests -q` — **42 passed in 0.42s**.
  - Telegram-бот: `clients/telegram_bot/.venv/bin/pytest clients/telegram_bot/tests -c clients/telegram_bot/pytest.ini -q` — **30 passed in 0.32s**.
  - Server & E2E: `server/.venv/bin/pytest server/tests tests/e2e -q` — **111 passed in 39.11s**.
  - **Всего**: **222 passed** (суммарно < 45 секунд).
- **Линтер и форматирование**:
  - `server/.venv/bin/ruff check .` — **All checks passed!**
  - `server/.venv/bin/ruff format --check .` — **168 files already formatted.**
- **Изоляция импортов (CLI проверка)**:
  `grep -rn --exclude-dir=.venv "from server\|from shared\|from edge_agent\|from clients.cli\|import server\|import shared" clients/telegram_bot` — **0 совпадений (пусто)**.
- **База данных**: файлы `growvision.db*` в корне проекта не создаются, `git status --short` чист после тестов.

---

## 6. Доработки и исправления: Раунд 4 (Ветка `stage-7/bot-fixes-4`)

### 6.1. Выполненные задачи
1. **Кнопка «📷 Камеры установки» и управление камерами (`handlers/installations.py`, `keyboards/inline.py`):**
   - В карточке установки добавлена кнопка `inst:cameras:{id}`.
   - Обработчик отображает актуальный список привязанных камер с индикацией статуса (🟢 для `is_online=True`, 🔴 для `is_online=False`), кнопками быстрого перехода к камерам и возврата к карточке `inst:view:{id}`.
   - Зарегистрированы заглушки `frm:close`, `frm:noop`, `pag:noop` (`await callback.answer()`).
2. **Галерея плановых кадров и ролевой доступ (`handlers/timelapse.py`, `api_client.py`):**
   - Роут сервера `/api/timelapse/{id}/frames` возвращает `{"installation_id": ..., "frames": [...]}`: поддержан безопасный парсинг `res.get("frames", [])` с fallback на список.
   - Для роли `guest` отображается только последний плановый кадр с подписью о доступности полной галереи биологам/разработчикам и кнопкой закрытия `frm:close`.
   - Для ролей `biologist` и `developer` доступна полная галерея с пагинацией и переключением слоёв оригинал/нейросеть.
3. **Блок 🐞 DEBUG (`utils/formatters.py`, `handlers/control.py`, `handlers/cameras.py`):**
   - Извлечение строки лога `debug_info.get("log_line")` из `resp["debug_info"]["log_line"]` с выводом строк «Команда:» и «Лог:».
   - Экранирование пользовательских и отладочных строк через `html.escape`.
4. **Валидация GPIO в мастере и редактировании (`handlers/installations.py`):**
   - На шагах `waiting_light_pin` и `waiting_flash_pin`, а также в `process_edit_value` проверяется диапазон `0 <= pin <= 53`.
   - При невалидном вводе выводится предупреждение, FSM-состояние не сбрасывается.
5. **Фильтрация архива установок (`handlers/installations.py`):**
   - При отображении архива (`show_archived=True`) фильтруются только элементы с `inst.get("is_archived") is True`.
6. **Роли при ручном снимке (`handlers/control.py`):**
   - Для роли `biologist` отправляется только сырой снимок (raw) через `send_photo`.
   - Для роли `developer` отправляется медиагруппа (сырой кадр + разметка нейросетью).
7. **Команда `/version` и экранирование приветствия (`handlers/start_auth.py`):**
   - Извлечение `server_version` или `version` из `GET /api/version` (без `vнеизвестно`).
   - Заменен пример логина «Иван Петров» на «Андрей Бармин» в строке подсказки.
   - Экранирован `username` в приветствии.
8. **Зависимости и обработка невалидного токена (`main.py`, `requirements.txt`):**
   - В `requirements.txt` добавлен `aiohttp-socks>=0.8.0`.
   - В `wait_for_telegram_connection` перехватывается `TelegramUnauthorizedError`: пишется критический лог и процесс немедленно завершается (`sys.exit(1)`), предотвращая зацикливание при битом токене.
9. **Удаление архивной установки с кадрами (`server/api/installations.py`):**
   - Для архивной установки (`inst.is_archived is True`) разрешено удаление при любом количестве кадров (камеры отвязываются, записи `Capture` в БД и файлы медиа `server/media/<id>` удаляются).
   - Для неархивной установки при наличии кадров сохраняется возврат `409 Conflict`.
10. **Сканирование камер (`server/api/cameras.py`):**
    - В результаты сканирования возвращаются только реально обнаруженные камеры в момент опроса. Отключенные камеры помечаются `is_online = False` и остаются в базе данных (видны в карточке установки со статусом оффлайн).
11. **Паритет настройки `capture_delay_sec` (Server ↔ Edge ↔ Bot ↔ CLI):**
    - **Сервер:** системная настройка в `system_settings` (default 1.5 с, диапазон 0.0..30.0 с, эндпоинты `GET/PATCH /api/system/settings`, `GET /api/system/status`), колонка `Installation.capture_delay_sec` в БД, передача параметров в heartbeat и claim command edge-агента.
    - **Edge-агент:** учет задержки `capture_delay_sec` перед снимком в `hardware.py` и `orchestrator.py`.
    - **Telegram-бот:** кнопка «⏱ Задержка съёмки» в меню «💡 Свет и реле» и меню установки с пресетами (`0.5 с`, `1.5 с`, `3.0 с`, `5.0 с`; для установки — «🔄 Использовать общую») и вводом `0.0..30.0` без сброса FSM.
    - **CLI:** команды `system delay <seconds>`, `unit delay <target> [seconds] [--default]`, опции `--delay` и `--clear-delay` в `unit add` и `unit edit`, отображение в `system status` и `_render_unit_card`.
12. **Edge-агент (мьютекс, раздельные кадры, формула роста):**
    - В `orchestrator.py` команды используют `hardware.acquire_lock(blocking=True, timeout=10.0)`.
    - `frame_index` ведется раздельно для каждой пары `(installation_id, camera_id)` начиная с 0.
    - В `synthetic.py` реализована ступенчатая формула `compute_plant_radius(frame_index, max_radius)` и поворот `angle_offset = (frame_index * 7) % 360`.

### 6.2. Итоговые результаты проверок
- **Unit-тесты Telegram-бота**: `51 passed in 0.56s` (+7 тестов в `test_bot_fixes_round4.py`).
- **CLI-клиент**: `45 passed in 0.56s` (+3 теста задержки съёмки).
- **Edge-агент**: `49 passed in 11.04s`.
- **Server & E2E**: `123 passed in 69.90s`.
- **Всего в проекте**: **268 passed** (все тесты зеленые).
- **Линтер и форматирование**:
  - `server/.venv/bin/ruff check .` — **All checks passed!**
  - `server/.venv/bin/ruff format --check .` — **193 files already formatted.**
- **Изоляция и чистота**:
  - `server/media/` содержит только `.gitkeep`.
  - `VERSION` не изменялась: `0.3.1`.

### 5.1. Выполненные задачи
1. **Шаг ArUco в мастере создания установки (`handlers/installations.py`):**
   - Кнопка «Пропустить» отправляет `aruco_size_mm = None` (`null`, размер не задан, расчет площади в пикселях `px`).
   - Подсказка «по умолчанию 50 мм» убрана из текста шага 5.
   - Ввод положительного числа (`> 0`) задаёт размер маркера в мм как и прежде.
   - При нажатии «Пропустить» в сформированной карточке выводится `не задан (площадь в px)`.
2. **Отказоустойчивое подключение при старте (`main.py`):**
   - Функция `wait_for_telegram_connection` при сетевой ошибке подключения к `api.telegram.org` повторяет попытки со ступенчатыми паузами `5, 10, 20, 40`, далее каждые `60` секунд.
   - В лог выводится обязательное предупреждение: `Нет доступа к api.telegram.org: проверьте VPN или TELEGRAM_PROXY`.
   - Процесс продолжается до успешного подключения либо до получения сигнала Ctrl+C / SIGINT / SIGTERM.
3. **Самовосстановление long polling при сетевых сбоях (`main.py`):**
   - Функция `run_polling_with_reconnect` оборачивает `dp.start_polling` и автоматически восстанавливает опрос при обрыве сети по той же схеме пауз (`5, 10, 20, 40, 60...` с) без падения процесса.
   - E2E сценарий `network_reconnect_flow` подтверждает успешную обработку команд после восстановления сети.
4. **Маскирование учетных данных прокси (`config.py`, `utils/debug_logger.py`, `main.py`):**
   - Функция `mask_proxy_url` маскирует логин и пароль в URL прокси до вида `protocol://***:***@host:port`.
   - Маскирование применяется в `BotConfig.__repr__`, в сообщениях логов `main.py` и в `mask_sensitive_data`.
5. **HTML-экранирование пользовательского ввода (`utils/formatters.py`, хэндлеры):**
   - Все строки пользователей (логин, имя и культура установки, имена камер, заметки на «*») экранируются через `html.escape` во всех сообщениях с HTML-разметкой.
   - Исключены ошибки `TelegramBadRequest: can't parse entities` при наличии символов `<`, `>`, `&` (например, `A<B&C`, `X<Y>`).
6. **Безопасность журнала и изоляция логов тестов:**
   - В `DebugLoggingMiddleware` и `DebugLogger` гарантировано отсутствие текста пароля в JSONL:
     - Входящие сообщения в состоянии `waiting_password` логируются как `***`.
     - Ошибки хэндлеров на шаге пароля маскируют текст ошибки в `***`.
     - Баг-заметки на `*` на шаге пароля маскируют текст заметки в `***`.
   - Все тестовые наборы (unit-тесты в `conftest.py`, E2E в `fake_telegram.py`) перенаправляют логи во временные каталоги `tmp_path`.
   - Боевой каталог `clients/telegram_bot/logs/` отсутствует либо абсолютно пуст после прогона.

### 5.2. Итоговые результаты проверок
- **Unit-тесты Telegram-бота**: `44 passed in 0.44s` (+6 тестов в `test_bot_fixes_round3.py`).
- **E2E тесты Telegram-бота**: `7 passed in 22.95s` (+2 сценария `network_reconnect_flow`, `html_escaping_and_security`).
- **Регрессионные тесты всех компонентов**:
  - Edge Agent: `45 passed in 3.14s`.
  - CLI-клиент: `42 passed in 0.43s`.
  - Telegram-бот: `44 passed in 0.44s`.
  - Server & E2E: `120 passed in 65.00s`.
  - **Всего**: **251 passed** (все тесты зеленые).
- **Линтер и форматирование**:
  - `server/.venv/bin/ruff check .` — **All checks passed!**
  - `server/.venv/bin/ruff format --check .` — **190 files already formatted.**
- **Изоляция и чистота**:
  - `grep -rn "target_msg\.from_user" clients/telegram_bot` — **0 совпадений**.
  - `grep -rn --exclude-dir=.venv "from server\|from shared\|from edge_agent\|from clients.cli\|import server\|import shared" clients/telegram_bot` — **0 совпадений**.
  - Каталог `clients/telegram_bot/logs/` отсутствует/пуст.
  - Каталог `server/media/` содержит только `.gitkeep`.
  - `VERSION` сохранена: `0.3.1`.


---

## 4. Доработки и исправления: Раунд 2 (Ветка `stage-7/bot-fixes-2`)

### 4.1. Выполненные задачи
1. **Профиль и даты (`handlers/start_auth.py`):**
   - Добавлена поддержка команды `/profile` (и кнопки `[ 👤 Профиль / Выход ]`).
   - Отображаются `login`, `authorized_at`, `last_active_at` из ответа `GET /api/auth/me`.
   - Прочерк «—» отображается только при реальном значении `null` или пустой строке.
2. **Карточка установки (`utils/formatters.py`, `handlers/installations.py`):**
   - Форматирование адаптировано под реальный контракт `GET /api/installations/{id}`: `culture_name`, `aruco_size_mm`, `last_area_display`, `light_gpio_pin`, `flash_gpio_pin`, `is_archived`, `created_at`, камеры (`auto_name`, `hardware_id`).
   - После изменения или сброса размера ArUco в карточке выводится актуальное значение (при сбросе шлется `{"aruco_size_mm": None}`).
3. **Безопасный `user_id` в inline-кнопках:**
   - Значение `user_id` извлекается строго из `callback.from_user.id` и передается явным параметром (включая `_apply_interval` в `system.py` и `_finish_create_installation` в `installations.py`).
   - Проверка `grep -rn "target_msg\.from_user" clients/telegram_bot` дает строго 0 совпадений.
4. **Системные команды (`main.py`):**
   - При старте бота регистрируются команды `/start`, `/help`, `/version`, `/login`, `/cancel` через `bot.set_my_commands`.
5. **Подсказка при неизвестном сообщении (`handlers/common.py`):**
   - При получении произвольного текста вне FSM бот отвечает: `Не понял. Список команд: /help`.
6. **Быстрые баг-заметки (`handlers/common.py`, `utils/debug_logger.py`):**
   - Сообщение, начинающееся с `*`, перехватывается глобальным роутером в любом FSM-состоянии без сброса FSM.
   - Заметка сохраняется в буфер/файл, пользователю возвращается `Записано: BUG-LOG-N`.
7. **Структурированный JSONL-журнал и CLI-инструмент (`utils/debug_logger.py`, `tools/bug_log.py`):**
   - При `BOT_DEBUG_LOG=true` ведется ротируемый (7 дней) JSONL-лог `clients/telegram_bot/logs/` (добавлен в `.gitignore`).
   - Реализовано маскирование чувствительных данных (пароли, токен бота, client key).
   - CLI-утилита `python -m clients.telegram_bot.tools.bug_log` поддерживает команды `list`, `show <N>`, `tail`.
8. **Поддержка `TELEGRAM_PROXY` (`config.py`, `main.py`):**
   - Поддержаны протоколы `http://` и `socks5://`, учетные данные прокси маскируются в логах.
   - Повторные попытки с задержкой при сбоях связи с Telegram API.
   - Обновлены `.env.example` и `docs/BOT_SPEC.md` (п. 1.1).
9. **Улучшения мастера создания установки (`handlers/installations.py`):**
   - На шаге 1 выполняется проверка занятости имени (`GET /api/installations?include_archived=true`). При занятом имени бот просит ввести другое без сброса FSM. Финальная обработка 409 сохранена.
   - На шаге 5 выводится подсказка «по умолчанию 50 мм» и кнопка «⏭ Пропустить (по умолчанию 50 мм)», отправляющая `50.0`.
10. **Фейковый сервер Telegram Bot API и E2E тесты (`tests/e2e/fake_telegram.py`, `tests/e2e/test_bot_fixes_round2_e2e.py`):**
    - Реализован легковесный aiohttp-сервер Telegram Bot API для детерминированного тестирования aiogram long-polling, где `callback.message.from_user` — бот, а `callback.from_user` — пользователь.
    - Реализовано 5 сценариев E2E тестов, проверяющих все ключевые исправления раунда 2.

### 4.2. Итоговые результаты проверок
- **Unit-тесты Telegram-бота**: `38 passed in 0.39s`.
- **E2E тесты Telegram-бота (раунд 2)**: `5 passed in 16.19s`.
- **Регрессионные тесты всех компонентов**:
  - Edge Agent: `45 passed in 3.16s`.
  - CLI-клиент: `42 passed in 0.43s`.
  - Telegram-бот: `38 passed in 0.39s`.
  - Server & E2E: `118 passed in 58.31s`.
  - **Всего**: **243 passed**.
- **Линтер и форматирование**:
  - `server/.venv/bin/ruff check .` — **All checks passed!**
  - `server/.venv/bin/ruff format --check .` — **187 files already formatted.**
- **Изоляция и чистота**:
  - `grep -rn "target_msg\.from_user" clients/telegram_bot` — **0 совпадений**.
  - `grep -rn --exclude-dir=.venv "from server\|from shared\|from edge_agent\|from clients.cli\|import server\|import shared" clients/telegram_bot` — **0 совпадений**.
  - `git status` не содержит файлов баз данных `growvision.db*`.

