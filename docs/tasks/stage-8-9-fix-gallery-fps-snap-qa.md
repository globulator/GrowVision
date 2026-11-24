# Задача: Этап 8.9 / QA-Tester (Fix Gallery, FPS & SNAP)

Режим: FAST
Ветка: stage-8/fix-gallery-fps-snap
Коммит Builder: ca80d0b
Handoff Builder: docs/handoff/stage-8-9-fix-gallery-fps-snap.md
Журнал проверок: docs/qa_log.md

Прочитать перед началом:
- docs/PROJECT.md (особенно раздел 9: правила скорости и тестов — ОБЯЗАТЕЛЬНО)
- docs/WEB_SPEC.md
- docs/BOT_SPEC.md
- docs/API_CONTRACT.md
- docs/STATE.md
- docs/handoff/stage-8-9-fix-gallery-fps-snap.md

Цель: Провести независимую сквозную верификацию доработок этапа 8.9:
1. Web-галерея (`clients/web/js/gallery.js`):
   - Проверить, что кадры корректно читают `raw_url || raw_image_url` и `annotated_url || annotated_image_url`, без битых ссылок и с корректной загрузкой `<img>`.
2. Telegram-бот (`clients/telegram_bot/handlers/timelapse.py`):
   - Проверить отсутствие хардкода 30 FPS.
   - Проверить динамический расчёт: для 4 кадров бот рассчитывает 1 с и 4 FPS (кнопка `▶️ Собрать видео (4 FPS)`, callback_data и подпись к видео).
   - При `total_frames / duration > 30` выводится предупреждение об автопрореживании до 30 FPS.
3. Edge-агент (`edge_agent/`):
   - Проверить `command_poll_interval_sec == 0.5` по умолчанию.
   - В mock-режиме проверить быстрое выполнение `SNAP` (1–3 с) без искусственных задержек `time.sleep` и прогрева камеры.
4. Тесты и качество:
   - Запустить тесты (server, edge, bot, e2e) и линтер ruff.

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
