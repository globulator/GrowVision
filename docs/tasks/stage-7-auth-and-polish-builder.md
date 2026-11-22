# Задача: Этап 7.3 — Авторизация по одноразовому коду, управление сессиями, доработки CV-разметки, UX-полировка и фиксы раунда 5
Режим: FULL
Ветка: stage-7/auth-and-polish
Цель: Реализовать авторизацию по одноразовым кодам и управление сессиями, исправить подтверждённые баги живого теста, улучшить CV-разметку (линейки, центр, метаданные), добавить mock ArUco overlay и UX-улучшения в Telegram-боте и CLI.

## Прочитать перед началом:
- `docs/PROJECT.md` (включая раздел 9 — правила скорости и тестов)
- `docs/API_CONTRACT.md`
- `docs/DB_SCHEMA.md`
- `docs/BOT_SPEC.md`
- `docs/CLI_SPEC.md`
- `docs/USER_GUIDE.md`
- `docs/handoff/bot.md`

## Правила скорости и тестов (Раздел 9 PROJECT.md):
1. Тесты быстрые: один тест не дольше 3 с (тест с настоящим ffmpeg до 10 с), весь набор не дольше 60 с. Настоящие ожидания (sleep, ответ RPi 10/15 с, пороги 30/60/90 с) в тестах запрещены: константы времени подменяются через monkeypatch.
2. Состояние для тестов готовится через публичные эндпоинты (RPi онлайн только через POST /api/edge/heartbeat). Прямая запись в БД допустима только для данных, у которых нет эндпоинта.
3. QA и Builder выносят общие фикстуры в conftest.py и используют их повторно.
4. Любой запуск тестов выполняется как `timeout 300 ... pytest`, у вызовов ffmpeg стоит timeout.
5. Две подряд неудачные попытки починить один и тот же тест: стоп, вернуть статус BLOCKED с описанием причины, не делать третью попытку наугад.
6. Чистый git status: после прогона тестов и работы не должно оставаться временных файлов (например `growvision.db*`, файлов в `server/media/` кроме `.gitkeep`). Линтер и форматтер `ruff` должны проходить без ошибок.

## Allowed Paths (можно создавать и менять только это):
- `server/`
- `edge_agent/`
- `clients/telegram_bot/`
- `clients/cli/`
- `shared/`
- `tests/e2e/`
- `docs/API_CONTRACT.md`
- `docs/BOT_SPEC.md`
- `docs/CLI_SPEC.md`
- `docs/DB_SCHEMA.md`
- `docs/USER_GUIDE.md`
- `docs/handoff/`
- `VERSION`

## Только чтение:
- `docs/PROJECT.md`
- `docs/STATE.md` (ведёт только Coordinator)
- `docs/team_roles/`

---

## Критерии приёмки:

### Часть A. Авторизация по одноразовому коду и управление сессиями
1. **Модель БД (`server/db/models.py`, `server/db/database.py`):**
   - В `User` (`users`) добавить `is_active = Column(Boolean, nullable=False, default=True)` и `logged_out_at = Column(DateTime(timezone=True), nullable=True)`. При выходе (`logout`) или принудительном завершении сессии (кике) запись `User` **не удалять**, а переводить в `is_active = False` с фиксацией `logged_out_at` (сохраняется история сессий и привязка `login` к `external_id`).
   - Добавить таблицу `invite_codes` (`InviteCode`): `id`, `code` (уникальная строка 6–8 символов), `role` (по умолчанию `'biologist'`), `created_by_user_id` (FK `users.id`), `created_at`, `expires_at` (`created_at + 10 минут`), `used_at` (nullable), `used_by_user_id` (FK `users.id`, nullable), `is_revoked` (Boolean, default=False).
   - Обеспечить безопасную миграцию/добавление новых колонок (включая `installations.capture_delay_sec`, `users.is_active`, `users.logged_out_at`) и таблицы `invite_codes` в `server/db/database.py` при старте на существующей SQLite БД.

2. **Серверная логика авторизации и сессий (`server/api/auth.py`, `server/api/deps.py`):**
   - В `deps.py` (`get_current_user`, `get_current_user_optional`) считать авторизованными только `User` с `is_active == True`.
   - `POST /api/auth/login`: поле `login` сделать опциональным (`login: str | None = None`).
     - Для `client_type == "cli"` принимать **только** постоянный пароль разработчика (`settings.developer_password`).
     - Для `client_type == "telegram"`: `settings.developer_password` -> `developer`; активный одноразовый код из `invite_codes` (`used_at IS NULL`, `is_revoked == False`, `expires_at > now`) -> `biologist`.
     - Если `(client_type, external_id)` уже есть в `users` и `body.login` не передан — подтягивать сохранённый `user.login`.
     - Если записи в `users` нет и `body.login` не передан (первый вход) — **не сжигать** код, вернуть `{"status": "needs_login", "role": role}`, чтобы бот запросил Имя и Фамилию и повторил запрос с `login`.
     - При успешном завершении входа по коду сразу пометить его использованным (`used_at = now`, `used_by_user_id = user.id`), активировать сессию (`is_active = True`, `logged_out_at = None`, обновить `authorized_at`, `last_active_at`).
   - Новые эндпоинты (только `developer`):
     - `POST /api/auth/invites` — генерация одноразового кода (TTL 10 мин);
     - `GET /api/auth/invites` — список активных неиспользованных кодов;
     - `DELETE /api/auth/invites/{invite_id}` — отзыв неиспользованного кода (`is_revoked = True`);
     - `GET /api/auth/sessions` — список всех сессий (активных и неактивных) с `id`, `client_type`, `external_id`, `login`, `role`, `is_active`, `authorized_at`, `last_active_at`, `logged_out_at`;
     - `POST /api/auth/sessions/{session_id}/terminate` — принудительное завершение любой активной сессии (и биолога, и другого разработчика).

3. **Telegram-бот — авторизация, кликабельные имена и раздел «👥 Сессии» (`clients/telegram_bot/`):**
   - Вход (`/login`): сразу запрашивать пароль или одноразовый код (без предварительного логина; сообщение удалять, в логах маскировать). Если сервер вернул `needs_login`, запросить Имя и Фамилию (пример: `Андрей Бармин`) и завершить вход. При повторном входе после выхода/кика имя подтягивается само.
   - Везде в боте, где выводится имя Telegram-пользователя, рендерить кликабельную ссылку по цифровому ID: `<a href="tg://user?id={external_id}">{html.escape(login)}</a>` (не использовать `@username`).
   - Добавить раздел **«👥 Сессии»** (только `developer`): генерация кода для биолога (код в `<code>`, срок 10 мин) + список активных кодов с кнопкой «❌ Отозвать»; список всех активных и неактивных сессий; карточка конкретного пользователя (кликабельное имя, роль, статус, время авторизации и последнего действия) с кнопкой «🚫 Завершить сессию».

4. **CLI (`clients/cli/`):**
   - Оставить вход в CLI только для роли `developer` (логин + пароль разработчика).
   - Добавить команды `auth invite create|list|revoke`, `auth sessions`, `auth kick <session_id>`.

---

### Часть B. Исправления багов и улучшения по итогам живого теста (раунд 5)
5. **Исправить 3 подтверждённых бага из живого теста:**
   - **Кнопка отмены при удалении установки (`clients/telegram_bot/handlers/installations.py:677`):** убрать мутацию замороженного объекта `callback.data = f"inst:view:{inst_id}"` (падает с `ValidationError: Instance is frozen`) и вызывать отображение карточки установки напрямую по `inst_id` (проверить все аналогичные `cnf:*:no` хэндлеры).
   - **Площадь в галерее кадров (`clients/telegram_bot/handlers/timelapse.py:275-282`):** сервер в `/api/timelapse/{id}/frames` отдаёт готовое поле `area_display`. Читать сначала `target_frame.get("area_display")`, и только если его нет — фолбэк на `total_area_cm2` / `total_area_px` (как уже сделано в `control.py:470-480`).
   - **Пустой `Лог:` в `🐞 DEBUG` при сканировании камер (`edge_agent/orchestrator.py:364`, `_handle_scan_cameras`):** добавить передачу непустого `debug_log_line` в `CommandResult(...)` (например `f"[CAM] scan_cameras -> found {len(cameras)}: {[c.hardware_id for c in cameras]}"`), чтобы блок `🐞 DEBUG` в боте выводил и `Команда:`, и `Лог:`.

6. **Улучшение CV-разметки на `annotated`-кадре (`edge_agent/` или `server/` — где формируется `annotated`):**
   - На кадре с разметкой накладывать **линейку слева и снизу** от обнаруженного растения (в `см`, если калибровано по ArUco, иначе в `px`), яркого **фиолетового** цвета (например BGR `(240, 32, 160)`).
   - В центре каждого обнаруженного куста/области растения ставить **красную точку** (архитектурно поддержать цикл по найденным объектам, сейчас в MVP один куст).
   - В углах кадра аккуратно и адаптивно к разрешению камеры (масштабировать шрифт и толщину линий от `min(width, height)`) выводить: **площадь растения**, **название установки**, **культуру**, **имя камеры**, **время съёмки**.
   - Ко всем элементам разметки (текст, линейка, контуры, точка) добавить тонкую тёмную **обводку** для читаемости на любом фоне.

7. **Наложение ArUco-маркера в mock-режиме (`edge_agent/config.py`, `edge_agent/synthetic.py`):**
   - Вынести наложение синтетического ArUco-маркера в отдельный параметр конфигурации (например `MOCK_OVERLAY_ARUCO` / `mock_overlay_aruco`, по умолчанию `true`), расположить рядом с параметром интервала mock-съёмки (10 сек). Действует **только** в `HARDWARE_MODE=mock`, в реальном режиме `rpi` никогда не накладывается.

8. **UX-улучшения в Telegram-боте (`clients/telegram_bot/`):**
   - **Переключение исходник/разметка для ручного снимка:** для роли `developer` отправлять ручной снимок с inline-кнопкой переключения «Исходник / Разметка» (как в галерее таймлапса), для `biologist` по-прежнему только `raw` без разметки.
   - **Меню управления реле (общее и конкретной установки):** показывать текущее состояние света и вспышки, а вместо 4 кнопок («Вкл»/«Выкл» для света и вспышки) сделать **2 кнопки-переключателя** с целевым действием (например, если свет включён — кнопка «💡 Свет: выкл», если выключен — «💡 Свет: вкл»; аналогично для вспышки).
   - **Расширенные настройки таймлапса:** после выбора установки (и камеры, если их больше одной) добавить шаг выбора длительности видео с кнопкой рекомендованного значения по умолчанию (автоматически рассчитанного для максимальной плавности видео от числа кадров) + пресеты/ручной ввод длительности.

---

### Часть C. Документация, тесты и версионирование
9. **Обновление спецификаций:**
   - Обновить `docs/DB_SCHEMA.md`, `docs/API_CONTRACT.md`, `docs/BOT_SPEC.md`, `docs/CLI_SPEC.md`, `docs/USER_GUIDE.md`.
10. **Покрытие тестами:**
    - Покрыть unit- и E2E-тестами все пункты 1–8 (включая `cnf:delete:*:no`, `area_display` в галерее, непустой `debug_log` при `scan_cameras`, одноразовые коды, кик сессий, `tg://user?id=...`, переключатели реле, линейку и оверлей на `annotated`, `mock_overlay_aruco`).
11. **Проверки качества:**
    - Все тесты репозитория зелёные, `ruff check .` и `ruff format --check .` чистые, `git status --short` пуст.
    - Формат коммитов: `тип(scope): описание`, scope обязателен.

Файл заметок: `docs/handoff/bot.md`
Команды проверки:
- `server/.venv/bin/python -m pytest server/tests tests/e2e -q`
- `edge_agent/.venv/bin/python -m pytest edge_agent/tests -q`
- `clients/cli/.venv/bin/python -m pytest clients/cli/tests -q`
- `clients/telegram_bot/.venv/bin/python -m pytest clients/telegram_bot/tests -q`
- `server/.venv/bin/ruff check .`
- `server/.venv/bin/ruff format --check .`
