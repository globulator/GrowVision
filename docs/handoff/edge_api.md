# Handoff: Этап 2 / Edge API сервера

## Реализация
1. `server/core/state.py`: реализован потокобезопасный класс `InMemoryState` (хранение связей `command_id -> capture_id` и списка камер из `scanned_cameras`).
2. `server/api/deps.py`: проверка заголовка `X-Edge-Key` через `secrets.compare_digest` с возвратом 401 при несовпадении или отсутствии.
3. `server/api/edge.py`:
   - `POST /api/edge/heartbeat`: сохранение телеметрии в `system_settings.rpi_last_telemetry`, обновление статуса известных камер (`is_online`, `last_seen_at`), возврат конфигурации и списка активных установок.
   - `POST /api/edge/commands/claim`: перевод просроченных команд `pending` (>30 с) в `timeout`, выборка самой старой команды `pending`, перевод в `running`, возврат массива из 0 или 1 элемента с поддержкой одиночных и глобальных команд.
   - `POST /api/edge/commands/{command_id}/result`: проверка статуса `running` (иначе 409), сохранение результатов и логов, обработка `SCAN_CAMERAS` (создание новых камер, обновление существующих и кэширование в памяти процесса).
   - `POST /api/edge/captures`: валидация метаданных (422 при ошибках), нормализация `captured_at` до секунд в UTC, проверка камеры (409 если не зарегистрирована) и установки (404), дедупликация (200), сохранение файлов в `server/media/{installation_id}/` и замеров, связывание `command_id -> capture_id` в памяти процесса.
4. `server/main.py`: создание приложения FastAPI, подключение роутера `/api/edge`, перехват `IntegrityError` (409) и `RequestValidationError` (422).
5. `server/tests/test_edge_api.py`: полное покрытие всех 4 эндпоинтов, проверки авторизации, таймаутов, дедупликации, граничных случаев и ошибок.

## Результаты проверок
- `server/.venv/bin/pytest server/tests -q`: 27 passed.
- `server/.venv/bin/pytest tests/e2e/ -q`: 14 passed.
- `server/.venv/bin/ruff check .`: All checks passed!
- `server/.venv/bin/ruff format --check .`: 45 files already formatted.
- Статус: `READY_FOR_QA`.
