import json

import httpx

from clients.cli.main import app


def test_auth_info_command(cli_runner, temp_session_file):
    """Справка auth info."""
    result = cli_runner.invoke(app, ["auth", "info"])
    assert result.exit_code == 0
    assert "Раздел auth — Авторизация и управление сессией" in result.stdout
    assert "auth login" in result.stdout
    assert "auth status" in result.stdout


def test_auth_login_successful(cli_runner, temp_session_file, mock_client_factory):
    """Успешный 2-шаговый auth login со скрытым паролем и заголовками сессии."""
    captured_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        if request.url.path == "/api/auth/login":
            data = json.loads(request.content.decode())
            assert data["client_type"] == "cli"
            assert data["external_id"].startswith("cli_")
            assert data["login"] == "developer"
            assert data["password"] == "devpass"
            return httpx.Response(200, json={"role": "developer", "login": "developer"})
        return httpx.Response(404)

    mock_client_factory(handler)

    result = cli_runner.invoke(app, ["auth", "login"], input="developer\ndevpass\n")
    assert result.exit_code == 0
    assert "Авторизация успешна. Логин: developer, Роль: developer" in result.stdout

    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert req.headers["X-Client-Key"] == "test-key-123"
    assert req.headers["X-Client-Type"] == "cli"
    assert req.headers["X-External-Id"].startswith("cli_")


def test_auth_status_command(cli_runner, temp_session_file, mock_client_factory):
    """Команда auth status."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/me":
            return httpx.Response(
                200,
                json={
                    "login": "biologist",
                    "role": "biologist",
                    "authorized_at": "2026-11-20T10:00:00Z",
                    "last_active_at": "2026-11-20T10:05:00Z",
                },
            )
        return httpx.Response(404)

    mock_client_factory(handler)

    result = cli_runner.invoke(app, ["auth", "status"])
    assert result.exit_code == 0
    assert "Логин: biologist" in result.stdout
    assert "Роль: biologist" in result.stdout


def test_auth_logout_command(cli_runner, temp_session_file, mock_client_factory):
    """Команда auth logout."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/logout":
            return httpx.Response(200, json={"detail": "OK"})
        return httpx.Response(404)

    mock_client_factory(handler)

    result = cli_runner.invoke(app, ["auth", "logout"])
    assert result.exit_code == 0
    assert "Сессия завершена. Текущая роль: Гость." in result.stdout


def test_auth_invite_create_list_revoke(
    cli_runner, temp_session_file, mock_client_factory
):
    """Тест команд auth invite create, list, revoke."""
    invites_db = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth/invites" and request.method == "POST":
            data = json.loads(request.content.decode())
            inv = {
                "id": 10,
                "code": "CODE1234",
                "role": data.get("role", "biologist"),
                "created_at": "2026-11-22T21:00:00Z",
                "expires_at": "2026-11-22T21:10:00Z",
            }
            invites_db.append(inv)
            return httpx.Response(200, json=inv)
        if path == "/api/auth/invites" and request.method == "GET":
            return httpx.Response(200, json=invites_db)
        if path == "/api/auth/invites/10" and request.method == "DELETE":
            invites_db.clear()
            return httpx.Response(200, json={"status": "revoked"})
        return httpx.Response(404)

    mock_client_factory(handler)

    # 1. create
    res_create = cli_runner.invoke(app, ["auth", "invite", "create"])
    assert res_create.exit_code == 0
    assert "CODE1234" in res_create.stdout
    assert "biologist" in res_create.stdout

    # 2. list
    res_list = cli_runner.invoke(app, ["auth", "invite", "list"])
    assert res_list.exit_code == 0
    assert "CODE1234" in res_list.stdout

    # 3. revoke
    res_revoke = cli_runner.invoke(app, ["auth", "invite", "revoke", "10"])
    assert res_revoke.exit_code == 0
    assert "Код доступа #10 успешно отозван" in res_revoke.stdout


def test_auth_sessions_and_kick(cli_runner, temp_session_file, mock_client_factory):
    """Тест команд auth sessions и auth kick."""
    sessions_db = [
        {
            "id": 5,
            "client_type": "telegram",
            "external_id": "999888",
            "login": "Анна Биолог",
            "role": "biologist",
            "is_active": True,
            "authorized_at": "2026-11-22T19:00:00Z",
            "last_active_at": "2026-11-22T19:20:00Z",
            "logged_out_at": None,
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth/sessions" and request.method == "GET":
            return httpx.Response(200, json=sessions_db)
        if path == "/api/auth/sessions/5/terminate" and request.method == "POST":
            sessions_db[0]["is_active"] = False
            return httpx.Response(200, json={"status": "terminated"})
        return httpx.Response(404)

    mock_client_factory(handler)

    # 1. sessions
    res_sess = cli_runner.invoke(app, ["auth", "sessions"])
    assert res_sess.exit_code == 0
    assert "Анна Биолог" in res_sess.stdout
    assert "Активна" in res_sess.stdout

    # 2. kick
    res_kick = cli_runner.invoke(app, ["auth", "kick", "5"])
    assert res_kick.exit_code == 0
    assert "Сессия #5 успешно завершена" in res_kick.stdout
    assert sessions_db[0]["is_active"] is False
