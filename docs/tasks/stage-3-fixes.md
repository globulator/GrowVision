# Задача: Этап 3 / Исправления Client API, часть 1 (fixes)
Режим: LIGHT
Ветка: stage-3/fixes
Цель: Исправить проверку X-Client-Key в GET /api/version, валидацию полей установок и входа, запрет смены пинов в архиве и форматирование ошибок 422 RequestValidationError.
Прочитать перед началом: docs/team_roles/builder.md, docs/API_CONTRACT.md (разд. 1.1, 4.1, 4.2, 4.6), docs/DB_SCHEMA.md.

Allowed Paths (можно создавать и менять только это):
- server/main.py
- server/api/installations.py
- server/api/auth.py
- server/api/system.py
- server/tests/
- docs/handoff/client_api_fixes.md

Только чтение:
- docs/PROJECT.md
- docs/DB_SCHEMA.md
- docs/API_CONTRACT.md
- docs/REPO_AND_VERSIONING.md
- docs/team_roles/
- docs/STATE.md
- shared/

Критерии приёмки:
1. GET /api/version использует зависимость get_current_user_optional:
   - Неверный X-Client-Key возвращает 401 {"detail": "..."};
   - Без заголовков и с верным X-Client-Key возвращает 200 {"server_version": "..."} (API_CONTRACT п. 1.1).
2. PATCH /api/installations/{id}:
   - Передача явного null (None) для name, culture_name, light_gpio_pin, flash_gpio_pin возвращает 422, а не 409;
   - aruco_size_mm: null допустим и обнуляет значение поля в БД.
3. PATCH пинов (light_gpio_pin, flash_gpio_pin) у архивной установки (is_archived=True) возвращает 409 {"detail": "Установка в архиве"}, а не молчаливое 200.
4. Валидация входных данных (код 422):
   - name и culture_name после strip() не должны быть пустыми (в POST и PATCH /api/installations);
   - light_gpio_pin и flash_gpio_pin должны быть целыми числами в диапазоне от 0 до 53 включительно (BCM GPIO);
   - login и external_id в POST /api/auth/login после strip() не должны быть пустыми.
5. Обработчик RequestValidationError в server/main.py возвращает короткое читаемое сообщение в поле "detail" вида {"detail": "..."}, собранное из exc.errors() (поле и причина ошибки), без путей к файлам исходного кода, номеров строк и внутренних словарей pydantic. Код ответа 422.
6. Юнит-тесты в server/tests/ покрывают п. 1–5.
7. pytest server/tests проходит; ruff check . и ruff format --check . проходят без ошибок.
8. Формат коммитов: Conventional Commits — тип(scope): описание (scope: server). Версию VERSION не поднимать.

Команды проверки:
- ./server/.venv/bin/pytest server/tests -q
- ./server/.venv/bin/ruff check .
- ./server/.venv/bin/ruff format --check .

Файл заметок (handoff): docs/handoff/client_api_fixes.md
