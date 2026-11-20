# Реестр ручных задач пользователя

## Открытые задачи

### T-001 | Статус: [ ] не выполнена | Блокирует: приемку этапа 6 (v0.3.0)
**Зачем:** Подтвердить корректность работы Edge-агента с реальными GPIO-реле (свет, вспышка), USB/CSI-камерами и нейросетевой сегментацией на микрокомпьютере Raspberry Pi 5.
**Шаги:**
1. Подключите релейный модуль и камеры к Raspberry Pi 5 по схеме [docs/HARDWARE_WIRING.md](file:///home/mcperchik/study/GrowVision/docs/HARDWARE_WIRING.md).
2. Настройте системные пакеты и права пользователя на Raspberry Pi 5 согласно [docs/DEPLOYMENT_RPI.md](file:///home/mcperchik/study/GrowVision/docs/DEPLOYMENT_RPI.md):
   ```bash
   sudo apt install -y git python3-venv python3-pip v4l-utils libgl1 libglib2.0-0 python3-gpiozero python3-lgpio
   sudo usermod -aG video,gpio $USER
   ```
3. Склонируйте репозиторий, создайте виртуальное окружение и установите зависимости:
   ```bash
   python3 -m venv edge_agent/.venv
   edge_agent/.venv/bin/pip install -r edge_agent/requirements.txt
   edge_agent/.venv/bin/pip install opencv-python-headless onnxruntime gpiozero lgpio
   ```
4. Создайте `edge_agent/.env` на базе `edge_agent/.env.example`, настроив `SERVER_URL`, `EDGE_API_KEY` и `HARDWARE_MODE=rpi`.
5. Поместите файл обученной ONNX-модели в директорию `edge_agent/models/model.onnx`.
6. Запустите агент:
   ```bash
   edge_agent/.venv/bin/python -m edge_agent.main
   ```
**Как проверить, что готово:**
1. Выполните проверку статуса: `clients/cli/.venv/bin/python -m clients.cli.main system status` — статус `RPi: online`, отображаются реальные параметры платы (температура CPU, модель платы, аптайм).
2. Выполните поиск камер: `clients/cli/.venv/bin/python -m clients.cli.main camera scan` — камеры обнаружены и зарегистрированы.
3. Сделайте пробный снимок: `clients/cli/.venv/bin/python -m clients.cli.main snap take 1` — реле вспышки срабатывает, получен кадр с замером площади.
**Если не получилось:** Пришлите последние 50 строк лога агента (`journalctl -u growvision-edge -n 50` или вывод терминала), а также вывод системных сообщений ядра `dmesg | tail -n 30`.

## Выполненные задачи
