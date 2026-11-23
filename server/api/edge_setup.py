import secrets
import shlex
import subprocess
import time
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from server.api.deps import require_role
from server.config import get_settings
from server.db.models import User
from shared.logger import get_logger

logger = get_logger("server.api.edge_setup")

router = APIRouter(prefix="/api/edge/setup", tags=["edge-setup"])

# In-memory хранилище токенов bootstrap-скриптов.
# Не сохраняется в БД, очищается по TTL, токен удаляется сразу при первом обращении.
_setup_tokens: dict[str, dict[str, Any]] = {}


def _cleanup_stale_tokens() -> None:
    """Удаляет просроченные токены из оперативной памяти."""
    now = time.time()
    stale_keys = [k for k, v in _setup_tokens.items() if v["expires_at"] < now]
    for k in stale_keys:
        _setup_tokens.pop(k, None)


def _detect_repo_url() -> str | None:
    """Определяет публичный URL Git-репозитория."""
    settings = get_settings()
    if settings.repo_url:
        return settings.repo_url
    try:
        res = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=1.0,
            check=False,
        )
        if res.returncode == 0:
            url = res.stdout.strip()
            if url:
                return url
    except Exception as e:  # noqa: BLE001
        logger.debug("Не удалось определить URL git: %s", e)
    return None


class EdgeSetupTokenRequest(BaseModel):
    """Схема запроса генерации одноразового токена настройки Edge-агента."""

    model_config = ConfigDict(extra="ignore")

    server_url: str = Field(..., min_length=1)
    netbird_setup_key: str | None = Field(default=None)
    hardware_mode: Literal["rpi", "mock"] = Field(default="rpi")

    @field_validator("server_url")
    @classmethod
    def validate_server_url(cls, v: str) -> str:
        s = v.strip()
        if not s.startswith(("http://", "https://")):
            raise ValueError("Адрес сервера должен начинаться с http:// или https://")
        if any(ch in s for ch in ("\r", "\n", " ", "\t")):
            raise ValueError(
                "Адрес сервера не может содержать пробелы или переводы строк"
            )
        return s


@router.get("/defaults", response_model=dict[str, str | None])
def get_edge_setup_defaults(
    _current_user: Annotated[User, Depends(require_role("developer"))],
) -> dict[str, str | None]:
    """Возвращает настройки по умолчанию для помощника настройки Edge (только developer)."""
    settings = get_settings()
    return {
        "default_server_url": settings.edge_server_url,
        "repo_url": _detect_repo_url(),
    }


@router.post("/token", response_model=dict[str, Any])
def create_edge_setup_token(
    body: EdgeSetupTokenRequest,
    _current_user: Annotated[User, Depends(require_role("developer"))],
) -> dict[str, Any]:
    """Генерирует криптостойкий одноразовый токен установки Edge (TTL 10 мин, in-memory)."""
    _cleanup_stale_tokens()
    token = secrets.token_urlsafe(24)
    now = time.time()
    ttl = 600  # 10 минут (600 секунд)

    _setup_tokens[token] = {
        "server_url": body.server_url,
        "netbird_setup_key": (
            body.netbird_setup_key.strip() if body.netbird_setup_key else None
        ),
        "hardware_mode": body.hardware_mode,
        "repo_url": _detect_repo_url(),
        "created_at": now,
        "expires_at": now + ttl,
    }
    logger.info("Сгенерирован одноразовый токен настройки Edge-агента (TTL: %d с)", ttl)
    return {
        "token": token,
        "expires_in_sec": ttl,
        "script_path": f"/api/edge/setup/{token}",
    }


@router.get("/{token}")
def get_edge_setup_script(token: str) -> Response:
    """Возвращает одноразовый bash-скрипт установки Edge-агента и сразу сжигает токен."""
    _cleanup_stale_tokens()
    # Сжигаем токен при первом обращении
    data = _setup_tokens.pop(token, None)
    if not data or data["expires_at"] < time.time():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Токен установки не найден или истёк",
        )

    settings = get_settings()
    server_url_q = shlex.quote(data["server_url"])
    edge_api_key_q = shlex.quote(settings.edge_api_key)
    hardware_mode_q = shlex.quote(data["hardware_mode"])
    repo_url_q = shlex.quote(
        data["repo_url"] or "https://github.com/<owner>/<repo>.git"
    )
    netbird_key = data.get("netbird_setup_key")
    netbird_key_q = shlex.quote(netbird_key) if netbird_key else ""

    script = f"""#!/usr/bin/env bash
set -euo pipefail

echo "=============================================="
echo "    GrowVision Edge Agent Auto-Installer      "
echo "=============================================="

TARGET_USER="${{USER:-$(id -un)}}"
TARGET_HOME="${{HOME:-$(eval echo "~${{TARGET_USER}}")}}"
TARGET_DIR="${{TARGET_HOME}}/GrowVision"

echo "-> [1/7] Установка системных зависимостей (git, python3-venv, python3-pip, curl)..."
sudo apt-get update -y
sudo apt-get install -y git python3-venv python3-pip curl

NETBIRD_KEY={netbird_key_q}
if [ -n "$NETBIRD_KEY" ]; then
    echo "-> [2/7] Установка и настройка NetBird VPN..."
    if ! command -v netbird >/dev/null 2>&1; then
        curl -fsSL https://pkgs.netbird.io/install.sh | sudo sh
    fi
    sudo netbird up --setup-key "$NETBIRD_KEY" || echo "Предупреждение: не удалось подключить NetBird автоматически"
    sudo systemctl enable --now netbird || true
else
    echo "-> [2/7] NetBird пропущен (ключ не передан)"
fi

REPO_URL={repo_url_q}
echo "-> [3/7] Подготовка репозитория в $TARGET_DIR..."
if [ -d "$TARGET_DIR/.git" ]; then
    echo "Директория уже существует, обновление..."
    cd "$TARGET_DIR"
    git pull --ff-only || echo "Предупреждение: git pull не удался, продолжаю с текущей версией"
else
    echo "Клонирование репозитория..."
    git clone "$REPO_URL" "$TARGET_DIR"
    cd "$TARGET_DIR"
fi

echo "-> [4/7] Создание виртуального окружения edge_agent/.venv..."
if [ ! -d "$TARGET_DIR/edge_agent/.venv" ]; then
    python3 -m venv "$TARGET_DIR/edge_agent/.venv"
fi
"$TARGET_DIR/edge_agent/.venv/bin/pip" install --upgrade pip
if [ -f "$TARGET_DIR/edge_agent/requirements.txt" ]; then
    "$TARGET_DIR/edge_agent/.venv/bin/pip" install -r "$TARGET_DIR/edge_agent/requirements.txt"
fi

echo "-> [5/7] Запись конфигурации $TARGET_DIR/edge_agent/.env..."
SERVER_URL_VAL={server_url_q}
EDGE_KEY_VAL={edge_api_key_q}
HW_MODE_VAL={hardware_mode_q}

cat <<ENV_EOF > "$TARGET_DIR/edge_agent/.env"
SERVER_URL=${{SERVER_URL_VAL}}
EDGE_API_KEY=${{EDGE_KEY_VAL}}
HARDWARE_MODE=${{HW_MODE_VAL}}
ENV_EOF
chmod 600 "$TARGET_DIR/edge_agent/.env"

echo "-> [6/7] Создание и запуск systemd-службы growvision-edge..."
SERVICE_FILE="/etc/systemd/system/growvision-edge.service"
sudo tee "$SERVICE_FILE" > /dev/null <<SERVICE_EOF
[Unit]
Description=GrowVision Edge Agent
After=network-online.target netbird.service
Wants=network-online.target

[Service]
Type=simple
User=${{TARGET_USER}}
WorkingDirectory=${{TARGET_DIR}}/edge_agent
ExecStart=${{TARGET_DIR}}/edge_agent/.venv/bin/python main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE_EOF

sudo systemctl daemon-reload
sudo systemctl enable --now growvision-edge.service

echo "-> [7/7] Установка утилиты /usr/local/bin/gv-update..."
if [ -f "$TARGET_DIR/scripts/gv-update.sh" ]; then
    chmod +x "$TARGET_DIR/scripts/gv-update.sh"
    sudo ln -sf "$TARGET_DIR/scripts/gv-update.sh" /usr/local/bin/gv-update
fi

echo "=============================================="
echo "GrowVision Edge Agent успешно установлен!"
echo "=============================================="
"""
    return Response(content=script, media_type="text/x-shellscript")
