"""Тесты этапа 12 для CLI: 1/3 RGB пинов в unit add/edit/show и --color в light main/flash."""

import json

import httpx

from clients.cli.main import app


def test_cli_unit_add_edit_show_rgb_pins(
    cli_runner, temp_session_file, mock_client_factory
):
    """unit add/edit принимают 3 уникальных пина, отклоняют дубли/пересечения, unit show выводит RGB и HEX."""
    sent_payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/installations" and request.method == "POST":
            body = json.loads(request.content.decode())
            sent_payloads.append(body)
            return httpx.Response(
                201,
                json={
                    "id": 7,
                    "name": body["name"],
                    "culture_name": body["culture_name"],
                    "light_gpio_pin": body["light_gpio_pin"],
                    "flash_gpio_pin": body["flash_gpio_pin"],
                    "light_is_rgb": True,
                    "flash_is_rgb": False,
                    "light_color": "#00FFAA",
                    "flash_color": "#FFFFFF",
                    "light_state": True,
                    "flash_state": False,
                    "is_archived": False,
                },
            )
        if request.url.path == "/api/installations/7" and request.method == "PATCH":
            body = json.loads(request.content.decode())
            sent_payloads.append(body)
            return httpx.Response(
                200,
                json={
                    "id": 7,
                    "name": "RGB CLI",
                    "culture_name": "Салат",
                    "light_gpio_pin": body.get("light_gpio_pin", "17,27,22"),
                    "flash_gpio_pin": body.get("flash_gpio_pin", 26),
                    "light_is_rgb": True,
                    "flash_is_rgb": True,
                    "light_color": "#00FFAA",
                    "flash_color": "#FF00FF",
                    "is_archived": False,
                },
            )
        if request.url.path == "/api/installations" and request.method == "GET":
            return httpx.Response(200, json=[{"id": 7, "name": "RGB CLI"}])
        if request.url.path == "/api/installations/7" and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "id": 7,
                    "name": "RGB CLI",
                    "culture_name": "Салат",
                    "light_gpio_pin": "17,27,22",
                    "flash_gpio_pin": 26,
                    "light_is_rgb": True,
                    "flash_is_rgb": False,
                    "light_color": "#00FFAA",
                    "flash_color": "#FFFFFF",
                    "light_state": True,
                    "flash_state": False,
                    "is_archived": False,
                },
            )
        return httpx.Response(404)

    mock_client_factory(handler)

    # 1. Ошибка при дубле внутри RGB
    res_dup = cli_runner.invoke(
        app,
        [
            "unit",
            "add",
            "--name",
            "Bad",
            "--culture",
            "Салат",
            "--light-pin",
            "17,17,22",
            "--flash-pin",
            "26",
        ],
    )
    assert res_dup.exit_code == 1

    # 2. Ошибка при пересечении света и вспышки
    res_overlap = cli_runner.invoke(
        app,
        [
            "unit",
            "add",
            "--name",
            "Bad2",
            "--culture",
            "Салат",
            "--light-pin",
            "17,27,22",
            "--flash-pin",
            "22",
        ],
    )
    assert res_overlap.exit_code == 1

    # 3. Успешное создание RGB
    res_ok = cli_runner.invoke(
        app,
        [
            "unit",
            "add",
            "--name",
            "RGB CLI",
            "--culture",
            "Салат",
            "--light-pin",
            "17, 27, 22",
            "--flash-pin",
            "26",
        ],
    )
    assert res_ok.exit_code == 0
    assert sent_payloads[0]["light_gpio_pin"] == "17,27,22"
    assert sent_payloads[0]["flash_gpio_pin"] == 26
    assert "RGB" in res_ok.stdout
    assert "#00FFAA" in res_ok.stdout

    # 4. Успешный unit edit на 3 пина
    res_edit = cli_runner.invoke(
        app,
        ["unit", "edit", "7", "--flash-pin", "5,6,13"],
    )
    assert res_edit.exit_code == 0
    assert sent_payloads[1]["flash_gpio_pin"] == "5,6,13"

    # 5. unit show отображает RGB и HEX
    res_show = cli_runner.invoke(app, ["unit", "show", "7"])
    assert res_show.exit_code == 0
    assert "17,27,22" in res_show.stdout
    assert "RGB" in res_show.stdout
    assert "#00FFAA" in res_show.stdout


def test_cli_light_color_option(cli_runner, temp_session_file, mock_client_factory):
    """light main и light flash поддерживают --color (с state и без state) и отклоняют неверный HEX."""
    requests_log: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/installations":
            return httpx.Response(
                200, json=[{"id": 1, "name": "Бокс 1", "is_archived": False}]
            )
        if request.url.path in ("/api/control/light/main", "/api/control/light/flash"):
            body = json.loads(request.content.decode())
            requests_log.append((request.url.path, body))
            return httpx.Response(
                200,
                json={
                    "status": "done",
                    "result_message": "OK",
                    "color": body.get("color", "#FF8800"),
                    "is_rgb": True,
                },
            )
        return httpx.Response(404)

    mock_client_factory(handler)

    # 1. Только --color (без state)
    r1 = cli_runner.invoke(app, ["light", "main", "1", "--color", "ff8800"])
    assert r1.exit_code == 0
    assert requests_log[-1] == (
        "/api/control/light/main",
        {"target": 1, "color": "#FF8800"},
    )
    assert "#FF8800" in r1.stdout

    # 2. state + --color на вспышке
    r2 = cli_runner.invoke(app, ["light", "flash", "1", "on", "--color", "#00FFAA"])
    assert r2.exit_code == 0
    assert requests_log[-1] == (
        "/api/control/light/flash",
        {"target": 1, "state": True, "color": "#00FFAA"},
    )
    assert "#00FFAA" in r2.stdout

    # 3. on/off без --color не сбрасывает цвет (color не передаётся)
    r3 = cli_runner.invoke(app, ["light", "main", "1", "off"])
    assert r3.exit_code == 0
    assert requests_log[-1] == (
        "/api/control/light/main",
        {"target": 1, "state": False},
    )

    # 4. Некорректный HEX -> выход 1
    r_bad_hex = cli_runner.invoke(app, ["light", "main", "1", "--color", "XYZ"])
    assert r_bad_hex.exit_code == 1

    # 5. --color с target=all -> выход 1
    r_all_color = cli_runner.invoke(app, ["light", "main", "all", "--color", "#FF0000"])
    assert r_all_color.exit_code == 1
