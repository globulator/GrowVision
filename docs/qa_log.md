# Журнал контроля качества (QA Log)

## 1. Открытые дефекты и замечания
*(Открытых дефектов нет)*

---

## 2. Исправленные дефекты (верифицировано QA)
- **[STAGE-7.3] [server, edge_agent, telegram_bot, cli] Авторизация по коду, сессии, CV-разметка, UX-полировка и фиксы раунда 5 (верифицировано QA)**
  - **Коммиты:** `6383ff5` (реализация), `d6a0385` (state) (ветка `stage-7/auth-and-polish`)
  - **Ключевые проверенные критерии:**
    1. Модели БД: `User.is_active` (default True), `User.logged_out_at` (nullable DateTime), таблица `invite_codes` (`InviteCode`, TTL 10 мин, 6-8 симв., role='biologist'), безопасный `ALTER TABLE` в `init_db`.
    2. Серверная логика: в `deps.py` авторизованными признаются только `is_active == True`; в `POST /api/auth/login` поле `login` опционально; CLI принимает только developer_password; Telegram — developer_password или активный invite-code. При первом входе без логина отдаётся `{"status": "needs_login", "role": role}` без сжигания кода. При успешном логине с именем код сжигается. Эндпоинты developer: `POST/GET/DELETE /api/auth/invites`, `GET /api/auth/sessions`, `POST /api/auth/sessions/{id}/terminate`, `GET /api/control/captures/{id}`, `duration_sec` в сборке видео.
    3. Telegram-бот: `/login` сразу запрашивает пароль/код (сообщение удаляется, логи маскируются), при `needs_login` запрос Имени и Фамилии, при повторном — логин подтягивается сам; имена кликабельны (`<a href="tg://user?id={external_id}">{html.escape(login)}</a>`); раздел «👥 Сессии» для developer (создание кода в `<code>`, список кодов с отзывом, список сессий, кнопка кика).
    4. CLI: вход разрешён только developer (пароль разработчика); команды `auth invite create|list|revoke`, `auth sessions`, `auth kick <id>`.
    5. Исправлены 3 бага живого теста: убрана мутация frozen callback.data в кнопке отмены удаления установки; приоритетное чтение `area_display` в галерее таймлапса; непустой `debug_log_line` при сканировании камер.
    6. CV-разметка: фиолетовые линейки BGR (240, 32, 160) слева и снизу от куста в см/px; красная точка в центре куста; темная обводка ко всем элементам; адаптивные метаданные в углах кадра.
    7. Mock ArUco: параметр `mock_overlay_aruco` (default=True) в конфиге, действует строго в `HARDWARE_MODE=mock`.
    8. UX в боте: ручной снимок (для dev одно фото с inline-переключателем «🧠 Разметка» / «🖼 Исходник», для bio — только raw); переключатели реле («💡 Свет: 🟢/⚪️», «⚡ Вспышка: 🟢/⚪️»); интерактивный выбор длительности таймлапса (рекомендованное от 10 fps, пресеты 5/10/30 с и ручной ввод).
  - **Результат тестирования:** 279 passed (Server & E2E 126, Edge 51, Bot 55, CLI 47), ruff clean, git status clean.

- **[STAGE-7.2] [telegram_bot, server, edge_agent, cli] Комплексные исправления раунд 4 (верифицировано QA)**
  - **Коммит:** `b2e4643` (ветка `stage-7/bot-fixes-4`)
  - **Ключевые проверенные критерии:**
    1. Кнопка «📷 Камеры установки» (`inst:cameras:{id}`) выводит привязанные онлайн/оффлайн камеры с кнопками управления и возврата к карточке (`inst:view:{id}`); зарегистрированы callback-заглушки `frm:close`, `frm:noop`, `pag:noop`.
    2. Галерея плановых кадров парсит `res.get("frames", [])` с фолбэком на list; гость видит только последний кадр без листания; биолог и разработчик — полную галерею с пагинацией и слоями.
    3. Блок `🐞 DEBUG` читает `debug_info.get("log_line")` («Команда:», «Лог:») с экранированием.
    4. Валидация GPIO `0 <= pin <= 53` в мастере и редактировании без сброса состояния FSM.
    5. Фильтрация архива: отображаются только `is_archived is True` при `show_archived=True`.
    6. Ручной снимок: для биолога — только `raw` через `send_photo`; для разработчика — альбом `raw` + `annotated`.
    7. `/version` извлекает `server_version`/`version` без «vнеизвестно», пример логина «Андрей Бармин», экранирование `username`.
    8. Зависимость `aiohttp-socks>=0.8.0`, при `TelegramUnauthorizedError` логирование `CRITICAL` и немедленный выход (`sys.exit(1)`).
    9. Удаление архивной установки с кадрами разрешено (`DELETE /api/installations/{id}` с очисткой БД и диска), для неархивной сохраняется `409 Conflict`.
    10. Сканирование камер: возвращаются только реально обнаруженные камеры (отключенные помечаются оффлайн и остаются в БД).
    11. Паритет `capture_delay_sec` (Server ↔ Edge ↔ Bot ↔ CLI): системная настройка 0.0..30.0 с (default 1.5 с), установка, бот меню и CLI команды.
    12. Edge: захват мьютекса `acquire_lock(blocking=True, timeout=10.0)`, раздельный `frame_index` с 0, ступенчатая формула `compute_plant_radius` и вращение листьев.
  - **Результат тестирования:** 268 passed (Edge 49, CLI 45, Bot 51, Server & E2E 123), ruff clean, git status clean.

- **[BUG-001]** | Критичность: `MAJOR` | Статус: `FIXED`
  - **Обнаружен в коммите:** `a155d98` -> **Исправлен и подтверждён в коммите:** `4e155b8`
  - **Где:** `clients/telegram_bot/api_client.py:login` (`POST /api/auth/login`)
  - **Суть проблемы:** Передавалось поле `"username"` вместо `"login"` по спецификации `API_CONTRACT.md` п. 4.1 и `BOT_SPEC.md` п. 5.1, из-за чего сервер возвращал 422 Unprocessable Entity и авторизация в боте была невозможна.
  - **Результат повторной проверки:** В `clients/telegram_bot/api_client.py` скорректирован ключ payload на `"login"`. Повторные сквозные e2e-тесты подтвердили успешный двухэтапный вход, удаление пароля из чата, переключение ролей и выход. Регрессионный тест в `tests/e2e/test_bot_e2e.py::test_bot_e2e_auth_lifecycle` успешно пройден.

- **[BUG-002]** | Критичность: `MAJOR` | Статус: `FIXED`
  - **Обнаружен в коммите:** `a155d98` -> **Исправлен и подтверждён в коммите:** `4e155b8`
  - **Где:** `clients/telegram_bot/api_client.py` (`POST /api/control/snap`, `POST /api/control/light/main`, `POST /api/control/light/flash`, `POST /api/control/relay-test`)
  - **Суть проблемы:** Передавался ключ `"installation_id"` (или `None`) вместо обязательного поля `"target"` по спецификации `API_CONTRACT.md` п. 4.4, из-за чего сервер возвращал 422 Unprocessable Entity и команды управления светом, реле и ручной съёмкой были невозможны.
  - **Результат повторной проверки:** Реализован хелпер `_resolve_target`, в тело запросов передаётся поле `target: int | Literal["all"]`. Повторные сквозные e2e-тесты подтвердили корректное управление основным светом, вспышкой, тестом реле и ручной съёмкой snap с альбомом из 2 фото. Регрессионные тесты `test_bot_e2e_control_and_snap` и `test_bot_e2e_installations_and_cameras` в `tests/e2e/test_bot_e2e.py` успешно пройдены.
- **[MAJOR] [server/core/system_status.py] Статус онлайна Raspberry Pi определялся по устаревшей схеме `rpi_last_heartbeat_at` вместо `updated_at` записи `rpi_last_telemetry`, нарушался порядок проверок эндпоинтов управления.**
  - **Эндпоинты:** `POST /api/control/*`, `POST /api/cameras/scan`, `GET /api/system/status`.
  - **Исправление:** Реализована единая функция `is_rpi_online(db)` по `rpi_last_telemetry.updated_at` (сравнение в UTC <= 90 с). Ключ `rpi_last_heartbeat_at` как ключ таблицы `system_settings` полностью исключён. Обеспечен строгий порядок проверок: авторизация (401/403) -> валидация тела (422) -> валидация target (404/409) -> статус онлайна RPi (503). В `wait_command_result` добавлен немедленный возврат 504 при статусе timeout. В timelapse video добавлен таймаут ffmpeg 600 с, пропуск отсутствующих файлов и чистый 500 detail.
- **[MAJOR] [server/api/control.py] Значение `target: true` в JSON запросах эндпоинтов управления не валидируется и неявно приводится к целому числу 1.**
  - **Эндпоинты:** `POST /api/control/snap`, `POST /api/control/light/main`, `POST /api/control/light/flash`, `POST /api/control/relay-test`.
  - **Исправление:** В моделях `SnapRequest`, `LightRequest`, `RelayTestRequest` добавлен параметр `mode="before"` в `@field_validator("target", mode="before")` и явная проверка `isinstance(v, bool)` с генерацией `ValueError("target не может быть boolean")` -> 422 Unprocessable Content. Верифицировано в регрессионных e2e-тестах.
- **[MAJOR] [clients/cli/commands/system.py] Парсинг телеметрии Raspberry Pi в команде `system status` не поддерживал вложенную структуру `software`/`hardware` ответа сервера.**
  - **Эндпоинт/Команда:** `system status` (Client API `GET /api/system/status`).
  - **Исправление:** Реализован парсинг вложенных словарей `software` и `hardware` с fallback на плоский формат. Аптайм форматируется только при наличии числового значения. Добавлены unit-тесты в `clients/cli/tests/test_system.py` и сквозной e2e-тест `test_cli_e2e_system_status_telemetry` в `tests/e2e/test_cli_e2e.py`. Все поля телеметрии отображаются корректно.
- **[MAJOR] [edge_agent, clients/cli] Доработки боевого режима Edge-агента для Raspberry Pi 5 (rpi) и обработки ошибок команд в CLI.**
  - **Компоненты:** `edge_agent`, `clients/cli`.
  - **Исправление:**
    1. Реализован строгий боевой режим (`HARDWARE_MODE=rpi`): исключены молчаливые подмены (падение без `gpiozero`/`lgpio`, ошибка при отсутствии `cv2`, `measurements: []` и передача сырого кадра в `annotated_image` без выдуманных площадей при сбое или отсутствии ONNX-модели с логированием `ERROR`).
    2. Сканирование камер переведено на поиск в `/dev/v4l/by-id/*-video-index0` со стабильным `hardware_id` и открытием по `os.path.realpath`.
    3. Добавлен прогрев камер `CAMERA_WARMUP_FRAMES` (5) и задержка переключения реле `RELAY_SWITCH_DELAY_MS` (200 мс).
    4. Логика реле: основной свет в норме ВКЛЮЧЕН (`light=1, flash=0`), на время кадра `light=0, flash=1`, возврат в исходное в `finally`. Поддержана полярность `RELAY_ACTIVE_HIGH`.
    5. Команда SNAP снимает все камеры установки (для target="all" — все активные установки); статус `done` при наличии хотя бы 1 кадра.
    6. Индексация растений упорядочена: сортировка контуров по убыванию площади, нумерация с 0 подряд.
    7. Офлайн-очередь: асинхронный сброс в отдельном потоке (до 10 пакетов под Lock), атомарная запись пакета через замену временной папки, 3 попытки `send_command_result`, таймаут загрузки кадра 60 с, защита `failed/.gitkeep`.
    8. Планировщик автосъемки переведен на астрономическую сетку UTC (`HH % N == 0 and MM == 0`).
    9. В CLI команды `camera scan`, `snap take`, `light main/flash/test` при `status == "error"` выводят ошибку в stderr и завершаются с кодом 1; `system status` выводит `model_name`.
    10. Устранены реальные задержки `sleep` в тестах, БД изолирована в тестах e2e (в корне нет `growvision.db*`, `git status --short` чист). Регрессионный набор (180 тестов) пройден успешно.
- **[MAJOR] [edge_agent] Переключение реле на каждом Heartbeat, сброс состояния света после RELAY_TEST и некорректный резолв путей к модели.**
  - **Компонент:** `edge_agent`.
  - **Исправление:**
    1. Инициализация реле переведена на отслеживание новых установок (`_initialized_installations`) под неблокирующим аппаратным мьютексом (`acquire_lock(blocking=False)`); повторный heartbeat не меняет пины, ручные команды `LIGHT_MAIN off` сохраняются.
    2. В `HardwareManager` реализован метод `get_pin_state(pin) -> bool`, команда `RELAY_TEST` фиксирует исходные состояния пинов и восстанавливает их в блоке `finally` (включённый свет остаётся включённым).
    3. Относительные пути (`MODEL_PATH`, `OFFLINE_QUEUE_DIR`) резолвятся относительно корня репозитория (`REPO_ROOT`); зафиксирован единый путь к модели `edge_agent/models/lettuce_seg_v1.onnx` во всех конфигурациях и документации; в `DEPLOYMENT_RPI.md` строка `<OWNER>` заменена на «ваш аккаунт GitHub».
    4. Добавлены unit-тесты (39 тестов), полный регрессионный набор (184 теста) пройден успешно.

---

## 3. Рекомендации по улучшению UX и новым проверкам
- *(Выполнено в stage-4/fixes)* Устранено дублирование логики очистки таймаутов в `server/api/edge.py` (`edge_claim_command`) вызовом `expire_stale_commands(db, now)` из `server.core.timeouts`.
