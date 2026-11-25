import httpx
import typer

from clients.cli.client import APIClient
from clients.cli.formatting import console, err_console, print_debug_info
from clients.cli.pins import normalize_hex_color
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
  [green]light info[/green]                                   - Показать эту справку
  [green]light main <target> [on|off] [--color #HEX][/green]  - Управление основным светом и HEX-цветом RGB
  [green]light flash <target> [on|off] [--color #HEX][/green] - Управление вспышкой и HEX-цветом RGB
  [green]light test <target>[/green]                          - Тест реле: включение (для RGB — радуга)

[bold]Параметр <target>:[/bold] ID установки, точное название или 'all' (ко всем активным).

[bold]Примеры:[/bold]
  light main 1 on
  light main 1 --color #FF8800
  light main 1 on --color 00FFAA
  light main all off
  light flash "Установка 1" on --color #FFFFFF
  light test 1
"""
    console.print(info_text)


def _execute_light_command(
    *,
    target: str,
    state: str | None,
    color: str | None,
    is_flash: bool,
) -> None:
    if state is None and color is None:
        err_console.print(
            "Ошибка: укажите состояние (on/off) и/или цвет --color #RRGGBB.\n",
            style="bold red",
        )
        raise typer.Exit(code=1)

    bool_state = _parse_state(state) if state is not None else None
    norm_color: str | None = None
    if color is not None:
        try:
            norm_color = normalize_hex_color(color)
        except ValueError as exc:
            err_console.print(f"Ошибка: {exc}\n", style="bold red")
            raise typer.Exit(code=1) from exc

    context = "light flash" if is_flash else "light main"
    endpoint = "/api/control/light/flash" if is_flash else "/api/control/light/main"
    channel_genitive = "вспышки" if is_flash else "основного света"

    client = APIClient()
    unit_target = resolve_installation_target(
        client=client,
        target_str=target,
        context=context,
        allow_all=True,
        include_archived=False,
    )

    if unit_target == "all" and norm_color is not None:
        err_console.print(
            "Ошибка: --color можно указать только для конкретной установки, не для 'all'.\n",
            style="bold red",
        )
        raise typer.Exit(code=1)

    payload: dict[str, object] = {"target": unit_target}
    if bool_state is not None:
        payload["state"] = bool_state
    if norm_color is not None:
        payload["color"] = norm_color

    timeout = _get_timeout(unit_target, client)
    target_str = (
        f"установки #{unit_target}"
        if isinstance(unit_target, int)
        else "всех активных установок"
    )
    if bool_state is not None:
        action_str = "включения" if bool_state else "выключения"
        console.print(
            f"[dim]Отправка команды {action_str} {channel_genitive} для {target_str}...[/dim]"
        )
    else:
        console.print(
            f"[dim]Применение цвета {norm_color} {channel_genitive} для {target_str}...[/dim]"
        )

    resp = client.request(
        method="POST",
        url=endpoint,
        context=context,
        json_data=payload,
        timeout=timeout,
    )

    if isinstance(resp, dict):
        status = resp.get("status")
        msg = resp.get("result_message", "Команда выполнена")
        print_debug_info(resp.get("debug_info"))
        if status == "error":
            err_console.print(msg)
            raise typer.Exit(code=1)
        color_info = ""
        if resp.get("color") and (resp.get("is_rgb") or norm_color is not None):
            color_info = f" [cyan](цвет: {resp.get('color')})[/cyan]"
        console.print(f"[green]{msg}[/green]{color_info}")


@light_app.command(name="main")
def light_main(
    target: str = typer.Argument(..., help="ID, название установки или 'all'"),
    state: str | None = typer.Argument(
        None, help="on или off (опционально при --color)"
    ),
    color: str | None = typer.Option(
        None, "--color", "-c", help="HEX-цвет RGB-ленты (#RRGGBB)"
    ),
) -> None:
    """Управление основным освещением и HEX-цветом RGB (Developer)."""
    _execute_light_command(target=target, state=state, color=color, is_flash=False)


@light_app.command(name="flash")
def light_flash(
    target: str = typer.Argument(..., help="ID, название установки или 'all'"),
    state: str | None = typer.Argument(
        None, help="on или off (опционально при --color)"
    ),
    color: str | None = typer.Option(
        None, "--color", "-c", help="HEX-цвет RGB-ленты (#RRGGBB)"
    ),
) -> None:
    """Управление подсветкой для фото (вспышкой) и HEX-цветом RGB (Developer)."""
    _execute_light_command(target=target, state=state, color=color, is_flash=True)


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
