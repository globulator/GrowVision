import subprocess

import httpx

from clients.cli.main import app


def test_version_command(cli_runner, temp_session_file, mock_client_factory):
    """Команда version."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/version"
        return httpx.Response(200, json={"server_version": "0.1.0-abc1234"})

    mock_client_factory(handler)
    res = cli_runner.invoke(app, ["version"])
    assert res.exit_code == 0
    assert "GrowVision server: 0.1.0-abc1234" in res.stdout


def test_matrix_http_errors(cli_runner, temp_session_file, mock_client_factory):
    """Проверка вывода ошибок по матрице CLI_SPEC.md разд. 4.1."""
    test_cases = [
        (401, {"detail": "Сессия истекла"}, "Выполните вход: auth login"),
        (401, {"detail": "Неверный X-Client-Key"}, "Проверьте CLIENT_API_KEY"),
        (
            403,
            {"detail": "Недостаточно прав"},
            "Эта операция доступна только для роли Developer",
        ),
        (
            404,
            {"detail": "Установка не найдена"},
            "Проверьте список доступных объектов через unit list",
        ),
        (409, {"detail": "Установка в архиве"}, "Используйте unit archive"),
        (
            422,
            {"detail": "У установки несколько камер, укажите camera_id: 1, 2"},
            "Укажите --camera-id <id>",
        ),
        (429, {"detail": "Слишком много попыток"}, "Повторите попытку"),
        (503, {"detail": "Raspberry Pi офлайн"}, "Проверьте питание Raspberry Pi 5"),
        (
            504,
            {"detail": "Превышено время ожидания ответа Raspberry Pi"},
            "фактический результат неизвестен",
        ),
        (
            500,
            {"detail": "Internal Crash"},
            "Сервер вернул внутреннюю ошибку (HTTP 500)",
        ),
    ]

    for status_code, body, expected_hint in test_cases:

        def make_handler(sc: int, b: dict):
            def handler(request: httpx.Request) -> httpx.Response:
                headers = {"Retry-After": "60"} if sc == 429 else {}
                return httpx.Response(sc, json=b, headers=headers)

            return handler

        mock_client_factory(make_handler(status_code, body))
        res = cli_runner.invoke(app, ["unit", "show", "1"])
        assert res.exit_code == 1
        assert expected_hint in res.output
        if status_code != 500:
            assert body["detail"] in res.output


def test_network_connect_error(cli_runner, temp_session_file, monkeypatch):
    """Сетевая ошибка: ConnectError."""

    def mock_get_client(self, timeout):
        def error_handler(request):
            raise httpx.ConnectError("Connection refused")

        return httpx.Client(transport=httpx.MockTransport(error_handler))

    monkeypatch.setattr("clients.cli.client.APIClient._get_client", mock_get_client)
    res = cli_runner.invoke(app, ["unit", "list"])
    assert res.exit_code == 1
    assert "Не удалось подключиться к серверу по адресу" in res.output
    assert "Убедитесь, что сервис server запущен" in res.output


def test_network_timeout_error(cli_runner, temp_session_file, monkeypatch):
    """Сетевая ошибка: TimeoutException."""

    def mock_get_client(self, timeout):
        def timeout_handler(request):
            raise httpx.TimeoutException("Read timed out")

        return httpx.Client(transport=httpx.MockTransport(timeout_handler))

    monkeypatch.setattr("clients.cli.client.APIClient._get_client", mock_get_client)
    res = cli_runner.invoke(app, ["unit", "list"])
    assert res.exit_code == 1
    assert "Сервер не ответил за" in res.output
    assert "фактическое выполнение на Raspberry Pi неизвестно" in res.output


def test_verbose_flag(cli_runner, temp_session_file, mock_client_factory):
    """Флаг --verbose выводит HTTP-запрос и ответ."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"server_version": "0.1.0"})

    mock_client_factory(handler)
    res = cli_runner.invoke(app, ["--verbose", "version"])
    assert res.exit_code == 0
    assert "> GET http://testserver:8000/api/version" in res.output
    assert "< HTTP 200" in res.output


def test_strict_isolation_forbidden_imports():
    """Критерий 8: Строгий запрет импортов из server/, shared/, edge_agent/."""
    pattern = (
        "from "
        + "server"
        + "|"
        + "from "
        + "shared"
        + "|"
        + "from "
        + "edge_agent"
        + "|"
        + "import "
        + "server"
        + "|"
        + "import "
        + "shared"
    )
    cmd = [
        "grep",
        "-rn",
        "--include=*.py",
        "--exclude-dir=__pycache__",
        "--exclude=test_errors_and_verbose.py",
        pattern,
        "clients/cli",
    ]
    res = subprocess.run(cmd, check=False, capture_output=True, text=True)
    assert res.returncode == 1, f"Обнаружены запрещенные импорты:\n{res.stdout}"
    assert res.stdout.strip() == ""
