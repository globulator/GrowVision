# План реализации: Этап 1 / Каркас репозитория и модели БД

1. Каркас каталогов и корневые файлы: .gitignore, VERSION, requirements-dev.txt (.gitkeep в пустых каталогах) (REPO_AND_VERSIONING.md разд. 2, PROJECT.md разд. 2).
2. Зависимости и переменные окружения сервера: server/requirements.txt, server/.env.example (REPO_AND_VERSIONING.md разд. 2, п. 3.1).
3. shared/version.py: get_app_version() с чтением VERSION и хэшем Git (REPO_AND_VERSIONING.md п. 6.1).
4. shared/logger.py: единый логгер с поддержкой DEBUG_MODE (REPO_AND_VERSIONING.md разд. 2, API_CONTRACT.md п. 1.3).
5. shared/schemas.py: Pydantic-модели Heartbeat, Command, CaptureMeta (API_CONTRACT.md разд. 3.1–3.4).
6. server/config.py: загрузка и валидация конфигурации сервера (REPO_AND_VERSIONING.md п. 3.1).
7. server/db/database.py: движок SQLite, события PRAGMA (foreign_keys, WAL, busy_timeout=5000), sessionmaker (DB_SCHEMA.md п. 4.1).
8. server/db/models.py: 7 таблиц ORM со всеми связями, ON DELETE, autoincrement, CHECK и индексами (DB_SCHEMA.md разд. 3, 4).
9. server/tests/: юнит-тесты на таблицы, PRAGMA, частичные индексы пинов, version.py и schemas.py (DB_SCHEMA.md разд. 4, карточка задачи).
10. Проверка качества: pytest server/tests -q, ruff check ., ruff format --check . (PROJECT.md разд. 2).
