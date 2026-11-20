import subprocess
from pathlib import Path


def test_strict_isolation_forbidden_imports():
    """Проверка строгой изоляции edge_agent: никаких импортов из server и clients."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    edge_agent_dir = repo_root / "edge_agent"

    # Разбиваем паттерн на части, чтобы grep не матчил сам себя
    pattern = (
        "from "
        + "server"
        + r"\|from "
        + "clients"
        + r"\|import "
        + "server"
        + r"\|import "
        + "clients"
    )

    cmd = [
        "grep",
        "-rn",
        "--include=*.py",
        f"--exclude={Path(__file__).name}",
        "--exclude-dir=__pycache__",
        pattern,
        str(edge_agent_dir),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert result.returncode == 1, (
        f"Обнаружены запрещенные импорты из server/clients:\n{result.stdout}"
    )
    assert result.stdout.strip() == ""
