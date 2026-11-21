# Задача: Этап 6 (доработки 2) — реле при Heartbeat, возврат пинов после RELAY_TEST, пути и документация

- **Этап**: 6 (Edge-агент для Raspberry Pi 5 — fixes 2)
- **Компонент**: `edge`
- **Режим**: LIGHT
- **Ветка**: `stage-6/fixes-2`
- **Версию**: не поднимать (0.2.1 сохраняется; теги не ставить)
- **Allowed Paths**:
  - `edge_agent/`
  - `tests/e2e/`
  - `docs/DEPLOYMENT_RPI.md`
  - `docs/handoff/edge.md`

## Прочитать перед началом
1. `docs/PROJECT.md`, включая **раздел 9 («Правила скорости и тестов»)** — обязателен для Builder и QA!
   - Быстрые тесты (<3 с на тест, <60 с весь набор).
   - Запрещены реальные `sleep` (длительность `RELAY_TEST` параметризовать/подменять).
   - Запуск через `timeout 300 ... pytest`.
   - При 2 неудачах подряд — остановка и статус `BLOCKED`.
2. `docs/REPO_AND_VERSIONING.md` п. 3.2.
3. `docs/API_CONTRACT.md` разд. 3.

## Критерии приёмки
1. **Реле при Heartbeat (`perform_heartbeat`)**:
   - `perform_heartbeat` НЕ переключает реле на каждом heartbeat!
   - Основной свет включается и вспышка выключается (`light=True, flash=False`) **только** для новых установок, которых агент ещё не инициализировал (вести `set` инициализированных установок по `id` и пинам).
   - Инициализация реле выполняется **только если аппаратный мьютекс свободен** (`hardware.lock.acquire(blocking=False)`). Если мьютекс занят (идёт съёмка), переключение реле не производится и откладывается до следующего heartbeat.
   - Ручная команда `LIGHT_MAIN` / `LIGHT_FLASH` сохраняется до следующей плановой съёмки или перезапуска агента (heartbeat не сбрасывает её).
   - Heartbeat, пришедший во время выполнения съёмки, не меняет состояние пинов.
2. **Возврат пинов после `RELAY_TEST`**:
   - Перед тестом сохраняется текущее состояние каждого проверяемого пина (`prev_states = {pin: hardware.get_pin_state(pin)}`).
   - В блоке `finally` каждый пин возвращается в своё сохранённое исходное состояние `prev_states[pin]` (включённый основной свет остаётся включённым, а не гасится).
   - В `HardwareManager` реализовать метод чтения текущего состояния пина (`get_pin_state(pin) -> bool`), работающий как в mock, так и в rpi режиме.
3. **Резолв путей и унификация модели**:
   - Относительные пути в `.env` (`MODEL_PATH`, `OFFLINE_QUEUE_DIR`) при чтении в `config.py` всегда считаются относительно корня репозитория (`REPO_ROOT = Path(__file__).resolve().parent.parent`).
   - Везде используется единый путь к модели: `edge_agent/models/lettuce_seg_v1.onnx`:
     - в `edge_agent/.env.example` (`MODEL_PATH=edge_agent/models/lettuce_seg_v1.onnx`),
     - в `edge_agent/config.py` (`DEFAULT_MODEL_PATH = REPO_ROOT / "edge_agent" / "models" / "lettuce_seg_v1.onnx"`),
     - в `docs/DEPLOYMENT_RPI.md`.
   - В `docs/DEPLOYMENT_RPI.md`: вместо `<OWNER>` в ссылке на релиз указать «ваш аккаунт GitHub».
4. **Unit-тесты (быстрые, без реального железа и sleep)** в `edge_agent/tests/`:
   - (а) Два `perform_heartbeat` подряд не переключают пины уже инициализированной установки;
   - (б) Выключение основного света через `LIGHT_MAIN off` остаётся выключенным после следующего `perform_heartbeat`;
   - (в) `perform_heartbeat` при занятом аппаратном мьютексе не трогает пины;
   - (г) `RELAY_TEST` возвращает пины в исходное состояние (включённый свет остаётся включённым);
   - (д) Относительный `MODEL_PATH` резолвится от корня репозитория.

Формат коммитов: Conventional Commits (`fix(edge): ...`, `test(edge): ...`, `docs(edge): ...`).
По завершении обновить `docs/handoff/edge.md` и сообщить `READY_FOR_QA`.
