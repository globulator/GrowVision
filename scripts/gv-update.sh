#!/usr/bin/env bash
set -euo pipefail

# Определение директории репозитория GrowVision
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "=== GrowVision Update (gv-update) ==="
echo "Рабочая директория: ${PROJECT_ROOT}"
cd "${PROJECT_ROOT}"

echo "-> [1/3] Проверка и подтягивание изменений (git pull --ff-only)..."
git pull --ff-only

echo "-> [2/3] Обновление виртуальных окружений Python..."

# Сервер
if [ -d "${PROJECT_ROOT}/server/.venv" ] && [ -f "${PROJECT_ROOT}/server/requirements.txt" ]; then
    echo "  Обновление зависимостей server..."
    "${PROJECT_ROOT}/server/.venv/bin/pip" install --upgrade pip --quiet
    "${PROJECT_ROOT}/server/.venv/bin/pip" install -r "${PROJECT_ROOT}/server/requirements.txt" --quiet
fi

# Telegram-бот
if [ -d "${PROJECT_ROOT}/clients/telegram_bot/.venv" ] && [ -f "${PROJECT_ROOT}/clients/telegram_bot/requirements.txt" ]; then
    echo "  Обновление зависимостей telegram_bot..."
    "${PROJECT_ROOT}/clients/telegram_bot/.venv/bin/pip" install --upgrade pip --quiet
    "${PROJECT_ROOT}/clients/telegram_bot/.venv/bin/pip" install -r "${PROJECT_ROOT}/clients/telegram_bot/requirements.txt" --quiet
fi

# CLI
if [ -d "${PROJECT_ROOT}/clients/cli/.venv" ] && [ -f "${PROJECT_ROOT}/clients/cli/requirements.txt" ]; then
    echo "  Обновление зависимостей cli..."
    "${PROJECT_ROOT}/clients/cli/.venv/bin/pip" install --upgrade pip --quiet
    "${PROJECT_ROOT}/clients/cli/.venv/bin/pip" install -r "${PROJECT_ROOT}/clients/cli/requirements.txt" --quiet
fi

# Edge-агент
if [ -d "${PROJECT_ROOT}/edge_agent/.venv" ] && [ -f "${PROJECT_ROOT}/edge_agent/requirements.txt" ]; then
    echo "  Обновление зависимостей edge_agent..."
    "${PROJECT_ROOT}/edge_agent/.venv/bin/pip" install --upgrade pip --quiet
    "${PROJECT_ROOT}/edge_agent/.venv/bin/pip" install -r "${PROJECT_ROOT}/edge_agent/requirements.txt" --quiet
fi

echo "-> [3/3] Перезапуск установленных systemd-служб..."
SERVICES=("growvision-server" "growvision-bot" "growvision-edge")
RESTARTED=0

for svc in "${SERVICES[@]}"; do
    if systemctl list-unit-files "${svc}.service" --no-legend 2>/dev/null | grep -q "${svc}.service"; then
        echo "  Перезапуск ${svc}..."
        if [ "$EUID" -eq 0 ]; then
            systemctl restart "${svc}"
        else
            sudo systemctl restart "${svc}"
        fi
        RESTARTED=$((RESTARTED + 1))
    fi
done

if [ "$RESTARTED" -eq 0 ]; then
    echo "  Службы systemd не найдены или не установлены (ручной запуск)."
else
    echo "  Успешно перезапущено служб: ${RESTARTED}."
fi

echo "=== Обновление GrowVision успешно завершено! ==="
