import os

import typer

from clients.cli.client import APIClient
from clients.cli.commands.auth import auth_app
from clients.cli.commands.camera import camera_app
from clients.cli.commands.light import light_app
from clients.cli.commands.shell import run_shell
from clients.cli.commands.snap import snap_app
from clients.cli.commands.system import system_app
from clients.cli.commands.unit import unit_app
from clients.cli.formatting import console

app = typer.Typer(
    name="growvision",
    help="Консольный клиент GrowVision",
    no_args_is_help=False,
    invoke_without_command=True,
)

# Подключение всех 6 разделов
app.add_typer(auth_app, name="auth")
app.add_typer(unit_app, name="unit")
app.add_typer(camera_app, name="camera")
app.add_typer(light_app, name="light")
app.add_typer(snap_app, name="snap")
app.add_typer(system_app, name="system")


@app.callback()
def main_callback(
    ctx: typer.Context,
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Подробный вывод HTTP-запросов и ответов"
    ),
) -> None:
    """Глобальный обработчик параметров CLI."""
    if verbose:
        os.environ["CLI_DEBUG"] = "1"

    # Если команда не передана, запускаем интерактивный shell
    if ctx.invoked_subcommand is None:
        run_shell(lambda args: app(args, standalone_mode=False))


@app.command(name="shell")
def shell_command() -> None:
    """Запуск интерактивной оболочки с приглашением growvision>."""
    run_shell(lambda args: app(args, standalone_mode=False))


@app.command(name="version")
def version_command() -> None:
    """Версия сервера GrowVision."""
    client = APIClient()
    resp = client.request(
        method="GET",
        url="/api/version",
        context="version",
    )
    if isinstance(resp, dict):
        srv_ver = resp.get("server_version", "неизвестно")
        console.print(f"GrowVision server: {srv_ver}")


@app.command(name="help")
def help_command() -> None:
    """Глобальная справка по проекту и клиенту GrowVision."""
    help_text = """[bold green]GrowVision — Автоматизированная система мониторинга растений[/bold green]

[bold]Описание ролей пользователей:[/bold]
  • [bold]Гость[/bold]       — публичный просмотр установок и плановых кадров (без авторизации).
  • [bold]Biologist[/bold]   — просмотр установок, ручная фотосъёмка, сборка видео таймлапсов.
  • [bold]Developer[/bold]   — полный доступ: реле света и вспышки, сканирование камер, настройки.

[bold]Глобальные команды:[/bold]
  [green]help[/green]       - Показать эту общую справку
  [green]version[/green]    - Показать версию сервера GrowVision
  [green]shell[/green]      - Интерактивная оболочка (приглашение growvision>)

[bold]Разделы команд:[/bold]
  [green]auth[/green]       - Авторизация и управление сессией (login, status, logout)
  [green]unit[/green]       - Управление установками (list, show, add, edit, archive, delete)
  [green]camera[/green]     - Управление камерами на Raspberry Pi (list, scan, bind, unbind)
  [green]light[/green]      - Управление освещением и реле (main, flash, test)
  [green]snap[/green]       - Ручная фотосъёмка и сборка таймлапсов (take, video)
  [green]system[/green]     - Системный статус RPi и расписание (status, schedule, debug)

[bold]Базовые примеры использования:[/bold]
  growvision auth login
  growvision unit list
  growvision snap take 1
  growvision help
"""
    console.print(help_text)


if __name__ == "__main__":
    app()
