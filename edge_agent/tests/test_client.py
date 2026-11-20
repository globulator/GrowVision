import json

import httpx

from edge_agent.client import EdgeAPIClient
from shared.schemas import (
    CaptureMeta,
    CommandResult,
    HeartbeatHardware,
    HeartbeatRequest,
    HeartbeatSoftware,
)


def test_heartbeat_success():
    """Проверка отправки Heartbeat и разбора ответа."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/edge/heartbeat"
        assert request.headers["X-Edge-Key"] == "secret-key"
        body = json.loads(request.content)
        assert body["software"]["edge_version"] == "0.1.0"
        return httpx.Response(
            200,
            json={
                "capture_interval_hours": 2,
                "debug_mode": False,
                "active_installations": [
                    {
                        "id": 1,
                        "light_gpio_pin": 17,
                        "flash_gpio_pin": 27,
                        "camera_hardware_ids": ["cam-1"],
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    client = EdgeAPIClient("http://test", "secret-key", transport=transport)

    req = HeartbeatRequest(
        software=HeartbeatSoftware(
            edge_version="0.1.0",
            python_version="3.12.0",
            model_name="m.onnx",
        ),
        hardware=HeartbeatHardware(
            board_model="RPi 5",
            os_info="Linux",
            uptime_sec=100,
            cpu_temp_c=45.0,
            cpu_usage_percent=10.0,
            ram_used_mb=500,
            ram_total_mb=4000,
            disk_free_gb=15.0,
            gpio_mode="mock",
        ),
        offline_queue_count=0,
        detected_cameras=[],
    )

    resp = client.send_heartbeat(req)
    assert resp is not None
    assert resp.capture_interval_hours == 2
    assert len(resp.active_installations) == 1
    assert resp.active_installations[0].light_gpio_pin == 17


def test_claim_command_success():
    """Проверка забора команды из очереди."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/edge/commands/claim"
        assert request.headers["X-Edge-Key"] == "secret-key"
        return httpx.Response(
            200,
            json=[
                {
                    "id": 101,
                    "command_type": "LIGHT_MAIN",
                    "is_global": False,
                    "installation": {
                        "id": 1,
                        "light_gpio_pin": 17,
                        "flash_gpio_pin": 27,
                        "camera_hardware_ids": ["cam-1"],
                    },
                    "payload": {"state": True},
                }
            ],
        )

    transport = httpx.MockTransport(handler)
    client = EdgeAPIClient("http://test", "secret-key", transport=transport)

    cmds = client.claim_command()
    assert len(cmds) == 1
    assert cmds[0].id == 101
    assert cmds[0].command_type == "LIGHT_MAIN"
    assert cmds[0].installation.light_gpio_pin == 17


def test_send_command_result_retries_on_network_error():
    """Проверка повторных попыток send_command_result (до 3 раз)."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise httpx.ConnectError("Network is down")
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    client = EdgeAPIClient("http://test", "secret-key", transport=transport)

    res = CommandResult(status="done", result_message="OK")
    success = client.send_command_result(101, res, max_retries=3, retry_delay_sec=0.0)
    assert success is True
    assert call_count == 3


def test_send_command_result_conflict_409():
    """Проверка обработки 409 (команда ушла в таймаут)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "Command not in running state"})

    transport = httpx.MockTransport(handler)
    client = EdgeAPIClient("http://test", "secret-key", transport=transport)

    res = CommandResult(status="done", result_message="Выполнено успешно")
    success = client.send_command_result(101, res, max_retries=3, retry_delay_sec=0.0)
    assert success is False


def test_upload_capture_timeout_60s():
    """Проверка настройки таймаута загрузки кадра 60 секунд."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert "multipart/form-data" in request.headers["content-type"]
        return httpx.Response(201, json={"capture_id": 55})

    transport = httpx.MockTransport(handler)
    client = EdgeAPIClient(
        "http://test",
        "secret-key",
        upload_timeout=60.0,
        transport=transport,
    )
    assert client.upload_timeout == 60.0

    meta = CaptureMeta(
        installation_id=1,
        camera_hardware_id="cam-1",
        captured_at="2026-11-20T12:00:00Z",
        is_manual=True,
        command_id=101,
    )

    code, body = client.upload_capture(meta, b"fake-raw", b"fake-ann")
    assert code == 201
    assert body["capture_id"] == 55
