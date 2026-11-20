from typing import Any

import typer
from rich.panel import Panel
from rich.table import Table

from clients.cli.client import APIClient
from clients.cli.formatting import console
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

    if "light_gpio_pin" in inst and "flash_gpio_pin" in inst:
        lines.append(
            f"[bold]GPIO основного света:[/bold] GPIO {inst.get('light_gpio_pin')}"
        )
        lines.append(f"[bold]GPIO вспышки:[/bold] GPIO {inst.get('flash_gpio_pin')}")

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
  [green]unit archive <target> [-y][/green]                              - Перевести установку в архив (Developer)
  [green]unit delete <target> [-y][/green]                               - Удалить установку (Developer)

[bold]Примеры:[/bold]
  unit list
  unit show 1
  unit show "Установка 1"
  unit show all
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
    light_pin: int = typer.Option(
        None, "--light-pin", help="GPIO-пин основного освещения"
    ),
    flash_pin: int = typer.Option(
        None, "--flash-pin", help="GPIO-пин подсветки для фото"
    ),
    aruco_mm: float = typer.Option(
        None, "--aruco-mm", help="Размер стороны маркера ArUco в мм (опционально)"
    ),
) -> None:
    """Добавление новой установки (Developer)."""
    if not name:
        name = typer.prompt("Название установки").strip()
    if not culture:
        culture = typer.prompt("Культура").strip()
    if light_pin is None:
        light_pin = int(typer.prompt("GPIO пин основного света"))
    if flash_pin is None:
        flash_pin = int(typer.prompt("GPIO пин вспышки"))

    payload: dict[str, Any] = {
        "name": name,
        "culture_name": culture,
        "light_gpio_pin": light_pin,
        "flash_gpio_pin": flash_pin,
    }
    if aruco_mm is not None:
        payload["aruco_size_mm"] = aruco_mm

    client = APIClient()
    inst = client.request(
        method="POST",
        url="/api/installations",
        context="unit add",
        json_data=payload,
    )

    if isinstance(inst, dict):
        console.print("[green]Установка успешно добавлена:[/green]")
        console.print(_render_unit_card(inst))


@unit_app.command(name="edit")
def unit_edit(
    target: str = typer.Argument(..., help="ID или название установки"),
    name: str = typer.Option(None, "--name", help="Новое название установки"),
    culture: str = typer.Option(None, "--culture", help="Новое название культуры"),
    light_pin: int = typer.Option(
        None, "--light-pin", help="Новый GPIO-пин основного света"
    ),
    flash_pin: int = typer.Option(None, "--flash-pin", help="Новый GPIO-пин вспышки"),
    aruco_mm: float = typer.Option(
        None, "--aruco-mm", help="Новый размер стороны маркера ArUco в мм"
    ),
    clear_aruco: bool = typer.Option(
        False, "--clear-aruco", help="Сбросить маркер ArUco (удалить привязку)"
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
    if light_pin is not None:
        payload["light_gpio_pin"] = light_pin
    if flash_pin is not None:
        payload["flash_gpio_pin"] = flash_pin
    if clear_aruco:
        payload["aruco_size_mm"] = None
    elif aruco_mm is not None:
        payload["aruco_size_mm"] = aruco_mm

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
        console.print("[green]Параметры установки успешно обновлены:[/green]")
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
