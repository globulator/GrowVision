import json

import httpx

from clients.cli.main import app


def test_camera_info_command(cli_runner, temp_session_file):
    """Справка camera info."""
    result = cli_runner.invoke(app, ["camera", "info"])
    assert result.exit_code == 0
    assert "Раздел camera — Камеры на Raspberry Pi" in result.stdout
    assert "camera list" in result.stdout
    assert "camera scan" in result.stdout


def test_camera_list(cli_runner, temp_session_file, mock_client_factory):
    """Команда camera list."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/cameras"
        return httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "auto_name": "Logitech C270",
                    "hardware_id": "usb-cam-1",
                    "is_online": True,
                    "installation_name": "Бокс №1",
                    "last_seen_at": "2026-11-20T12:00:00Z",
                }
            ],
        )

    mock_client_factory(handler)
    res = cli_runner.invoke(app, ["camera", "list"])
    assert res.exit_code == 0
    assert "Logitech C270" in res.stdout
    assert "usb-cam-1" in res.stdout
    assert "Установка «Бокс №1»" in res.stdout


def test_camera_scan_with_debug(cli_runner, temp_session_file, mock_client_factory):
    """Команда camera scan с блоком debug_info."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/cameras/scan"
        return httpx.Response(
            200,
            json={
                "command_id": 15,
                "status": "done",
                "cameras": [
                    {
                        "id": 1,
                        "auto_name": "USB Cam",
                        "hardware_id": "usb-01",
                        "installation_name": None,
                    }
                ],
                "debug_info": {
                    "command": {"id": 15, "command_type": "SCAN_CAMERAS"},
                    "log_line": "[INFO] USB scan found 1 cameras",
                },
            },
        )

    mock_client_factory(handler)
    res = cli_runner.invoke(app, ["camera", "scan"])
    assert res.exit_code == 0
    assert "Свободна — можно привязать" in res.stdout
    assert "DEBUG" in res.stdout
    assert "[INFO] USB scan found 1 cameras" in res.stdout


def test_camera_bind_and_unbind(cli_runner, temp_session_file, mock_client_factory):
    """Команды camera bind и camera unbind."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/installations":
            return httpx.Response(
                200, json=[{"id": 2, "name": "Бокс №2", "is_archived": False}]
            )
        if request.url.path == "/api/cameras":
            return httpx.Response(200, json=[{"id": 3, "hardware_id": "hw-cam-3"}])
        if request.url.path == "/api/cameras/3/bind":
            data = json.loads(request.content.decode())
            if data["installation_id"] == 2:
                return httpx.Response(
                    200, json={"id": 3, "installation_name": "Бокс №2"}
                )
            if data["installation_id"] is None:
                return httpx.Response(200, json={"id": 3, "installation_name": None})
        return httpx.Response(404)

    mock_client_factory(handler)

    # 1. Bind по hardware_id и имени установки
    res_bind = cli_runner.invoke(app, ["camera", "bind", "hw-cam-3", "Бокс №2"])
    assert res_bind.exit_code == 0
    assert "успешно привязана к установке «Бокс №2»" in res_bind.stdout

    # 2. Unbind по числовому ID
    res_unbind = cli_runner.invoke(app, ["camera", "unbind", "3"])
    assert res_unbind.exit_code == 0
    assert "успешно отвязана от установки" in res_unbind.stdout


def test_camera_scan_error_status(cli_runner, temp_session_file, mock_client_factory):
    """Команда camera scan при status == 'error' выводит ошибку в stderr и завершается с кодом 1."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "command_id": 16,
                "status": "error",
                "result_message": "Камера недоступна, попробуйте позже",
                "cameras": [],
            },
        )

    mock_client_factory(handler)
    res = cli_runner.invoke(app, ["camera", "scan"])
    assert res.exit_code == 1
    assert "Камера недоступна, попробуйте позже" in (res.stderr + res.stdout)
