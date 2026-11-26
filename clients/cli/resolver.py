import sys
from typing import Literal

import httpx

from clients.cli.client import APIClient
from clients.cli.formatting import print_error


def resolve_installation_target(
    client: APIClient,
    target_str: str,
    context: str,
    allow_all: bool = False,
    include_archived: bool = False,
) -> int | Literal["all"]:
    """Разрешает цель установки: числовой ID, точное имя или 'all'."""
    raw = target_str.strip()

    # 1. Проверка на 'all'
    if raw.lower() == "all":
        if not allow_all:
            print_error(
                context=context,
                detail="Массовое действие для данной команды запрещено.",
                hint="Укажите конкретный ID или точное название установки вместо 'all'.",
            )
            sys.exit(1)
        return "all"

    # 2. Проверка на числовой ID
    if raw.isdigit():
        return int(raw)

    # 3. Поиск по точному имени через GET /api/installations
    installations = client.request(
        method="GET",
        url="/api/installations",
        context=context,
        params={"include_archived": include_archived},
    )

    if isinstance(installations, list):
        for inst in installations:
            if inst.get("name") == raw:
                return int(inst["id"])

    # Если не найдено
    arch_note = " (с учетом архива)" if include_archived else " (среди активных)"
    print_error(
        context=context,
        detail=f"Установка с названием «{raw}» не найдена{arch_note}.",
        hint="Проверьте список доступных установок через команду unit list.",
    )
    sys.exit(1)


def resolve_camera_target(
    client: APIClient,
    camera_str: str,
    context: str,
) -> int:
    """Разрешает камеру: числовой ID или hardware_id."""
    raw = camera_str.strip()

    # 1. Числовой ID
    if raw.isdigit():
        return int(raw)

    # 2. Поиск по hardware_id через GET /api/cameras
    cameras = client.request(
        method="GET",
        url="/api/cameras",
        context=context,
    )

    if isinstance(cameras, list):
        for cam in cameras:
            if cam.get("hardware_id") == raw:
                return int(cam["id"])

    print_error(
        context=context,
        detail=f"Камера с идентификатором «{raw}» не найдена.",
        hint="Проверьте список зарегистрированных камер через команду camera list.",
    )
    sys.exit(1)


def get_bulk_command_timeout(target: int | str, client: APIClient) -> float:
    """Вычисляет таймаут по CLI_SPEC.md разд. 2.4."""
    if target == "all":
        # Узнаем количество активных установок
        try:
            items = client.request(
                method="GET",
                url="/api/installations",
                context="get active count",
                params={"include_archived": False},
            )
            n = len(items) if isinstance(items, list) else 1
        except (httpx.HTTPError, SystemExit):
            n = 1
        return float(30 + 10 * n)
    return 30.0
