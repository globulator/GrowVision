# Журнал контроля качества (QA Log)

## 1. Открытые дефекты и замечания
- **[MAJOR] [server/api/control.py] Значение `target: true` в JSON запросах эндпоинтов управления не валидируется и неявно приводится к целому числу 1.**
  - **Эндпоинты:** `POST /api/control/snap`, `POST /api/control/light/main`, `POST /api/control/light/flash`, `POST /api/control/relay-test`.
  - **Шаги воспроизведения:** Отправить POST-запрос с телом `{"target": true}` (или `{"target": true, "state": true}`).
  - **Ожидаемое поведение:** Валидация входных данных отклоняет boolean значение для target кодом 422 Unprocessable Content (`target: целое число или 'all'`).
  - **Фактическое поведение:** В моделях `SnapRequest`, `LightRequest`, `RelayTestRequest` валидатор `@field_validator("target")` объявлен без `mode="before"`. Из-за этого Pydantic v2 выполняет стандартную коэрсию `true -> 1` до вызова валидатора, проверка `if isinstance(v, bool):` получает число `1` и пропускает значение. Запрос пытается выполниться на установке с ID=1 (возвращает 404, либо выполняет команду на установке 1).

---

## 2. Исправленные дефекты (верифицировано QA)
*(Записи отсюда никогда не удаляются.)*

---

## 3. Рекомендации по улучшению UX и новым проверкам
- Устранить дублирование логики очистки таймаутов в `server/api/edge.py` (`edge_claim_command`), заменив ручной проход по командам на вызов `expire_stale_commands(db, now)` из `server.core.timeouts`.
