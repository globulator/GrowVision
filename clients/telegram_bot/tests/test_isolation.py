import ast
from pathlib import Path

import pytest

FORBIDDEN_MODULES = {"server", "shared", "edge_agent", "clients.cli"}


def test_strict_isolation_forbidden_imports():
    """Тест строгой изоляции: Telegram-бот не должен импортировать модули из server, shared, edge_agent, clients.cli."""
    bot_dir = Path(__file__).resolve().parent.parent

    # Сканируем все .py файлы в каталоге clients/telegram_bot, исключая .venv
    py_files = [p for p in bot_dir.rglob("*.py") if ".venv" not in p.parts]
    assert len(py_files) > 5, (
        "Должно быть найдено достаточное количество исходных файлов бота"
    )

    violations = []
    for py_file in py_files:
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except SyntaxError as e:
            pytest.fail(f"Синтаксическая ошибка в {py_file}: {e}")

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name.split(".")[0]
                    if name in FORBIDDEN_MODULES:
                        violations.append(
                            f"{py_file.name}:{node.lineno} import {alias.name}"
                        )
            elif isinstance(node, ast.ImportFrom) and node.module:
                mod = node.module.split(".")[0]
                if mod in FORBIDDEN_MODULES or node.module == "clients.cli":
                    violations.append(
                        f"{py_file.name}:{node.lineno} from {node.module} import ..."
                    )

    assert not violations, (
        "Обнаружены запрещенные архитектурные импорты:\n" + "\n".join(violations)
    )
