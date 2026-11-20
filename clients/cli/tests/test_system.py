import json

import httpx

from clients.cli.main import app


def test_system_info_command(cli_runner, temp_session_file):
    """Справка system info."""
    result = cli_runner.invoke(app, ["system", "info"])
    assert result.exit_code == 0
    assert "Раздел system — Состояние RPi и глобальные настройки" in result.stdout
    assert "system status" in result.stdout
    assert "system schedule" in result.stdout
    assert "system debug" in result.stdout


def test_system_status_online(cli_runner, temp_session_file, mock_client_factory):
    """Команда system status с реальной вложенной структурой software/hardware телеметрии."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/system/status"
        return httpx.Response(
            200,
            json={
                "server_version": "0.1.0-test",
                "rpi_connection": "online",
                "rpi_last_heartbeat_at": "2026-11-20T12:00:00Z",
                "settings": {"capture_interval_hours": 2, "debug_mode": True},
                "rpi_telemetry": {
                    "software": {
                        "edge_version": "0.1.0",
                        "python_version": "3.12.8",
                        "model_name": "GrowVision Edge",
                    },
                    "hardware": {
                        "board_model": "Raspberry Pi 5 Model B",
                        "os_info": "Linux Debian 12 (bookworm)",
                        "uptime_sec": 7320,
                        "cpu_temp_c": 42.5,
                        "cpu_usage_percent": 12.0,
                        "ram_used_mb": 512,
                        "ram_total_mb": 4096,
                        "disk_free_gb": 28.5,
                        "gpio_mode": "gpiod",
                    },
                    "offline_queue_count": 3,
                },
            },
        )

    mock_client_factory(handler)
    res = cli_runner.invoke(app, ["system", "status"])
    assert res.exit_code == 0
    assert "0.1.0-test" in res.stdout
    assert "online" in res.stdout
    assert "0.1.0" in res.stdout
    assert "3.12.8" in res.stdout
    assert "Raspberry Pi 5 Model B (Linux Debian 12 (bookworm))" in res.stdout
    assert "gpiod" in res.stdout
    assert "2 ч 2 мин" in res.stdout
    assert "42.5 °C" in res.stdout
    assert "12.0 %" in res.stdout
    assert "512 / 4096 МБ" in res.stdout
    assert "28.5 ГБ" in res.stdout
    assert "3" in res.stdout


def test_system_status_flat_fallback(
    cli_runner, temp_session_file, mock_client_factory
):
    """Команда system status с плоским словарем телеметрии (обратная совместимость)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "server_version": "0.1.0-flat",
                "rpi_connection": "online",
                "rpi_last_heartbeat_at": "2026-11-20T12:00:00Z",
                "settings": {"capture_interval_hours": 1, "debug_mode": False},
                "rpi_telemetry": {
                    "edge_version": "0.1.0",
                    "python_version": "3.12.8",
                    "model_name": "RPi-Flat",
                    "os_info": "Debian",
                    "gpio_mode": "mock",
                    "uptime_seconds": 3660,
                    "cpu_temp_c": 39.0,
                    "cpu_load_percent": 8.5,
                    "ram_used_mb": 256,
                    "ram_total_mb": 2048,
                    "disk_free_gb": 15.0,
                    "offline_queue_count": 0,
                },
            },
        )

    mock_client_factory(handler)
    res = cli_runner.invoke(app, ["system", "status"])
    assert res.exit_code == 0
    assert "RPi-Flat (Debian)" in res.stdout
    assert "1 ч 1 мин" in res.stdout
    assert "8.5 %" in res.stdout
    assert "39.0 °C" in res.stdout


def test_system_schedule_with_warning(
    cli_runner, temp_session_file, mock_client_factory
):
    """Команда system schedule с выводом предупреждения."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/system/settings"
        data = json.loads(request.content.decode())
        assert data["capture_interval_hours"] == 3
        return httpx.Response(
            200,
            json={
                "capture_interval_hours": 3,
                "debug_mode": False,
                "warning": "Смена интервала автосъёмки приведёт к неравномерному шагу таймлапса",
            },
        )

    mock_client_factory(handler)
    res = cli_runner.invoke(app, ["system", "schedule", "3"])
    assert res.exit_code == 0
    assert "Интервал автосъёмки успешно изменён на 3 ч" in res.stdout
    assert "Смена интервала автосъёмки приведёт к неравномерному шагу" in res.stdout


def test_system_debug_toggle(cli_runner, temp_session_file, mock_client_factory):
    """Команда system debug on/off."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/system/settings"
        data = json.loads(request.content.decode())
        assert data["debug_mode"] is True
        return httpx.Response(
            200, json={"capture_interval_hours": 1, "debug_mode": True}
        )

    mock_client_factory(handler)
    res = cli_runner.invoke(app, ["system", "debug", "on"])
    assert res.exit_code == 0
    assert "Режим отладки успешно включен" in res.stdout
