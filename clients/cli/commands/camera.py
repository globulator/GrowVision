import typer
from rich.table import Table

from clients.cli.client import APIClient
from clients.cli.formatting import console, print_debug_info
from clients.cli.resolver import resolve_camera_target, resolve_installation_target

camera_app = typer.Typer(
    name="camera", help="Управление камерами на Raspberry Pi", no_args_is_help=True
)


@camera_app.command(name="info")
def camera_info() -> None:
    """Справка по разделу camera."""
    info_text = """[bold cyan]Раздел camera — Камеры на Raspberry Pi[/bold cyan]

[bold]Назначение:[/bold] Просмотр, поиск (сканирование) и привязка USB/CSI камер к установкам.
[bold]Минимальная роль:[/bold] Developer (для всех команд раздела).

[bold]Список подкоманд:[/bold]
  [green]camera info[/green]                         - Показать эту справку
  [green]camera list[/green]                         - Список зарегистрированных камер
  [green]camera scan[/green]                         - Поиск подключенных камер на Raspberry Pi
  [green]camera bind <камера> <установка>[/green]   - Привязать камеру к установке
  [green]camera unbind <камера>[/green]             - Отвязать камеру от установки

[bold]Примеры:[/bold]
  camera list
  camera scan
  camera bind 1 "Установка 1"
  camera unbind 1
"""
    console.print(info_text)


@camera_app.command(name="list")
def camera_list() -> None:
    """Список всех зарегистрированных камер (Developer)."""
    client = APIClient()
    items = client.request(
        method="GET",
        url="/api/cameras",
        context="camera list",
    )

    if not items or not isinstance(items, list):
        console.print(
            "[yellow]Зарегистрированных камер пока нет. Выполните camera scan.[/yellow]"
        )
        return

    table = Table(title="Зарегистрированные камеры", header_style="bold cyan")
    table.add_column("ID", justify="right")
    table.add_column("Модель", justify="left")
    table.add_column("Hardware ID", justify="left")
    table.add_column("Статус", justify="center")
    table.add_column("Привязка", justify="left")
    table.add_column("Последний сигнал", justify="center")

    for cam in items:
        status_str = (
            "[green]Online[/green]" if cam.get("is_online") else "[dim]Offline[/dim]"
        )
        inst_name = cam.get("installation_name")
        bind_str = f"Установка «{inst_name}»" if inst_name else "[dim]Свободна[/dim]"
        last_seen = cam.get("last_seen_at") or "—"

        table.add_row(
            str(cam.get("id")),
            str(cam.get("auto_name")),
            str(cam.get("hardware_id")),
            status_str,
            bind_str,
            last_seen,
        )

    console.print(table)


@camera_app.command(name="scan")
def camera_scan() -> None:
    """Запуск сканирования камер на Raspberry Pi (Developer)."""
    console.print("[dim]Выполняется сканирование камер на Raspberry Pi...[/dim]")
    client = APIClient()
    resp = client.request(
        method="POST",
        url="/api/cameras/scan",
        context="camera scan",
        timeout=30.0,
    )

    if not isinstance(resp, dict):
        return

    cameras = resp.get("cameras") or []
    if not cameras:
        console.print(
            "[yellow]Камеры не обнаружены. Проверьте USB-подключение камер к Raspberry Pi.[/yellow]"
        )
    else:
        table = Table(title="Обнаруженные камеры", header_style="bold cyan")
        table.add_column("ID", justify="right")
        table.add_column("Модель", justify="left")
        table.add_column("Hardware ID", justify="left")
        table.add_column("Привязка", justify="left")

        for cam in cameras:
            inst_name = cam.get("installation_name")
            bind_str = (
                f"Привязана к установке «{inst_name}»"
                if inst_name
                else "[green]Свободна — можно привязать[/green]"
            )
            table.add_row(
                str(cam.get("id")),
                str(cam.get("auto_name")),
                str(cam.get("hardware_id")),
                bind_str,
            )

        console.print(table)
        console.print(
            "[dim]Свободные камеры можно привязать: camera bind <id> <установка>[/dim]"
        )

    print_debug_info(resp.get("debug_info"))


@camera_app.command(name="bind")
def camera_bind(
    camera: str = typer.Argument(..., help="ID или Hardware ID камеры"),
    unit: str = typer.Argument(..., help="ID или название активной установки"),
) -> None:
    """Привязка камеры к гидропонной установке (Developer)."""
    client = APIClient()
    cam_id = resolve_camera_target(client, camera, context="camera bind")
    unit_id = resolve_installation_target(
        client=client,
        target_str=unit,
        context="camera bind",
        allow_all=False,
        include_archived=False,
    )

    resp = client.request(
        method="POST",
        url=f"/api/cameras/{cam_id}/bind",
        context="camera bind",
        json_data={"installation_id": unit_id},
    )

    inst_name = (
        resp.get("installation_name") if isinstance(resp, dict) else str(unit_id)
    )
    console.print(
        f"[green]Камера #{cam_id} успешно привязана к установке «{inst_name}».[/green]"
    )


@camera_app.command(name="unbind")
def camera_unbind(
    camera: str = typer.Argument(..., help="ID или Hardware ID камеры"),
) -> None:
    """Отвязка камеры от установки (Developer)."""
    client = APIClient()
    cam_id = resolve_camera_target(client, camera, context="camera unbind")

    client.request(
        method="POST",
        url=f"/api/cameras/{cam_id}/bind",
        context="camera unbind",
        json_data={"installation_id": None},
    )

    console.print(f"[green]Камера #{cam_id} успешно отвязана от установки.[/green]")
