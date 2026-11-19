import re
from pathlib import Path

from shared.version import get_app_version


def test_get_app_version_with_git():
    """Проверяет получение версии при наличии Git-репозитория."""
    version = get_app_version()
    # Ожидается формат: X.Y.Z-<7-значный хэш>
    pattern = r"^\d+\.\d+\.\d+-[0-9a-f]{7,40}$"
    assert re.match(pattern, version), f"Неожиданный формат версии: {version}"


def test_get_app_version_without_git(tmp_path: Path):
    """Проверяет получение версии без каталога .git (архив)."""
    version_file = tmp_path / "VERSION"
    version_file.write_text("0.1.0\n", encoding="utf-8")

    version = get_app_version(repo_root=tmp_path)
    assert version == "0.1.0"


def test_get_app_version_missing_version_file(tmp_path: Path):
    """Проверяет дефолтное значение при отсутствии файла VERSION."""
    version = get_app_version(repo_root=tmp_path)
    assert version == "0.0.0"
