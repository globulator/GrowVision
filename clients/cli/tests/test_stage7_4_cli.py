import json

import httpx

from clients.cli.main import app


def test_cli_auth_rename(cli_runner, temp_session_file, mock_client_factory):
    """Критерий 4: Команда auth rename <session_id> <new_name>."""
    captured_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        if request.url.path == "/api/auth/sessions/2" and request.method == "PATCH":
            data = json.loads(request.content.decode())
            assert data["login"] == "Петр Иванов"
            return httpx.Response(
                200,
                json={
                    "id": 2,
                    "client_type": "telegram",
                    "external_id": "999888",
                    "login": "Петр Иванов",
                    "role": "biologist",
                    "is_active": True,
                    "authorized_at": "2026-11-22T10:00:00Z",
                    "last_active_at": "2026-11-22T10:30:00Z",
                    "logged_out_at": None,
                },
            )
        return httpx.Response(404)

    mock_client_factory(handler)

    result = cli_runner.invoke(app, ["auth", "rename", "2", "Петр Иванов"])
    assert result.exit_code == 0
    assert (
        "Пользователь сессии #2 успешно переименован в «Петр Иванов»" in result.stdout
    )

    assert len(captured_requests) == 1
    assert captured_requests[0].method == "PATCH"


def test_cli_unit_add_warnings(cli_runner, temp_session_file, mock_client_factory):
    """Критерий 3: unit add отображает предупреждения о занятых пинах."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/installations" and request.method == "POST":
            return httpx.Response(
                201,
                json={
                    "id": 2,
                    "name": "Установка 2",
                    "culture_name": "Томат",
                    "is_archived": False,
                    "light_gpio_pin": 4,
                    "flash_gpio_pin": 6,
                    "warnings": [
                        "⚠️ Пин 4 уже занят («Установка 1»). Реле будут общими."
                    ],
                },
            )
        return httpx.Response(404)

    mock_client_factory(handler)

    result = cli_runner.invoke(
        app,
        [
            "unit",
            "add",
            "--name",
            "Установка 2",
            "--culture",
            "Томат",
            "--light-pin",
            "4",
            "--flash-pin",
            "6",
        ],
    )
    assert result.exit_code == 0
    assert "⚠️ Пин 4 уже занят («Установка 1»). Реле будут общими." in result.stdout
    assert "Установка успешно добавлена" in result.stdout


def test_cli_unit_edit_warnings(cli_runner, temp_session_file, mock_client_factory):
    """Критерий 3: unit edit отображает предупреждения о занятых пинах."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/installations/2" and request.method == "PATCH":
            return httpx.Response(
                200,
                json={
                    "id": 2,
                    "name": "Установка 2",
                    "culture_name": "Томат",
                    "is_archived": False,
                    "light_gpio_pin": 4,
                    "flash_gpio_pin": 5,
                    "warnings": [
                        "⚠️ Пин 5 уже занят («Установка 1»). Реле будут общими."
                    ],
                },
            )
        return httpx.Response(404)

    mock_client_factory(handler)

    result = cli_runner.invoke(
        app,
        [
            "unit",
            "edit",
            "2",
            "--flash-pin",
            "5",
        ],
    )
    assert result.exit_code == 0
    assert "⚠️ Пин 5 уже занят («Установка 1»). Реле будут общими." in result.stdout
    assert "Параметры установки успешно обновлены" in result.stdout
