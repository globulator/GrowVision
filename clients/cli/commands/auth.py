from typing import Any

import typer

from clients.cli.client import APIClient
from clients.cli.formatting import console, format_table

auth_app = typer.Typer(
    name="auth", help="Авторизация и управление сессией", no_args_is_help=True
)

invite_app = typer.Typer(
    name="invite", help="Управление одноразовыми кодами доступа", no_args_is_help=True
)
auth_app.add_typer(invite_app, name="invite")


@auth_app.command(name="info")
def auth_info() -> None:
    """Справка по разделу auth."""
    info_text = """[bold cyan]Раздел auth — Авторизация и управление сессией[/bold cyan]

[bold]Назначение:[/bold] Управление сессией пользователя и доступом в консольном клиенте.
[bold]Минимальная роль:[/bold] Developer (для CLI разрешён вход только роли Developer).

[bold]Список подкоманд:[/bold]
  [green]auth info[/green]                   - Показать эту справку
  [green]auth login[/green]                  - Вход разработчика по логину и паролю
  [green]auth status[/green]                 - Проверить текущую сессию и роль
  [green]auth logout[/green]                 - Завершить сессию
  [green]auth invite create [--role][/green] - Создать одноразовый код для биолога
  [green]auth invite list[/green]            - Список активных кодов доступа
  [green]auth invite revoke <id>[/green]    - Отозвать одноразовый код
  [green]auth sessions[/green]               - Список всех сессий
  [green]auth kick <session_id>[/green]     - Принудительно завершить сессию

[bold]Примеры:[/bold]
  auth login
  auth invite create
  auth invite list
  auth sessions
  auth kick 2
"""
    console.print(info_text)


@auth_app.command(name="login")
def auth_login() -> None:
    """Вход разработчика в систему по логину и паролю."""
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
            "Сессия активна:\n"
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


# ================== Инвайты (Developer) ==================


@invite_app.command(name="create")
def invite_create(
    role: str = typer.Option(
        "biologist", "--role", "-r", help="Роль для нового кода (biologist)"
    ),
) -> None:
    """Создать одноразовый код доступа (срок 10 минут)."""
    client = APIClient()
    resp = client.request(
        method="POST",
        url="/api/auth/invites",
        context="auth invite create",
        json_data={"role": role},
    )

    if isinstance(resp, dict):
        code = resp.get("code")
        expires_at = resp.get("expires_at")
        console.print(
            f"[green]Создан одноразовый код:[/green] [bold yellow]{code}[/bold yellow] "
            f"(роль: [bold cyan]{role}[/bold cyan], срок 10 мин, до {expires_at})"
        )


@invite_app.command(name="list")
def invite_list() -> None:
    """Список активных неиспользованных кодов доступа."""
    client = APIClient()
    resp = client.request(
        method="GET",
        url="/api/auth/invites",
        context="auth invite list",
    )

    if isinstance(resp, list):
        if not resp:
            console.print("[yellow]Нет активных кодов доступа.[/yellow]")
            return

        columns = [
            ("ID", "right"),
            ("Код", "center"),
            ("Роль", "left"),
            ("Создан", "center"),
            ("Истекает", "center"),
        ]
        rows: list[list[Any]] = []
        for inv in resp:
            rows.append(
                [
                    inv.get("id"),
                    f"[bold yellow]{inv.get('code')}[/bold yellow]",
                    inv.get("role"),
                    inv.get("created_at"),
                    inv.get("expires_at"),
                ]
            )

        table = format_table("Активные одноразовые коды", columns, rows)
        console.print(table)


@invite_app.command(name="revoke")
def invite_revoke(
    invite_id: int = typer.Argument(..., help="ID кода для отзыва"),
) -> None:
    """Отозвать неиспользованный код доступа."""
    client = APIClient()
    client.request(
        method="DELETE",
        url=f"/api/auth/invites/{invite_id}",
        context="auth invite revoke",
    )
    console.print(f"[green]Код доступа #{invite_id} успешно отозван.[/green]")


# ================== Сессии и кик (Developer) ==================


@auth_app.command(name="sessions")
def auth_sessions() -> None:
    """Список всех сессий пользователей."""
    client = APIClient()
    resp = client.request(
        method="GET",
        url="/api/auth/sessions",
        context="auth sessions",
    )

    if isinstance(resp, list):
        if not resp:
            console.print("[yellow]Сессии отсутствуют.[/yellow]")
            return

        columns = [
            ("ID", "right"),
            ("Клиент", "center"),
            ("Внешний ID", "left"),
            ("Логин", "left"),
            ("Роль", "center"),
            ("Статус", "center"),
            ("Вход", "center"),
            ("Активность", "center"),
            ("Выход", "center"),
        ]
        rows: list[list[Any]] = []
        for s in resp:
            is_active = s.get("is_active", False)
            status_str = (
                "[green]Активна[/green]" if is_active else "[dim]Завершена[/dim]"
            )
            rows.append(
                [
                    s.get("id"),
                    s.get("client_type"),
                    s.get("external_id"),
                    s.get("login"),
                    s.get("role"),
                    status_str,
                    s.get("authorized_at"),
                    s.get("last_active_at"),
                    s.get("logged_out_at") or "—",
                ]
            )

        table = format_table("Сессии пользователей", columns, rows)
        console.print(table)


@auth_app.command(name="kick")
def auth_kick(
    session_id: int = typer.Argument(..., help="ID сессии для завершения"),
) -> None:
    """Принудительное завершение сессии пользователя."""
    client = APIClient()
    client.request(
        method="POST",
        url=f"/api/auth/sessions/{session_id}/terminate",
        context="auth kick",
    )
    console.print(
        f"[green]Сессия #{session_id} успешно завершена (пользователь отключён).[/green]"
    )
