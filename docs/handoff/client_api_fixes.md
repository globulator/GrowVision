# План и отчёт реализации: Этап 3 / Исправления Client API, часть 1 (fixes)

## План и реализация
1. `server/api/system.py`: в `GET /api/version` добавлена зависимость `get_current_user_optional`. Без заголовка и с верным `X-Client-Key` -> 200 `{"server_version": ...}`; с неверным `X-Client-Key` -> 401 `{"detail": "Неверный X-Client-Key"}`.
2. `server/api/installations.py`:
   - В `InstallationPatch`: запрет передачи явного `null` для `name`, `culture_name`, `light_gpio_pin`, `flash_gpio_pin` (валидатор возвращает 422). Поле `aruco_size_mm: null` допустимо и обнуляет значение в БД.
   - В `patch_installation`: попытка смены пинов (`light_gpio_pin`, `flash_gpio_pin`) у архивной установки (`is_archived=True`) возвращает 409 `{"detail": "Установка в архиве"}`.
   - Валидация диапазонов и строк: `name` и `culture_name` после `strip()` не должны быть пустыми (в POST и PATCH /api/installations); `light_gpio_pin` и `flash_gpio_pin` должны быть строго целыми числами в диапазоне 0..53 (BCM GPIO).
3. `server/api/auth.py`: в `LoginRequest` добавлена валидация `login` и `external_id` (после `strip()` не должны быть пустыми строками, код 422).
4. `server/main.py`: обработчик `RequestValidationError` форматирует читаемое сообщение `{"detail": "..."}` из `exc.errors()` (поле и причина ошибки) без внутренних трейсбеков, путей и номеров строк.
5. `server/tests/test_client_api_fixes.py`: добавлено 5 комплексных тестов, покрывающих критерии 1–5 с изоляцией базы данных и медиа-файлов.

## Результаты верификации
- `pytest server/tests`: 41 passed, 0 failed.
- `ruff check .`: All checks passed.
- `ruff format --check .`: 63 files already formatted.
- Статус: **READY_FOR_QA**
