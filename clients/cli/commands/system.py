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
  [green]system delay <seconds>[/green]    - Настроить глобальную задержку съёмки (0.0..30.0 с)
  [green]system debug <on|off>[/green]     - Включить или выключить режим отладки

[bold]Примеры:[/bold]
  system status
  system schedule 2
  system delay 1.5
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

    capture_delay = settings.get("capture_delay_sec", 1.5)
    server_lines = [
        f"[bold]Версия сервера:[/bold] {srv_ver}",
        f"[bold]Статус RPi:[/bold] {conn_str}",
        f"[bold]Последний сигнал RPi:[/bold] {hb_at}",
        f"[bold]Интервал автосъёмки:[/bold] {interval} ч",
        f"[bold]Задержка съёмки:[/bold] {capture_delay} с",
        f"[bold]Режим отладки:[/bold] {debug_mode}",
    ]
    console.print(
        Panel("\n".join(server_lines), title="Сервер GrowVision", expand=False)
    )

    telemetry = resp.get("rpi_telemetry")
    if telemetry and isinstance(telemetry, dict):
        sw = telemetry.get("software")
        if not isinstance(sw, dict):
            sw = telemetry
        hw = telemetry.get("hardware")
        if not isinstance(hw, dict):
            hw = telemetry

        edge_ver = sw.get("edge_version") or telemetry.get("edge_version") or "—"
        py_ver = sw.get("python_version") or telemetry.get("python_version") or "—"
        model_name = sw.get("model_name") or telemetry.get("model_name") or "—"

        board = (
            hw.get("board_model")
            or telemetry.get("board_model")
            or (telemetry.get("model_name") if "software" not in telemetry else "—")
            or "—"
        )
        os_info = hw.get("os_info") or telemetry.get("os_info") or "—"
        model_os = f"{board} ({os_info})" if os_info != "—" else board

        gpio_mode = hw.get("gpio_mode") or telemetry.get("gpio_mode") or "—"

        uptime_raw = (
            hw.get("uptime_sec")
            if hw.get("uptime_sec") is not None
            else hw.get("uptime_seconds")
        )
        if uptime_raw is None:
            uptime_raw = (
                telemetry.get("uptime_sec")
                if telemetry.get("uptime_sec") is not None
                else telemetry.get("uptime_seconds")
            )
        if uptime_raw is not None and isinstance(uptime_raw, (int, float)):
            uptime_sec = int(uptime_raw)
            uptime_str = f"{uptime_sec // 3600} ч {(uptime_sec % 3600) // 60} мин"
        else:
            uptime_str = "—"

        cpu_temp = (
            hw.get("cpu_temp_c")
            if hw.get("cpu_temp_c") is not None
            else telemetry.get("cpu_temp_c")
        )
        cpu_temp_str = f"{cpu_temp} °C" if cpu_temp is not None else "—"

        cpu_usage = (
            hw.get("cpu_usage_percent")
            if hw.get("cpu_usage_percent") is not None
            else hw.get("cpu_load_percent")
        )
        if cpu_usage is None:
            cpu_usage = (
                telemetry.get("cpu_usage_percent")
                if telemetry.get("cpu_usage_percent") is not None
                else telemetry.get("cpu_load_percent")
            )
        cpu_usage_str = f"{cpu_usage} %" if cpu_usage is not None else "—"

        ram_used = (
            hw.get("ram_used_mb")
            if hw.get("ram_used_mb") is not None
            else telemetry.get("ram_used_mb")
        )
        ram_total = (
            hw.get("ram_total_mb")
            if hw.get("ram_total_mb") is not None
            else telemetry.get("ram_total_mb")
        )
        if ram_used is not None and ram_total is not None:
            ram_str = f"{ram_used} / {ram_total} МБ"
        else:
            ram_str = "—"

        disk_free = (
            hw.get("disk_free_gb")
            if hw.get("disk_free_gb") is not None
            else telemetry.get("disk_free_gb")
        )
        disk_str = f"{disk_free} ГБ" if disk_free is not None else "—"

        queue_count = telemetry.get("offline_queue_count", 0)

        rpi_lines = [
            f"[bold]Edge Agent:[/bold] {edge_ver}",
            f"[bold]Python:[/bold] {py_ver}",
            f"[bold]Модель ONNX:[/bold] {model_name}",
            f"[bold]Модель / ОС:[/bold] {model_os}",
            f"[bold]Режим GPIO:[/bold] {gpio_mode}",
            f"[bold]Аптайм:[/bold] {uptime_str}",
            f"[bold]Температура CPU:[/bold] {cpu_temp_str}",
            f"[bold]Загрузка CPU:[/bold] {cpu_usage_str}",
            f"[bold]RAM:[/bold] {ram_str}",
            f"[bold]Свободно на диске:[/bold] {disk_str}",
            f"[bold]Очередь офлайн:[/bold] {queue_count}",
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


@system_app.command(name="delay")
def system_delay(
    seconds: float = typer.Argument(
        ..., help="Задержка стабилизации света перед съёмкой в секундах (0.0 .. 30.0)"
    ),
) -> None:
    """Изменение глобальной задержки съёмки (Developer)."""
    if seconds < 0.0 or seconds > 30.0:
        err_console.print(
            "Ошибка (system delay): Задержка съёмки должна быть числом от 0.0 до 30.0 секунд.\n",
            style="bold red",
        )
        raise typer.Exit(code=1)

    val = round(seconds, 2)
    client = APIClient()
    resp = client.request(
        method="PATCH",
        url="/api/system/settings",
        context="system delay",
        json_data={"capture_delay_sec": val},
    )

    if isinstance(resp, dict):
        console.print(
            f"[green]Глобальная задержка съёмки успешно изменена на {val} с.[/green]"
        )


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
