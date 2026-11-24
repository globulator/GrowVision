import re
import subprocess
from typing import Any

from shared.logger import get_logger

logger = get_logger("edge_agent.camera_controls")


def categorize_control(name: str) -> str:
    """Определяет категорию папки для контрола камеры."""
    name_lower = name.lower()
    if any(
        k in name_lower for k in ("focus", "zoom", "sharpness", "power_line_frequency")
    ):
        return "focus"
    if any(
        k in name_lower
        for k in (
            "exposure",
            "gain",
            "brightness",
            "contrast",
            "backlight",
            "gamma",
        )
    ):
        return "exposure"
    if any(
        k in name_lower
        for k in ("white_balance", "temperature", "saturation", "hue", "color")
    ):
        return "color"
    return "exposure"


def parse_v4l2_controls(stdout: str) -> list[dict[str, Any]]:
    """Парсит вывод v4l2-ctl --list-ctrls-menus в структурированный список контролов.

    Извлекает min, max, step, default, value, flags (включая inactive), элементы menu.
    """
    controls: list[dict[str, Any]] = []
    current_ctrl: dict[str, Any] | None = None

    for line in stdout.splitlines():
        line_stripped = line.strip()
        if not line_stripped:
            continue

        # Заголовки разделов (User Controls, Camera Controls)
        if line_stripped.endswith("Controls"):
            continue

        # Проверка на пункт меню (например "0: Auto Mode")
        menu_match = re.match(r"^(\d+):\s+(.*)$", line_stripped)
        if menu_match and current_ctrl and current_ctrl.get("type") == "menu":
            menu_id = int(menu_match.group(1))
            menu_label = menu_match.group(2).strip()
            current_ctrl["menu_items"].append({"id": menu_id, "label": menu_label})
            continue

        # Новая строка контрола:
        # brightness 0x00980900 (int) : min=-64 max=64 step=1 default=0 value=0 flags=inactive
        ctrl_match = re.match(
            r"^([a-zA-Z0-9_]+)\s+(0x[0-9a-fA-F]+)\s+\(([a-zA-Z0-9_]+)\)\s*:\s*(.*)$",
            line_stripped,
        )
        if ctrl_match:
            name = ctrl_match.group(1)
            cid = ctrl_match.group(2)
            ctype = ctrl_match.group(3)
            rest = ctrl_match.group(4)

            props: dict[str, Any] = {}
            for token in rest.split():
                if "=" in token:
                    k, v = token.split("=", 1)
                    try:
                        props[k] = int(v)
                    except ValueError:
                        props[k] = v

            is_inactive = "inactive" in rest.lower()

            current_ctrl = {
                "name": name,
                "id": cid,
                "type": ctype,
                "min": props.get("min"),
                "max": props.get("max"),
                "step": props.get("step", 1),
                "default": props.get("default"),
                "value": props.get("value"),
                "is_inactive": is_inactive,
                "category": categorize_control(name),
                "menu_items": [],
            }
            controls.append(current_ctrl)

    return controls


def query_camera_formats_and_sizes(dev_path: str) -> list[dict[str, Any]]:
    """Парсит поддерживаемые форматы и разрешения через v4l2-ctl --list-formats-ext."""
    formats: list[dict[str, Any]] = []
    try:
        res = subprocess.run(
            ["v4l2-ctl", "-d", dev_path, "--list-formats-ext"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        if res.returncode == 0 and res.stdout:
            current_fmt = None
            fmt_sizes: dict[str, list[str]] = {}
            for line in res.stdout.splitlines():
                fmt_match = re.search(r"\[\d+\]:\s*'([A-Z0-9]{4})'", line)
                if fmt_match:
                    current_fmt = fmt_match.group(1)
                    if current_fmt not in fmt_sizes:
                        fmt_sizes[current_fmt] = []
                    continue
                size_match = re.search(r"Size:\s+Discrete\s+(\d+)x(\d+)", line)
                if size_match and current_fmt:
                    size_str = f"{size_match.group(1)}x{size_match.group(2)}"
                    if size_str not in fmt_sizes[current_fmt]:
                        fmt_sizes[current_fmt].append(size_str)

            for fmt, sizes in fmt_sizes.items():
                formats.append({"format": fmt, "resolutions": sizes})
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"Ошибка чтения форматов для {dev_path}: {exc}")

    if not formats:
        formats = [
            {"format": "MJPG", "resolutions": ["1920x1080", "1280x720", "640x480"]},
            {"format": "YUYV", "resolutions": ["1280x720", "640x480"]},
        ]
    return formats


def get_default_mock_controls() -> list[dict[str, Any]]:
    """Возвращает реалистичный набор элементов управления для mock-режима."""
    return [
        {
            "name": "brightness",
            "id": "0x00980900",
            "type": "int",
            "min": -64,
            "max": 64,
            "step": 1,
            "default": 0,
            "value": 0,
            "is_inactive": False,
            "category": "exposure",
            "menu_items": [],
        },
        {
            "name": "contrast",
            "id": "0x00980901",
            "type": "int",
            "min": 0,
            "max": 95,
            "step": 1,
            "default": 0,
            "value": 0,
            "is_inactive": False,
            "category": "exposure",
            "menu_items": [],
        },
        {
            "name": "saturation",
            "id": "0x00980902",
            "type": "int",
            "min": 0,
            "max": 100,
            "step": 1,
            "default": 64,
            "value": 64,
            "is_inactive": False,
            "category": "color",
            "menu_items": [],
        },
        {
            "name": "white_balance_automatic",
            "id": "0x0098090c",
            "type": "bool",
            "min": 0,
            "max": 1,
            "step": 1,
            "default": 1,
            "value": 1,
            "is_inactive": False,
            "category": "color",
            "menu_items": [],
        },
        {
            "name": "white_balance_temperature",
            "id": "0x0098091a",
            "type": "int",
            "min": 2800,
            "max": 6500,
            "step": 10,
            "default": 4600,
            "value": 4600,
            "is_inactive": True,  # Неактивен, пока включен white_balance_automatic
            "category": "color",
            "menu_items": [],
        },
        {
            "name": "auto_exposure",
            "id": "0x009a0901",
            "type": "menu",
            "min": 0,
            "max": 3,
            "step": 1,
            "default": 0,
            "value": 0,
            "is_inactive": False,
            "category": "exposure",
            "menu_items": [
                {"id": 0, "label": "Авто"},
                {"id": 1, "label": "Ручной"},
                {"id": 2, "label": "Приоритет выдержки"},
                {"id": 3, "label": "Приоритет диафрагмы"},
            ],
        },
        {
            "name": "exposure_time_absolute",
            "id": "0x009a0902",
            "type": "int",
            "min": 1,
            "max": 5000,
            "step": 1,
            "default": 156,
            "value": 156,
            "is_inactive": True,  # Неактивен в авто-режиме экспозиции
            "category": "exposure",
            "menu_items": [],
        },
        {
            "name": "gain",
            "id": "0x00980913",
            "type": "int",
            "min": 0,
            "max": 100,
            "step": 1,
            "default": 0,
            "value": 0,
            "is_inactive": False,
            "category": "exposure",
            "menu_items": [],
        },
        {
            "name": "focus_automatic_continuous",
            "id": "0x009a090c",
            "type": "bool",
            "min": 0,
            "max": 1,
            "step": 1,
            "default": 1,
            "value": 1,
            "is_inactive": False,
            "category": "focus",
            "menu_items": [],
        },
        {
            "name": "focus_absolute",
            "id": "0x009a090a",
            "type": "int",
            "min": 0,
            "max": 1024,
            "step": 1,
            "default": 0,
            "value": 0,
            "is_inactive": True,  # Неактивен, пока включен автофокус
            "category": "focus",
            "menu_items": [],
        },
        {
            "name": "sharpness",
            "id": "0x0098091b",
            "type": "int",
            "min": 0,
            "max": 6,
            "step": 1,
            "default": 3,
            "value": 3,
            "is_inactive": False,
            "category": "focus",
            "menu_items": [],
        },
        {
            "name": "power_line_frequency",
            "id": "0x00980918",
            "type": "menu",
            "min": 0,
            "max": 2,
            "step": 1,
            "default": 1,
            "value": 1,
            "is_inactive": False,
            "category": "focus",
            "menu_items": [
                {"id": 0, "label": "Отключено"},
                {"id": 1, "label": "50 Гц"},
                {"id": 2, "label": "60 Гц"},
            ],
        },
    ]


def query_camera_controls(
    dev_path: str, mock_mode: bool = False
) -> list[dict[str, Any]]:
    """Опрашивает поддерживаемые v4l2-контролы устройства."""
    if mock_mode:
        return get_default_mock_controls()

    try:
        res = subprocess.run(
            ["v4l2-ctl", "-d", dev_path, "--list-ctrls-menus"],
            capture_output=True,
            text=True,
            timeout=2.5,
            check=False,
        )
        if res.returncode == 0 and res.stdout:
            parsed = parse_v4l2_controls(res.stdout)
            if parsed:
                return parsed
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"Ошибка чтения контролов v4l2 для {dev_path}: {exc}")

    return get_default_mock_controls()


def set_v4l2_control(
    dev_path: str, ctrl_name: str, value: Any, mock_mode: bool = False
) -> bool:
    """Устанавливает значение контрола через v4l2-ctl --set-ctrl."""
    if mock_mode:
        logger.info(f"[MOCK] Установлен контрол {ctrl_name}={value} для {dev_path}")
        return True

    try:
        res = subprocess.run(
            ["v4l2-ctl", "-d", dev_path, f"--set-ctrl={ctrl_name}={value}"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        return res.returncode == 0
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"Не удалось установить контрол {ctrl_name}={value} для {dev_path}: {exc}"
        )
        return False
