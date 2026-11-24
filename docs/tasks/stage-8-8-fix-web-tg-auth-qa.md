# Задача: Этап 8.8 / QA-Tester (Fix Web TG Auth)

Режим: FAST
Ветка: stage-8/fix-web-tg-auth
Коммит Builder: ff9b3c6
Handoff Builder: docs/handoff/stage-8-8-fix-web-tg-auth.md
Журнал проверок: docs/qa_log.md

Прочитать перед началом:
- docs/PROJECT.md (особенно раздел 9: правила скорости и тестов — ОБЯЗАТЕЛЬНО)
- docs/WEB_SPEC.md
- docs/BOT_SPEC.md
- docs/API_CONTRACT.md
- docs/STATE.md
- docs/handoff/stage-8-8-fix-web-tg-auth.md

Цель: Провести независимую сквозную верификацию доработок этапа 8.8:
1. Удаление проверки личного пароля:
   - Проверить, что завершение веб-регистрации через `POST /api/auth/web/register/complete` успешно проходит без передачи поля `password` и без ошибки валидации.
2. Реальный `bot_username`:
   - Проверить, что `GET /api/version` и `GET /api/auth/web/me` возвращают поле `bot_username` (через Telegram API `getMe` или fallback), а ссылки `https://t.me/<bot_username>` в вебе используют это значение.
3. Бесшовный поток входа и регистрации:
   - Проверить HTML/CSS `#modal-register` (закрытый `password-group`, корректные стили отображения без наложения или скрытия).
   - Проверить, что при возврате 403 `telegram_verification_required` форма входа автоматически стартует регистрацию, сохраняет `accessCode` и открывает модалку сразу на Шаге 2 (ввод кода), а на Шаге 3 подставляет `recommended_name` и сохранённый код доступа.
4. Отслеживание статуса доставки и валидация `/start`:
   - Проверить, что для неизвестного числового TG ID возвращается 400 с понятным требованием нажать `/start` в боте.
   - Проверить фиксацию `delivery_error` через `POST /api/auth/web/pending-codes/{code_id}/fail` и опрос статуса через `GET /api/auth/web/register/code-status`.
5. Тесты и линтер:
   - Запустить автотесты и линтеры.

Команды проверки:
- `server/.venv/bin/python -m pytest server/tests -q`
- `clients/telegram_bot/.venv/bin/python -m pytest clients/telegram_bot/tests -q`
- `server/.venv/bin/python -m pytest tests/e2e -q`
- `server/.venv/bin/ruff check .`
- `server/.venv/bin/ruff format --check .`
- `git status --short` (проверка чистоты рабочей директории)

Результат зафиксировать в docs/qa_log.md и опубликовать отчёт на swarm board:
- `QA_VERDICT: PASS`
- или `QA_VERDICT: FAIL` с перечнем дефектов (ID).
