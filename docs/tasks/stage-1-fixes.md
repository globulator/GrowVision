# Задача: Этап 1 / Исправления моделей и валидации схем (fixes)
Режим: LIGHT
Ветка: stage-1/fixes
Цель: Исправить каскадное удаление команд в SQLAlchemy ORM при удалении установки и добавить валидацию Pydantic для area_px и aruco_area_px.
Прочитать перед началом: docs/team_roles/builder.md, docs/DB_SCHEMA.md (п. 4.2), docs/API_CONTRACT.md (разд. 3.4).

Allowed Paths (можно создавать и менять только это):
- server/db/models.py
- shared/schemas.py
- server/tests/
- docs/handoff/fixes.md

Только чтение:
- docs/PROJECT.md
- docs/DB_SCHEMA.md
- docs/API_CONTRACT.md
- docs/REPO_AND_VERSIONING.md
- docs/team_roles/
- docs/STATE.md

Критерии приёмки:
1. Удаление установки без кадров, но с командами, проходит и удаляет связанные команды (добавить passive_deletes=True в отношение Installation.edge_commands в server/db/models.py).
2. Удаление установки с кадрами по-прежнему даёт IntegrityError (captures.installation_id ON DELETE RESTRICT).
3. В shared/schemas.py: PlantMeasurementMeta.area_px: Field(..., ge=0); CaptureMeta.aruco_area_px: Field(..., gt=0). Значения -1 и 0 для aruco_area_px и -1 для area_px вызывают ValidationError.
4. Юнит-тесты в server/tests/ покрывают п. 1–3.
5. pytest server/tests проходит.
6. ruff check . и ruff format --check . проходят без ошибок.
7. Формат коммитов: Conventional Commits — тип(scope): описание (типы: fix, test, chore, refactor; scope обязателен: server, shared). Версию VERSION не поднимать.

Команды проверки:
- pytest server/tests -q
- ruff check .
- ruff format --check .

Файл заметок (handoff): docs/handoff/fixes.md
