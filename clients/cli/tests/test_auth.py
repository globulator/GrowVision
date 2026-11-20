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
