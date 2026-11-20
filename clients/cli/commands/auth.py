import typer

from clients.cli.client import APIClient
from clients.cli.formatting import console

auth_app = typer.Typer(
    name="auth", help="Авторизация и управление сессией", no_args_is_help=True
)


@auth_app.command(name="info")
def auth_info() -> None:
    """Справка по разделу auth."""
    info_text = """[bold cyan]Раздел auth — Авторизация и управление сессией[/bold cyan]

[bold]Назначение:[/bold] Управление сессией пользователя в консольном клиенте.
[bold]Минимальная роль:[/bold] Гость (для login/info), Biologist/Developer (для status/logout).

[bold]Список подкоманд:[/bold]
  [green]auth info[/green]    - Показать эту справку
  [green]auth login[/green]   - Интерактивный вход по логину и паролю
  [green]auth status[/green]  - Проверить текущую сессию и роль
  [green]auth logout[/green]  - Завершить сессию (возврат к роли Гость)

[bold]Примеры:[/bold]
  auth login
  auth status
"""
    console.print(info_text)


@auth_app.command(name="login")
def auth_login() -> None:
    """Интерактивный 2-шаговый вход в систему."""
    login = typer.prompt("Логин").strip()
    if not login:
        console.print("[red]Логин не может быть пустым.[/red]")
        raise typer.Exit(code=1)

    password = typer.prompt("Пароль", hide_input=True)

    client = APIClient()
    payload = {
        "client_type": "cli",
        "external_id": client.config.external_id,
        "login": login,
        "password": password,
    }

    resp = client.request(
        method="POST",
        url="/api/auth/login",
        context="auth login",
        json_data=payload,
    )

    if isinstance(resp, dict):
        role = resp.get("role", "неизвестно")
        console.print(
            f"[green]Авторизация успешна. Логин: {login}, Роль: {role}[/green]"
        )


@auth_app.command(name="status")
def auth_status() -> None:
    """Проверка текущего статуса сессии."""
    client = APIClient()
    resp = client.request(
        method="GET",
        url="/api/auth/me",
        context="auth status",
    )

    if isinstance(resp, dict):
        login = resp.get("login", "—")
        role = resp.get("role", "—")
        auth_at = resp.get("authorized_at", "—")
        last_act = resp.get("last_active_at", "—")
        console.print(
            f"Сессия активна:\n"
            f"  Логин: [bold]{login}[/bold]\n"
            f"  Роль: [bold cyan]{role}[/bold cyan]\n"
            f"  Авторизован: {auth_at}\n"
            f"  Последняя активность: {last_act}"
        )


@auth_app.command(name="logout")
def auth_logout() -> None:
    """Завершение текущей сессии."""
    client = APIClient()
    client.request(
        method="POST",
        url="/api/auth/logout",
        context="auth logout",
    )
    console.print("[green]Сессия завершена. Текущая роль: Гость.[/green]")
