# Задача: Этап 2 / Исправления Edge API (fixes)
Режим: LIGHT
Ветка: stage-2/fixes
Цель: Исправить обработку не-ASCII ключа X-Edge-Key, валидацию captured_at в captures, таймауты running команд в claim, сохранение пустого результата SCAN_CAMERAS и получение версии FastAPI из get_app_version().
Прочитать перед началом: docs/team_roles/builder.md, docs/API_CONTRACT.md (разд. 1.1, 1.4, 3.2, 3.3, 3.4).

Allowed Paths (можно создавать и менять только это):
- server/api/deps.py
- server/api/edge.py
- server/main.py
- server/tests/
- docs/handoff/edge_api_fixes.md

Только чтение:
- docs/PROJECT.md
- docs/DB_SCHEMA.md
- docs/API_CONTRACT.md
- docs/REPO_AND_VERSIONING.md
- docs/team_roles/
- docs/STATE.md
- shared/

Критерии приёмки:
1. Заголовок X-Edge-Key с не-ASCII символами возвращает 401 {"detail": "..."}, а не 500 (сравнивать байты в compare_digest: .encode("utf-8")).
2. POST /api/edge/captures с невалидным captured_at (например "not-a-date") возвращает 422 {"detail": "..."}, а не 500.
3. В эндпоинте POST /api/edge/commands/claim перед выдачей команды просроченные команды в статусе running переводятся в timeout:
   - Одиночные команды (is_global=False): если started_at старше 60 секунд от текущего времени UTC;
   - SNAP с is_global=True: если started_at старше (15 + 10 * N) + 15 секунд, где N — число активных установок (installations с is_archived=False).
4. Результат выполнения команды SCAN_CAMERAS сохраняется в памяти процесса (state.set_scan_cameras_result) и при пустом списке scanned_cameras (проверять `if payload.scanned_cameras is not None:`).
5. Версия приложения FastAPI в server/main.py получается динамически вызовом shared.version.get_app_version(), а не захардкожена строкой.
6. Юнит-тесты в server/tests/ покрывают п. 1–5. Тесты не должны создавать или оставлять файлов в рабочем каталоге server/media/ (использовать tmp_path и мокирование MEDIA_DIR / media_root).
7. pytest server/tests проходит.
8. ruff check . и ruff format --check . проходят без ошибок.
9. Формат коммитов: Conventional Commits — тип(scope): описание (scope обязателен: server; типы: fix, test, chore, refactor). Форматирование ruff идёт в тот же коммит, что и код. Версию VERSION не поднимать.

Команды проверки:
- ./server/.venv/bin/pytest server/tests -q
- ./server/.venv/bin/ruff check .
- ./server/.venv/bin/ruff format --check .

Файл заметок (handoff): docs/handoff/edge_api_fixes.md
