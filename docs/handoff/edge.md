# Отчёт о реализации: Этап 6 — Edge-агент для Raspberry Pi 5

- **Статус**: READY_FOR_QA
- **Ветка**: `stage-6/edge-agent`
- **Компонент**: `edge`
- **Карточка**: `docs/tasks/stage-6-edge.md`
- **Спецификация API**: `docs/API_CONTRACT.md` (раздел 3: Edge API)

---

## 1. Выполненные задачи и архитектурные решения

### 1.1. Структура компонента и строгая изоляция
- Edge-агент реализован в каталоге `edge_agent/` с собственным виртуальным окружением `.venv` и зависимостями в `edge_agent/requirements.txt` (`httpx`, `pydantic`, `python-dotenv`, `psutil`, `pytest`, `pytest-asyncio`).
- Точка входа: `python -m edge_agent.main`.
- **Строгая изоляция**:
  - Edge-агент взаимодействует с сервером исключительно через HTTP по спецификации `API_CONTRACT.md` с обязательным заголовком `X-Edge-Key`.
  - Запрещены любые импорты из `server/` и `clients/`. Проверка `grep -rn --include="*.py" "from server|from clients|import server|import clients" edge_agent` возвращает абсолютно пустой вывод (подтверждено тестом `test_strict_isolation_forbidden_imports`).
  - Разрешено и используется общее ядро `shared/` (`shared.schemas`, `shared.version`, `shared.logger`).
- Изоляция секретов и временных данных:
  - Создан `edge_agent/.gitignore`, исключающий `.env`, виртуальное окружение и данные локальной офлайн-очереди.
  - Подготовлен файл примера конфигурации `edge_agent/.env.example`.

### 1.2. Конфигурация (`edge_agent/config.py`)
- Загрузка настроек через `load_config()` с приоритетом переменных окружения над файлом `.env` (без засорения глобального `os.environ`).
- Валидация наличия обязательного ключа `EDGE_API_KEY`.
- Поддержка параметров: `SERVER_URL`, `EDGE_API_KEY`, `HARDWARE_MODE`, `HEARTBEAT_INTERVAL_SEC`, `CLAIM_POLL_INTERVAL_SEC`, `OFFLINE_QUEUE_DIR`, `MODELS_DIR`, `MODEL_NAME`, `CAPTURE_INTERVAL_HOURS`, `DEBUG_MODE`.

### 1.3. Аппаратный модуль и аппаратный мьютекс (`edge_agent/hardware.py`)
- Поддержка двух режимов через `HARDWARE_MODE`:
  - `HARDWARE_MODE=rpi`: реальное управление пинами через `gpiozero.OutputDevice` / `lgpio`, сканирование видеоустройств V4L2 через sysfs (`/sys/class/video4linux`), захват кадров со вспышкой через `cv2.VideoCapture`.
  - `HARDWARE_MODE=mock`: безопасный запуск на ПК и в тестах. Эмуляция состояния пинов в памяти, генерация детерминированных тестовых камер (`mock-usb-cam-1`, `mock-csi-cam-2`), генерация валидных JPEG-кадров со вспышкой.
- **Единый аппаратный мьютекс**:
  - Реализован потокобезопасный `threading.Lock()` на всё аппаратное обеспечение.
  - Плановая автосъёмка имеет наивысший приоритет. При поступлении ручной команды во время занятости оборудования ручная команда не ждет в очереди и мгновенно отклоняется с сообщением:
    `"Камера недоступна, попробуйте позже"`.
- Сбор полной аппаратной телеметрии хоста в `get_hardware_telemetry()`:
  - `board_model` (чтение из `/proc/device-tree/model` или fallback),
  - `os_info` (`platform.platform()`),
  - `uptime_sec` (чтение из `/proc/uptime`),
  - `cpu_temp_c` (чтение из `/sys/class/thermal/thermal_zone0/temp`),
  - `cpu_usage_percent` (`psutil.cpu_percent()`),
  - `ram_used_mb` и `ram_total_mb` (`psutil.virtual_memory()`),
  - `disk_free_gb` (`shutil.disk_usage()`),
  - `gpio_mode` (`"rpi"` или `"mock"`).

### 1.4. Компьютерное зрение и нейросеть (`edge_agent/vision.py`)
- Детекция ArUco-маркера:
  - Поиск маркеров по словарю `cv2.aruco.DICT_4X4_50` с поддержкой как нового `cv2.aruco.ArucoDetector`, так и классического API OpenCV.
  - Вычисление площади первого маркера в пикселях: `aruco_area_px`.
  - В mock-режиме: детерминированный возврат `1200.0 px`.
- Сегментация растений через ONNX:
  - Поддержка инференса ONNX-модели сегментации (`ort.InferenceSession`) с предварительной нормализацией кадра и постобработкой контуров `cv2.findContours`.
  - Вычисление площади контуров растений (`measurements: [PlantMeasurementMeta(plant_index=i, area_px=...)]`).
  - Отрисовка зелёной маски и контуров сегментации на `annotated_image`.
  - В mock-режиме (и при отсутствии файла модели): детерминированная генерация замера `area_px = 5400.0` и аннотированного кадра с зелёным контуром.

### 1.5. Сетевой клиент Edge API (`edge_agent/client.py`)
- Построен на `httpx.Client` с заголовком `X-Edge-Key`:
  - `send_heartbeat(telemetry)` -> `POST /api/edge/heartbeat` (отправка `HeartbeatRequest`, прием `HeartbeatResponse`).
  - `claim_command()` -> `POST /api/edge/commands/claim` (забор 1 команды со сменой статуса `pending -> running`).
  - `send_command_result(cmd_id, result)` -> `POST /api/edge/commands/{cmd_id}/result` (отправка статуса `done` / `error`, обработка 409 Conflict).
  - `upload_capture(metadata, raw_bytes, annotated_bytes)` -> `POST /api/edge/captures` (`multipart/form-data`).

### 1.6. Локальная офлайн-очередь (`edge_agent/offline_queue.py`)
- Буферизация снимков в `edge_agent/offline_queue/{timestamp}_{installation_id}_{uuid}/` (`metadata.json`, `raw.jpg`, `ann.jpg`).
- Подсчет ожидающих пакетов в `get_queue_count()` для включения в телеметрию Heartbeat.
- Выгрузка в `flush_queue()`:
  - Коды `200` или `201` -> пакет успешно удаляется из очереди.
  - Код `409` (камера не зарегистрирована) -> пакет остается в очереди до регистрации камеры через `SCAN_CAMERAS`.
  - Коды `422` или `404` -> пакет перемещается в `edge_agent/offline_queue/failed/`, чтобы битые данные не блокировали очередь.
  - Сетевые ошибки (ConnectError) -> выгрузка прерывается до следующей попытки связи.

### 1.7. Оркестратор жизненного цикла (`edge_agent/orchestrator.py` и `main.py`)
- Параллельные потоки:
  1. `HeartbeatLoop`: отправка телеметрии раз в 30 с, обновление интервала автосъемки, флага `debug_mode` и списка активных установок; при наличии пакетов в очереди — запуск сброса `flush_queue`.
  2. `CommandPollLoop`: опрос очереди команд раз в 2–3 с:
     - `LIGHT_MAIN`: включение/выключение основного света на установке или всех (`target=all`).
     - `LIGHT_FLASH`: включение/выключение вспышки.
     - `RELAY_TEST`: кратковременный 3-секундный тест реле с автоматическим возвратом пинов в OFF.
     - `SCAN_CAMERAS`: поиск камер через V4L2/sysfs и возврат списка `scanned_cameras`.
     - `SNAP`: включение вспышки, захват кадра, детекция маркера и сегментация, **загрузка кадра на сервер с `command_id` строго перед отправкой отчета `result`**.
  3. `SchedulerLoop`: плановая автосъемка всех активных установок по расписанию (`capture_interval_hours`).
- Корректная обработка сигналов `SIGINT` и `SIGTERM` с освобождением ресурсов GPIO и остановкой потоков.

### 1.8. Документация
- `docs/DEPLOYMENT_RPI.md`: подробное руководство по развертыванию на Raspberry Pi 5 (установка зависимостей, настройка окружения, systemd unit-файл `growvision-edge.service`, диагностика).
- `docs/HARDWARE_WIRING.md`: схема подключения модулей реле (пины BCM 17, BCM 27, +5V, GND), подключение USB и CSI камер, требования к электропитанию 27W.

---

## 2. Результаты тестирования и линтинга

1. **Юнит-тесты Edge-агента (`edge_agent/tests/`)**:
   - `test_config.py` (4 теста): дефолты, валидация API-ключа, загрузка из файла, переопределение из env.
   - `test_hardware.py` (6 тестов): управление пинами, тест реле, сканирование камер, захват кадра, аппаратный мьютекс, телеметрия.
   - `test_vision.py` (3 теста): детекция ArUco, сегментация растений, комплексный пайплайн.
   - `test_offline_queue.py` (5 тестов): сохранение, подсчет, удаление при 200/201, сохранение при 409, перенос в `failed/` при 422/404 и битых пакетах.
   - `test_client.py` (5 тестов): Heartbeat, Claim, Result (200 и 409), загрузка multipart/form-data.
   - `test_orchestrator.py` (7 тестов): команды света, вспышки, реле-теста, сканирования, SNAP (порядок кадр -> result), приоритет плановой съемки над ручной командой, буферизация в офлайн-очередь при сбое сети.
   - `test_isolation.py` (1 тест): строгая проверка отсутствия импортов из `server/` и `clients/`.
   - **Итог**: `31 passed in 0.18s`.

2. **Статический анализ и стиль (Ruff)**:
   - `server/.venv/bin/ruff check .`: All checks passed!
   - `server/.venv/bin/ruff format --check .`: 122 files already formatted.

3. **Соблюдение ограничений**:
   - `VERSION` не изменялся.
   - Все файлы строго в пределах Allowed Paths (`edge_agent/`, `docs/DEPLOYMENT_RPI.md`, `docs/HARDWARE_WIRING.md`, `docs/handoff/edge.md`).
