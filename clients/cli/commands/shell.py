import contextlib
import shlex
from typing import Any

import httpx

from clients.cli.client import APIClient
from clients.cli.formatting import console, err_console


def run_shell(app_caller: Any) -> None:
    """Запуск интерактивной оболочки growvision>."""
    # Получаем версию сервера для баннера (если сервер недоступен — без ошибки)
    server_version = ""
    with contextlib.suppress(httpx.HTTPError, OSError):
        config = APIClient().config
        with httpx.Client(base_url=config.server_url, timeout=2.0) as cl:
            r = cl.get("/api/version")
            if r.status_code == 200:
                data = r.json()
                if "server_version" in data:
                    server_version = f" {data['server_version']}"

    console.print(f"[bold green]GrowVision{server_version}[/bold green]")
    console.print(
        "[dim]Интерактивная оболочка. Введите команду или 'help'. Для выхода введите 'exit' или 'quit'.[/dim]\n"
    )

    while True:
        try:
            line = input("growvision> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Выход из GrowVision shell.[/dim]")
            break

        if not line:
            continue

        if line.lower() in ("exit", "quit", "q"):
            console.print("[dim]Выход из GrowVision shell.[/dim]")
            break

        try:
            args = shlex.split(line)
        except ValueError as e:
            err_console.print(f"Ошибка парсинга команды: {e}", style="bold red")
            continue

        try:
            app_caller(args)
        except SystemExit:
            # Предотвращаем выход из shell при sys.exit в подкомандах
            pass
        except Exception as e:  # noqa: BLE001
            err_console.print(f"Ошибка выполнения: {e}", style="bold red")
