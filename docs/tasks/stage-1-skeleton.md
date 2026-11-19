# Задача: Этап 1 / Каркас репозитория и модели БД
Режим: FULL
Ветка: stage-1/skeleton
Цель: Создать структуру каталогов по REPO_AND_VERSIONING.md, корневые файлы (.gitignore, VERSION, requirements-dev.txt), модуль shared (version.py, logger.py, schemas.py), базовый сервер (requirements.txt, .env.example, config.py, db/database.py, db/models.py) и тесты моделей и shared в server/tests/.
Прочитать перед началом: docs/team_roles/builder.md, docs/PROJECT.md (разд. 2, 3, 5 этап 1), docs/REPO_AND_VERSIONING.md (разд. 2, 4.1, 6.1), docs/DB_SCHEMA.md (все разделы), docs/API_CONTRACT.md (разд. 1, 3.1–3.4 для Pydantic-схем Heartbeat, Command, CaptureMeta).

Allowed Paths (можно создавать и менять только это):
- .gitignore
- VERSION
- requirements-dev.txt
- shared/
- server/config.py
- server/requirements.txt
- server/.env.example
- server/db/
- server/tests/
- edge_agent/models/.gitkeep
- edge_agent/tests/.gitkeep
- server/media/.gitkeep
- clients/cli/tests/.gitkeep
- clients/telegram_bot/tests/.gitkeep
- clients/web/.gitkeep
- docs/handoff/skeleton.md

Только чтение:
- docs/PROJECT.md
- docs/DB_SCHEMA.md
- docs/API_CONTRACT.md
- docs/REPO_AND_VERSIONING.md
- docs/team_roles/
- docs/STATE.md

Критерии приёмки:
0. Созданы все каталоги каркаса из REPO_AND_VERSIONING.md, разд. 2 (с .gitkeep в пустых каталогах: edge_agent/models/, edge_agent/tests/, server/media/, clients/cli/tests/, clients/telegram_bot/tests/, clients/web/), .gitignore по спецификации, VERSION с содержимым ровно "0.0.0", requirements-dev.txt (pytest, pytest-asyncio, httpx, ruff).
1. server/requirements.txt содержит fastapi, uvicorn, sqlalchemy, python-multipart, python-dotenv. server/.env.example заполнен по REPO_AND_VERSIONING.md п. 3.1.
2. shared/version.py реализует get_app_version() (возвращает 0.0.0-<хэш> в git и 0.0.0 без .git).
3. shared/logger.py реализует единый логгер с поддержкой DEBUG_MODE.
4. shared/schemas.py содержит Pydantic-модели Heartbeat, Command, CaptureMeta по API_CONTRACT.md разд. 3.
5. server/config.py загружает и валидирует настройки сервера из server/.env по REPO_AND_VERSIONING.md п. 3.1.
6. server/db/database.py настраивает SQLite: PRAGMA foreign_keys = ON, PRAGMA journal_mode = WAL, PRAGMA busy_timeout = 5000, сессии SQLAlchemy.
7. server/db/models.py реализует ровно 7 таблиц из DB_SCHEMA.md (installations, cameras, captures, plant_measurements, users, edge_commands, system_settings) со всеми полями, типами, внешними ключами (ON DELETE по таблице п. 4.2), sqlite_autoincrement=True, частичными уникальными индексами uq_active_light_pin и uq_active_flash_pin (where is_archived = 0), составными индексами и CHECK-ограничениями.
8. Юнит-тесты в server/tests/:
   - Создание БД на пустом SQLite даёт ровно 7 таблиц из DB_SCHEMA.md; PRAGMA foreign_keys = 1, journal_mode = wal, busy_timeout = 5000.
   - Две активные установки с одинаковым GPIO-пином нарушают ограничение, а архивная с тем же пином — нет (DB_SCHEMA.md п. 4.3).
   - get_app_version() возвращает корректную версию с хэшем коммита.
   - Валидация Pydantic-моделей в shared/schemas.py.
9. Тесты запускаются и проходят (`pytest server/tests -q`).
10. `ruff check .` и `ruff format --check .` проходят без ошибок.

Команды проверки:
- pytest server/tests -q
- ruff check .

Файл заметок (handoff): docs/handoff/skeleton.md
