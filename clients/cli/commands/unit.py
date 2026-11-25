from typing import Any

import typer
from rich.panel import Panel
from rich.table import Table

from clients.cli.client import APIClient
from clients.cli.formatting import console, err_console
from clients.cli.pins import (
    format_gpio_pins,
    parse_gpio_pins,
    validate_light_flash_disjoint,
)
from clients.cli.resolver import resolve_installation_target

unit_app = typer.Typer(
    name="unit", help="Управление гидропонными установками", no_args_is_help=True
)


def _render_unit_card(inst: dict[str, Any]) -> Panel:
    """Отрисовывает подробную карточку установки с адаптацией под роль."""
    lines = [
        f"[bold]ID:[/bold] {inst.get('id')}",
        f"[bold]Название:[/bold] {inst.get('name')}",
        f"[bold]Культура:[/bold] {inst.get('culture_name')}",
        f"[bold]Статус:[/bold] {'В архиве' if inst.get('is_archived') else 'Активна'}",
        f"[bold]Последняя площадь:[/bold] {inst.get('last_area_display') or '—'}",
    ]

    if "aruco_size_mm" in inst:
        aruco_val = inst.get("aruco_size_mm")
        lines.append(
            f"[bold]Размер маркера ArUco:[/bold] {f'{aruco_val} мм' if aruco_val else 'не задан'}"
        )

    if "capture_delay_sec" in inst:
        delay_val = inst.get("capture_delay_sec")
        delay_str = (
            f"{delay_val} с" if delay_val is not None else "по умолчанию (общая)"
        )
        lines.append(f"[bold]Задержка съёмки:[/bold] {delay_str}")

    if "light_gpio_pin" in inst and "flash_gpio_pin" in inst:
        light_st_str = ""
        if "light_state" in inst:
            light_st_str = (
                " [green]● ON[/green]"
                if inst.get("light_state")
                else " [dim]○ OFF[/dim]"
            )
        flash_st_str = ""
        if "flash_state" in inst:
            flash_st_str = (
                " [green]● ON[/green]"
                if inst.get("flash_state")
                else " [dim]○ OFF[/dim]"
            )
        light_pin_val = inst.get("light_gpio_pin")
        flash_pin_val = inst.get("flash_gpio_pin")
        light_rgb_str = ""
        if inst.get("light_is_rgb") or "," in str(light_pin_val or ""):
            light_rgb_str = (
                f" [cyan](RGB, {inst.get('light_color') or '#FFFFFF'})[/cyan]"
            )
        flash_rgb_str = ""
        if inst.get("flash_is_rgb") or "," in str(flash_pin_val or ""):
            flash_rgb_str = (
                f" [cyan](RGB, {inst.get('flash_color') or '#FFFFFF'})[/cyan]"
            )
        lines.append(
            f"[bold]GPIO основного света:[/bold] GPIO {light_pin_val}{light_rgb_str}{light_st_str}"
        )
        lines.append(
            f"[bold]GPIO вспышки:[/bold] GPIO {flash_pin_val}{flash_rgb_str}{flash_st_str}"
        )

    if "cameras" in inst:
        cams = inst.get("cameras") or []
        lines.append(f"[bold]Привязанные камеры ({len(cams)}):[/bold]")
        if not cams:
            lines.append("  (камеры не привязаны)")
        else:
            for c in cams:
                status = (
                    "[green]online[/green]"
                    if c.get("is_online")
                    else "[dim]offline[/dim]"
                )
                hw_id_str = f" [{c.get('hardware_id')}]" if "hardware_id" in c else ""
                lines.append(
                    f"  • ID {c.get('id')}: {c.get('auto_name')}{hw_id_str} ({status})"
                )

    return Panel(
        "\n".join(lines),
        title=f"Установка #{inst.get('id')} — {inst.get('name')}",
        expand=False,
    )


@unit_app.command(name="info")
def unit_info() -> None:
    """Справка по разделу unit."""
    info_text = """[bold cyan]Раздел unit — Гидропонные установки[/bold cyan]

[bold]Назначение:[/bold] Просмотр, добавление, редактирование, архивация и удаление установок.
[bold]Минимальная роль:[/bold] Гость (list, show, info), Biologist (edit имени/культуры), Developer (все).

[bold]Список подкоманд:[/bold]
  [green]unit info[/green]                                              - Показать эту справку
  [green]unit list [--archived][/green]                                 - Список всех установок
  [green]unit show <target>[/green]                                      - Карточка установки (или all для всех)
  [green]unit add [--name .. --culture .. --light-pin ..][/green]       - Добавить установку (Developer)
  [green]unit edit <target> [параметры][/green]                          - Изменить параметры установки
  [green]unit delay <target> [seconds] [--default][/green]               - Настроить задержку съёмки для установки
  [green]unit archive <target> [-y][/green]                              - Перевести установку в архив (Developer)
  [green]unit delete <target> [-y][/green]                               - Удалить установку (Developer)

[bold]Примеры:[/bold]
  unit list
  unit show 1
  unit show "Установка 1"
  unit show all
  unit delay 1 2.0
  unit delay 1 --default
"""
    console.print(info_text)


@unit_app.command(name="list")
def unit_list(
    archived: bool = typer.Option(
        False, "--archived", "-a", help="Включая архивные установки"
    ),
) -> None:
    """Список всех гидропонных установок."""
    client = APIClient()
    items = client.request(
        method="GET",
        url="/api/installations",
        context="unit list",
        params={"include_archived": archived},
    )

    if not items or not isinstance(items, list):
        console.print("[yellow]Установок пока нет.[/yellow]")
        return

    first = items[0]
    has_cameras = "cameras" in first
    has_gpio = "light_gpio_pin" in first

    table = Table(title="Гидропонные установки", header_style="bold cyan")
    table.add_column("ID", justify="right")
    table.add_column("Название", justify="left")
    table.add_column("Культура", justify="left")
    table.add_column("Площадь", justify="right")
    table.add_column("Статус", justify="center")

    if has_cameras:
        table.add_column("Камеры", justify="left")
    if has_gpio:
        table.add_column("Light GPIO", justify="right")
        table.add_column("Flash GPIO", justify="right")

    for inst in items:
        status_str = (
            "[dim]В архиве[/dim]"
            if inst.get("is_archived")
            else "[green]Активна[/green]"
        )
        area_str = inst.get("last_area_display") or "—"
        row = [
            str(inst.get("id")),
            str(inst.get("name")),
            str(inst.get("culture_name")),
            area_str,
            status_str,
        ]

        if has_cameras:
            cams = inst.get("cameras") or []
            if not cams:
                cam_str = "—"
            else:
                cam_parts = [
                    f"{c.get('auto_name')} ({'on' if c.get('is_online') else 'off'})"
                    for c in cams
                ]
                cam_str = ", ".join(cam_parts)
            row.append(cam_str)

        if has_gpio:
            row.append(str(inst.get("light_gpio_pin")))
            row.append(str(inst.get("flash_gpio_pin")))

        table.add_row(*row)

    console.print(table)


@unit_app.command(name="show")
def unit_show(
    target: str = typer.Argument(..., help="ID, название установки или 'all'"),
) -> None:
    """Подробная карточка установки (или всех активных для 'all')."""
    client = APIClient()
    if target.strip().lower() == "all":
        items = client.request(
            method="GET",
            url="/api/installations",
            context="unit show all",
            params={"include_archived": False},
        )
        if not items or not isinstance(items, list):
            console.print("[yellow]Нет активных установок.[/yellow]")
            return
        for inst in items:
            console.print(_render_unit_card(inst))
        return

    unit_id = resolve_installation_target(
        client=client,
        target_str=target,
        context="unit show",
        allow_all=False,
        include_archived=True,
    )

    inst = client.request(
        method="GET",
        url=f"/api/installations/{unit_id}",
        context="unit show",
    )
    if isinstance(inst, dict):
        console.print(_render_unit_card(inst))


@unit_app.command(name="add")
def unit_add(
    name: str = typer.Option(None, "--name", help="Уникальное название установки"),
    culture: str = typer.Option(None, "--culture", help="Название культуры"),
    light_pin: str = typer.Option(
        None,
        "--light-pin",
        help="GPIO-пин основного освещения (1 пин 0..53 или 3 пина RGB через запятую)",
    ),
    flash_pin: str = typer.Option(
        None,
        "--flash-pin",
        help="GPIO-пин подсветки для фото (1 пин 0..53 или 3 пина RGB через запятую)",
    ),
    aruco_mm: float = typer.Option(
        None, "--aruco-mm", help="Размер стороны маркера ArUco в мм (опционально)"
    ),
    delay: float = typer.Option(
        None,
        "--delay",
        "--capture-delay",
        help="Задержка съёмки в секундах (0.0 .. 30.0)",
    ),
) -> None:
    """Добавление новой установки (Developer)."""
    if not name:
        name = typer.prompt("Название установки").strip()
    if not culture:
        culture = typer.prompt("Культура").strip()
    if light_pin is None:
        light_pin = typer.prompt("GPIO пин основного света (или 3 пина через запятую)")
    if flash_pin is None:
        flash_pin = typer.prompt("GPIO пин вспышки (или 3 пина через запятую)")

    try:
        parsed_light = parse_gpio_pins(light_pin, field_label="GPIO пин света")
        parsed_flash = parse_gpio_pins(flash_pin, field_label="GPIO пин вспышки")
        validate_light_flash_disjoint(parsed_light, parsed_flash)
    except ValueError as exc:
        err_console.print(f"Ошибка (unit add): {exc}\n", style="bold red")
        raise typer.Exit(code=1) from exc

    payload: dict[str, Any] = {
        "name": name,
        "culture_name": culture,
        "light_gpio_pin": format_gpio_pins(parsed_light),
        "flash_gpio_pin": format_gpio_pins(parsed_flash),
    }
    if aruco_mm is not None:
        payload["aruco_size_mm"] = aruco_mm
    if delay is not None:
        if delay < 0.0 or delay > 30.0:
            err_console.print(
                "Ошибка (unit add): Задержка съёмки должна быть числом от 0.0 до 30.0 секунд.\n",
                style="bold red",
            )
            raise typer.Exit(code=1)
        payload["capture_delay_sec"] = round(delay, 2)

    client = APIClient()
    inst = client.request(
        method="POST",
        url="/api/installations",
        context="unit add",
        json_data=payload,
    )

    if isinstance(inst, dict):
        warnings = inst.get("warnings") or []
        for w in warnings:
            console.print(f"[yellow]{w}[/yellow]")
        console.print("[green]Установка успешно добавлена:[/green]")
        console.print(_render_unit_card(inst))


@unit_app.command(name="edit")
def unit_edit(
    target: str = typer.Argument(..., help="ID или название установки"),
    name: str = typer.Option(None, "--name", help="Новое название установки"),
    culture: str = typer.Option(None, "--culture", help="Новое название культуры"),
    light_pin: str = typer.Option(
        None,
        "--light-pin",
        help="Новый GPIO-пин основного света (1 пин или 3 пина RGB через запятую)",
    ),
    flash_pin: str = typer.Option(
        None,
        "--flash-pin",
        help="Новый GPIO-пин вспышки (1 пин или 3 пина RGB через запятую)",
    ),
    aruco_mm: float = typer.Option(
        None, "--aruco-mm", help="Новый размер стороны маркера ArUco в мм"
    ),
    clear_aruco: bool = typer.Option(
        False, "--clear-aruco", help="Сбросить маркер ArUco (удалить привязку)"
    ),
    delay: float = typer.Option(
        None,
        "--delay",
        "--capture-delay",
        help="Новая задержка съёмки в секундах (0.0 .. 30.0)",
    ),
    clear_delay: bool = typer.Option(
        False,
        "--clear-delay",
        "--default-delay",
        help="Сбросить задержку съёмки на общую системную",
    ),
) -> None:
    """Изменение параметров установки."""
    client = APIClient()
    unit_id = resolve_installation_target(
        client=client,
        target_str=target,
        context="unit edit",
        allow_all=False,
        include_archived=True,
    )

    payload: dict[str, Any] = {}
    if name is not None:
        payload["name"] = name
    if culture is not None:
        payload["culture_name"] = culture
    parsed_light_edit: list[int] | None = None
    parsed_flash_edit: list[int] | None = None
    try:
        if light_pin is not None:
            parsed_light_edit = parse_gpio_pins(light_pin, field_label="GPIO пин света")
            payload["light_gpio_pin"] = format_gpio_pins(parsed_light_edit)
        if flash_pin is not None:
            parsed_flash_edit = parse_gpio_pins(
                flash_pin, field_label="GPIO пин вспышки"
            )
            payload["flash_gpio_pin"] = format_gpio_pins(parsed_flash_edit)
        if parsed_light_edit is not None and parsed_flash_edit is not None:
            validate_light_flash_disjoint(parsed_light_edit, parsed_flash_edit)
    except ValueError as exc:
        err_console.print(f"Ошибка (unit edit): {exc}\n", style="bold red")
        raise typer.Exit(code=1) from exc
    if clear_aruco:
        payload["aruco_size_mm"] = None
    elif aruco_mm is not None:
        payload["aruco_size_mm"] = aruco_mm
    if clear_delay:
        payload["capture_delay_sec"] = None
    elif delay is not None:
        if delay < 0.0 or delay > 30.0:
            err_console.print(
                "Ошибка (unit edit): Задержка съёмки должна быть числом от 0.0 до 30.0 секунд.\n",
                style="bold red",
            )
            raise typer.Exit(code=1)
        payload["capture_delay_sec"] = round(delay, 2)

    if not payload:
        console.print("[yellow]Не указаны параметры для изменения.[/yellow]")
        return

    inst = client.request(
        method="PATCH",
        url=f"/api/installations/{unit_id}",
        context="unit edit",
        json_data=payload,
    )

    if isinstance(inst, dict):
        warnings = inst.get("warnings") or []
        for w in warnings:
            console.print(f"[yellow]{w}[/yellow]")
        console.print("[green]Параметры установки успешно обновлены:[/green]")
        console.print(_render_unit_card(inst))


@unit_app.command(name="delay")
def unit_delay(
    target: str = typer.Argument(..., help="ID или название установки"),
    seconds: float = typer.Argument(
        None, help="Задержка съёмки в секундах (0.0 .. 30.0). Не указывайте при сбросе."
    ),
    default: bool = typer.Option(
        False, "--default", "-d", help="Сбросить на общую системную задержку"
    ),
) -> None:
    """Настройка задержки съёмки для конкретной установки (Developer)."""
    if default:
        new_val = None
    elif seconds is not None:
        if seconds < 0.0 or seconds > 30.0:
            err_console.print(
                "Ошибка (unit delay): Задержка съёмки должна быть числом от 0.0 до 30.0 секунд.\n",
                style="bold red",
            )
            raise typer.Exit(code=1)
        new_val = round(seconds, 2)
    else:
        err_console.print(
            "Ошибка (unit delay): Укажите задержку в секундах или используйте флаг --default.\n",
            style="bold red",
        )
        raise typer.Exit(code=1)

    client = APIClient()
    unit_id = resolve_installation_target(
        client=client,
        target_str=target,
        context="unit delay",
        allow_all=False,
        include_archived=True,
    )

    inst = client.request(
        method="PATCH",
        url=f"/api/installations/{unit_id}",
        context="unit delay",
        json_data={"capture_delay_sec": new_val},
    )
    if isinstance(inst, dict):
        val_str = f"{new_val} с" if new_val is not None else "общая системная"
        console.print(
            f"[green]Задержка съёмки для установки #{unit_id} установлена: {val_str}.[/green]"
        )
        console.print(_render_unit_card(inst))


@unit_app.command(name="archive")
def unit_archive(
    target: str = typer.Argument(..., help="ID или название установки"),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Подтвердить архивацию без вопроса"
    ),
) -> None:
    """Перевод установки в архив (Developer)."""
    client = APIClient()
    unit_id = resolve_installation_target(
        client=client,
        target_str=target,
        context="unit archive",
        allow_all=False,
        include_archived=False,
    )

    if not yes:
        confirm = typer.confirm(
            f"Перевести установку #{unit_id} в архив? Все её камеры будут отвязаны."
        )
        if not confirm:
            console.print("[yellow]Действие отменено пользователем.[/yellow]")
            return

    inst = client.request(
        method="POST",
        url=f"/api/installations/{unit_id}/archive",
        context="unit archive",
    )

    inst_name = inst.get("name") if isinstance(inst, dict) else str(unit_id)
    console.print(
        f"[green]Установка «{inst_name}» переведена в архив. Все её камеры отвязаны.[/green]"
    )


@unit_app.command(name="delete")
def unit_delete(
    target: str = typer.Argument(..., help="ID или название установки"),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Подтвердить удаление без вопроса"
    ),
) -> None:
    """Удаление установки без снимков (Developer)."""
    client = APIClient()
    unit_id = resolve_installation_target(
        client=client,
        target_str=target,
        context="unit delete",
        allow_all=False,
        include_archived=True,
    )

    if not yes:
        confirm = typer.confirm(
            f"Удалить установку #{unit_id}? (Удаление возможно, только если нет снимков)"
        )
        if not confirm:
            console.print("[yellow]Действие отменено пользователем.[/yellow]")
            return

    client.request(
        method="DELETE",
        url=f"/api/installations/{unit_id}",
        context="unit delete",
    )
    console.print(f"[green]Установка #{unit_id} успешно удалена.[/green]")
