import httpx
import pytest

from clients.telegram_bot.config import BotConfig
from clients.telegram_bot.tests.conftest import create_mock_api_client
from clients.telegram_bot.utils.errors import ApiError


@pytest.mark.asyncio
async def test_api_client_headers(bot_config: BotConfig):
    """Проверка обязательных служебных заголовков во всех запросах."""
    captured_headers = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_headers.update(dict(request.headers))
        return httpx.Response(200, json={"status": "ok"})

    client = create_mock_api_client(bot_config, handler)
    try:
        await client.get_installations(user_id=12345678)
        assert captured_headers.get("x-client-key") == bot_config.client_api_key
        assert captured_headers.get("x-client-type") == "telegram"
        assert captured_headers.get("x-external-id") == "12345678"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_auth_me_returns_none_on_401(bot_config: BotConfig):
    """get_me возвращает None при HTTP 401 (Гость)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "Unauthorized"})

    client = create_mock_api_client(bot_config, handler)
    try:
        res = await client.get_me(user_id=999)
        assert res is None
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_login_and_logout(bot_config: BotConfig):
    """Проверка вызова логина и логаута с проверкой поля login."""
    captured_payloads: dict[str, dict] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        if request.url.path == "/api/auth/login":
            captured_payloads["login"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200, json={"status": "ok", "role": "biologist", "login": "Ivan"}
            )
        if request.url.path == "/api/auth/logout":
            return httpx.Response(200, json={"status": "logged_out"})
        return httpx.Response(404)

    client = create_mock_api_client(bot_config, handler)
    try:
        login_res = await client.login(user_id=777, username="Ivan", password="secret")
        assert login_res["role"] == "biologist"
        assert captured_payloads["login"]["login"] == "Ivan"
        assert "username" not in captured_payloads["login"]

        logout_res = await client.logout(user_id=777)
        assert logout_res["status"] == "logged_out"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_control_commands_and_snap(bot_config: BotConfig):
    """Проверка команд управления светом, реле и съёмкой с проверкой поля target."""
    captured_bodies: dict[str, dict] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        path = request.url.path
        if path == "/api/control/light/main":
            captured_bodies["light"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200, json={"status": "done", "result_message": "Свет включен"}
            )
        if path == "/api/control/relay-test":
            captured_bodies["relay"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200, json={"status": "done", "result_message": "Тест реле завершен"}
            )
        if path == "/api/control/snap":
            captured_bodies["snap"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "status": "done",
                    "result_message": "Снимок готов",
                    "captures": [
                        {
                            "installation_id": 1,
                            "camera_hardware_id": "usb-cam-1",
                            "raw_url": "/media/raw.jpg",
                            "annotated_url": "/media/ann.jpg",
                            "total_area_cm2": 42.5,
                        }
                    ],
                },
            )
        return httpx.Response(404)

    client = create_mock_api_client(bot_config, handler)
    try:
        r1 = await client.control_light_main(user_id=1, target=10, state=True)
        assert r1["status"] == "done"
        assert captured_bodies["light"]["target"] == 10
        assert "installation_id" not in captured_bodies["light"]

        r2 = await client.control_relay_test(user_id=1, target=None)
        assert r2["status"] == "done"
        assert captured_bodies["relay"]["target"] == "all"
        assert "installation_id" not in captured_bodies["relay"]

        r3 = await client.take_snap(user_id=1, target=1)
        assert r3["status"] == "done"
        assert captured_bodies["snap"]["target"] == 1
        assert "installation_id" not in captured_bodies["snap"]
        assert len(r3["captures"]) == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_api_error_raised_on_failure(bot_config: BotConfig):
    """Проверка генерации ApiError с извлечением detail."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "Установка уже существует"})

    client = create_mock_api_client(bot_config, handler)
    try:
        with pytest.raises(ApiError) as excinfo:
            await client.create_installation(user_id=1, data={"name": "Duplicate"})
        assert excinfo.value.status_code == 409
        assert "Установка уже существует" in excinfo.value.detail
    finally:
        await client.close()
