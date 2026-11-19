# Handoff: Этап 1 / Исправления моделей и валидации схем (fixes)

## Реализация
1. `server/db/models.py`: для отношения `Installation.edge_commands` добавлен параметр `passive_deletes=True`, благодаря чему при удалении установки команды каскадно удаляются средствами SQLite (`ondelete="CASCADE"`).
2. `shared/schemas.py`:
   - `PlantMeasurementMeta.area_px`: `Field(..., ge=0)`
   - `CaptureMeta.aruco_area_px`: `Field(default=None, gt=0)`
3. `server/tests/test_database.py`: добавлен юнит-тест `test_installation_delete_with_commands_cascades`.
4. `server/tests/test_schemas.py`: добавлен юнит-тест `test_measurement_and_aruco_bounds_validation`.

## Результаты проверок
- `pytest server/tests -q`: 22 passed.
- `ruff check .`: All checks passed!
- `ruff format --check .`: 36 files already formatted.
- Статус: `READY_FOR_QA`.
