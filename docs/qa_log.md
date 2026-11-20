# Журнал контроля качества (QA Log)

## 1. Открытые дефекты и замечания
*(Открытых дефектов нет)*

---

## 2. Исправленные дефекты (верифицировано QA)
- **[MAJOR] [server/core/system_status.py] Статус онлайна Raspberry Pi определялся по устаревшей схеме `rpi_last_heartbeat_at` вместо `updated_at` записи `rpi_last_telemetry`, нарушался порядок проверок эндпоинтов управления.**
  - **Эндпоинты:** `POST /api/control/*`, `POST /api/cameras/scan`, `GET /api/system/status`.
  - **Исправление:** Реализована единая функция `is_rpi_online(db)` по `rpi_last_telemetry.updated_at` (сравнение в UTC <= 90 с). Ключ `rpi_last_heartbeat_at` как ключ таблицы `system_settings` полностью исключён. Обеспечен строгий порядок проверок: авторизация (401/403) -> валидация тела (422) -> валидация target (404/409) -> статус онлайна RPi (503). В `wait_command_result` добавлен немедленный возврат 504 при статусе timeout. В timelapse video добавлен таймаут ffmpeg 600 с, пропуск отсутствующих файлов и чистый 500 detail.
- **[MAJOR] [server/api/control.py] Значение `target: true` в JSON запросах эндпоинтов управления не валидируется и неявно приводится к целому числу 1.**
  - **Эндпоинты:** `POST /api/control/snap`, `POST /api/control/light/main`, `POST /api/control/light/flash`, `POST /api/control/relay-test`.
  - **Исправление:** В моделях `SnapRequest`, `LightRequest`, `RelayTestRequest` добавлен параметр `mode="before"` в `@field_validator("target", mode="before")` и явная проверка `isinstance(v, bool)` с генерацией `ValueError("target не может быть boolean")` -> 422 Unprocessable Content. Верифицировано в регрессионных e2e-тестах.
- **[MAJOR] [clients/cli/commands/system.py] Парсинг телеметрии Raspberry Pi в команде `system status` не поддерживал вложенную структуру `software`/`hardware` ответа сервера.**
  - **Эндпоинт/Команда:** `system status` (Client API `GET /api/system/status`).
  - **Исправление:** Реализован парсинг вложенных словарей `software` и `hardware` с fallback на плоский формат. Аптайм форматируется только при наличии числового значения. Добавлены unit-тесты в `clients/cli/tests/test_system.py` и сквозной e2e-тест `test_cli_e2e_system_status_telemetry` в `tests/e2e/test_cli_e2e.py`. Все поля телеметрии отображаются корректно.

---

## 3. Рекомендации по улучшению UX и новым проверкам
- *(Выполнено в stage-4/fixes)* Устранено дублирование логики очистки таймаутов в `server/api/edge.py` (`edge_claim_command`) вызовом `expire_stale_commands(db, now)` из `server.core.timeouts`.
