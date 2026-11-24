# Задача: Этап 8.8 / Builder (Fix Web TG Auth)

Режим: FAST
Ветка: stage-8/fix-web-tg-auth
Цель: Исправить авторизацию Web SPA через Telegram: отказ от проверки личных паролей на сервере, получение реального bot_username с fallback, бесшовный переход от формы логина к коду верификации, отслеживание статуса доставки одноразовых кодов через бота.

Прочитать перед началом:
- docs/PROJECT.md (раздел 9: правила скорости и тестов)
- docs/WEB_SPEC.md
- docs/BOT_SPEC.md
- docs/API_CONTRACT.md
- docs/STATE.md

Allowed Paths:
- server/
- clients/web/
- clients/telegram_bot/
- tests/e2e/
- docs/handoff/stage-8-8-fix-web-tg-auth.md
- docs/tasks/

Только чтение:
- edge_agent/
- shared/
- docs/DB_SCHEMA.md
- docs/PROJECT.md
- docs/REPO_AND_VERSIONING.md
- VERSION

Критерии приёмки:
1. Удаление проверки личного пароля в register/complete на сервере:
   - В `server/api/auth.py` в эндпоинте `POST /api/auth/web/register/complete` и схеме `WebRegisterCompleteRequest` убрана обязательная проверка длины личного пароля (пароли пользователей упразднены).
2. Передача реального `bot_username` с сервера:
   - В `server/core/bot_info.py` реализована функция получения `bot_username` через Telegram API `getMe` по токену `TELEGRAM_BOT_TOKEN` с кешированием в памяти и fallback на `BOT_USERNAME`/`GrowVisionBot`.
   - Эндпоинты `GET /api/version` и `GET /api/auth/web/me` отдают реальный `bot_username`.
3. Бесшовный вход и регистрация в Web SPA:
   - В `clients/web/index.html` закрыт тег `.password-group` перед подсказкой ссылки на бота.
   - В `clients/web/style.css` добавлены явные правила для `.reg-step`, `.reg-step.hidden` и `#modal-register.hidden`.
   - В `clients/web/js/auth.js` при HTTP 403 `telegram_verification_required` из формы логина автоматически вызывается `api.registerStart(telegram)`, сохраняется `accessCode`, открывается модалка сразу на Шаге 2 (ввод кода), а на Шаге 3 предзаполняются имя и `accessCode`.
   - Добавлен переход со ссылки `#link-to-login` на форму входа.
4. Предупреждения при невозможности доставить код в Telegram:
   - Проверка `/start` и для числового TG ID в `resolve_telegram_contact` и `auth_web_login`: возвращать HTTP 400 с текстом о необходимости открыть бота и нажать `/start`.
   - Отслеживание статуса доставки: колонка `delivery_error` в `web_verification_codes`, эндпоинты `POST /api/auth/web/pending-codes/{code_id}/fail` и `GET /api/auth/web/register/code-status?telegram_id=...`.
   - В `clients/telegram_bot/` (`api_client.py` и `main.py`) сообщать серверу о провале доставки кода.
   - В `clients/web/js/auth.js` на Шаге 2 опрашивать статус доставки и при ошибке выводить inline-предупреждение в `#reg-error-step2`.
5. Тесты и линтер:
   - Зеленый `test_stage8_web_e2e.py` и полный E2E/Unit suite.
   - Чистый `ruff check .` и `ruff format --check .`.
   - 0 эмодзи в `clients/web/`, 0 alert/confirm/prompt.
