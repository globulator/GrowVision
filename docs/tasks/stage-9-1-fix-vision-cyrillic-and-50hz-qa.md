# Задача: Этап 9.1 / QA-Tester (Fix Vision Cyrillic & 50 Hz)

Режим: FAST
Ветка: stage-9/fix-vision-cyrillic-and-50hz
Коммит Builder: 59b6547
Handoff Builder: docs/handoff/stage-9-1-fix-vision-cyrillic-and-50hz.md
Журнал проверок: docs/qa_log.md

Прочитать перед началом:
- docs/PROJECT.md (особенно раздел 9: правила скорости и тестов — ОБЯЗАТЕЛЬНО)
- docs/API_CONTRACT.md
- docs/STATE.md
- docs/handoff/stage-9-1-fix-vision-cyrillic-and-50hz.md

Цель: Провести независимую верификацию доработок этапа 9.1 (режим FAST):
1. Кириллица на CV-разметке (`edge_agent/vision.py`, `edge_agent/requirements.txt`):
   - Проверить отрисовку текста в `draw_cv_annotations` через `Pillow` (`PIL.ImageDraw`, `PIL.ImageFont`) с загрузкой TTF-шрифта с кириллицей (и безопасным фолбэком, если шрифт не найден).
   - Проверить, что `Pillow` добавлен в `edge_agent/requirements.txt`.
2. HSV-сегментация и фильтрация шума (`edge_agent/vision.py`):
   - Проверить адаптивный порог минимальной площади контура (например, `max(150.0, (w * h) * 0.0005)`).
   - Проверить повышенный порог насыщенности `S` (~60–70) и морфологическую очистку (`cv2.morphologyEx` OPEN + CLOSE) перед `findContours`, чтобы серый фон и мелкие шумы на 1280×960 отсекались, а растения на синтетических кадрах стабильно находились.
3. Частота сети 50 Гц (`edge_agent/hardware.py`):
   - Проверить выставление `power_line_frequency=1` (50 Hz) через `v4l2-ctl` при настройке/открытии камеры в режиме `rpi` и при автокалибровке, с тихим игнорированием ошибки при отсутствии контрола.
4. Тесты и качество:
   - Проверить наличие/актуальность тестов на кириллицу через Pillow, фильтрацию шума/серого фона в HSV и `power_line_frequency=1`.
   - Запустить полный прогон тестов, `ruff check .` и `ruff format --check .`.

Команды проверки:
- `edge_agent/.venv/bin/python -m pytest edge_agent/tests -q`
- `server/.venv/bin/python -m pytest server/tests -q`
- `clients/telegram_bot/.venv/bin/python -m pytest clients/telegram_bot/tests -q`
- `server/.venv/bin/python -m pytest tests/e2e -q`
- `server/.venv/bin/ruff check .`
- `server/.venv/bin/ruff format --check .`
- `git status --short`

Результат зафиксировать в docs/qa_log.md и опубликовать отчёт на swarm board:
- `QA_VERDICT: PASS`
- или `QA_VERDICT: FAIL` с перечнем дефектов (ID).
