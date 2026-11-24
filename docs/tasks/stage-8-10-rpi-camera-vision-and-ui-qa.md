# Задача: Этап 8.10 / QA-Tester (RPi Camera, Vision & UI)

Режим: FULL
Ветка: stage-8/rpi-camera-vision-and-ui
Коммит Builder: 92371a7
Handoff Builder: docs/handoff/stage-8-10-rpi-camera-vision-and-ui.md
Журнал проверок: docs/qa_log.md

Прочитать перед началом:
- docs/PROJECT.md (особенно раздел 9: правила скорости и тестов — ОБЯЗАТЕЛЬНО)
- docs/WEB_SPEC.md
- docs/BOT_SPEC.md
- docs/API_CONTRACT.md
- docs/STATE.md
- docs/handoff/stage-8-10-rpi-camera-vision-and-ui.md

Цель: Провести независимую сквозную верификацию доработок этапа 8.10:
1. Web UI — панель телеметрии Raspberry Pi (`clients/web/`):
   - Проверить, что переключатель режимов mock/rpi полностью отсутствует в интерфейсе.
   - Проверить отображение read-only параметров: статус online/offline, бейдж режима, модель платы, CPU/RAM/температура, свободное место на диске и размер офлайн-очереди.
   - Проверить соблюдение инвариантов: 0 эмодзи, 0 alerts/prompts/confirms.
2. HSV-сегментация в боевом режиме без ONNX (`edge_agent/vision.py`):
   - Проверить, что при `mock_mode=False` и отсутствии `.onnx`-файла сегментация использует `_segment_plants_hsv` (возвращает полигоны и площадь) с логированием WARNING вместо возврата `[]`.
3. Автоподбор качества и умный прогрев (`edge_agent/hardware.py`):
   - Проверить структуру `CameraProfile` и опрос `v4l2-ctl` с безопасным фолбэком на дефолты OpenCV при ошибке.
   - Проверить флаги открытия (`cv2.CAP_V4L2`, `CAP_PROP_BUFFERSIZE=1`, приоритет `MJPG`, до 3 попыток при ошибках).
   - Проверить умный прогрев по стабилизации яркости `|luma(t) - luma(t-1)| < 1.5` и логирование в формате `[SNAP]`.
   - Проверить отсутствие искусственных жестких микролимитов на вместимость `offline_queue.py`.
4. Автокалибровка в Telegram-боте (`edge_agent/`, `server/`, `clients/telegram_bot/`):
   - Проверить команду `AUTOCALIBRATE_CAMERA` в `EdgeOrchestrator`.
   - Проверить эндпоинт `POST /api/cameras/{camera_id}/autocalibrate`.
   - Проверить кнопку «⚙️ Автокалибровка» в карточке камеры в боте с выводом сводки и автозапуск при привязке камеры.
5. Тесты и линтер:
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
