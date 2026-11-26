#!/usr/bin/env python3
"""Единый интерактивный и CLI-мастер установки и развёртывания GrowVision.

Использует исключительно стандартную библиотеку Python (stdlib only).
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import json
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Константы профилей, топологий и поддерживаемых переменных окружения
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProfileSpec:
    """Описание профиля установки GrowVision."""

    id: str
    name: str
    title: str
    description: str
    components: tuple[str, ...]
    is_dev: bool = False


PROFILES: dict[str, ProfileSpec] = {
    "1": ProfileSpec(
        id="1",
        name="full",
        title="Full (All-in-One)",
        description="Server + Edge Agent + Telegram Bot + CLI на одной машине",
        components=("server", "edge_agent", "telegram_bot", "cli"),
    ),
    "2": ProfileSpec(
        id="2",
        name="server",
        title="Server only",
        description="Только сервер FastAPI + SQLite + Web SPA",
        components=("server",),
    ),
    "3": ProfileSpec(
        id="3",
        name="edge",
        title="Edge Agent only (RPi)",
        description="Только Edge-агент для Raspberry Pi (камеры, реле, ONNX)",
        components=("edge_agent",),
    ),
    "4": ProfileSpec(
        id="4",
        name="bot",
        title="Telegram Bot only",
        description="Только Telegram-бот (aiogram 3)",
        components=("telegram_bot",),
    ),
    "5": ProfileSpec(
        id="5",
        name="cli",
        title="CLI only",
        description="Только консольный клиент управления",
        components=("cli",),
    ),
    "6": ProfileSpec(
        id="6",
        name="dev",
        title="Dev / Mock",
        description="Локальная разработка без железа (HARDWARE_MODE=mock, MOCK_CAMERAS=true)",
        components=("server", "edge_agent", "telegram_bot", "cli"),
        is_dev=True,
    ),
}

PROFILE_ALIASES: dict[str, str] = {
    **{spec.id: spec.id for spec in PROFILES.values()},
    **{spec.name: spec.id for spec in PROFILES.values()},
    "mock": "6",
    "edge_agent": "3",
    "telegram_bot": "4",
}

TOPOLOGIES: dict[str, str] = {
    "A": "Монолит (127.0.0.1) — все компоненты на одном хосте",
    "B": "Раздельная (Server + Bot на одном хосте, Edge отдельно на RPi)",
    "C": "Распределённая (Server, Edge и Bot на разных хостах / через VPN)",
}

COMPONENT_PATHS: dict[str, dict[str, str]] = {
    "server": {
        "dir": "server",
        "requirements": "server/requirements.txt",
        "env_example": "server/.env.example",
        "env_file": "server/.env",
        "venv": "server/.venv",
    },
    "edge_agent": {
        "dir": "edge_agent",
        "requirements": "edge_agent/requirements.txt",
        "env_example": "edge_agent/.env.example",
        "env_file": "edge_agent/.env",
        "venv": "edge_agent/.venv",
    },
    "telegram_bot": {
        "dir": "clients/telegram_bot",
        "requirements": "clients/telegram_bot/requirements.txt",
        "env_example": "clients/telegram_bot/.env.example",
        "env_file": "clients/telegram_bot/.env",
        "venv": "clients/telegram_bot/.venv",
    },
    "cli": {
        "dir": "clients/cli",
        "requirements": "clients/cli/requirements.txt",
        "env_example": "clients/cli/.env.example",
        "env_file": "clients/cli/.env",
        "venv": "clients/cli/.venv",
    },
}

# Строгий белый список поддерживаемых переменных окружения (сверено с config.py каждого компонента)
SUPPORTED_ENV_KEYS: dict[str, tuple[str, ...]] = {
    "server": (
        "SERVER_HOST",
        "SERVER_PORT",
        "DATABASE_URL",
        "EDGE_API_KEY",
        "CLIENT_API_KEY",
        "BIOLOGIST_PASSWORD",
        "DEVELOPER_PASSWORD",
        "VIDEO_MAX_FPS",
        "VIDEO_MIN_DURATION_SEC",
        "REPO_URL",
        "EDGE_SERVER_URL",
        "MOCK_MODE",
        "BOT_USERNAME",
        "TELEGRAM_BOT_TOKEN",
        "ESP_ADAPTER_ENABLED",
        "ESP_ADAPTER_HOST",
        "ESP_ADAPTER_PORT",
    ),
    "edge_agent": (
        "SERVER_URL",
        "EDGE_API_KEY",
        "HARDWARE_MODE",
        "MOCK_CAMERAS",
        "MODEL_PATH",
        "HEARTBEAT_INTERVAL_SEC",
        "COMMAND_POLL_INTERVAL_SEC",
        "RELAY_SWITCH_DELAY_MS",
        "CAMERA_WARMUP_FRAMES",
        "RELAY_ACTIVE_HIGH",
        "OFFLINE_QUEUE_DIR",
        "CAPTURE_INTERVAL_HOURS",
        "DEBUG_MODE",
        "TEST_CAPTURE_INTERVAL_SEC",
        "MOCK_OVERLAY_ARUCO",
        "UPLOAD_TIMEOUT_SEC",
        "ESP_BRIDGE_ENABLED",
        "ESP_BRIDGE_HOST",
        "ESP_BRIDGE_PORT",
    ),
    "telegram_bot": (
        "TELEGRAM_BOT_TOKEN",
        "SERVER_URL",
        "CLIENT_API_KEY",
        "WEB_PUBLIC_URL",
        "TELEGRAM_PROXY",
        "BOT_DEBUG_LOG",
    ),
    "cli": (
        "SERVER_URL",
        "CLIENT_API_KEY",
        "CLI_DEBUG",
    ),
}

SECRET_KEYS: frozenset[str] = frozenset(
    {
        "EDGE_API_KEY",
        "CLIENT_API_KEY",
        "BIOLOGIST_PASSWORD",
        "DEVELOPER_PASSWORD",
        "TELEGRAM_BOT_TOKEN",
    }
)

PLACEHOLDER_VALUES: frozenset[str] = frozenset(
    {
        "",
        "...",
        "test-client-secret-key",
        "your-edge-secret-key-here",
        "generate_strong_secret_for_edge_here",
        "generate_strong_secret_for_clients_here",
    }
)


def resolve_profile(profile_arg: str) -> ProfileSpec:
    """Разрешает идентификатор или имя профиля в ProfileSpec."""
    key = profile_arg.strip().lower()
    profile_id = PROFILE_ALIASES.get(key)
    if not profile_id or profile_id not in PROFILES:
        valid = ", ".join(f"{s.id} ({s.name})" for s in PROFILES.values())
        raise ValueError(f"Неизвестный профиль '{profile_arg}'. Доступны: {valid}")
    return PROFILES[profile_id]


def resolve_topology(topology_arg: str) -> str:
    """Валидирует схему топологии (A, B или C)."""
    topo = topology_arg.strip().upper()
    if topo not in TOPOLOGIES:
        raise ValueError(f"Неизвестная топология '{topology_arg}'. Допустимы: A, B, C.")
    return topo


# ---------------------------------------------------------------------------
# DependencyManager
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RequirementSpec:
    """Спецификация зависимости из requirements.txt."""

    name: str
    raw_line: str
    constraints: tuple[tuple[str, str], ...] = ()


@dataclass
class DependencyReport:
    """Результат проверки зависимостей компонента."""

    component: str
    venv_path: Path
    venv_exists: bool
    python_bin: Path
    satisfied: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    outdated: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.venv_exists and not self.missing and not self.outdated


class DependencyManager:
    """Управление виртуальными окружениями и зависимостями из requirements.txt."""

    _REQ_LINE_RE = re.compile(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]+\])?(.*)$")
    _CONSTRAINT_RE = re.compile(r"(>=|<=|==|!=|~=|>|<)\s*([A-Za-z0-9_.\-+*]+)")

    @staticmethod
    def normalize_package_name(name: str) -> str:
        """Нормализует имя пакета по PEP 503."""
        return re.sub(r"[-_.]+", "-", name).lower()

    @staticmethod
    def _parse_version(version_str: str) -> tuple[int, ...]:
        """Преобразует строку версии в числовой кортеж для сравнения."""
        # Берём основную числовую часть до пререлизных суффиксов
        cleaned = version_str.strip().split("+")[0]
        parts: list[int] = []
        for token in cleaned.split("."):
            match = re.match(r"^(\d+)", token)
            if match:
                parts.append(int(match.group(1)))
            else:
                break
        return tuple(parts) if parts else (0,)

    @classmethod
    def compare_versions(cls, installed: str, op: str, required: str) -> bool:
        """Сравнивает установленную версию с требуемой по оператору."""
        v_inst = cls._parse_version(installed)
        v_req = cls._parse_version(required)
        max_len = max(len(v_inst), len(v_req))
        v_inst_pad = v_inst + (0,) * (max_len - len(v_inst))
        v_req_pad = v_req + (0,) * (max_len - len(v_req))

        if op == "==":
            return v_inst_pad == v_req_pad
        if op == "!=":
            return v_inst_pad != v_req_pad
        if op == ">=":
            return v_inst_pad >= v_req_pad
        if op == "<=":
            return v_inst_pad <= v_req_pad
        if op == ">":
            return v_inst_pad > v_req_pad
        if op == "<":
            return v_inst_pad < v_req_pad
        if op == "~=":
            # Совместимый релиз: >= required и совпадение префикса (кроме последнего компонента)
            if v_inst_pad < v_req_pad:
                return False
            prefix_len = max(1, len(v_req) - 1)
            return v_inst_pad[:prefix_len] == v_req_pad[:prefix_len]
        return True

    @classmethod
    def parse_requirements(cls, req_path: Path) -> list[RequirementSpec]:
        """Парсит requirements.txt, игнорируя комментарии и пустые строки."""
        if not req_path.is_file():
            return []

        specs: list[RequirementSpec] = []
        for raw_line in req_path.read_text(encoding="utf-8").splitlines():
            # Убираем комментарии
            line = raw_line.split("#", 1)[0].strip()
            if not line or line.startswith(("-r", "--", "-e")):
                continue
            # Убираем environment markers (; python_version < ...)
            line_no_marker = line.split(";", 1)[0].strip()
            match = cls._REQ_LINE_RE.match(line_no_marker)
            if not match:
                continue
            pkg_name = match.group(1)
            rest = match.group(2).strip()
            constraints = tuple(
                (op, ver) for op, ver in cls._CONSTRAINT_RE.findall(rest)
            )
            specs.append(
                RequirementSpec(
                    name=cls.normalize_package_name(pkg_name),
                    raw_line=line_no_marker,
                    constraints=constraints,
                )
            )
        return specs

    @classmethod
    def get_installed_versions(cls, python_bin: Path) -> dict[str, str]:
        """Возвращает словарь {normalized_name: version} для указанного Python."""
        if not python_bin.exists():
            return {}

        code = (
            "import importlib.metadata as m, json, re; "
            "print(json.dumps({re.sub(r'[-_.]+', '-', d.metadata['Name']).lower(): d.version "
            "for d in m.distributions() if d.metadata.get('Name')}))"
        )
        try:
            proc = subprocess.run(
                [str(python_bin), "-c", code],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                data = json.loads(proc.stdout.strip())
                if isinstance(data, dict):
                    return {str(k): str(v) for k, v in data.items()}
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            pass
        return {}

    @classmethod
    def check_component(cls, repo_root: Path, component: str) -> DependencyReport:
        """Проверяет состояние .venv и соответствие установленных пакетов requirements.txt."""
        paths = COMPONENT_PATHS[component]
        venv_path = repo_root / paths["venv"]
        venv_python = venv_path / "bin" / "python"
        if not venv_python.exists():
            # Поддержка Windows-раскладки на всякий случай
            win_python = venv_path / "Scripts" / "python.exe"
            if win_python.exists():
                venv_python = win_python

        venv_exists = venv_python.exists()
        check_python = venv_python if venv_exists else Path(sys.executable)
        installed = cls.get_installed_versions(check_python)
        reqs = cls.parse_requirements(repo_root / paths["requirements"])

        report = DependencyReport(
            component=component,
            venv_path=venv_path,
            venv_exists=venv_exists,
            python_bin=check_python,
        )

        for req in reqs:
            inst_ver = installed.get(req.name)
            if inst_ver is None:
                report.missing.append(req.raw_line)
                continue

            if all(
                cls.compare_versions(inst_ver, op, ver) for op, ver in req.constraints
            ):
                report.satisfied.append(f"{req.name}=={inst_ver}")
            else:
                report.outdated.append(f"{req.raw_line} (установлено: {inst_ver})")

        return report

    @staticmethod
    def ensure_venv(venv_dir: Path, system_site_packages: bool = False) -> Path:
        """Создаёт виртуальное окружение при его отсутствии и возвращает путь к python."""
        python_bin = venv_dir / "bin" / "python"
        if python_bin.exists():
            return python_bin

        cmd = [sys.executable, "-m", "venv"]
        if system_site_packages:
            cmd.append("--system-site-packages")
        cmd.append(str(venv_dir))
        subprocess.run(cmd, check=True)
        return python_bin

    @staticmethod
    def install_requirements(python_bin: Path, req_path: Path) -> bool:
        """Устанавливает зависимости из requirements.txt с помощью pip."""
        if not req_path.is_file():
            return False
        subprocess.run(
            [str(python_bin), "-m", "pip", "install", "--upgrade", "pip"],
            check=True,
        )
        subprocess.run(
            [str(python_bin), "-m", "pip", "install", "-r", str(req_path)],
            check=True,
        )
        return True


# ---------------------------------------------------------------------------
# HardwareDetector
# ---------------------------------------------------------------------------


@dataclass
class HardwareInfo:
    """Собранная информация об ОС, железе и системных утилитах."""

    os_name: str
    arch: str
    is_raspberry_pi: bool
    rpi_model: str | None
    video_devices: list[str]
    v4l_by_id_devices: list[str]
    gpio_devices: list[str]
    tools: dict[str, bool]

    def summary_lines(self) -> list[str]:
        """Возвращает человекочитаемое описание обнаруженного железа."""
        lines = [
            f"ОС / Архитектура: {self.os_name} / {self.arch}",
            f"Raspberry Pi: {'Да (' + (self.rpi_model or 'RPi') + ')' if self.is_raspberry_pi else 'Нет'}",
            f"Видеоустройства (/dev/video*): {len(self.video_devices)} ({', '.join(self.video_devices) or 'нет'})",
            f"USB-камеры (/dev/v4l/by-id): {len(self.v4l_by_id_devices)}",
            f"GPIO (/dev/gpiomem, gpiochip*): {', '.join(self.gpio_devices) or 'не обнаружены'}",
        ]
        tools_str = ", ".join(
            f"{name}={'OK' if avail else 'нет'}"
            for name, avail in sorted(self.tools.items())
        )
        lines.append(f"Системные утилиты: {tools_str}")
        return lines


class HardwareDetector:
    """Определитель аппаратной платформы, камер, GPIO и системных служб."""

    def __init__(
        self,
        dev_dir: Path = Path("/dev"),
        proc_model_path: Path = Path("/proc/device-tree/model"),
        fw_model_path: Path = Path("/sys/firmware/devicetree/base/model"),
        systemd_run_dir: Path = Path("/run/systemd/system"),
    ) -> None:
        self.dev_dir = dev_dir
        self.proc_model_path = proc_model_path
        self.fw_model_path = fw_model_path
        self.systemd_run_dir = systemd_run_dir

    def _detect_rpi(self) -> tuple[bool, str | None]:
        for path in (self.proc_model_path, self.fw_model_path):
            with contextlib.suppress(OSError):
                if path.is_file():
                    text = (
                        path.read_text(encoding="utf-8", errors="ignore")
                        .replace("\x00", "")
                        .strip()
                    )
                    if "raspberry pi" in text.lower():
                        return True, text
        return False, None

    def detect(self) -> HardwareInfo:
        """Выполняет полную диагностику окружения."""
        is_rpi, rpi_model = self._detect_rpi()

        video_devices: list[str] = []
        gpio_devices: list[str] = []
        v4l_by_id_devices: list[str] = []

        if self.dev_dir.is_dir():
            video_devices = sorted(
                str(p)
                for p in self.dev_dir.glob("video*")
                if p.name.startswith("video")
            )
            gpiomem = self.dev_dir / "gpiomem"
            if gpiomem.exists():
                gpio_devices.append(str(gpiomem))
            gpio_devices.extend(sorted(str(p) for p in self.dev_dir.glob("gpiochip*")))

            v4l_dir = self.dev_dir / "v4l" / "by-id"
            if v4l_dir.is_dir():
                v4l_by_id_devices = sorted(str(p) for p in v4l_dir.iterdir())

        tools = {
            "systemd": shutil.which("systemctl") is not None
            or self.systemd_run_dir.is_dir(),
            "v4l2-ctl": shutil.which("v4l2-ctl") is not None,
            "rpicam-still": shutil.which("rpicam-still") is not None,
            "libcamera-still": shutil.which("libcamera-still") is not None,
            "netbird": shutil.which("netbird") is not None,
            "tailscale": shutil.which("tailscale") is not None,
            "ffmpeg": shutil.which("ffmpeg") is not None,
        }

        return HardwareInfo(
            os_name=platform.system(),
            arch=platform.machine(),
            is_raspberry_pi=is_rpi,
            rpi_model=rpi_model,
            video_devices=video_devices,
            v4l_by_id_devices=v4l_by_id_devices,
            gpio_devices=gpio_devices,
            tools=tools,
        )


# ---------------------------------------------------------------------------
# NetworkChecker
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VpnStatus:
    """Статус VPN-клиента (NetBird / Tailscale)."""

    provider: str
    installed: bool
    connected: bool
    ip: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class HealthCheckResult:
    """Результат проверки доступности сервера GrowVision."""

    url: str
    reachable: bool
    method: str
    detail: str


class NetworkChecker:
    """Проверка локальных IP, NetBird/Tailscale и доступности SERVER_URL."""

    @staticmethod
    def get_local_ips() -> list[str]:
        """Определяет локальные IPv4-адреса хоста без сторонних библиотек."""
        ips: list[str] = []

        # Попытка через исходящий UDP-сокет (без реальной отправки пакетов)
        with contextlib.suppress(OSError):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.connect(("8.8.8.8", 80))
                ip = sock.getsockname()[0]
                if ip and not ip.startswith("127.") and ip not in ips:
                    ips.append(ip)
            finally:
                sock.close()

        # Попытка через getaddrinfo
        with contextlib.suppress(OSError):
            hostname = socket.gethostname()
            for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
                addr = info[4][0]
                if (
                    isinstance(addr, str)
                    and not addr.startswith("127.")
                    and addr not in ips
                ):
                    ips.append(addr)

        return ips

    @staticmethod
    def check_netbird() -> VpnStatus:
        """Проверяет статус NetBird (`netbird status`)."""
        if shutil.which("netbird") is None:
            return VpnStatus(
                provider="netbird",
                installed=False,
                connected=False,
                detail="Не установлен",
            )

        try:
            proc = subprocess.run(
                ["netbird", "status"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            output = (proc.stdout or "") + "\n" + (proc.stderr or "")
            ip_match = re.search(
                r"NetBird IP:\s*([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)", output
            )
            connected = "connected" in output.lower() and (
                "disconnected" not in output.lower() or ip_match is not None
            )
            return VpnStatus(
                provider="netbird",
                installed=True,
                connected=connected,
                ip=ip_match.group(1) if ip_match else None,
                detail=output.strip().splitlines()[0]
                if output.strip()
                else "нет вывода",
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return VpnStatus(
                provider="netbird", installed=True, connected=False, detail=str(exc)
            )

    @staticmethod
    def check_tailscale() -> VpnStatus:
        """Проверяет статус Tailscale (`tailscale ip -4`)."""
        if shutil.which("tailscale") is None:
            return VpnStatus(
                provider="tailscale",
                installed=False,
                connected=False,
                detail="Не установлен",
            )

        try:
            proc = subprocess.run(
                ["tailscale", "ip", "-4"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                ip = proc.stdout.strip().splitlines()[0].strip()
                return VpnStatus(
                    provider="tailscale",
                    installed=True,
                    connected=True,
                    ip=ip,
                    detail=f"IP: {ip}",
                )
            return VpnStatus(
                provider="tailscale",
                installed=True,
                connected=False,
                detail=(proc.stderr or "Не подключен").strip(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return VpnStatus(
                provider="tailscale", installed=True, connected=False, detail=str(exc)
            )

    @staticmethod
    def check_server_health(server_url: str, timeout: float = 3.0) -> HealthCheckResult:
        """Проверяет доступность сервера через GET /health с fallback на TCP connect."""
        clean_url = server_url.strip().rstrip("/")
        health_url = f"{clean_url}/health"

        try:
            req = urllib.request.Request(health_url, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return HealthCheckResult(
                    url=clean_url,
                    reachable=200 <= resp.status < 500,
                    method="http",
                    detail=f"HTTP {resp.status}",
                )
        except urllib.error.HTTPError as exc:
            # Сервер ответил (даже 404/401 означает, что сервер жив)
            return HealthCheckResult(
                url=clean_url,
                reachable=True,
                method="http",
                detail=f"HTTP {exc.code}",
            )
        except (urllib.error.URLError, OSError, ValueError) as exc:
            # Fallback: TCP connect на host:port
            parsed = urllib.parse.urlparse(clean_url)
            host = parsed.hostname
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            if host:
                try:
                    with socket.create_connection((host, port), timeout=timeout):
                        return HealthCheckResult(
                            url=clean_url,
                            reachable=True,
                            method="tcp",
                            detail=f"TCP {host}:{port} открыт",
                        )
                except OSError as tcp_exc:
                    return HealthCheckResult(
                        url=clean_url,
                        reachable=False,
                        method="tcp",
                        detail=f"Недоступен ({tcp_exc})",
                    )
            return HealthCheckResult(
                url=clean_url,
                reachable=False,
                method="http",
                detail=f"Ошибка проверки: {exc}",
            )


# ---------------------------------------------------------------------------
# EnvManager
# ---------------------------------------------------------------------------


class EnvManager:
    """Безопасное формирование и обновление .env файлов на базе .env.example."""

    @staticmethod
    def generate_secret(nbytes: int = 24) -> str:
        """Генерирует криптостойкий случайный ключ."""
        return secrets.token_urlsafe(nbytes)

    @staticmethod
    def parse_env_file(path: Path) -> dict[str, str]:
        """Читает пары KEY=VALUE из .env или .env.example (игнорирует комментарии)."""
        if not path.is_file():
            return {}

        result: dict[str, str] = {}
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            if key:
                result[key] = value
        return result

    @classmethod
    def _is_placeholder(cls, value: str | None) -> bool:
        if value is None:
            return True
        return value.strip() in PLACEHOLDER_VALUES

    @classmethod
    def build_env(
        cls,
        component: str,
        example_path: Path,
        target_path: Path,
        overrides: dict[str, str] | None = None,
        overwrite_secrets: bool = False,
    ) -> dict[str, str]:
        """Формирует итоговый словарь переменных .env строго по белому списку компонента."""
        supported = SUPPORTED_ENV_KEYS[component]
        example_vars = cls.parse_env_file(example_path)
        existing_vars = cls.parse_env_file(target_path)
        overrides = overrides or {}

        final_vars: dict[str, str] = {}
        for key in supported:
            if key in overrides:
                val = overrides[key]
            elif key in existing_vars:
                val = existing_vars[key]
            elif key in example_vars:
                val = example_vars[key]
            else:
                continue

            if key in SECRET_KEYS and key != "TELEGRAM_BOT_TOKEN":
                existing_val = existing_vars.get(key)
                if (
                    not overwrite_secrets
                    and existing_val
                    and not cls._is_placeholder(existing_val)
                    and key not in overrides
                ):
                    val = existing_val
                elif cls._is_placeholder(val):
                    val = cls.generate_secret()

            final_vars[key] = val

        # Если были явно переданы дополнительные поддерживаемые overrides
        for key, val in overrides.items():
            if key in supported:
                final_vars[key] = val

        return final_vars

    @staticmethod
    def write_env(
        target_path: Path, values: dict[str, str], secret_mode: bool = True
    ) -> None:
        """Записывает .env файл и устанавливает права 0o600 при наличии секретов."""
        target_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"{k}={v}" for k, v in values.items()]
        target_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        if secret_mode and os.name == "posix":
            with contextlib.suppress(OSError):
                os.chmod(target_path, 0o600)


# ---------------------------------------------------------------------------
# SystemdGenerator
# ---------------------------------------------------------------------------


class SystemdGenerator:
    """Генератор systemd unit-файлов для компонентов GrowVision."""

    @staticmethod
    def render_server_unit(repo_root: Path, user: str) -> str:
        return (
            "[Unit]\n"
            "Description=GrowVision Backend Server (FastAPI + SQLite)\n"
            "After=network.target\n\n"
            "[Service]\n"
            "Type=simple\n"
            f"User={user}\n"
            f"WorkingDirectory={repo_root}\n"
            f"ExecStart={repo_root}/server/.venv/bin/python -m server.main\n"
            "Restart=always\n"
            "RestartSec=5\n"
            "Environment=PYTHONUNBUFFERED=1\n\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        )

    @staticmethod
    def render_edge_unit(repo_root: Path, user: str) -> str:
        return (
            "[Unit]\n"
            "Description=GrowVision Edge Agent\n"
            "After=network-online.target\n"
            "Wants=network-online.target\n\n"
            "[Service]\n"
            "Type=simple\n"
            f"User={user}\n"
            f"WorkingDirectory={repo_root}\n"
            f"ExecStart={repo_root}/edge_agent/.venv/bin/python -m edge_agent.main\n"
            "Restart=always\n"
            "RestartSec=5\n"
            "Environment=PYTHONUNBUFFERED=1\n"
            "KillMode=mixed\n"
            "TimeoutStopSec=10\n\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        )

    @staticmethod
    def render_bot_unit(
        repo_root: Path, user: str, with_local_server: bool = True
    ) -> str:
        after_line = (
            "After=network.target growvision-server.service"
            if with_local_server
            else "After=network.target"
        )
        return (
            "[Unit]\n"
            "Description=GrowVision Telegram Bot (aiogram 3)\n"
            f"{after_line}\n\n"
            "[Service]\n"
            "Type=simple\n"
            f"User={user}\n"
            f"WorkingDirectory={repo_root}\n"
            f"ExecStart={repo_root}/clients/telegram_bot/.venv/bin/python -m clients.telegram_bot.main\n"
            "Restart=always\n"
            "RestartSec=5\n"
            "Environment=PYTHONUNBUFFERED=1\n\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        )

    @classmethod
    def generate_units(
        cls,
        repo_root: Path,
        output_dir: Path,
        components: tuple[str, ...],
        user: str | None = None,
    ) -> list[Path]:
        """Создаёт .service файлы в output_dir для выбранных компонентов."""
        target_user = user or getpass.getuser()
        output_dir.mkdir(parents=True, exist_ok=True)
        created: list[Path] = []
        has_server = "server" in components

        if has_server:
            p = output_dir / "growvision-server.service"
            p.write_text(
                cls.render_server_unit(repo_root, target_user), encoding="utf-8"
            )
            created.append(p)

        if "edge_agent" in components:
            p = output_dir / "growvision-edge.service"
            p.write_text(cls.render_edge_unit(repo_root, target_user), encoding="utf-8")
            created.append(p)

        if "telegram_bot" in components:
            p = output_dir / "growvision-bot.service"
            p.write_text(
                cls.render_bot_unit(
                    repo_root, target_user, with_local_server=has_server
                ),
                encoding="utf-8",
            )
            created.append(p)

        return created

    @staticmethod
    def install_instructions(unit_paths: list[Path]) -> list[str]:
        """Формирует команды для ручной установки юнитов в /etc/systemd/system/."""
        if not unit_paths:
            return []
        names = " ".join(p.name for p in unit_paths)
        src_dir = unit_paths[0].parent
        return [
            f"sudo cp {src_dir}/*.service /etc/systemd/system/",
            "sudo systemctl daemon-reload",
            f"sudo systemctl enable --now {names}",
        ]


# ---------------------------------------------------------------------------
# Оркестрация CLI и интерактивного мастера
# ---------------------------------------------------------------------------


def _prompt_choice(prompt: str, valid: list[str], default: str) -> str:
    while True:
        raw = input(f"{prompt} [{default}]: ").strip()
        val = raw or default
        if val in valid:
            return val
        print(f"Некорректный ввод '{val}'. Допустимые значения: {', '.join(valid)}")


def _prompt_text(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    raw = input(f"{prompt}{suffix}: ").strip()
    return raw or default


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="install.sh",
        description="Мастер установки, диагностики и настройки GrowVision.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Только диагностика системы, зависимостей и сети без записи .env и установки пакетов.",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Неинтерактивный режим (значения по умолчанию или из CLI-флагов).",
    )
    parser.add_argument(
        "--profile",
        type=str,
        default=None,
        help="Профиль установки: 1..6 или full|server|edge|bot|cli|dev.",
    )
    parser.add_argument(
        "--topology",
        type=str,
        default=None,
        help="Топология развёртывания: A (монолит), B (Server+Bot / Edge), C (раздельно).",
    )
    parser.add_argument(
        "--server-url",
        type=str,
        default=None,
        help="URL сервера GrowVision для Edge/Bot/CLI (например http://100.x.x.x:8000).",
    )
    parser.add_argument(
        "--output-systemd",
        type=Path,
        default=None,
        help="Каталог для генерации systemd .service файлов.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Корень репозитория GrowVision (по умолчанию определяется автоматически).",
    )
    parser.add_argument(
        "--overwrite-secrets",
        action="store_true",
        help="Перегенерировать существующие секретные ключи в .env.",
    )
    return parser


def _compute_component_overrides(
    profile: ProfileSpec,
    topology: str,
    server_url: str,
    shared_edge_key: str,
    shared_client_key: str,
    bot_token: str = "",
    telegram_proxy: str = "",
) -> dict[str, dict[str, str]]:
    """Рассчитывает переопределения переменных окружения для каждого компонента."""
    overrides: dict[str, dict[str, str]] = {}

    if "server" in profile.components:
        srv: dict[str, str] = {
            "SERVER_HOST": "127.0.0.1" if profile.is_dev else "0.0.0.0",
            "MOCK_MODE": "true" if profile.is_dev else "false",
        }
        if shared_edge_key:
            srv["EDGE_API_KEY"] = shared_edge_key
        if shared_client_key:
            srv["CLIENT_API_KEY"] = shared_client_key
        overrides["server"] = srv

    if "edge_agent" in profile.components:
        edge_url = "http://127.0.0.1:8000" if topology == "A" else server_url
        edge: dict[str, str] = {
            "SERVER_URL": edge_url,
            "HARDWARE_MODE": "mock" if profile.is_dev else "rpi",
            "MOCK_CAMERAS": "true" if profile.is_dev else "false",
        }
        if shared_edge_key:
            edge["EDGE_API_KEY"] = shared_edge_key
        overrides["edge_agent"] = edge

    if "telegram_bot" in profile.components:
        bot_url = "http://127.0.0.1:8000" if topology in ("A", "B") else server_url
        bot: dict[str, str] = {"SERVER_URL": bot_url}
        if shared_client_key:
            bot["CLIENT_API_KEY"] = shared_client_key
        if bot_token:
            bot["TELEGRAM_BOT_TOKEN"] = bot_token
        if telegram_proxy:
            bot["TELEGRAM_PROXY"] = telegram_proxy
        overrides["telegram_bot"] = bot

    if "cli" in profile.components:
        cli_url = "http://127.0.0.1:8000" if topology == "A" else server_url
        cli: dict[str, str] = {"SERVER_URL": cli_url}
        if shared_client_key:
            cli["CLIENT_API_KEY"] = shared_client_key
        overrides["cli"] = cli

    return overrides


def run_installer(argv: list[str] | None = None) -> int:
    """Точка входа мастера установки GrowVision."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    repo_root = (
        args.repo_root.resolve()
        if args.repo_root
        else Path(__file__).resolve().parent.parent
    )

    print("=== GrowVision Installer & Diagnostics ===")
    print(f"Корень проекта: {repo_root}")

    # 1. Диагностика железа и сети
    hw_info = HardwareDetector().detect()
    for line in hw_info.summary_lines():
        print(f"  {line}")

    local_ips = NetworkChecker.get_local_ips()
    netbird = NetworkChecker.check_netbird()
    tailscale = NetworkChecker.check_tailscale()
    print(f"  Локальные IP: {', '.join(local_ips) or 'не определены'}")
    print(
        f"  NetBird: {'подключен (' + (netbird.ip or 'IP?') + ')' if netbird.connected else netbird.detail}"
    )
    print(
        f"  Tailscale: {'подключен (' + (tailscale.ip or 'IP?') + ')' if tailscale.connected else tailscale.detail}"
    )

    # 2. Выбор профиля
    default_profile = "3" if hw_info.is_raspberry_pi else "1"
    if args.profile:
        try:
            profile = resolve_profile(args.profile)
        except ValueError as exc:
            print(f"Ошибка: {exc}", file=sys.stderr)
            return 2
    elif args.non_interactive:
        profile = PROFILES[default_profile]
    else:
        print("\nВыберите профиль установки:")
        for spec in PROFILES.values():
            print(f"  {spec.id}) {spec.title} — {spec.description}")
        chosen = _prompt_choice("Профиль", list(PROFILES.keys()), default_profile)
        profile = PROFILES[chosen]

    print(f"\nВыбран профиль: [{profile.id}] {profile.title}")

    # 3. Выбор топологии
    default_topo = "A" if profile.id in ("1", "6") else "C"
    if args.topology:
        try:
            topology = resolve_topology(args.topology)
        except ValueError as exc:
            print(f"Ошибка: {exc}", file=sys.stderr)
            return 2
    elif args.non_interactive:
        topology = default_topo
    else:
        print("\nВыберите топологию развёртывания:")
        for k, desc in TOPOLOGIES.items():
            print(f"  {k}) {desc}")
        topology = _prompt_choice(
            "Топология", list(TOPOLOGIES.keys()), default_topo
        ).upper()

    print(f"Топология: {topology} — {TOPOLOGIES[topology]}")

    # Определяем SERVER_URL
    suggested_ip = netbird.ip or (local_ips[0] if local_ips else "127.0.0.1")
    default_server_url = (
        "http://127.0.0.1:8000" if topology == "A" else f"http://{suggested_ip}:8000"
    )
    if args.server_url:
        server_url = args.server_url.rstrip("/")
    elif args.non_interactive or topology == "A":
        server_url = default_server_url
    else:
        server_url = _prompt_text("Введите SERVER_URL", default_server_url).rstrip("/")

    # 4. Проверка зависимостей
    print("\nПроверка зависимостей:")
    reports: list[DependencyReport] = []
    for comp in profile.components:
        rep = DependencyManager.check_component(repo_root, comp)
        reports.append(rep)
        status = "OK" if rep.ok else "ТРЕБУЕТСЯ УСТАНОВКА"
        print(
            f"  - {comp}: {status} "
            f"(venv={'да' if rep.venv_exists else 'нет'}, "
            f"отсутствует={len(rep.missing)}, устарело={len(rep.outdated)})"
        )

    # 5. Режим --check: диагностика завершена, ничего не записываем
    if args.check:
        if topology in ("B", "C") or "server" not in profile.components:
            hc = NetworkChecker.check_server_health(server_url)
            print(
                f"\nПроверка доступности {hc.url}: "
                f"{'ДОСТУПЕН' if hc.reachable else 'НЕДОСТУПЕН'} ({hc.method}: {hc.detail})"
            )
        print("\nРежим --check: изменения на диск не вносились.")
        return 0

    # 6. Создание .venv и установка зависимостей
    for comp in profile.components:
        paths = COMPONENT_PATHS[comp]
        venv_dir = repo_root / paths["venv"]
        req_file = repo_root / paths["requirements"]
        use_sys_pkgs = comp == "edge_agent" and hw_info.is_raspberry_pi
        print(f"\nНастройка окружения {comp} ({venv_dir})...")
        py_bin = DependencyManager.ensure_venv(
            venv_dir, system_site_packages=use_sys_pkgs
        )
        DependencyManager.install_requirements(py_bin, req_file)

    # 7. Формирование .env файлов
    existing_server_env = EnvManager.parse_env_file(repo_root / "server/.env")
    shared_edge_key = existing_server_env.get("EDGE_API_KEY", "")
    if not shared_edge_key or EnvManager._is_placeholder(shared_edge_key):
        shared_edge_key = (
            EnvManager.generate_secret() if "server" in profile.components else ""
        )

    shared_client_key = existing_server_env.get("CLIENT_API_KEY", "")
    if not shared_client_key or EnvManager._is_placeholder(shared_client_key):
        shared_client_key = (
            EnvManager.generate_secret() if "server" in profile.components else ""
        )

    bot_token = ""
    telegram_proxy = ""
    if "telegram_bot" in profile.components and not args.non_interactive:
        bot_token = _prompt_text(
            "TELEGRAM_BOT_TOKEN (оставьте пустым, чтобы заполнить позже)", ""
        )
        telegram_proxy = _prompt_text(
            "TELEGRAM_PROXY (опционально, например socks5://127.0.0.1:1080)", ""
        )

    overrides_map = _compute_component_overrides(
        profile=profile,
        topology=topology,
        server_url=server_url,
        shared_edge_key=shared_edge_key,
        shared_client_key=shared_client_key,
        bot_token=bot_token,
        telegram_proxy=telegram_proxy,
    )

    print("\nФормирование конфигураций .env:")
    for comp in profile.components:
        paths = COMPONENT_PATHS[comp]
        ex_path = repo_root / paths["env_example"]
        tg_path = repo_root / paths["env_file"]
        values = EnvManager.build_env(
            component=comp,
            example_path=ex_path,
            target_path=tg_path,
            overrides=overrides_map.get(comp, {}),
            overwrite_secrets=args.overwrite_secrets,
        )
        EnvManager.write_env(tg_path, values, secret_mode=True)
        print(f"  - Записан {paths['env_file']} ({len(values)} параметров)")

    # 8. Генерация systemd юнитов (если запрошено)
    if args.output_systemd:
        created_units = SystemdGenerator.generate_units(
            repo_root=repo_root,
            output_dir=args.output_systemd,
            components=profile.components,
        )
        print(f"\nСгенерированы systemd unit-файлы в {args.output_systemd}:")
        for unit in created_units:
            print(f"  - {unit}")
        for cmd in SystemdGenerator.install_instructions(created_units):
            print(f"  $ {cmd}")

    print("\nУстановка и настройка GrowVision завершены.")
    return 0


def main() -> None:
    raise SystemExit(run_installer())


if __name__ == "__main__":
    main()
