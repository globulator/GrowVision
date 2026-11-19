# Задача: Этап 3 / Client API, часть 1 (auth, installations, cameras, system)
Режим: FULL
Ветка: stage-3/client-api-part1
Цель: Реализовать эндпоинты Client API для аутентификации, управления установками, привязки камер, получения версии и системных настроек по API_CONTRACT.md разд. 1 и 4.1–4.3, 4.6.
Прочитать перед началом: docs/team_roles/builder.md, docs/PROJECT.md (разд. 2, 3, 5 этап 3), docs/API_CONTRACT.md (разд. 1, 4.1–4.3, 4.6), docs/DB_SCHEMA.md (разд. 3, 4.1–4.5).

Allowed Paths (можно создавать и менять только это):
- server/main.py
- server/api/
- server/core/
- server/tests/
- docs/handoff/client_api_part1.md

Только чтение:
- docs/PROJECT.md
- docs/DB_SCHEMA.md
- docs/API_CONTRACT.md
- docs/REPO_AND_VERSIONING.md
- docs/team_roles/
- docs/STATE.md
- shared/

Критерии приёмки:
1. Авторизация клиентов в server/api/deps.py по API_CONTRACT.md разд. 1.1:
   - Проверка X-Client-Key против CLIENT_API_KEY (сравнение байтов secrets.compare_digest);
   - Публичные эндпоинты (GET /api/version, GET /api/installations, GET /api/installations/{id}) доступны Гостю без заголовков или с верным X-Client-Key без сессии; неверный X-Client-Key -> 401 {"detail": "..."};
   - Защищённые эндпоинты требуют сессию в users по (client_type, external_id); нет сессии -> 401; роли не хватает -> 403;
   - При успешном запросе авторизованного пользователя поле last_active_at обновляется в users.
2. Аутентификация в server/api/auth.py (разд. 4.1):
   - POST /api/auth/login: вход по client_type, external_id, login, password; проверка паролей BIOLOGIST_PASSWORD / DEVELOPER_PASSWORD через secrets.compare_digest; создание/обновление сессии в users; неверный пароль -> 401;
   - Лимит входа: не более 5 попыток POST /api/auth/login в минуту на пару (client_type, external_id), включая неудачные; 6-я попытка -> 429 с заголовком Retry-After;
   - POST /api/auth/logout: удаление сессии из users -> 200 {"ok": true};
   - GET /api/auth/me: 200 {"login": "...", "role": "...", "authorized_at": "...Z", "last_active_at": "...Z"}.
3. Установки в server/api/installations.py (разд. 4.2):
   - GET /api/installations (?include_archived=false) и GET /api/installations/{id}: проекции ролей:
     * Гость: id, name, culture_name, is_archived, last_area_display (без пинов и камер);
     * Biologist: поля Гостя + aruco_size_mm, created_at, cameras[] (id, auto_name, is_online);
     * Developer: поля Biologist + light_gpio_pin, flash_gpio_pin, cameras[].hardware_id;
     * last_area_display рассчитывается сервером: последний плановый кадр (is_manual=FALSE), plant_index=0 ("48.3 см²" при наличии маркера и aruco_size_mm, "15430 px" без маркера, null при отсутствии);
   - POST /api/installations: Developer -> 201; проверка уникальности имени (409) и пинов среди активных установок (409, в т.ч. свет одной = вспышка другой);
   - PATCH /api/installations/{id}: Biologist правит только name, culture_name (попытка изменить другие поля -> 403); Developer правит все поля; 409 при конфликте имени/пинов;
   - POST /api/installations/{id}/archive: Developer -> 200: is_archived=True, у всех камер установки installation_id=NULL;
   - DELETE /api/installations/{id}: Developer -> 204 при 0 снимков в captures; при наличии снимков -> 409 {"detail": "У установки есть снимки, используйте архивацию"}.
4. Камеры в server/api/cameras.py (разд. 4.3):
   - GET /api/cameras: Developer -> 200 список всех камер (id, hardware_id, auto_name, is_online, last_seen_at, installation_id, installation_name);
   - POST /api/cameras/{camera_id}/bind: Developer -> 200 привязка ({"installation_id": N}) или отвязка ({"installation_id": null}); 404 если камера или установка не найдены; 409 если установка в архиве.
5. Версия и система в server/api/system.py (разд. 4.6):
   - GET /api/version: доступен Гостю -> 200 {"server_version": ...} через shared.version.get_app_version();
   - GET /api/system/status: Developer -> 200 (server_version, rpi_connection: online/offline по порогу 90 с от rpi_last_heartbeat_at, settings, rpi_telemetry);
   - PATCH /api/system/settings: Developer -> 200 (capture_interval_hours, debug_mode, warning при смене интервала); валидация: capture_interval_hours целое >= 1 (дробные -> 422).
6. Обработка ошибок: коды и тексты строго по API_CONTRACT.md. Ни IntegrityError, ни неожиданные исключения не уходят наружу как 500 (перехват и аккуратная трансляция в 400/404/409/422).
7. Даты: SQLite возвращает naive datetime; отдавать клиентам в ISO 8601 UTC с суффиксом Z.
8. Тесты в server/tests/ изолированы через tmp_path / monkeypatch и не пишут в рабочие файлы server/media/ и growvision.db.
9. pytest server/tests проходит; ruff check . и ruff format --check . без ошибок.
10. Формат коммитов: Conventional Commits — тип(scope): описание (scope: server). Форматирование ruff идёт в тот же коммит, что и код. Версию VERSION не поднимать.

Команды проверки:
- ./server/.venv/bin/pytest server/tests -q
- ./server/.venv/bin/ruff check .
- ./server/.venv/bin/ruff format --check .

Файл заметок (handoff): docs/handoff/client_api_part1.md
