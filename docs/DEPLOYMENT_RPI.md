# Руководство по развертыванию Edge-агента на Raspberry Pi 5

В данном руководстве описан процесс установки, настройки и запуска Edge-агента системы **GrowVision** на микрокомпьютере Raspberry Pi 5 под управлением Raspberry Pi OS (Bookworm, 64-bit).

---

## 1. Системные требования

- **Плата**: Raspberry Pi 5 (рекомендуется 4 ГБ или 8 ГБ RAM).
- **ОС**: Raspberry Pi OS (Debian 12 Bookworm, 64-bit).
- **Питание**: Официальный блок питания Raspberry Pi 27W USB-C Power Delivery (5.1V / 5.0A).
- **Сетевое подключение**: Wi-Fi или Ethernet со стабильным доступом к серверу GrowVision.
- **Подключенное оборудование**:
  - Релейный модуль (управление светом и вспышкой).
  - USB или CSI камеры (с поддержкой V4L2).

---

## 2. Подготовка операционной системы

Обновите пакетную базу и установите необходимые системные утилиты и библиотеки:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git python3-venv python3-pip v4l-utils libgl1 libglib2.0-0 python3-gpiozero python3-lgpio
```

Убедитесь, что текущий пользователь (`pi` или ваш системный пользователь) входит в группы `video` и `gpio`:

```bash
sudo usermod -aG video,gpio $USER
```

---

## 3. Клонирование и установка зависимостей

1. Клонируйте репозиторий проекта:
```bash
cd ~
git clone <URL_РЕПОЗИТОРИЯ>/GrowVision.git
cd GrowVision
```

2. Создайте изолированное виртуальное окружение для Edge-агента:
```bash
python3 -m venv edge_agent/.venv
source edge_agent/.venv/bin/activate
```

3. Установите базовые зависимости:
```bash
pip install --upgrade pip
pip install -r edge_agent/requirements.txt
```

4. Для работы компьютерного зрения и аппаратного доступа на Raspberry Pi установите дополнительные библиотеки:
```bash
pip install opencv-python-headless onnxruntime gpiozero lgpio
```

---

## 4. Конфигурация окружения (.env)

Создайте файл `edge_agent/.env` на основе примера:

```bash
cp edge_agent/.env.example edge_agent/.env
nano edge_agent/.env
```

Заполните параметры:

```ini
# URL сервера GrowVision
SERVER_URL=http://192.168.1.100:8000

# Секретный ключ для авторизации (совпадает с EDGE_API_KEY на сервере)
EDGE_API_KEY=your-secure-edge-key-here

# Режим работы с оборудованием: 'rpi' для реального железа, 'mock' для тестов
HARDWARE_MODE=rpi

# Интервалы опроса (в секундах)
HEARTBEAT_INTERVAL_SEC=30
CLAIM_POLL_INTERVAL_SEC=2.0

# Локальная офлайн-очередь и модели
OFFLINE_QUEUE_DIR=edge_agent/offline_queue
MODELS_DIR=edge_agent/models
MODEL_NAME=lettuce_seg_v1.onnx

# Дефолтный интервал плановой съемки (в часах)
CAPTURE_INTERVAL_HOURS=1

# Режим отладки (true/false)
DEBUG_MODE=false
```

---

## 5. Проверка работоспособности вручную

Перед настройкой системной службы выполните тестовый запуск агента:

```bash
cd ~/GrowVision
source edge_agent/.venv/bin/activate
python -m edge_agent.main
```

В консоли должны появиться строки инициализации логгера, успешная отправка Heartbeat и опрос очереди команд. Для остановки нажмите `Ctrl+C`.

---

## 6. Настройка автозапуска через systemd

Для непрерывной фоновой работы создайте системную службу `systemd`:

1. Создайте unit-файл `/etc/systemd/system/growvision-edge.service`:

```bash
sudo nano /etc/systemd/system/growvision-edge.service
```

2. Вставьте следующую конфигурацию (замените `mcperchik` на имя вашего пользователя, если оно отличается):

```ini
[Unit]
Description=GrowVision Edge Agent for Raspberry Pi 5
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=mcperchik
WorkingDirectory=/home/mcperchik/GrowVision
ExecStart=/home/mcperchik/GrowVision/edge_agent/.venv/bin/python -m edge_agent.main
Restart=always
RestartSec=5s
Environment=PYTHONUNBUFFERED=1

# Безопасность и лимиты
KillMode=mixed
TimeoutStopSec=10s

[Install]
WantedBy=multi-user.target
```

3. Активируйте и запустите службу:

```bash
sudo systemctl daemon-reload
sudo systemctl enable growvision-edge.service
sudo systemctl start growvision-edge.service
```

4. Проверьте статус службы и просмотрите логи:

```bash
sudo systemctl status growvision-edge.service
journalctl -u growvision-edge.service -f
```

---

## 7. Диагностика и устранение неисправностей

### 7.1. Проверка обнаружения камер
Для проверки физического обнаружения USB/CSI-камер выполните:
```bash
v4l2-ctl --list-devices
```
Если устройства отсутствуют:
- Проверьте надежность подключения кабеля USB или шлейфа CSI.
- Убедитесь, что камера не занята другим процессом: `lsof /dev/video*`.

### 7.2. Проверка управления GPIO
Если реле не переключается:
- Проверьте номера пинов (в системе используется нумерация BCM, а не физические номера контактов).
- Проверьте наличие питания 5V на плате реле.
- Убедитесь, что библиотека `lgpio` корректно установлена: `python -c "import lgpio"`.

### 7.3. Локальная офлайн-очередь
При потере связи с сервером снимки буферизуются в `edge_agent/offline_queue/`.
После восстановления связи они автоматически выгружаются на сервер во время очередного Heartbeat или плановой автосъемки.
Битые пакеты изолируются в каталоге `edge_agent/offline_queue/failed/`.
