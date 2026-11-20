# Задача: Доработки Этапа 5 (CLI-клиент) — отображение телеметрии Raspberry Pi

- **Этап**: 5 (доработки CLI-клиента)
- **Компонент**: `cli`
- **Режим**: LIGHT
- **Ветка**: `stage-5/fixes`
- **Версия**: не поднимать
- **Allowed Paths**:
  - `clients/cli/commands/system.py`
  - `clients/cli/tests/test_system.py`
  - `tests/e2e/test_cli_e2e.py`
  - `docs/handoff/cli.md`

## Прочитать перед началом
1. `docs/PROJECT.md`, включая **раздел 9 («Правила скорости и тестов»)**.
2. `docs/CLI_SPEC.md`, раздел 3.7 (`system status`).
3. `server/api/system.py` (формирование ответа `GET /api/system/status`) и `shared/schemas.py` (`HeartbeatRequest`, `HeartbeatSoftware`, `HeartbeatHardware`).

## Проблема
На ручной проверке команды `system status`: при статусе `RPi: online` панель «Телеметрия Raspberry Pi» выводит прочерки (`—`) и `Аптайм: 0 ч 0 мин`.
Причина: `server/api/system.py` возвращает `rpi_telemetry` из сохранённого JSON `HeartbeatRequest`, имеющего вложенные словари `software` и `hardware`, а также поля `uptime_sec` (вместо `uptime_seconds`), `cpu_usage_percent` (вместо `cpu_load_percent`), `board_model`. Команда `system status` в `clients/cli/commands/system.py` ожидала плоскую структуру словаря с другими именами ключей.

## Критерии приёмки
1. **Парсинг телеметрии в `clients/cli/commands/system.py`**:
   - Извлекать данные с учётом реальной вложенной структуры `rpi_telemetry`:
     - `software`: `edge_version`, `python_version`, `model_name`
     - `hardware`: `board_model`, `os_info`, `uptime_sec`, `cpu_temp_c`, `cpu_usage_percent`, `ram_used_mb`, `ram_total_mb`, `disk_free_gb`, `gpio_mode`
     - Верхний уровень: `offline_queue_count`
   - Для совместимости предусмотреть fallback (поддерживать чтение как из вложенных `software`/`hardware`, так и напрямую из плоского словаря, если вложенные секции отсутствуют).
   - Аптайм форматировать из секунд `uptime_sec`: `{uptime_sec // 3600} ч {(uptime_sec % 3600) // 60} мин`.
   - Загрузку процессора брать из `cpu_usage_percent` (fallback на `cpu_load_percent`).
   - Модель/ОС: `{board_model} ({os_info})` (fallback модели на `model_name`).
2. **Юнит-тесты в `clients/cli/tests/test_system.py`**:
   - Обновить тест `test_system_status_online` с реальной вложенной структурой ответа сервера (`software`, `hardware`).
   - Добавить явные проверки (`assert`) на присутствие реальных значений параметров телеметрии в `res.stdout` (температура CPU, проценты загрузки, RAM, диск, аптайм, модель платы, ОС, версия агента).
3. **E2E-тесты в `tests/e2e/test_cli_e2e.py`**:
   - Добавить сквозной e2e-тест команды `system status` через CLI-подпроцесс на реальном FastAPI-сервере.
   - Сценарий: после отправки валидного heartbeat от mock RPi вызывается подпроцесс CLI `system status`, проверяется успешный код 0 и наличие реальных чисел телеметрии в выводе (отсутствие прочерков при online).
4. **Изоляция и качество**:
   - Строгая изоляция сохраняется (`grep -rn --include="*.py" "from server\|from shared\|from edge_agent\|import server\|import shared" clients/cli` пуст).
   - Все тесты (`clients/cli/tests`, `server/tests`, `tests/e2e`) проходят быстро (<60с).
   - `ruff check .` и `ruff format --check .` проходят без ошибок.
   - Обновить `docs/handoff/cli.md` и сообщить `READY_FOR_QA`.
