import json

import httpx

from clients.cli.main import app


def test_light_info_command(cli_runner, temp_session_file):
    """Справка light info."""
    result = cli_runner.invoke(app, ["light", "info"])
    assert result.exit_code == 0
    assert "Раздел light — Управление светом и вспышкой" in result.stdout
    assert "light main" in result.stdout
    assert "light flash" in result.stdout
    assert "light test" in result.stdout


def test_light_main_and_flash(cli_runner, temp_session_file, mock_client_factory):
    """Команды light main и light flash."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/control/light/main":
            data = json.loads(request.content.decode())
            assert data["target"] == 1
            assert data["state"] is True
            return httpx.Response(
                200, json={"status": "done", "result_message": "Основной свет включен"}
            )
        if request.url.path == "/api/control/light/flash":
            data = json.loads(request.content.decode())
            assert data["target"] == "all"
            assert data["state"] is False
            return httpx.Response(
                200, json={"status": "done", "result_message": "Вспышка выключена"}
            )
        if request.url.path == "/api/installations":
            return httpx.Response(
                200,
                json=[{"id": 1, "is_archived": False}, {"id": 2, "is_archived": False}],
            )
        return httpx.Response(404)

    mock_client_factory(handler)

    res_main = cli_runner.invoke(app, ["light", "main", "1", "on"])
    assert res_main.exit_code == 0
    assert "Основной свет включен" in res_main.stdout

    res_flash = cli_runner.invoke(app, ["light", "flash", "all", "off"])
    assert res_flash.exit_code == 0
    assert "Вспышка выключена" in res_flash.stdout


def test_light_test_relay(cli_runner, temp_session_file, mock_client_factory):
    """Команда light test."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/control/relay-test"
        data = json.loads(request.content.decode())
        assert data["target"] == 2
        return httpx.Response(
            200, json={"status": "done", "result_message": "Тест реле завершен успешно"}
        )

    mock_client_factory(handler)

    res = cli_runner.invoke(app, ["light", "test", "2"])
    assert res.exit_code == 0
    assert "Тест реле завершен успешно" in res.stdout


def test_light_invalid_state(cli_runner, temp_session_file):
    """Ошибка при некорректном значении state."""
    res = cli_runner.invoke(app, ["light", "main", "1", "not_a_state"])
    assert res.exit_code == 1
    assert "Некорректное состояние" in res.stdout
