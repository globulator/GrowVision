import json
import os
from typing import Any

from rich.console import Console
from rich.table import Table

_width = int(os.environ.get("COLUMNS", "160"))
console = Console(width=_width)
err_console = Console(stderr=True, width=_width)


def print_error(context: str, detail: str, hint: str | None = None) -> None:
    """Выводит ошибку по спецификации CLI_SPEC.md разд. 4."""
    msg = f"Ошибка ({context}): {detail}\n"
    if hint:
        msg += f"Рекомендация: {hint}\n"
    err_console.print(msg, end="", style="bold red")


def print_debug_info(debug_info: dict[str, Any] | None) -> None:
    """Выводит блок отладочной информации debug_info для роли Developer."""
    if not debug_info:
        return
    cmd_str = json.dumps(debug_info.get("command"), ensure_ascii=False)
    log_line = debug_info.get("log_line", "")
    lines = [
        "[dim]--- DEBUG ---[/dim]",
        f"[dim]Команда: {cmd_str}[/dim]",
        f"[dim]Лог RPi: {log_line}[/dim]",
    ]
    console.print("\n".join(lines))


def format_table(
    title: str,
    columns: list[tuple[str, str]],  # (название, стиль/выравнивание)
    rows: list[list[Any]],
) -> Table:
    """Создает rich Table со стандартным стилем."""
    table = Table(title=title, show_lines=False, header_style="bold cyan")
    for col_name, justify in columns:
        table.add_column(col_name, justify=justify)
    for row in rows:
        table.add_row(*[str(cell) if cell is not None else "—" for cell in row])
    return table
