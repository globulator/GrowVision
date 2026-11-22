# Карточка задачи: QA-проверка Этапа 7.3 (Авторизация по коду, управление сессиями, CV-разметка, UX-полировка и фиксы раунда 5)

- **Компонент**: `server`, `edge_agent`, `clients/telegram_bot`, `clients/cli`, `shared`
- **Ветка**: `stage-7/auth-and-polish`
- **Режим**: FULL
- **Спецификации**: `docs/PROJECT.md` (включая раздел 9), `docs/API_CONTRACT.md`, `docs/DB_SCHEMA.md`, `docs/BOT_SPEC.md`, `docs/CLI_SPEC.md`, `docs/USER_GUIDE.md`
- **Отчёт реализации**: `docs/handoff/bot.md`

---

## Цель проверки
Провести независимое тестирование и верификацию реализации Этапа 7.3:

### Часть A. Авторизация по коду и сессии
1. **Модели БД и миграции (`server/db/models.py`, `server/db/database.py`):**
   - В `User`: поля `is_active` (default True), `logged_out_at` (nullable DateTime). При `logout` или завершении сессии запись не удаляется, а переводится в `is_active=False` с фиксацией времени.
   - Таблица `invite_codes` (`InviteCode`): `id`, `code` (уникальный 6–8 символов), `role` (default 'biologist'), `created_by_user_id`, `created_at`, `expires_at` (10 мин), `used_at`, `used_by_user_id`, `is_revoked`.
   - Безопасная миграция/добавление колонок при старте на существующей SQLite БД.
2. **Серверные эндпоинты (`server/api/auth.py`, `server/api/deps.py`):**
   - `deps.py`: авторизованными считаются только пользователи с `is_active == True`.
   - `POST /api/auth/login`: опциональный `login`. Для CLI только `developer_password`. Для Telegram: `developer_password` -> developer; одноразовый код -> biologist. При первом входе без логина возвращается `{"status": "needs_login", "role": role}` без сжигания кода. При повторном входе подтягивается сохранённый логин. При успешном входе код сжигается (`used_at`, `used_by_user_id`).
   - Эндпоинты разработчика:
     - `POST /api/auth/invites` — создание кода (TTL 10 мин);
     - `GET /api/auth/invites` — активные неиспользованные коды;
     - `DELETE /api/auth/invites/{invite_id}` — отзыв кода (`is_revoked=True`);
     - `GET /api/auth/sessions` — список всех сессий (активные и неактивные);
     - `POST /api/auth/sessions/{session_id}/terminate` — принудительное завершение сессии.
3. **Telegram-бот (`clients/telegram_bot/`):**
   - Вход `/login`: сразу запрос пароля/кода (сообщение удаляется, логи маскируются). При `needs_login` запрос Имени и Фамилии. При повторном входе имя подтягивается автоматически.
   - Имена пользователей выводятся кликабельной ссылкой: `<a href="tg://user?id={external_id}">{html.escape(login)}</a>`.
   - Раздел «👥 Сессии» для разработчика: генерация кода биолога (`<code>`), список кодов с отзывом, список сессий, карточка пользователя с завершением сессии.
4. **CLI (`clients/cli/`):**
   - Вход в CLI только для `developer`.
   - Команды `auth invite create|list|revoke`, `auth sessions`, `auth kick <session_id>`.

### Часть B. Исправления живого теста и UX
5. **3 подтверждённых бага из живого теста:**
   - Отмена при удалении установки (`cnf:delete:*:no`): не мутировать frozen объект `callback.data`, открывать карточку напрямую.
   - Галерея таймлапса: чтение `target_frame.get("area_display")` в первую очередь с фолбэком на `total_area_cm2`/`total_area_px`.
   - `Лог:` в `🐞 DEBUG` при `scan_cameras`: передача непустого `debug_log_line` в `CommandResult`.
6. **CV-разметка (`annotated`):**
   - Фиолетовая линейка слева и снизу от куста (в см при ArUco, иначе в px).
   - Красная точка в центре обнаруженного растения.
   - Угловые метаданные (площадь, установка, культура, камера, время) с масштабированием шрифта и тёмной обводкой.
7. **Mock ArUco overlay (`edge_agent/`):**
   - Параметр `MOCK_OVERLAY_ARUCO` (default true) в конфиге mock-режима. В реальном `rpi` никогда не накладывается.
8. **UX-улучшения в боте:**
   - Ручной снимок: для разработчика inline-кнопка переключения «Исходник / Разметка», для биолога — только raw.
   - Меню реле: 2 кнопки-переключателя с целевым действием вместо 4 раздельных.
   - Таймлапс: выбор длительности с рекомендованным значением по умолчанию и пресетами.

### Часть C. Раздел 9 PROJECT.md и качество
9. Быстрые тесты (<3 с на тест, весь набор <60 с, без `sleep`).
10. Чистый `git status --short`, линтер `ruff check .` и `ruff format --check .` без ошибок.

---

## Формат отчёта QA
- Вердикт: `PASS` или `FAIL`
- Проверенные коммиты
- Число выполненных критериев и тестов
- Список дефектов в `docs/qa_log.md` (если обнаружены)
