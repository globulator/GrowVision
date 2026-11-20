# Задача: Этап 4 / Исправление Client API, часть 2 (fixes)
Режим: FULL
Ветка: stage-4/fixes
Цель: Исправить блокирующий дефект проверки онлайна RPi (is_rpi_online по rpi_last_telemetry.updated_at), порядок проверок, убрать дублирование в server/core/, оптимизировать ожидание команд (504 при timeout), убрать костыли и заглушки в timelapse/video (timeout=600, get_media_dir(), чистый detail), исключить утечки app_state.
Прочитать перед началом: docs/team_roles/builder.md, docs/API_CONTRACT.md (разд. 1.4, 4.3, 4.4, 4.5), docs/DB_SCHEMA.md.

Allowed Paths (можно создавать и менять только это):
- server/api/control.py
- server/api/cameras.py
- server/api/timelapse.py
- server/api/system.py
- server/core/
- server/tests/
- docs/handoff/client_api_part2_fixes.md

Только чтение:
- docs/PROJECT.md
- docs/DB_SCHEMA.md
- docs/API_CONTRACT.md
- docs/REPO_AND_VERSIONING.md
- docs/team_roles/
- docs/STATE.md
- shared/

Критерии приёмки:
1. Реализовать единую функцию is_rpi_online(db) -> tuple[bool, datetime | None] (или bool) в server/core/: RPi считается online, если updated_at записи rpi_last_telemetry в system_settings не старше 90 секунд (сравнение в UTC). Её используют control, cameras/scan и system/status. Ключ rpi_last_heartbeat_at как ключ таблицы system_settings полностью исключить из кода и тестов.
2. В эндпоинтах control и cameras/scan строго соблюдать порядок проверок:
   - Авторизация и роль (401/403);
   - Валидация тела запроса (422);
   - Проверка target: существование (404), архивация (409), "all" без активных (409);
   - Проверка онлайна RPi (503 {"detail": "Raspberry Pi офлайн"}).
3. Устранить дублирование логики:
   - Общая реализация проверки онлайна RPi в server/core/;
   - Общая реализация проверки is_debug_mode(db) в server/core/;
   - Общий валидатор target в server/core/.
4. В wait_command_result: если команда в БД перешла в статус "timeout", не ждать полный таймаут клиента, а сразу возвращать 504 {"detail": "..."}.
5. В server/api/timelapse.py:
   - Убрать генерацию заглушек-JPEG и функцию resolve_media_file_path с обходными путями;
   - Пути к файлам кадров строить строго через get_media_dir();
   - Кадры, чьи файлы на диске отсутствуют, пропускаются; если пригодных файлов не осталось -> 404 {"detail": "Плановые снимки не найдены"};
   - Для вызова ffmpeg в subprocess.run установить timeout=600;
   - При ошибке ffmpeg (ненулевой код возврата или таймаут) возвращать клиенту краткий detail без stderr и путей (логировать ошибку в server logger).
6. Предотвращение утечек app_state:
   - При ответе snap со статусом error запись команды также удаляется из app_state;
   - В фоновую задачу очистки (lifespan) добавить периодическую (раз в 60 с) очистку записей app_state для команд, которые в БД имеют статус timeout, error или done и завершены более 5 минут назад.
7. Юнит-тесты в server/tests/ обновлены: убрана запись rpi_last_heartbeat_at, тесты покрывают п. 1–6.
8. pytest server/tests проходит; ruff check . и ruff format --check . проходят без ошибок.
9. Формат коммитов: Conventional Commits — тип(scope): описание (scope: server). Версию VERSION не поднимать.

Команды проверки:
- ./server/.venv/bin/pytest server/tests -q
- ./server/.venv/bin/ruff check .
- ./server/.venv/bin/ruff format --check .

Файл заметок (handoff): docs/handoff/client_api_part2_fixes.md
