# Задача: Этап 9.1 / Builder (Fix Vision Cyrillic & 50 Hz)

Режим: FAST
Ветка: stage-9/fix-vision-cyrillic-and-50hz
Цель: Исправить отображение кириллицы на CV-разметке через Pillow, улучшить фильтрацию мелкого шума и серого фона в HSV-сегментации и выставлять частоту сети 50 Гц (`power_line_frequency=1`) для V4L2-камер на Raspberry Pi 5.

Прочитать перед началом:
- docs/PROJECT.md (особенно раздел 9: правила скорости и тестов — ОБЯЗАТЕЛЬНО)
- docs/STATE.md
- docs/API_CONTRACT.md

Allowed Paths (можно создавать и менять только это):
- edge_agent/
- tests/
- docs/handoff/stage-9-1-fix-vision-cyrillic-and-50hz.md
- docs/tasks/

Только чтение:
- server/
- shared/
- clients/
- docs/DB_SCHEMA.md
- docs/PROJECT.md
- docs/REPO_AND_VERSIONING.md

Диагностированные дефекты на реальной Raspberry Pi 5 (1280×960):
1. `edge_agent/vision.py` (`draw_cv_annotations`): `cv2.putText` не поддерживает кириллицу — на аннотированных кадрах вместо русских подписей отображаются `?????????`.
2. `edge_agent/vision.py` (`_segment_plants_hsv`):
   - Порог минимальной площади контура `50.0 px` слишком мал для 1280×960, из-за чего кадр засоряется десятками мелких ложных контуров и подписей.
   - Порог насыщенности `S >= 40` захватывает слабо-зеленящий серый фон в один гигантский контур.
3. `edge_agent/hardware.py`: на камере Logitech C270 по умолчанию стоит `power_line_frequency = 2 (60 Hz)`, что даёт мерцание под лампами 50 Гц в РФ.

Критерии приёмки:
1. Корректная кириллица на разметке (`edge_agent/vision.py`, `edge_agent/requirements.txt`):
   - Заменить отрисовку текста в `draw_cv_annotations` на `Pillow` (`PIL.ImageDraw`, `PIL.ImageFont`) с загрузкой системного TTF-шрифта с поддержкой кириллицы (например, поиск `DejaVuSans.ttf` / `LiberationSans-Regular.ttf` в `/usr/share/fonts/...` с безопасным фолбэком, если шрифт не найден).
   - Убедиться, что `Pillow` указан в `edge_agent/requirements.txt`. (Если в `edge_agent/.venv` нет `Pillow`, проверить наличие или сообщить `BLOCKED` если установка невозможна; если есть в системе/venv — использовать).
2. Улучшение HSV-сегментации и фильтрации шума (`edge_agent/vision.py`):
   - Сделать минимальный порог площади контура адаптивным к разрешению кадра (например, `max(150.0, (w * h) * 0.0005)`), чтобы на 1280×960 отсекались мелкие артефакты.
   - Уточнить диапазон зелёного в HSV (поднять нижний порог насыщенности `S` до ~60–70, чтобы серый фон с лёгким оттенком не попадал в маску) и добавить морфологическую очистку (`cv2.morphologyEx` OPEN + CLOSE) перед `findContours`.
   - Проверить, что существующие тесты на синтетических кадрах продолжают стабильно находить растение.
3. Частота сети 50 Гц (`edge_agent/hardware.py`):
   - При настройке/открытии камеры в режиме `rpi` (или при автокалибровке) выставлять `power_line_frequency=1` (50 Hz) через `v4l2-ctl`, если контрол поддерживается камерой; при отсутствии контрола или ошибке — тихо игнорировать без падения захвата.
4. Тесты и качество:
   - Добавить/обновить unit-тесты на отрисовку кириллицы через Pillow, адаптивную фильтрацию мелких контуров и отсечение серого фона в HSV, а также безопасное применение `power_line_frequency=1`.
   - Все тесты зелёные (`edge_agent/tests`, `server/tests`, `clients/telegram_bot/tests`, `tests/e2e`), `ruff check .` и `ruff format --check .` чистые, `git status --short` пуст.

Команды проверки:
- `edge_agent/.venv/bin/python -m pytest edge_agent/tests -q`
- `server/.venv/bin/python -m pytest server/tests -q`
- `server/.venv/bin/python -m pytest tests/e2e -q`
- `server/.venv/bin/ruff check .`
- `server/.venv/bin/ruff format --check .`

Файл заметок (handoff): docs/handoff/stage-9-1-fix-vision-cyrillic-and-50hz.md
