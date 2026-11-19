# Handoff: Этап 2 / Исправления Edge API (fixes)

## Реализация
1. `server/api/deps.py`: `secrets.compare_digest` производит сравнение байтов UTF-8 (`x_edge_key.encode("utf-8")`), предотвращая исключение 500 при не-ASCII символах в ключе; возвращается 401.
2. `server/api/edge.py`: функция `_normalize_utc_datetime` перехватывает `ValueError` и `TypeError` при парсинге `captured_at` и возвращает `HTTPException(422)`.
3. `server/api/edge.py`: в эндпоинте `POST /api/edge/commands/claim` реализован перевод зависших команд со статусом `running` в `timeout`:
   - Одиночные (`is_global=False`): при `started_at` старше 60 с;
   - Глобальный SNAP (`is_global=True`): при `started_at` старше `(15 + 10 * N) + 15` с, где N — количество активных установок.
4. `server/api/edge.py`: результат `SCAN_CAMERAS` сохраняется в памяти процесса и при пустом списке (`if body.scanned_cameras is not None:`).
5. `server/main.py`: версия FastAPI берется динамически через `shared.version.get_app_version()`.
6. `server/api/edge.py` и `server/tests/`: вынесена функция `get_media_dir()`, мокируемая в тестах через `monkeypatch` в `tmp_path`, благодаря чему рабочий каталог `server/media/` остается чистым.
7. `server/tests/test_edge_api.py`: добавлены тесты на проверку не-ASCII ключа, невалидной даты (422), таймаутов running команд, пустого результата SCAN_CAMERAS и версии приложения.

## Результаты проверок
- `server/.venv/bin/pytest server/tests -q`: 28 passed.
- `server/.venv/bin/pytest tests/e2e -q`: 19 passed.
- `server/.venv/bin/ruff check .`: All checks passed!
- `server/.venv/bin/ruff format --check .`: 49 files already formatted.
- Статус: `READY_FOR_QA`.
