# Задача: Этап 8.7 / QA-Tester (UX Polish & Realtime)

Режим: FULL
Ветка: stage-8/ux-polish-and-realtime
Коммит Builder: 2a7df59
Handoff Builder: docs/handoff/stage-8-7-ux-polish-and-realtime.md
Журнал проверок: docs/qa_log.md

Прочитать перед началом:
- docs/PROJECT.md (особенно раздел 9: правила скорости и тестов — ОБЯЗАТЕЛЬНО)
- docs/WEB_SPEC.md
- docs/BOT_SPEC.md
- docs/API_CONTRACT.md
- docs/STATE.md
- docs/handoff/stage-8-7-ux-polish-and-realtime.md

Цель: Провести независимую сквозную верификацию доработок этапа 8.7:
1. Авторизация и регистрация:
   - Вход незарегистрированного пользователя через POST /api/auth/web/login возвращает 403 telegram_verification_required, не создаёт сессию, не сжигает инвайт.
   - POST /api/auth/web/register/start отдаёт recommended_name из TelegramContact, в веб-форме оно подставляется и редактируется.
   - Ссылка на Telegram-бота https://t.me/<BOT_USERNAME> доступна в интерфейсе, bot_username отдается с сервера.
2. Галерея и таймлапс:
   - В галерее рендерятся реальные кадры <img> с URL из API, onerror вызывает заглушку битого кадра.
   - Поле ввода длительности gallery-duration-input подставляет рекомендованное значение Math.max(1, Math.round(framesCount / 15)).
   - Динамический расчёт FPS и вывод предупреждений (>30 fps, <1 fps, <1 c).
3. Мягкая обработка ошибок:
   - showError не вызывается при рутинных ошибках (сеть, офлайн RPi, пустая галерея, ошибки валидации).
   - Ошибки валидации форм отображаются инлайн под полями ввода.
   - Подтверждение выхода выполнено как инлайн-блок в шапке (без window.confirm и showConfirm).
4. Тематические заглушки и загрузка:
   - 4 категории A, B, C, D со случайным выбором пар SVG+фраза (placeholders.js).
   - Спокойный серый текст #94a3b8, центрирование, 0 Unicode-эмодзи.
   - Случайные фразы при съёмке тестового снимка с циклической SVG-анимацией роста.
5. Автоопределение RPi / Mock:
   - edge_agent/config.py проверяет модель в /proc/device-tree/model И наличие gpiozero; на ПК без них auto выбирает mock.
   - Онлайн-статус платы сбрасывается в offline по таймауту 35.0 с.
   - Переключатель режима в rpi_setup блокируется, если плата rpi недоступна.
6. Вёрстка и поллинг:
   - Баннер #server-offline-banner в потоке документа под шапкой с рамкой 1px solid #ef4444.
   - Футер прижат к низу страницы (min-height: 100vh).
   - Фоновый поллинг каждые 5 с.

Команды проверки:
- `server/.venv/bin/python -m pytest server/tests -q`
- `edge_agent/.venv/bin/python -m pytest edge_agent/tests -q`
- `clients/telegram_bot/.venv/bin/python -m pytest clients/telegram_bot/tests -q`
- `server/.venv/bin/python -m pytest tests/e2e -q`
- `server/.venv/bin/ruff check .`
- `server/.venv/bin/ruff format --check .`
- `git status --short` (проверка чистоты рабочей директории)

Результат зафиксировать в docs/qa_log.md и опубликовать отчёт на swarm board:
- `QA_VERDICT: PASS`
- или `QA_VERDICT: FAIL` с перечнем дефектов (ID).
