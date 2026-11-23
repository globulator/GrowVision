#!/usr/bin/env bash
set -euo pipefail

# Определение директории репозитория GrowVision
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TARGET_USER="${USER:-$(id -un)}"

echo "=============================================="
echo "    GrowVision Server Setup & Deployment      "
echo "=============================================="
echo "Рабочая директория: ${PROJECT_ROOT}"
echo "Пользователь службы: ${TARGET_USER}"

echo "-> [1/6] Установка системных пакетов (git, python3-venv, python3-pip, curl, sqlite3, ffmpeg)..."
if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -y
    sudo apt-get install -y git python3-venv python3-pip curl sqlite3 ffmpeg
fi

echo "-> [2/6] Настройка виртуального окружения server/.venv..."
if [ ! -d "${PROJECT_ROOT}/server/.venv" ]; then
    python3 -m venv "${PROJECT_ROOT}/server/.venv"
fi
"${PROJECT_ROOT}/server/.venv/bin/pip" install --upgrade pip --quiet
"${PROJECT_ROOT}/server/.venv/bin/pip" install -r "${PROJECT_ROOT}/server/requirements.txt" --quiet

echo "-> [3/6] Настройка виртуального окружения clients/telegram_bot/.venv..."
if [ ! -d "${PROJECT_ROOT}/clients/telegram_bot/.venv" ]; then
    python3 -m venv "${PROJECT_ROOT}/clients/telegram_bot/.venv"
fi
"${PROJECT_ROOT}/clients/telegram_bot/.venv/bin/pip" install --upgrade pip --quiet
"${PROJECT_ROOT}/clients/telegram_bot/.venv/bin/pip" install -r "${PROJECT_ROOT}/clients/telegram_bot/requirements.txt" --quiet

echo "-> [4/6] Проверка и создание конфигурации server/.env..."
if [ ! -f "${PROJECT_ROOT}/server/.env" ]; then
    echo "  Файл server/.env не найден, генерация из шаблона со случайными ключами..."
    
    # Генерация криптостойких ключей
    GEN_EDGE_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
    GEN_CLIENT_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
    GEN_DEV_PWD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(16))')"

    cat <<ENV_EOF > "${PROJECT_ROOT}/server/.env"
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
DATABASE_URL=sqlite:///./growvision.db
EDGE_API_KEY=${GEN_EDGE_KEY}
CLIENT_API_KEY=${GEN_CLIENT_KEY}
DEVELOPER_PASSWORD=${GEN_DEV_PWD}
VIDEO_MAX_FPS=10
VIDEO_MIN_DURATION_SEC=2
REPO_URL=https://github.com/<owner>/<repo>.git
ENV_EOF
    chmod 600 "${PROJECT_ROOT}/server/.env"
    echo "  Файл server/.env успешно создан с правами 600."
    echo "  Сгенерированный пароль разработчика: ${GEN_DEV_PWD}"
else
    echo "  Файл server/.env уже существует, сохраняем существующие настройки."
fi

echo "-> [5/6] Настройка systemd-служб (growvision-server, growvision-bot)..."

# Служба сервера
SERVER_SERVICE="/etc/systemd/system/growvision-server.service"
sudo tee "${SERVER_SERVICE}" > /dev/null <<SERVICE_EOF
[Unit]
Description=GrowVision Backend Server
After=network.target

[Service]
Type=simple
User=${TARGET_USER}
WorkingDirectory=${PROJECT_ROOT}
ExecStart=${PROJECT_ROOT}/server/.venv/bin/uvicorn server.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
SERVICE_EOF

# Служба бота (если настроен BOT_TOKEN)
BOT_SERVICE="/etc/systemd/system/growvision-bot.service"
sudo tee "${BOT_SERVICE}" > /dev/null <<SERVICE_EOF
[Unit]
Description=GrowVision Telegram Bot
After=network.target growvision-server.service

[Service]
Type=simple
User=${TARGET_USER}
WorkingDirectory=${PROJECT_ROOT}/clients/telegram_bot
ExecStart=${PROJECT_ROOT}/clients/telegram_bot/.venv/bin/python main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE_EOF

sudo systemctl daemon-reload
sudo systemctl enable --now growvision-server.service || true
echo "  Служба growvision-server зарегистрирована."

echo "-> [5.1/6] Настройка беспарольного перезапуска служб через sudo (/etc/sudoers.d/growvision)..."
if [ -d "/etc/sudoers.d" ]; then
    echo "${TARGET_USER} ALL=(ALL) NOPASSWD: /bin/systemctl restart growvision-server, /bin/systemctl restart growvision-bot, /bin/systemctl restart growvision-edge, /bin/systemctl status growvision-*, /usr/bin/systemctl restart growvision-server, /usr/bin/systemctl restart growvision-bot, /usr/bin/systemctl restart growvision-edge, /usr/bin/systemctl status growvision-*" | sudo tee /etc/sudoers.d/growvision > /dev/null
    sudo chmod 0440 /etc/sudoers.d/growvision || true
    echo "  Правило sudoers для growvision успешно добавлено."
fi

echo "-> [6/6] Установка утилиты /usr/local/bin/gv-update..."
chmod +x "${PROJECT_ROOT}/scripts/gv-update.sh"
sudo ln -sf "${PROJECT_ROOT}/scripts/gv-update.sh" /usr/local/bin/gv-update

echo "=============================================="
echo "GrowVision Server успешно установлен и готов к работе!"
echo "=============================================="
