# Задача QA: Этап 6 (доработки) — приёмочное тестирование боевого режима Edge-агента и CLI

- **Этап**: 6 (Edge-агент для Raspberry Pi 5 — fixes)
- **Компонент**: `edge`, `cli`
- **Режим**: FULL
- **Ветка**: `stage-6/fixes`
- **Allowed Paths**: `tests/e2e/`, `docs/qa_log.md`

## Прочитать перед началом
1. `docs/PROJECT.md`, включая **раздел 9 («Правила скорости и тестов»)** — обязателен для Builder и QA!
   - Быстрые тесты (<3 с на тест, <60 с весь набор).
   - Запрещены реальные `sleep`.
   - Запуск через `timeout 300 ... pytest`.
   - При 2 неудачах подряд — остановка и статус `BLOCKED`.
2. `docs/tasks/stage-6-fixes.md` — все 13 критериев приёмки.
3. `docs/handoff/edge.md` — отчёт Builder-а об архитектуре и реализации фиксов.

## Сценарии и критерии проверки
1. **Строгий боевой режим (rpi) без молчаливых подмен**:
   - При `HARDWARE_MODE=rpi`: при отсутствии библиотек `gpiozero`/`lgpio` агент завершается с понятной ошибкой; при отсутствии OpenCV кадр не захватывается и не загружается, команда получает `status: "error"`; при отсутствии файла модели или ошибке инференса — замеры `measurements: []`, в лог `ERROR`, `annotated_image` равен исходному кадру без выдуманных площадей; при отсутствии `cv2` `aruco_area_px = null`.
   - Невалидный `HARDWARE_MODE` -> ошибка конфигурации при старте.
   - В heartbeat `software.model_name` отдаёт реальное имя модели или `"none"`, `gpio_mode` — реальный режим.
2. **Сканирование камер**:
   - Поиск через `/dev/v4l/by-id/*-video-index0`, стабильный `hardware_id`, открытие по `os.path.realpath`, неизвестная камера -> ошибка. Системные пути параметризованы.
3. **Прогрев и задержка реле**:
   - `CAMERA_WARMUP_FRAMES` (5) и `RELAY_SWITCH_DELAY_MS` (200 мс).
4. **Последовательность реле**:
   - Основной свет в норме ВКЛЮЧЕН (`light=1, flash=0`).
   - На время кадра `light=0, flash=1`, восстановление состояния в `finally`.
   - Лог `"[GPIO] Inst#N: light=0, flash=1 -> captured K frames"` в `debug_log_line`.
5. **SNAP**:
   - Снимает все камеры установки (для `all` — все активные установки). `done` при наличии хотя бы 1 кадра.
6. **Полярность реле**:
   - `RELAY_ACTIVE_HIGH` (true по умолч.), свет включен при старте.
7. **Индексация растений**:
   - Сортировка контуров по убыванию площади, нумерация `plant_index` с 0 подряд.
8. **Офлайн-очередь**:
   - Flush в отдельном потоке (до 10 пакетов под Lock), атомарная запись (временная папка -> rename), 3 попытки `send_command_result`, таймаут загрузки 60 с.
9. **Планировщик**:
   - Запуск в начале каждого N-го часа UTC (`HH % N == 0 and MM == 0`).
10. **Конфигурация и игнорирование**:
    - `MODEL_PATH`, `COMMAND_POLL_INTERVAL_SEC`, пустой `EDGE_API_KEY=` в `.env.example`, `*.db-wal` и `*.db-shm` в `.gitignore`.
11. **Документы**:
    - `DEPLOYMENT_RPI.md`, `HARDWARE_WIRING.md`, убрано упоминание CSI из CLI `camera info`.
12. **CLI**:
    - `camera scan`, `snap take`, `light main/flash/test` при `status == "error"` выводят ошибку в stderr и завершаются кодом 1; `system status` показывает `model_name`.
13. **Тесты и чистота репозитория**:
    - Убраны реальные sleep.
    - БД изолирована (в корне нет `growvision.db*`, `git status --short` чист после тестов).
    - `edge_agent/offline_queue/failed/.gitkeep` сохранён.
    - Полный прогон всех тестов (`edge_agent/tests`, `clients/cli/tests`, `server/tests`, `tests/e2e`) проходит чисто.

По результатам проверки сделай запись в `docs/qa_log.md` и вынеси вердикт (`PASS` / `FAIL`).
