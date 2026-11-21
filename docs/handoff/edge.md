# Отчёт о реализации: Этап 6 (доработки 2) — реле, пути конфигурации и тесты Edge-агента

- **Статус**: READY_FOR_QA
- **Ветка**: `stage-6/fixes-2`
- **Компонент**: `edge`
- **Карточка**: `docs/tasks/stage-6-fixes-2.md`
- **Спецификация API**: `docs/API_CONTRACT.md` (раздел 3: Edge API)

---

## 1. Выполненные задачи и архитектурные решения

### 1.1. Инициализация реле в Heartbeat и сохранение ручного состояния
- В `EdgeOrchestrator` внедрено отслеживание инициализированных установок через `self._initialized_installations: set[tuple[int, int, int]]` по ключу `(installation_id, light_gpio_pin, flash_gpio_pin)`.
- `init_installations_light()` вызывается в `perform_heartbeat()` только для новых установок, которых ещё нет в `_initialized_installations`.
- Инициализация выполняется строго под неблокирующим аппаратным мьютексом (`hardware.acquire_lock(blocking=False)`). Если мьютекс занят (идёт съёмка или выполнение ручной команды), пины не трогаются, а инициализация откладывается до следующего heartbeat.
- Ручные команды `LIGHT_MAIN` и `LIGHT_FLASH` сохраняют своё состояние при последующих heartbeat (не сбрасываются). Состояние сохраняется до следующей плановой автосъёмки или перезапуска агента.
- Heartbeat во время съёмки не меняет состояние пинов.

### 1.2. Возврат исходного состояния пинов после `RELAY_TEST`
- В `HardwareManager` реализован метод `get_pin_state(pin: int) -> bool` для обоих режимов:
  - `mock`: считывание из словаря виртуальных пинов `_mock_pins`.
  - `rpi`: считывание логического состояния устройства `OutputDevice` (`is_active` / `value`).
- В `HardwareManager.test_relay` фиксируются исходные состояния всех тестируемых пинов (`prev_states = {pin: self.get_pin_state(pin) for pin in pins}`), пины кратковременно включаются, а в блоке `finally` гарантированно возвращаются в их исходное состояние до теста (включённый свет остаётся включённым).

### 1.3. Резолв относительных путей от корня репозитория
- В `edge_agent/config.py` зафиксирован корень репозитория:
  `REPO_ROOT = Path(__file__).resolve().parent.parent`.
- Реализована функция `resolve_path(p: str | Path) -> Path`, гарантирующая, что любые относительные пути в переменных окружения (`MODEL_PATH`, `OFFLINE_QUEUE_DIR`) резолвятся от `REPO_ROOT`, а не от текущего каталога запуска процесса.
- Везде унифицирован единый путь к модели: `edge_agent/models/lettuce_seg_v1.onnx` (в `.env.example`, `config.py`, `docs/DEPLOYMENT_RPI.md`).
- В `docs/DEPLOYMENT_RPI.md` заполнитель `<OWNER>` заменён на «ваш аккаунт GitHub».

### 1.4. Быстрые юнит-тесты без реальных sleep и железа
В `edge_agent/tests/` реализованы целевые проверки (<60 с):
- `test_subsequent_heartbeats_do_not_retrigger_relay`: два heartbeat подряд не меняют пины уже инициализированной установки (`set_light`/`set_flash` повторно не вызываются).
- `test_light_main_off_persists_after_subsequent_heartbeat`: `LIGHT_MAIN off` остаётся выключенным после следующего heartbeat.
- `test_heartbeat_defers_light_init_when_mutex_locked`: heartbeat при занятом мьютексе не трогает пины и успешно выполняет инициализацию на следующем heartbeat после освобождения мьютекса.
- `test_mock_relay_test_restores_initial_state` и `test_handle_command_relay_test_zero_duration`: `RELAY_TEST` возвращает пины в исходное состояние (включённый свет остаётся включённым).
- `test_relative_paths_resolved_from_repo_root`: относительный `MODEL_PATH` и `OFFLINE_QUEUE_DIR` корректно резолвятся от корня репозитория (`REPO_ROOT`).

---

## 2. Результаты проверок и метрики

1. **Юнит-тесты Edge-агента (`edge_agent/tests/`)**:
   - `39 passed in 0.82s` (`edge_agent/.venv/bin/pytest edge_agent -c edge_agent/pytest.ini -v`).
2. **Юнит-тесты CLI (`clients/cli/tests/`)**:
   - `42 passed in 0.45s` (`clients/cli/.venv/bin/pytest clients/cli/tests -q`).
3. **Серверные и e2e тесты (`server/tests/`, `tests/e2e/`)**:
   - `103 passed in 22.87s` (`server/.venv/bin/pytest server/tests tests/e2e -q`).
4. **Общий регрессионный набор**:
   - 184 теста успешно пройдено за <25 с (<60 с по разделу 9 `PROJECT.md`).
5. **Статический анализ и линтинг**:
   - `server/.venv/bin/ruff check .`: All checks passed!
   - `server/.venv/bin/ruff format --check .`: 132 files already formatted.
6. **Состояние репозитория**:
   - `git status --short` чист, временные файлы и базы данных отсутствуют.
   - Защищенные файлы спецификаций и `VERSION` сохранены без изменений.
