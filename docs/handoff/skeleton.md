# Handoff: Этап 1 / Каркас репозитория и модели БД

## План и реализация
1. Каркас каталогов и корневые файлы (.gitignore, VERSION="0.0.0", requirements-dev.txt, .gitkeep) — выполнено (коммит `be84763`).
2. server/requirements.txt и server/.env.example — выполнено (коммит `ef7d91e`).
3. shared/version.py и shared/logger.py — выполнено (коммит `c7142af`).
4. shared/schemas.py — Pydantic-модели Edge API (Heartbeat, Command, CaptureMeta) — выполнено (коммит `4737788`).
5. server/config.py — загрузка и валидация конфигурации — выполнено (коммит `fa1a9da`).
6. server/db/database.py и server/db/models.py — 7 таблиц ORM, PRAGMA SQLite, индексы, CHECK — выполнено (коммит `d0b97d1`).
7. Стилистика и форматирование (ruff) — выполнено (коммит `39cedae`).
8. server/tests/ — 20 юнит-тестов (модели, PRAGMA, частичные индексы, schemas, versioning, conftest) — выполнено (коммиты `5a34b8d`, `36c9cc1`).

## Результаты проверок
- `pytest server/tests -q`: 20 passed.
- `ruff check .`: All checks passed!
- `ruff format --check .`: 29 files already formatted.
- Ветка: `stage-1/skeleton`.
- Статус: `READY_FOR_QA`.
