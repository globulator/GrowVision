# Задача: Этап 2 / Edge API сервера
Режим: FULL
Ветка: stage-2/edge-api
Цель: Реализовать 4 серверных эндпоинта /api/edge/* для взаимодействия с Raspberry Pi по API_CONTRACT.md разд. 3, с проверкой X-Edge-Key, управлением очередью команд и сохранением снимков.
Прочитать перед началом: docs/team_roles/builder.md, docs/PROJECT.md (разд. 2, 3, 5 этап 2), docs/API_CONTRACT.md (разд. 1, 3), docs/DB_SCHEMA.md (разд. 3, 4), docs/REPO_AND_VERSIONING.md (разд. 4.2).

Allowed Paths (можно создавать и менять только это):
- server/main.py
- server/api/
- server/core/
- server/services/
- server/tests/
- docs/handoff/edge_api.md

Только чтение:
- docs/PROJECT.md
- docs/DB_SCHEMA.md
- docs/API_CONTRACT.md
- docs/REPO_AND_VERSIONING.md
- docs/team_roles/
- docs/STATE.md
- shared/

Критерии приёмки:
1. Четыре эндпоинта /api/edge/* отвечают строго по API_CONTRACT.md разд. 3:
   - POST /api/edge/heartbeat: сохранение телеметрии в system_settings.rpi_last_telemetry, обновление is_online/last_seen_at для известных камер, ответ 200 с capture_interval_hours, debug_mode, active_installations. Неизвестные камеры не создаются и игнорируются.
   - POST /api/edge/commands/claim: перевод просроченных команд pending (>30 с) в timeout; взятие одной самой старой команды pending (created_at ASC), перевод в running, started_at=now, attempts+=1; ответ 200 (массив из 0 или 1 элемента).
   - POST /api/edge/commands/{command_id}/result: сохранение result_message и debug_log_line; если команда не в running (например timeout) -> 409; для SCAN_CAMERAS создание новых камер (is_online=True) и обновление существующих, список hardware_id хранится в памяти процесса; ответ 200 {"ok": true}.
   - POST /api/edge/captures: multipart/form-data с metadata (JSON), raw_image (.jpg), annotated_image (.jpg). Нормализация captured_at до секунд в UTC перед проверкой уникальности; если камера неизвестна -> 409 {"detail": "Камера не зарегистрирована, выполните сканирование камер"}; если установка не найдена -> 404; сохранение файлов в server/media/{installation_id}/ (raw_{capture_id}.jpg, ann_{capture_id}.jpg); создание записей в captures и plant_measurements; ответ 201 {"capture_id": ...}. Принимаются кадры архивных установок.
2. Неверный или отсутствующий заголовок X-Edge-Key на всех эндпоинтах -> 401, формат ошибки {"detail": "..."}.
3. Повторная загрузка кадра с теми же (installation_id, camera_id, captured_at) -> 200 с прежним {"capture_id": ...}; замеры и файлы на диске не перезаписываются.
4. Ошибки валидации тела запроса дают 422 {"detail": "..."}, а не 500; IntegrityError нигде не выходит наружу как 500.
5. Команда pending старше 30 с получает статус timeout.
6. Связь command_id -> capture_id из метаданных кадра хранится только в памяти процесса (API_CONTRACT п. 3.4) и в БД не пишется.
7. Все тесты в server/tests/ проходят (юнит-тесты на каждый эндпоинт и граничные случаи).
8. ruff check . и ruff format --check . проходят без ошибок.
9. Формат коммитов: Conventional Commits — тип(scope): описание (типы: feat, fix, test, chore, refactor; scope обязателен: server). Форматирование ruff идёт в тот же коммит, что и код. Версию VERSION не поднимать.

Команды проверки:
- ./server/.venv/bin/pytest server/tests -q
- ./server/.venv/bin/ruff check .
- ./server/.venv/bin/ruff format --check .

Файл заметок (handoff): docs/handoff/edge_api.md
