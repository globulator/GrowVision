# Руководство по развертыванию Edge-агента на Raspberry Pi

В данном руководстве описан процесс установки, настройки и запуска Edge-агента системы **GrowVision** на микрокомпьютере Raspberry Pi под управлением Raspberry Pi OS (Debian 12 Bookworm, 64-bit).

> [!TIP]
> Вы можете автоматизировать создание `edge_agent/.venv` (с `--system-site-packages` для `gpiozero`/`lgpio`), проверку камер/GPIO, настройку `edge_agent/.env` и генерацию `growvision-edge.service` с помощью единого мастера [`install.sh`](../install.sh):
> ```bash
> # Интерактивная установка Edge-агента (профиль 3):
> ./install.sh --profile 3 --topology B --output-systemd deploy/systemd
> # Только диагностика железа, камер, GPIO и доступности сервера:
> ./install.sh --check --non-interactive --profile 3 --topology B --server-url http://<SERVER_IP>:8000
> ```
> При распределённом развёртывании (топологии **B** и **C**) рекомендуется связывать RPi и сервер через **NetBird** или **Tailscale** — мастер автоматически обнаруживает VPN-интерфейс и проверяет `/health` сервера.

---

## 1. Системные требования и поддерживаемые платформы

- **Рекомендуемая платформа**: Raspberry Pi 5 (4 ГБ или 8 ГБ RAM).
- **Минимально поддерживаемая платформа**: Raspberry Pi 3 Model B (64-битная ОС, 1 ГБ RAM).
  > [!TIP]
  > При установке на Raspberry Pi 3 Model B (1 ГБ RAM) рекомендуется использовать **Raspberry Pi OS Lite** (без графической оболочки Desktop), настроить zram-swap (`sudo apt install -y zram-tools`) и следить за потреблением оперативной памяти при инференсе нейросети.
- **Поддерживаемые камеры**:
  > [!IMPORTANT]
  > Поддерживаются **только стандартные USB UVC камеры** с драйвером Video4Linux2 (`/dev/v4l/by-id/*-video-index0`). Подключение CSI-камер через шлейф (libcamera / Picamera2) в текущей версии агента **не поддерживается**.
- **ОС**: Raspberry Pi OS (Debian 12 Bookworm, 64-bit).
- **Питание**: Официальный блок питания Raspberry Pi 27W USB-C PD (для RPi 5) или качественный адаптер 5.1V / 2.5A (для RPi 3).
- **Исполнительное оборудование**: 2- или 4-канальный модуль реле с управлением через GPIO (основной фитосвет и вспышка).

---

## 2. Подготовка операционной системы

Обновите пакетную базу и установите системные библиотеки и пакеты управления GPIO:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git python3-venv python3-pip v4l-utils libgl1 libglib2.0-0 python3-gpiozero python3-lgpio
```

Добавьте текущего пользователя в группы `video` и `gpio`:

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

2. Создайте изолированное виртуальное окружение с доступом к системным пакетам (`--system-site-packages` для использования системных `gpiozero` и `lgpio`):
```bash
python3 -m venv --system-site-packages edge_agent/.venv
source edge_agent/.venv/bin/activate
```

3. Установите базовые зависимости агента:
```bash
pip install --upgrade pip
pip install -r edge_agent/requirements.txt
```

4. Установите библиотеки компьютерного зрения:
```bash
pip install opencv-python-headless onnxruntime
```

---

## 4. Загрузка ONNX-модели нейросети

Модель сегментации растений распространяется через GitHub Releases репозитория проекта:

```bash
mkdir -p edge_agent/models
# Пример загрузки модели версии model-v1 из релиза:
curl -L -o edge_agent/models/lettuce_seg_v1.onnx \
  https://github.com/<ваш аккаунт GitHub>/GrowVision/releases/download/model-v1/lettuce_seg_v1.onnx
```

Если модель временно не установлена, агент запустится в безопасном режиме: замеры будут пустыми (`measurements = []`), а в лог будет записано предупреждение `ERROR`.

---

## 5. Конфигурация окружения (.env)

Создайте файл `edge_agent/.env` на основе примера:

```bash
cp edge_agent/.env.example edge_agent/.env
nano edge_agent/.env
```

Заполните параметры:

```ini
# URL сервера GrowVision
SERVER_URL=http://192.168.1.100:8000

# Секретный ключ для авторизации (задаётся администратором, совпадает с сервером)
EDGE_API_KEY=your-edge-secret-key-here

# Режим работы оборудования: 'rpi' для реального железа, 'mock' для тестов
HARDWARE_MODE=rpi

# Путь к загруженной ONNX модели
MODEL_PATH=edge_agent/models/lettuce_seg_v1.onnx

# Интервалы опроса (в секундах)
HEARTBEAT_INTERVAL_SEC=30
COMMAND_POLL_INTERVAL_SEC=2

# Параметры реле и камеры
RELAY_SWITCH_DELAY_MS=200
CAMERA_WARMUP_FRAMES=5
RELAY_ACTIVE_HIGH=true

# Локальная офлайн-очередь
OFFLINE_QUEUE_DIR=edge_agent/offline_queue

# Дефолтный интервал плановой съемки (в часах)
CAPTURE_INTERVAL_HOURS=1

# Режим отладки (true/false)
DEBUG_MODE=false
```

---

## 6. Проверка работы вручную

Выполните тестовый запуск агента:

```bash
cd ~/GrowVision
source edge_agent/.venv/bin/activate
python -m edge_agent.main
```

В консоли отобразится старт фоновых циклов Heartbeat, опроса команд и планировщика. Для остановки нажмите `Ctrl+C`.

---

## 7. Настройка автозапуска через systemd

Для работы в фоновом режиме в виде службы операционной системы:

1. Создайте unit-файл `/etc/systemd/system/growvision-edge.service`:

```bash
sudo nano /etc/systemd/system/growvision-edge.service
```

2. Вставьте конфигурацию (в качестве примера указан пользователь `pi` и каталог `/home/pi/GrowVision` — укажите фактические имя пользователя и путь к каталогу):

```ini
[Unit]
Description=GrowVision Edge Agent for Raspberry Pi
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/GrowVision
ExecStart=/home/pi/GrowVision/edge_agent/.venv/bin/python -m edge_agent.main
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

4. Проверьте статус и журналы:

```bash
sudo systemctl status growvision-edge.service
journalctl -u growvision-edge.service -f
```

---

## 8. Диагностика и устранение неисправностей

### 8.1. Проверка USB-камер
Агент ищет камеры по пути `/dev/v4l/by-id/*-video-index0`:
```bash
ls -la /dev/v4l/by-id/
```
Убедитесь, что камера определяется и не занята сторонними процессами (`lsof /dev/video*`).

### 8.2. Проверка полярности реле
Если при включении питания свет выключается вместо включения — проверьте параметр `RELAY_ACTIVE_HIGH` в `edge_agent/.env`. Сверьтесь с индикацией встроенных светодиодов на плате реле (подробнее в `docs/HARDWARE_WIRING.md`).
