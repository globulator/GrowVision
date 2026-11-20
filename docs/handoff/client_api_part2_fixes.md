# Отчёт о реализации: Этап 4 / Исправление Client API, часть 2 (fixes)

Статус: READY_FOR_QA
Ветка: stage-4/fixes
Карточка: docs/tasks/stage-4-fixes.md

## 1. Выполненные изменения

### 1.1. Единая логика статуса подключения RPi и устранение дублирования
- Создан модуль `server/core/system_status.py`:
  - `is_rpi_online(db: Session) -> tuple[bool, datetime | None]`: RPi считается онлайн, если `updated_at` записи `rpi_last_telemetry` в `system_settings` не старше 90 секунд (сравнение в UTC).
  - `check_rpi_online(db: Session) -> None`: выбрасывает HTTP 503 `{"detail": "Raspberry Pi офлайн"}`, если RPi офлайн.
  - `is_debug_mode(db: Session) -> bool`: возвращает статус включения `debug_mode` в таблице `system_settings`.
  - `validate_target_value(v: Any) -> int | Literal["all"]`: строгий валидатор target (запрет bool -> ValueError, положительное целое или "all").
- Ключ `rpi_last_heartbeat_at` как ключ таблицы `system_settings` полностью исключён из кода сервера и тестов `server/tests/`. В ответе эндпоинта `GET /api/system/status` поле `rpi_last_heartbeat_at` формируется из `updated_at` записи `rpi_last_telemetry` в соответствии с API_CONTRACT.md.

### 1.2. Соблюдение строгого порядка проверок
- В эндпоинтах `server/api/control.py` (`POST /api/control/snap`, `POST /api/control/light/main`, `POST /api/control/light/flash`, `POST /api/control/relay-test`):
  1. Авторизация и роль (401/403) — через FastAPI Depends (`require_role`).
  2. Валидация тела запроса (422) — через Pydantic v2 c `validate_target_value`.
  3. Проверка target (404/409):
     - Существование установки -> 404 `{"detail": "Установка не найдена"}`;
     - Архивность установки -> 409 `{"detail": "Установка в архиве"}`;
     - `target: "all"` при отсутствии активных установок -> 409 `{"detail": "Нет активных установок"}`.
  4. Проверка онлайна RPi (503 `{"detail": "Raspberry Pi офлайн"}`) — через `check_rpi_online(db)`.
- В эндпоинте `server/api/cameras.py` (`POST /api/cameras/scan`):
  1. Авторизация и роль (401/403);
  2. Проверка онлайна RPi (503 `{"detail": "Raspberry Pi офлайн"}`).

### 1.3. Оптимизация ожидания команд RPi
- В `wait_command_result` (`server/api/control.py`) и `scan_cameras` (`server/api/cameras.py`):
  - При переходе команды в статус `timeout` цикл ожидания немедленно завершается возвратом HTTP 504 `{"detail": "Превышено время ожидания ответа Raspberry Pi"}`.

### 1.4. Таймлапс и видео без костылей и с безопасной обработкой ошибок
- В `server/api/timelapse.py`:
  - Полностью удалены генерация заглушек-JPEG и вспомогательная функция `resolve_media_file_path`.
  - Пути к файлам кадров разрешаются строго через `get_media_dir()` (`get_frame_file_path`).
  - Кадры, чьи файлы на диске отсутствуют, пропускаются. Если пригодных файлов не осталось -> HTTP 404 `{"detail": "Плановые снимки не найдены"}`.
  - Для вызова `subprocess.run(["ffmpeg", ...])` установлен `timeout=600`.
  - При возникновении `TimeoutExpired` или `CalledProcessError` ошибка логируется в `server.timelapse` logger, а клиенту возвращается чистый HTTP 500 `{"detail": "Ошибка кодирования видео"}` без утечки локальных путей и stderr.

### 1.5. Предотвращение утечек `app_state`
- В `server/core/state.py` добавлен метод `cleanup_stale_entries(db, max_age_seconds=300.0)`, удаляющий записи команд со статусами `timeout`, `error`, `done`, завершённых более 5 минут назад.
- В `server/core/timeouts.py` в функцию `expire_stale_commands` интегрирован периодический вызов (раз в 60 секунд) очистки `app_state.cleanup_stale_entries(db)`.
- В `server/api/control.py` при ответе SNAP со статусом `error` команда удаляется из `app_state.pop_captures_by_command`.
- В `server/api/cameras.py` при ответе сканирования со статусом `error` команда удаляется из `app_state.pop_scanned_cameras`.

### 1.6. Юнит-тесты
- В `server/tests/test_client_api_part2.py`:
  - `set_rpi_online` обновлён для работы с `rpi_last_telemetry.updated_at`.
  - `test_control_rpi_offline_and_target_validations` полностью покрывает строгий порядок проверок: 401/403 -> 422 -> 404/409 (при офлайн RPi) -> 503 (при валидном target и офлайн RPi).
  - Добавлен тест `test_control_wait_command_timeout_immediate_504` для проверки немедленного 504 при статусе timeout.
  - Добавлен тест `test_control_snap_error_cleans_app_state` для проверки очистки `app_state` при ошибке выполнения snap.
  - Добавлен тест `test_cameras_scan_error_and_timeout` для проверки мгновенного 504 и очистки памяти при сканировании.
  - Добавлен тест `test_app_state_cleanup_stale_entries` для проверки периодической очистки устаревших записей памяти.
  - Добавлен тест `test_timelapse_missing_files_and_ffmpeg_errors` для проверки пропуска отсутствующих файлов, 404 при 0 файлов, таймаута 600 с и чистого detail `{"detail": "Ошибка кодирования видео"}` при сбоях ffmpeg.

## 2. Результаты проверок

- `./server/.venv/bin/pytest server/tests -q`: 55 passed, 1 warning (deprecation warning testclient httpx) in 2.95s.
- `./server/.venv/bin/ruff check .`: All checks passed!
- `./server/.venv/bin/ruff format --check .`: 74 files already formatted.
