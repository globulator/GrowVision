# Реестр ручных задач пользователя

## Открытые задачи

### T-001 | Статус: [ ] не выполнена | Блокирует: приемку этапа 6 (v0.3.0)
**Зачем:** Подтвердить корректность работы Edge-агента в строгом боевом режиме (`HARDWARE_MODE=rpi`) с реальными GPIO-реле (свет, вспышка), USB-камерами (UVC, V4L2) и нейросетевой сегментацией на микрокомпьютере Raspberry Pi 5 или Raspberry Pi 3 Model B.
**Шаги:**
1. Подготовьте плату Raspberry Pi (рекомендуется **Raspberry Pi 5**; также поддерживается **Raspberry Pi 3 Model B** с 64-битной ОС Raspberry Pi OS Lite и zram) согласно [docs/DEPLOYMENT_RPI.md](file:///home/mcperchik/study/GrowVision/docs/DEPLOYMENT_RPI.md).
2. Подключите релейный модуль и USB-камеры по схеме [docs/HARDWARE_WIRING.md](file:///home/mcperchik/study/GrowVision/docs/HARDWARE_WIRING.md). Убедитесь, что используются USB-камеры с поддержкой UVC (CSI-камеры не поддерживаются драйвером V4L2).
3. Проверьте, что в системе определились камеры с постоянными путями:
   ```bash
   ls -la /dev/v4l/by-id/*-video-index0
   ```
4. Настройте системные пакеты и права пользователя на Raspberry Pi:
   ```bash
   sudo apt install -y git python3-venv python3-pip v4l-utils libgl1 libglib2.0-0 python3-gpiozero python3-lgpio
   sudo usermod -aG video,gpio $USER
   ```
5. Склонируйте репозиторий, создайте виртуальное окружение с доступом к системным пакетам (`--system-site-packages` для работы системных `gpiozero` и `lgpio`) и установите зависимости:
   ```bash
   python3 -m venv --system-site-packages edge_agent/.venv
   edge_agent/.venv/bin/pip install -r edge_agent/requirements.txt
   edge_agent/.venv/bin/pip install opencv-python-headless onnxruntime
   ```
6. Скопируйте файл конфигурации `edge_agent/.env.example` в `edge_agent/.env`:
   - Задайте рабочий `SERVER_URL` и общий `EDGE_API_KEY`.
   - Укажите `HARDWARE_MODE=rpi`.
   - Проверьте полярность реле: `RELAY_ACTIVE_HIGH=true` (или `false`, если плата с опторазвязкой управляется низким уровнем).
   - Задайте путь к модели: `MODEL_PATH=edge_agent/models/lettuce_seg_v1.onnx`.
7. Поместите файл обученной ONNX-модели в директорию `edge_agent/models/lettuce_seg_v1.onnx` (при отсутствии файла модель помечается `none`, а замеры площади передаются пустыми `measurements: []` без вымышленных значений).
8. Запустите агент:
   ```bash
   edge_agent/.venv/bin/python -m edge_agent.main
   ```
**Как проверить, что готово:**
1. Проверьте телеметрию: `clients/cli/.venv/bin/python -m clients.cli.main system status` — статус `RPi: online`, реальные температура CPU, аптайм, модель платы (Raspberry Pi 5 или 3 Model B) и имя загруженной ONNX-модели (`lettuce_seg_v1.onnx`) в блоке ПО RPi.
2. Проверьте обнаружение камер: `clients/cli/.venv/bin/python -m clients.cli.main camera scan` — найдены камеры по стабильным ID из `/dev/v4l/by-id`.
3. Выполните пробный снимок: `clients/cli/.venv/bin/python -m clients.cli.main snap take 1` — основной свет временно выключается, вспышка включается, происходит съемка всех камер установки, затем свет возвращается в исходное состояние, а снимок и замеры сохраняются на сервере.
4. Проверьте ручное управление светом: `clients/cli/.venv/bin/python -m clients.cli.main light main 1 off` — свет выключается и НЕ включается обратно при последующих Heartbeat; `clients/cli/.venv/bin/python -m clients.cli.main light test 1` — реле тестируется и возвращает исходное состояние.
**Если не получилось:** Пришлите последние 50 строк лога агента (`journalctl -u growvision-edge -n 50` или консольный вывод), а также системные логи ядра `dmesg | tail -n 30`.

## Выполненные задачи
