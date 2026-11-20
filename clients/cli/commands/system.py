import typer
from rich.panel import Panel

from clients.cli.client import APIClient
from clients.cli.commands.light import _parse_state
from clients.cli.formatting import console, err_console

system_app = typer.Typer(
    name="system", help="Состояние RPi и системные настройки", no_args_is_help=True
)


@system_app.command(name="info")
def system_info() -> None:
    """Справка по разделу system."""
    info_text = """[bold cyan]Раздел system — Состояние RPi и глобальные настройки[/bold cyan]

[bold]Назначение:[/bold] Мониторинг телеметрии Raspberry Pi 5, настройка расписания и режима отладки.
[bold]Минимальная роль:[/bold] Developer (для всех команд раздела).

[bold]Список подкоманд:[/bold]
  [green]system info[/green]              - Показать эту справку
  [green]system status[/green]            - Сводка по серверу и телеметрия Raspberry Pi
  [green]system schedule <hours>[/green]   - Изменить интервал плановой автосъёмки (в часах)
  [green]system debug <on|off>[/green]     - Включить или выключить режим отладки

[bold]Примеры:[/bold]
  system status
  system schedule 2
  system debug on
"""
    console.print(info_text)


@system_app.command(name="status")
def system_status() -> None:
    """Сводка по серверу и телеметрия Raspberry Pi (Developer)."""
    client = APIClient()
    resp = client.request(
        method="GET",
        url="/api/system/status",
        context="system status",
    )

    if not isinstance(resp, dict):
        return

    srv_ver = resp.get("server_version", "—")
    rpi_conn = resp.get("rpi_connection", "offline")
    conn_str = "[green]online[/green]" if rpi_conn == "online" else "[red]offline[/red]"
    hb_at = resp.get("rpi_last_heartbeat_at") or "—"

    settings = resp.get("settings") or {}
    interval = settings.get("capture_interval_hours", "—")
    debug_mode = (
        "[green]включен[/green]"
        if settings.get("debug_mode")
        else "[dim]выключен[/dim]"
    )

    server_lines = [
        f"[bold]Версия сервера:[/bold] {srv_ver}",
        f"[bold]Статус RPi:[/bold] {conn_str}",
        f"[bold]Последний сигнал RPi:[/bold] {hb_at}",
        f"[bold]Интервал автосъёмки:[/bold] {interval} ч",
        f"[bold]Режим отладки:[/bold] {debug_mode}",
    ]
    console.print(
        Panel("\n".join(server_lines), title="Сервер GrowVision", expand=False)
    )

    telemetry = resp.get("rpi_telemetry")
    if telemetry and isinstance(telemetry, dict):
        rpi_lines = [
            f"[bold]Edge Agent:[/bold] {telemetry.get('edge_version', '—')}",
            f"[bold]Python:[/bold] {telemetry.get('python_version', '—')}",
            f"[bold]Модель / ОС:[/bold] {telemetry.get('model_name', '—')} ({telemetry.get('os_info', '—')})",
            f"[bold]Режим GPIO:[/bold] {telemetry.get('gpio_mode', '—')}",
            f"[bold]Аптайм:[/bold] {telemetry.get('uptime_seconds', 0) // 3600} ч {(telemetry.get('uptime_seconds', 0) % 3600) // 60} мин",
            f"[bold]Температура CPU:[/bold] {telemetry.get('cpu_temp_c', '—')} °C",
            f"[bold]Загрузка CPU:[/bold] {telemetry.get('cpu_load_percent', '—')} %",
            f"[bold]RAM:[/bold] {telemetry.get('ram_used_mb', '—')} / {telemetry.get('ram_total_mb', '—')} МБ",
            f"[bold]Свободно на диске:[/bold] {telemetry.get('disk_free_gb', '—')} ГБ",
            f"[bold]Очередь офлайн:[/bold] {telemetry.get('offline_queue_count', 0)}",
        ]
        console.print(
            Panel("\n".join(rpi_lines), title="Телеметрия Raspberry Pi", expand=False)
        )
    elif rpi_conn == "offline":
        console.print("[dim]Телеметрия RPi недоступна (устройство офлайн).[/dim]")


@system_app.command(name="schedule")
def system_schedule(
    hours: int = typer.Argument(
        ..., help="Интервал автосъёмки в часах (целое число >= 1)"
    ),
) -> None:
    """Изменение интервала автосъёмки (Developer)."""
    if hours < 1:
        err_console.print(
            "Ошибка (system schedule): Интервал должен быть целым числом не меньше 1 часа.\n",
            style="bold red",
        )
        raise typer.Exit(code=1)

    client = APIClient()
    resp = client.request(
        method="PATCH",
        url="/api/system/settings",
        context="system schedule",
        json_data={"capture_interval_hours": hours},
    )

    if isinstance(resp, dict):
        console.print(
            f"[green]Интервал автосъёмки успешно изменён на {hours} ч.[/green]"
        )
        warning = resp.get("warning")
        if warning:
            console.print(f"[yellow]Предупреждение: {warning}[/yellow]")


@system_app.command(name="debug")
def system_debug(
    state: str = typer.Argument(..., help="on или off"),
) -> None:
    """Включение или выключение режима отладки (Developer)."""
    bool_state = _parse_state(state)
    client = APIClient()
    client.request(
        method="PATCH",
        url="/api/system/settings",
        context="system debug",
        json_data={"debug_mode": bool_state},
    )

    status_str = "включен" if bool_state else "выключен"
    console.print(f"[green]Режим отладки успешно {status_str}.[/green]")
