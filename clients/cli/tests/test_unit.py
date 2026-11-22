import json

import httpx

from clients.cli.main import app


def test_unit_info_command(cli_runner, temp_session_file):
    """Справка unit info."""
    result = cli_runner.invoke(app, ["unit", "info"])
    assert result.exit_code == 0
    assert "Раздел unit — Гидропонные установки" in result.stdout
    assert "unit list" in result.stdout
    assert "unit show" in result.stdout


def test_unit_list_empty(cli_runner, temp_session_file, mock_client_factory):
    """Вывод unit list при отсутствии установок."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    mock_client_factory(handler)
    result = cli_runner.invoke(app, ["unit", "list"])
    assert result.exit_code == 0
    assert "Установок пока нет." in result.stdout


def test_unit_list_and_role_projections(
    cli_runner, temp_session_file, mock_client_factory
):
    """Вывод таблицы unit list с колонками для Developer."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/installations"
        return httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "name": "Бокс №1",
                    "culture_name": "Базилик",
                    "last_area_display": "45.0 см²",
                    "is_archived": False,
                    "cameras": [{"id": 10, "auto_name": "Cam 1", "is_online": True}],
                    "light_gpio_pin": 12,
                    "flash_gpio_pin": 13,
                }
            ],
        )

    mock_client_factory(handler)
    result = cli_runner.invoke(app, ["unit", "list"])
    assert result.exit_code == 0
    assert "Бокс №1" in result.stdout
    assert "Базилик" in result.stdout
    assert "45.0 см²" in result.stdout
    assert "Light GPIO" in result.stdout


def test_unit_show_by_id_and_by_name(
    cli_runner, temp_session_file, mock_client_factory
):
    """Команда unit show по ID и по имени установки."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/installations":
            return httpx.Response(
                200, json=[{"id": 5, "name": "Тестовый Бокс", "is_archived": False}]
            )
        if request.url.path == "/api/installations/5":
            return httpx.Response(
                200,
                json={
                    "id": 5,
                    "name": "Тестовый Бокс",
                    "culture_name": "Томат",
                    "is_archived": False,
                    "aruco_size_mm": 50.0,
                    "last_area_display": "100.0 см²",
                    "light_gpio_pin": 14,
                    "flash_gpio_pin": 15,
                    "cameras": [{"id": 2, "auto_name": "C270", "is_online": True}],
                },
            )
        return httpx.Response(404)

    mock_client_factory(handler)

    # 1. По ID
    res_id = cli_runner.invoke(app, ["unit", "show", "5"])
    assert res_id.exit_code == 0
    assert "Установка #5 — Тестовый Бокс" in res_id.stdout
    assert "50.0 мм" in res_id.stdout

    # 2. По имени
    res_name = cli_runner.invoke(app, ["unit", "show", "Тестовый Бокс"])
    assert res_name.exit_code == 0
    assert "Установка #5 — Тестовый Бокс" in res_name.stdout


def test_unit_show_all(cli_runner, temp_session_file, mock_client_factory):
    """Команда unit show all выводит все активные установки."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/installations"
        return httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "name": "Бокс 1",
                    "culture_name": "Мята",
                    "is_archived": False,
                },
                {
                    "id": 2,
                    "name": "Бокс 2",
                    "culture_name": "Салат",
                    "is_archived": False,
                },
            ],
        )

    mock_client_factory(handler)
    res = cli_runner.invoke(app, ["unit", "show", "all"])
    assert res.exit_code == 0
    assert "Установка #1 — Бокс 1" in res.stdout
    assert "Установка #2 — Бокс 2" in res.stdout


def test_unit_add_interactive_and_flags(
    cli_runner, temp_session_file, mock_client_factory
):
    """Команда unit add с флагами."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/installations" and request.method == "POST":
            data = json.loads(request.content.decode())
            assert data["name"] == "Новый Бокс"
            assert data["culture_name"] == "Укроп"
            assert data["light_gpio_pin"] == 20
            assert data["flash_gpio_pin"] == 21
            assert data["aruco_size_mm"] == 40.0
            return httpx.Response(201, json={"id": 10, **data, "is_archived": False})
        return httpx.Response(404)

    mock_client_factory(handler)
    res = cli_runner.invoke(
        app,
        [
            "unit",
            "add",
            "--name",
            "Новый Бокс",
            "--culture",
            "Укроп",
            "--light-pin",
            "20",
            "--flash-pin",
            "21",
            "--aruco-mm",
            "40",
        ],
    )
    assert res.exit_code == 0
    assert "Установка успешно добавлена" in res.stdout
    assert "Установка #10 — Новый Бокс" in res.stdout


def test_unit_edit_and_clear_aruco(cli_runner, temp_session_file, mock_client_factory):
    """Команда unit edit с флагом --clear-aruco."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/installations/1" and request.method == "PATCH":
            data = json.loads(request.content.decode())
            assert data["aruco_size_mm"] is None
            assert data["name"] == "Обновленный Бокс"
            return httpx.Response(
                200,
                json={
                    "id": 1,
                    "name": "Обновленный Бокс",
                    "culture_name": "Лук",
                    "aruco_size_mm": None,
                    "is_archived": False,
                },
            )
        return httpx.Response(404)

    mock_client_factory(handler)
    res = cli_runner.invoke(
        app, ["unit", "edit", "1", "--name", "Обновленный Бокс", "--clear-aruco"]
    )
    assert res.exit_code == 0
    assert "Параметры установки успешно обновлены" in res.stdout
    assert "не задан" in res.stdout

    # Запрет target all в unit edit
    res_all = cli_runner.invoke(app, ["unit", "edit", "all", "--name", "Test"])
    assert res_all.exit_code == 1
    assert "Массовое действие для данной команды запрещено" in res_all.output


def test_unit_archive_and_delete(cli_runner, temp_session_file, mock_client_factory):
    """Команды unit archive и unit delete с подтверждением -y."""

    def handler(request: httpx.Request) -> httpx.Response:
        if (
            request.url.path == "/api/installations/1/archive"
            and request.method == "POST"
        ):
            return httpx.Response(
                200, json={"id": 1, "name": "Архивированный Бокс", "is_archived": True}
            )
        if request.url.path == "/api/installations/1" and request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(404)

    mock_client_factory(handler)

    # 1. Archive
    res_arch = cli_runner.invoke(app, ["unit", "archive", "1", "-y"])
    assert res_arch.exit_code == 0
    assert "переведена в архив" in res_arch.stdout

    # 2. Delete
    res_del = cli_runner.invoke(app, ["unit", "delete", "1", "-y"])
    assert res_del.exit_code == 0
    assert "Установка #1 успешно удалена" in res_del.stdout


def test_unit_delay_and_options(cli_runner, temp_session_file, mock_client_factory):
    """Настройка задержки съёмки через unit add, unit edit и unit delay."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/installations" and request.method == "POST":
            data = json.loads(request.content.decode())
            assert data["capture_delay_sec"] == 2.0
            return httpx.Response(
                201,
                json={
                    "id": 10,
                    "name": "Новый Бокс",
                    "culture_name": "Шпинат",
                    "capture_delay_sec": 2.0,
                    "is_archived": False,
                },
            )
        if request.url.path == "/api/installations/1" and request.method == "PATCH":
            data = json.loads(request.content.decode())
            return httpx.Response(
                200,
                json={
                    "id": 1,
                    "name": "Бокс-1",
                    "culture_name": "Рукола",
                    "capture_delay_sec": data.get("capture_delay_sec"),
                    "is_archived": False,
                },
            )
        return httpx.Response(404)

    mock_client_factory(handler)

    # 1. unit add с --delay
    res_add = cli_runner.invoke(
        app,
        [
            "unit",
            "add",
            "--name",
            "Новый Бокс",
            "--culture",
            "Шпинат",
            "--light-pin",
            "17",
            "--flash-pin",
            "27",
            "--delay",
            "2.0",
        ],
    )
    assert res_add.exit_code == 0
    assert "2.0 с" in res_add.stdout

    # 2. unit edit с --delay
    res_edit = cli_runner.invoke(app, ["unit", "edit", "1", "--delay", "3.5"])
    assert res_edit.exit_code == 0
    assert "3.5 с" in res_edit.stdout

    # 3. unit edit с --clear-delay
    res_edit_clear = cli_runner.invoke(app, ["unit", "edit", "1", "--clear-delay"])
    assert res_edit_clear.exit_code == 0
    assert "по умолчанию (общая)" in res_edit_clear.stdout

    # 4. unit delay <target> <seconds>
    res_delay = cli_runner.invoke(app, ["unit", "delay", "1", "4.5"])
    assert res_delay.exit_code == 0
    assert "Задержка съёмки для установки #1 установлена: 4.5 с" in res_delay.stdout

    # 5. unit delay <target> --default
    res_delay_def = cli_runner.invoke(app, ["unit", "delay", "1", "--default"])
    assert res_delay_def.exit_code == 0
    assert "общая системная" in res_delay_def.stdout

    # 6. unit delay валидация
    res_delay_bad = cli_runner.invoke(app, ["unit", "delay", "1", "35.0"])
    assert res_delay_bad.exit_code == 1
    assert "от 0.0 до 30.0" in res_delay_bad.output
