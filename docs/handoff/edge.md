# Отчёт о реализации: Этап 6 (доработки) — боевой режим Edge-агента и CLI

- **Статус**: READY_FOR_QA
- **Ветка**: `stage-6/fixes`
- **Компонент**: `edge`, `cli`
- **Карточка**: `docs/tasks/stage-6-fixes.md`
- **Спецификация API**: `docs/API_CONTRACT.md` (раздел 3: Edge API)

---

## 1. Выполненные задачи и архитектурные решения

### 1.1. Строгий боевой режим (rpi) без молчаливых подмен
- `HARDWARE_MODE`: валидация на этапе загрузки конфигурации (`edge_agent/config.py`). Разрешены только `"rpi"` и `"mock"`; при любом ином значении выбрасывается `ValueError` с аварийным завершением агента.
- Если `HARDWARE_MODE=rpi`:
  - При отсутствии системных библиотек `gpiozero` / `lgpio` агент падает с `RuntimeError` без отката в mock (`edge_agent/hardware.py`).
  - При отсутствии `opencv` (`cv2`) захват кадра не производится, команда завершается с ошибкой (`status: "error"`), а `aruco_area_px` устанавливается в `None`.
  - При отсутствии ONNX-модели или ошибке инференса в лог пишется `ERROR`, замеры возвращаются пустыми (`measurements = []`), а аннотированное изображение строго равно исходному сырому кадру (без выдуманных площадей и синтетических контуров).
- В телеметрии Heartbeat:
  - `software.model_name` отдаёт реальное имя файла загруженной модели или `"none"`.
  - `hardware.gpio_mode` возвращает фактический режим работы (`"rpi"` или `"mock"`).

### 1.2. Сканирование и выбор камер
- Сканирование камер выполняется строго по каталогу `/dev/v4l/by-id/` с фильтрацией файлов `*-video-index0` (`edge_agent/hardware.py`).
- Имя файла `*-video-index0` является стабильным `hardware_id`.
- Захват кадра выполняется по реальному пути устройства (`os.path.realpath`).
- Если `camera_hardware_id` не найден в `/dev/v4l/by-id/`, агент генерирует исключение и возвращает ошибку (молчаливый откат на `/dev/video0` исключён).
- Пути к `/dev/v4l/by-id` и `/sys` параметризованы в `HardwareManager` для изоляции в тестах.

### 1.3. Прогрев и тайминги кадра
- Поддержаны параметры конфигурации из `REPO_AND_VERSIONING.md` п. 3.2:
  - `CAMERA_WARMUP_FRAMES` (по умолчанию 5): считывание и отбрасывание первых N кадров перед фиксацией целевого снимка.
  - `RELAY_SWITCH_DELAY_MS` (по умолчанию 200 мс): задержка стабилизации после переключения реле.

### 1.4. Последовательность реле света и вспышки
- Состояние в норме: основной свет ВКЛЮЧЕН (`light=1, flash=0`).
- На время съемки кадра:
  1. Основной свет выключается (`light=0`).
  2. Вспышка включается (`flash=1`).
  3. Задержка стабилизации `RELAY_SWITCH_DELAY_MS`.
  4. Захват кадра с прогревом `CAMERA_WARMUP_FRAMES`.
  5. В блоке `finally` гарантированно возвращается исходное состояние: вспышка выключается (`flash=0`), свет включается (`light=1`).
- Формируется строка отладочного лога для `debug_log_line`:
  `"[GPIO] Inst#N: light=0, flash=1 -> captured K frames"`.

### 1.5. SNAP для всех камер и установок
- Команда `SNAP` выполняет последовательную съёмку со всех привязанных камер установки (`camera_hardware_ids`), загружая каждый кадр на `/api/edge/captures` с `command_id = cmd.id`.
- Для `target="all"` (`cmd.is_global = True`) выполняется съёмка по всем активным установкам и всем их камерам.
- Статус команды `done`, если успешно снят хотя бы один кадр.

### 1.6. Полярность реле
- Добавлен параметр `RELAY_ACTIVE_HIGH` (по умолчанию `True`).
- При прямой логике (`active_high=True`) логическая 1 = HIGH, при инверсной (`active_high=False`) — LOW.
- При старте основной свет включается, вспышка выключается с учётом заданной полярности.

### 1.7. Индексация растений
- Контуры сегментации нейросети сортируются по площади (`cv2.contourArea`) по убыванию.
- После фильтрации шума контуры нумеруются `plant_index` подряд с 0 (`0, 1, 2, ...`).

### 1.8. Офлайн-очередь и надежность сети
- Сброс очереди (`flush_queue`) вынесен в отдельный фоновый поток под замком `threading.Lock()` (защита от параллельных flush).
- За один проход выгружается не более 10 пакетов (`max_batch_size = 10`).
- Запись пакета в очередь атомарна: запись во временный каталог `.tmp_{pkg_id}`, затем атомарный перенос `os.replace`.
- `send_command_result`: до 3 попыток отправки с паузой 1 с при сетевых сбоях.
- Таймаут загрузки кадра в HTTP-клиенте увеличен до 60 с (`upload_timeout_sec = 60.0`).
- Файл `failed/.gitkeep` защищен от случайного удаления.

### 1.9. Планировщик автосъемки
- Плановая съёмка запускается строго по астрономической сетке UTC: в начале каждого N-го часа (`HH % N == 0 and MM == 0`, где `N = capture_interval_hours`).

### 1.10. Конфигурация и игнорирование
- Поддержаны имена переменных `MODEL_PATH`, `COMMAND_POLL_INTERVAL_SEC`.
- В `edge_agent/.env.example` ключ `EDGE_API_KEY=` оставлен пустым.
- В корневой `.gitignore` добавлены `*.db-wal` и `*.db-shm`.

### 1.11. Документация
- `docs/DEPLOYMENT_RPI.md`: создание venv с `--system-site-packages`, установка apt-пакетов `python3-gpiozero`/`python3-lgpio`, примечание по Raspberry Pi 3 Model B (Raspberry Pi OS Lite, zram-swap), скачивание модели из релиза `model-v1`, пример `User=pi`, явное указание отсутствия поддержки CSI-камер.
- `docs/HARDWARE_WIRING.md`: проверка полярности реле по встроенному светодиоду, предупреждение о смертельной опасности 220 В.
- В `clients/cli` убрано упоминание CSI из справки `camera info`.

### 1.12. Доработки CLI
- `camera scan`: при `status == "error"` выводит `result_message` в `stderr` и завершается с кодом 1.
- `snap take`: при `status == "error"` выводит `result_message` в `stderr` и завершается с кодом 1.
- `light main`, `light flash`, `light test`: при `status == "error"` выводят `result_message` в `stderr` и завершаются с кодом 1.
- `system status`: отображает `model_name` в блоке ПО Raspberry Pi.

### 1.13. Тесты и изоляция
- Убраны реальные `time.sleep` (длительность `RELAY_TEST` параметризована).
- В `tests/e2e/conftest.py` изолирована БД: подменены `server.main.init_db`, `server.main.SessionLocal`, `server.db.database.SessionLocal`, `server.db.database.engine`. В корне репозитория после полного прогона тестов не создаются `growvision.db*`, `git status --short` чист.
- Добавлены юнит-тесты на все ветки боевого режима.

---

## 2. Результаты проверок и метрики

1. **Юнит-тесты Edge-агента (`edge_agent/tests/`)**:
   - `35 passed in 0.81s` (`edge_agent/.venv/bin/pytest edge_agent -c edge_agent/pytest.ini -q`).
2. **Юнит-тесты CLI (`clients/cli/tests/`)**:
   - `42 passed in 0.47s` (`clients/cli/.venv/bin/pytest clients/cli/tests -q`).
3. **Серверные и e2e тесты (`server/tests/`, `tests/e2e/`)**:
   - `103 passed in 22.58s` (`server/.venv/bin/pytest server/tests tests/e2e -q`).
4. **Общий регрессионный набор**:
   - 180 тестов успешно пройдено за <25 с (<60 с по разделу 9 `PROJECT.md`).
5. **Статический анализ и линтинг**:
   - `server/.venv/bin/ruff check .`: All checks passed!
   - `server/.venv/bin/ruff format --check .`: 130 files already formatted.
6. **Состояние репозитория**:
   - `git status --short` не содержит `growvision.db*` или временных файлов.
   - `VERSION` не изменялся.
