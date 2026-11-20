import httpx
import typer

from clients.cli.client import APIClient
from clients.cli.formatting import console, err_console, print_debug_info
from clients.cli.resolver import resolve_installation_target

light_app = typer.Typer(
    name="light", help="Управление освещением и реле", no_args_is_help=True
)


def _parse_state(state_str: str) -> bool:
    """Парсит строковое состояние в boolean."""
    s = state_str.strip().lower()
    if s in ("on", "1", "true", "вкл"):
        return True
    if s in ("off", "0", "false", "выкл"):
        return False
    console.print(
        f"[red]Некорректное состояние «{state_str}». Используйте on или off.[/red]"
    )
    raise typer.Exit(code=1)


def _get_timeout(target: int | str, client: APIClient) -> float:
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


@light_app.command(name="info")
def light_info() -> None:
    """Справка по разделу light."""
    info_text = """[bold cyan]Раздел light — Управление светом и вспышкой[/bold cyan]

[bold]Назначение:[/bold] Ручное управление реле основного света и вспышки на установках.
[bold]Минимальная роль:[/bold] Developer (для всех команд раздела).

[bold]Список подкоманд:[/bold]
  [green]light info[/green]                         - Показать эту справку
  [green]light main <target> <on|off>[/green]       - Включить или выключить основной свет
  [green]light flash <target> <on|off>[/green]      - Включить или выключить вспышку (подсветку)
  [green]light test <target>[/green]                 - Тест реле: включение обоих реле на 3 сек

[bold]Параметр <target>:[/bold] ID установки, точное название или 'all' (ко всем активным).

[bold]Примеры:[/bold]
  light main 1 on
  light main all off
  light flash "Установка 1" on
  light test 1
"""
    console.print(info_text)


@light_app.command(name="main")
def light_main(
    target: str = typer.Argument(..., help="ID, название установки или 'all'"),
    state: str = typer.Argument(..., help="on или off"),
) -> None:
    """Управление основным освещением (Developer)."""
    bool_state = _parse_state(state)
    client = APIClient()
    unit_target = resolve_installation_target(
        client=client,
        target_str=target,
        context="light main",
        allow_all=True,
        include_archived=False,
    )

    timeout = _get_timeout(unit_target, client)
    action_str = "включения" if bool_state else "выключения"
    target_str = (
        f"установки #{unit_target}"
        if isinstance(unit_target, int)
        else "всех активных установок"
    )
    console.print(
        f"[dim]Отправка команды {action_str} основного света для {target_str}...[/dim]"
    )

    resp = client.request(
        method="POST",
        url="/api/control/light/main",
        context="light main",
        json_data={"target": unit_target, "state": bool_state},
        timeout=timeout,
    )

    if isinstance(resp, dict):
        status = resp.get("status")
        msg = resp.get("result_message", "Команда выполнена")
        print_debug_info(resp.get("debug_info"))
        if status == "error":
            err_console.print(msg)
            raise typer.Exit(code=1)
        console.print(f"[green]{msg}[/green]")


@light_app.command(name="flash")
def light_flash(
    target: str = typer.Argument(..., help="ID, название установки или 'all'"),
    state: str = typer.Argument(..., help="on или off"),
) -> None:
    """Управление подсветкой для фото (вспышкой) (Developer)."""
    bool_state = _parse_state(state)
    client = APIClient()
    unit_target = resolve_installation_target(
        client=client,
        target_str=target,
        context="light flash",
        allow_all=True,
        include_archived=False,
    )

    timeout = _get_timeout(unit_target, client)
    action_str = "включения" if bool_state else "выключения"
    target_str = (
        f"установки #{unit_target}"
        if isinstance(unit_target, int)
        else "всех активных установок"
    )
    console.print(
        f"[dim]Отправка команды {action_str} вспышки для {target_str}...[/dim]"
    )

    resp = client.request(
        method="POST",
        url="/api/control/light/flash",
        context="light flash",
        json_data={"target": unit_target, "state": bool_state},
        timeout=timeout,
    )

    if isinstance(resp, dict):
        status = resp.get("status")
        msg = resp.get("result_message", "Команда выполнена")
        print_debug_info(resp.get("debug_info"))
        if status == "error":
            err_console.print(msg)
            raise typer.Exit(code=1)
        console.print(f"[green]{msg}[/green]")


@light_app.command(name="test")
def light_test(
    target: str = typer.Argument(..., help="ID, название установки или 'all'"),
) -> None:
    """Тест реле: включение света и вспышки на 3 секунды (Developer)."""
    client = APIClient()
    unit_target = resolve_installation_target(
        client=client,
        target_str=target,
        context="light test",
        allow_all=True,
        include_archived=False,
    )

    timeout = _get_timeout(unit_target, client)
    target_str = (
        f"установки #{unit_target}"
        if isinstance(unit_target, int)
        else "всех активных установок"
    )
    console.print(f"[dim]Запуск теста реле на 3 секунды для {target_str}...[/dim]")

    resp = client.request(
        method="POST",
        url="/api/control/relay-test",
        context="light test",
        json_data={"target": unit_target},
        timeout=timeout,
    )

    if isinstance(resp, dict):
        status = resp.get("status")
        msg = resp.get("result_message", "Тест реле завершен")
        print_debug_info(resp.get("debug_info"))
        if status == "error":
            err_console.print(msg)
            raise typer.Exit(code=1)
        console.print(f"[green]{msg}[/green]")
