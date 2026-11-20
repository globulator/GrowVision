from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from clients.cli.config import Config


@pytest.fixture
def cli_runner() -> CliRunner:
    """Typer CLI runner для запуска команд."""
    return CliRunner()


@pytest.fixture
def temp_session_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Изолирует файл сессии ~/.growvision_cli.json."""
    session_file = tmp_path / "test_growvision_cli.json"
    monkeypatch.setenv("GROWVISION_SESSION_FILE", str(session_file))
    monkeypatch.setenv("SERVER_URL", "http://testserver:8000")
    monkeypatch.setenv("CLIENT_API_KEY", "test-key-123")
    monkeypatch.setenv("CLI_DEBUG", "0")
    monkeypatch.setenv("COLUMNS", "220")

    # Обновляем ширину консолей rich
    import clients.cli.formatting as fmt

    fmt.console = fmt.Console(width=220)
    fmt.err_console = fmt.Console(stderr=True, width=220)

    return session_file


@pytest.fixture
def mock_client_factory(
    temp_session_file: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[[Callable[[httpx.Request], httpx.Response]], Config]:
    """Фабрика для подмены HTTP-транспорта в тестах."""

    def _setup_mock(handler: Callable[[httpx.Request], httpx.Response]):
        transport = httpx.MockTransport(handler)
        monkeypatch.setattr(
            "clients.cli.client.APIClient._get_client",
            lambda self, timeout: httpx.Client(
                base_url=self.config.server_url,
                headers=self.headers,
                timeout=timeout,
                transport=transport,
            ),
        )

    return _setup_mock
