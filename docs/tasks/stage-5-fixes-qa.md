# Задача QA: Верификация исправлений Этапа 5 (телеметрия RPi)

- **Этап**: 5 (доработки CLI-клиента)
- **Компонент**: `cli`
- **Режим**: LIGHT
- **Ветка**: `stage-5/fixes`
- **Allowed Paths**: `tests/e2e/`, `docs/qa_log.md`

## Прочитать перед началом
1. `docs/PROJECT.md` (раздел 9 — «Правила скорости и тестов»).
2. `docs/CLI_SPEC.md` п. 3.7 (`system status`).
3. `docs/tasks/stage-5-fixes.md` и `docs/handoff/cli.md`.

## Критерии приёмки
1. Верифицировать корректность отображения телеметрии RPi в `system status`:
   - При наличии валидного heartbeat от RPi в блоке «Телеметрия Raspberry Pi» отображаются реальные значения:
     - Edge Agent, Python, Модель / ОС, Режим GPIO
     - Аптайм (в часах и минутах, не `0 ч 0 мин`)
     - Температура CPU, Загрузка CPU, RAM, Свободно на диске, Очередь офлайн
   - При отсутствии данных или оффлайне — корректное предупреждение или fallback.
2. Проверить unit-тесты: `clients/cli/.venv/bin/pytest clients/cli/tests -q` (40 тестов).
3. Проверить сквозной e2e-тест: `server/.venv/bin/pytest tests/e2e/test_cli_e2e.py -q` (включая `test_cli_e2e_system_status_telemetry`).
4. Проверить изоляцию импортов: `grep -rn --include="*.py" "from server\|from shared\|from edge_agent\|import server\|import shared" clients/cli` пуст.
5. Линтер `ruff check .` и `ruff format --check .` проходят чисто.

Зафиксировать результаты в `docs/qa_log.md` и опубликовать вердикт (PASS / FAIL).
