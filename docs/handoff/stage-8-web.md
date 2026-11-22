# Handoff: Этап 8 — Полноценный Web SPA-клиент (роли, паритет с ботом, плеер таймлапса, консоль разработчика)

## 1. Контекст и цели этапа
- **Ветка:** `stage-8/web-spa`
- **Режим:** FULL
- **Цели:**
  1. Реализовать серверную поддержку авторизации `client_type = "web"` с использованием безопасной `HttpOnly`-cookie (`SameSite=Lax`, `Path=/`) без передачи `X-Client-Key` из браузерного JavaScript.
  2. Поддержать раздачу статических файлов `clients/web/` через FastAPI `StaticFiles` на корневой маршрут `/` без конфликтов с API-эндпоинтами (`/api/*`) и медиа-файлами (`/media/*`).
  3. Создать полноценный Web SPA-клиент на чистых HTML5, CSS3 и ES-модулях JavaScript (без Node.js, сборщиков, фреймворков и внешних CDN):
     - Ролевая модель: Гость (чтение установок и плеер), Биолог (редактирование установок, ручные снимки `raw`, тумблеры света и вспышки, плеер `raw`, экспорт MP4, смена имени), Разработчик (полный доступ).
     - Установки: создание и редактирование с валидацией GPIO (0..53) и предупреждением об общих пинах, архивация, удаление.
     - Плеер таймлапса по `docs/WEB_SPEC.md`: автоопределение камер с порогом 10 мин, параллельная предзагрузка до 6 запросов с индикатором и блокировкой управления, Play/Pause, ползунок, шаги, скорости 1x/2x/4x, зацикливание, переключатель слоев `🖼 Оригинал` / `🧠 Разметка`, локальное время браузера, экспорт MP4.
     - Камеры: сканирование с debug-логом, привязка/отвязка, редактирование названий.
     - Свет и реле: тумблеры с синхронизацией по пинам + кнопка «Тест реле».
     - Сессии: генерация инвайт-кодов для биологов (TTL 10 мин, копирование), список кодов с отзывом, мониторинг сессий, переименование и кик.
     - Система: статус Edge-агента, `debug_mode`, `auto_capture`, задержка съёмки.
     - Вкладка «💻 Терминал»: интерактивная консоль GrowVision (`help`, `status`, `unit`, `cam`, `light`, `flash`, `snap`, `relay-test`, `auth`, `sys`, `clear`) с историей ввода (`↑/↓`).
  4. Актуализация спецификаций и покрытие unit/E2E тестами.

---

## 2. Реализованные изменения

### 2.1. База данных и модели (`server/db/`)
- `server/db/models.py`:
  - В `User.__table_args__` обновлено ограничение: `CheckConstraint("client_type IN ('telegram', 'cli', 'web')", name="ck_users_client_type")`.

### 2.2. Серверная веб-авторизация и зависимости (`server/api/`)
- `server/api/deps.py`:
  - Добавлены функции генерации и проверки сессионных кук: `create_session_cookie(user)` и `verify_session_cookie(cookie_val, db)` с криптографической HMAC-подписью.
  - В `get_current_user_optional` и `get_current_user` добавлена прозрачная поддержка куки `gv_session: Annotated[str | None, Cookie()] = None`. Если кука присутствует и валидна, веб-пользователь авторизуется без необходимости передачи заголовка `X-Client-Key`.
- `server/api/auth.py`:
  - В `LoginRequest` добавлен тип клиента `"web"`.
  - Добавлены Pydantic-модели: `WebLoginRequest`, `WebLoginResponse`, `WebMeResponse`.
  - Эндпоинт `POST /api/auth/web/login`: вход по паролю разработчика или инвайт-коду биолога. При первом входе без логина возвращает `{"status": "needs_login", "role": role}` без сжигания кода. При успешном входе устанавливает HttpOnly-cookie `gv_session`. При повторном входе подтягивает сохранённое имя пользователя.
  - Эндпоинт `POST /api/auth/web/logout`: деактивация сессии (`is_active = False`) и удаление сессионной куки.
  - Эндпоинт `GET /api/auth/web/me`: проверка текущей сессии (возвращает данные пользователя или `{"role": "guest", "login": null}`).

### 2.3. Клиент Web SPA (`clients/web/`)
- `clients/web/index.html`: адаптивная HTML5-разметка двух экранов и 7 вкладок, модальные окна входа, редактирования установки, ручного снимка, экспорта видео и контейнер всплывающих тостов.
- `clients/web/style.css`: современный лаконичный стиль оформления с зеленой гидропонной палитрой, системными шрифтами (оффлайн-работа) и полной адаптивностью от 360 px.
- `clients/web/js/api.js`: модуль взаимодействия с сервером через `fetch()` (`credentials: 'same-origin'`). Не содержит секретов и `X-Client-Key`.
- `clients/web/js/player.js`: плеер таймлапса в точном соответствии с `docs/WEB_SPEC.md` (автоопределение камер, параллельная предзагрузка до 6 картинок, шкала, скорости 1x/2x/4x, зацикливание, слои, локальное время, экспорт MP4).
- `clients/web/js/units.js`: управление гидропонными установками (список, архивные, создание, редактирование с валидацией 0..53 и предупреждениями, удаление, ручные снимки).
- `clients/web/js/relays.js`: управление освещением и вспышкой с синхронизацией по пинам и тестом реле.
- `clients/web/js/cameras.js`: список камер, сканирование подключенных устройств с выводом debug-лога, привязка/отвязка, редактирование названия.
- `clients/web/js/sessions.js`: генерация одноразовых инвайт-кодов (TTL 10 мин) с копированием, отзыв кодов, список сессий всех клиентов с переименованием и киком.
- `clients/web/js/system.js`: мониторинг Raspberry Pi (uptime, CPU, RAM, температура, диск), переключатели `debug_mode`, `auto_capture` и задержка съёмки.
- `clients/web/js/terminal.js`: интерактивная веб-консоль GrowVision с набором встроенных команд (`help`, `status`, `unit`, `cam`, `light`, `flash`, `snap`, `relay-test`, `auth`, `sys`, `clear`) и историей стрелками.
- `clients/web/js/app.js`: главный контроллер SPA, управление ролями и модальными окнами.

### 2.4. Документация (`docs/`)
- `docs/API_CONTRACT.md`: описана cookie-авторизация Web SPA (`POST /api/auth/web/login`, `POST /api/auth/web/logout`, `GET /api/auth/web/me`) и поддержка куки в защищённых эндпоинтах.
- `docs/USER_GUIDE.md`: добавлен раздел 8 («Веб-интерфейс GrowVision (Web SPA)») с описанием ролей, возможностей плеера и терминала.

---

## 3. Результаты тестирования
- **Server unit-тесты:** `server/.venv/bin/python -m pytest server/tests -q` — 67 passed (включая `test_stage8_web_auth.py`).
- **CLI unit-тесты:** `clients/cli/.venv/bin/python -m pytest clients/cli/tests -q` — 50 passed.
- **Telegram Bot unit-тесты:** `clients/telegram_bot/.venv/bin/python -m pytest clients/telegram_bot/tests -q` — 59 passed.
- **Edge Agent unit-тесты:** `edge_agent/.venv/bin/python -m pytest edge_agent/tests -q` — 51 passed.
- **E2E тесты:** `server/.venv/bin/python -m pytest tests/e2e/test_stage8_web_e2e.py -q` — 1 passed.
- **Все E2E тесты:** `server/.venv/bin/python -m pytest tests/e2e -q` — 67 passed.
- **Общий объём тестов:** **294 теста passed** (без sleep, быстрые, <60 с на полный прогон).
- **Линтер и форматтер:** `ruff check .` и `ruff format --check .` чисты (222 файла).
