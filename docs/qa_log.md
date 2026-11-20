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
- **[MAJOR] [edge_agent, clients/cli] Доработки боевого режима Edge-агента для Raspberry Pi 5 (rpi) и обработки ошибок команд в CLI.**
  - **Компоненты:** `edge_agent`, `clients/cli`.
  - **Исправление:**
    1. Реализован строгий боевой режим (`HARDWARE_MODE=rpi`): исключены молчаливые подмены (падение без `gpiozero`/`lgpio`, ошибка при отсутствии `cv2`, `measurements: []` и передача сырого кадра в `annotated_image` без выдуманных площадей при сбое или отсутствии ONNX-модели с логированием `ERROR`).
    2. Сканирование камер переведено на поиск в `/dev/v4l/by-id/*-video-index0` со стабильным `hardware_id` и открытием по `os.path.realpath`.
    3. Добавлен прогрев камер `CAMERA_WARMUP_FRAMES` (5) и задержка переключения реле `RELAY_SWITCH_DELAY_MS` (200 мс).
    4. Логика реле: основной свет в норме ВКЛЮЧЕН (`light=1, flash=0`), на время кадра `light=0, flash=1`, возврат в исходное в `finally`. Поддержана полярность `RELAY_ACTIVE_HIGH`.
    5. Команда SNAP снимает все камеры установки (для target="all" — все активные установки); статус `done` при наличии хотя бы 1 кадра.
    6. Индексация растений упорядочена: сортировка контуров по убыванию площади, нумерация с 0 подряд.
    7. Офлайн-очередь: асинхронный сброс в отдельном потоке (до 10 пакетов под Lock), атомарная запись пакета через замену временной папки, 3 попытки `send_command_result`, таймаут загрузки кадра 60 с, защита `failed/.gitkeep`.
    8. Планировщик автосъемки переведен на астрономическую сетку UTC (`HH % N == 0 and MM == 0`).
    9. В CLI команды `camera scan`, `snap take`, `light main/flash/test` при `status == "error"` выводят ошибку в stderr и завершаются с кодом 1; `system status` выводит `model_name`.
    10. Устранены реальные задержки `sleep` в тестах, БД изолирована в тестах e2e (в корне нет `growvision.db*`, `git status --short` чист). Регрессионный набор (180 тестов) пройден успешно.

---

## 3. Рекомендации по улучшению UX и новым проверкам
- *(Выполнено в stage-4/fixes)* Устранено дублирование логики очистки таймаутов в `server/api/edge.py` (`edge_claim_command`) вызовом `expire_stale_commands(db, now)` из `server.core.timeouts`.
