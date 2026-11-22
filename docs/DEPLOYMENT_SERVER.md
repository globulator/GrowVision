# Руководство по развертыванию сервера и Telegram-бота GrowVision

В данном руководстве описан процесс установки, настройки и непрерывной работы бэкенд-сервера и Telegram-бота системы **GrowVision** на сервере или управляющем ПК под управлением Linux (Ubuntu 22.04/24.04 LTS или Debian 12 Bookworm).

---

## 1. Системные требования

- **Операционная система**: Linux (Ubuntu 22.04/24.04 LTS, Debian 12 64-bit).
- **Python**: версия 3.11 или новее (`python3 --version`).
- **Системные утилиты**: `ffmpeg` (библиотека `libx264` для кодирования MP4-таймлапсов), `git`, `ufw`.
- **Сеть**: статический локальный или публичный IP-адрес, открытый TCP-порт 8000 для входящих подключений от Raspberry Pi 5, веб-браузеров и CLI.
- **Дисковое пространство**: от 20 ГБ свободного места для долговременного хранения медиа-файлов кадра (`server/media/`) и базы данных SQLite.

---

## 2. Подготовка операционной системы

### 2.1. Установка системных пакетов

Обновите пакетную базу и установите компилятор, утилиты виртуальных окружений и `ffmpeg`:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git python3-venv python3-pip ffmpeg ufw
```

Убедитесь, что `ffmpeg` собран с поддержкой кодека `libx264`:
```bash
ffmpeg -encoders 2>/dev/null | grep libx264
```

### 2.2. Настройка брандмауэра (UFW)

Сервер принимает HTTP-запросы на порту 8000 от Raspberry Pi (Edge API) и пользователей (веб-плеер, CLI, внешние интеграции):

```bash
sudo ufw allow 8000/tcp comment 'GrowVision Server'
sudo ufw enable
sudo ufw status
```

---

## 3. Клонирование репозитория и окружения

1. Клонируйте репозиторий проекта в домашнюю директорию или в `/opt/GrowVision`:
```bash
cd /opt
sudo git clone https://github.com/growvision/GrowVision.git
sudo chown -R $USER:$USER /opt/GrowVision
cd /opt/GrowVision
```

> [!IMPORTANT]
> Клонируйте репозиторий с историей `.git`. Версионирование сервера и edge-агента (`shared/version.py`) опирается на git-хэш коммита (`0.3.1-<хэш>`).

2. Подготовьте изолированное окружение бэкенд-сервера:
```bash
python3 -m venv server/.venv
server/.venv/bin/pip install --upgrade pip
server/.venv/bin/pip install -r server/requirements.txt
```

3. Подготовьте изолированное окружение Telegram-бота:
```bash
python3 -m venv clients/telegram_bot/.venv
clients/telegram_bot/.venv/bin/pip install --upgrade pip
clients/telegram_bot/.venv/bin/pip install -r clients/telegram_bot/requirements.txt
```

---

## 4. Конфигурация окружения (.env)

### 4.1. Конфигурация сервера (`server/.env`)

Скопируйте шаблон и заполните параметры:
```bash
cp server/.env.example server/.env
nano server/.env
```

Пример настроек `server/.env`:
```ini
# Сетевой интерфейс и порт
SERVER_HOST=0.0.0.0
SERVER_PORT=8000

# Путь к SQLite базе данных (транзакции WAL включены автоматически)
DATABASE_URL=sqlite:///./growvision.db

# Секретный ключ для авторизации Raspberry Pi (заголовок X-Edge-Key)
EDGE_API_KEY=generate_strong_secret_for_edge_here

# Секретный ключ для клиентов (заголовок X-Client-Key для CLI и Telegram-бота)
CLIENT_API_KEY=generate_strong_secret_for_clients_here

# Пароли встроенных ролей для входа через POST /api/auth/login
BIOLOGIST_PASSWORD=secret_biologist_password
DEVELOPER_PASSWORD=secret_developer_password

# Параметры кодирования таймлапсов
VIDEO_MAX_FPS=10
VIDEO_MIN_DURATION_SEC=2
```

### 4.2. Конфигурация Telegram-бота (`clients/telegram_bot/.env`)

Скопируйте шаблон и заполните параметры:
```bash
cp clients/telegram_bot/.env.example clients/telegram_bot/.env
nano clients/telegram_bot/.env
```

Пример настроек `clients/telegram_bot/.env`:
```ini
# Токен бота от @BotFather
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz

# Внутренний адрес сервера GrowVision
SERVER_URL=http://127.0.0.1:8000

# Должен совпадать со значением CLIENT_API_KEY из server/.env
CLIENT_API_KEY=generate_strong_secret_for_clients_here

# Внешний URL для веб-плеера таймлапса (отправляется по кнопке в чате)
WEB_PUBLIC_URL=http://192.168.1.100:8000
```

---

## 5. Проверка ручного запуска

Перед переводом сервисов под управление systemd убедитесь в их работоспособности:

1. **Запуск сервера**:
```bash
server/.venv/bin/python -m server.main
```
> [!NOTE]
> Сервер запускается строго в **один воркер** (`workers=1`), так как ассоциации `command_id -> capture_id` и кэш результатов сканирования камер хранятся в потокобезопасной памяти процесса (`InMemoryState`).

- Проверьте доступность Swagger UI: `http://127.0.0.1:8000/docs`
- Проверьте отдачу веб-плеера: `http://127.0.0.1:8000/`

2. **Запуск Telegram-бота** (в отдельном терминале):
```bash
clients/telegram_bot/.venv/bin/python -m clients.telegram_bot.main
```
Отправьте боту команду `/start` в Telegram и проверьте появление приветственного меню. Остановите оба процесса сочетанием клавиш `Ctrl + C`.

---

## 6. Настройка служб systemd

Для непрерывной фоновой работы создайте два системных сервиса: `growvision-server` и `growvision-bot`.

### 6.1. Служба сервера: `/etc/systemd/system/growvision-server.service`

Создайте файл службы:
```bash
sudo nano /etc/systemd/system/growvision-server.service
```

Вставьте конфигурацию (замените `mcperchik` на имя вашего пользователя и проверьте путь к проекту):
```ini
[Unit]
Description=GrowVision Backend Server (FastAPI + SQLite)
After=network.target

[Service]
Type=simple
User=mcperchik
Group=mcperchik
WorkingDirectory=/opt/GrowVision
ExecStart=/opt/GrowVision/server/.venv/bin/python -m server.main
Restart=always
RestartSec=5
KillMode=process

# Защита и лимиты
LimitNOFILE=65536
StandardOutput=journal
StandardError=journal
SyslogIdentifier=growvision-server

[Install]
WantedBy=multi-user.target
```

### 6.2. Служба Telegram-бота: `/etc/systemd/system/growvision-bot.service`

Создайте файл службы:
```bash
sudo nano /etc/systemd/system/growvision-bot.service
```

Вставьте конфигурацию:
```ini
[Unit]
Description=GrowVision Telegram Bot Client (aiogram 3)
After=network.target growvision-server.service
Wants=growvision-server.service

[Service]
Type=simple
User=mcperchik
Group=mcperchik
WorkingDirectory=/opt/GrowVision
ExecStart=/opt/GrowVision/clients/telegram_bot/.venv/bin/python -m clients.telegram_bot.main
Restart=always
RestartSec=5

StandardOutput=journal
StandardError=journal
SyslogIdentifier=growvision-bot

[Install]
WantedBy=multi-user.target
```

---

## 7. Управление и мониторинг служб

1. Перечитайте конфигурацию systemd и включите автозапуск сервисов при старте системы:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now growvision-server.service
sudo systemctl enable --now growvision-bot.service
```

2. Проверьте статус работы:
```bash
sudo systemctl status growvision-server.service
sudo systemctl status growvision-bot.service
```

3. Просмотр журналов логов в реальном времени:
```bash
# Логи сервера
sudo journalctl -u growvision-server.service -f

# Логи Telegram-бота
sudo journalctl -u growvision-bot.service -f
```

4. Перезапуск при обновлении кода:
```bash
git pull origin main
sudo systemctl restart growvision-server growvision-bot
```
