import subprocess
from pathlib import Path


def get_app_version(repo_root: Path | None = None) -> str:
    """Возвращает версию приложения.

    Читает файл VERSION из корня репозитория. Если доступен Git и присутствует
    каталог .git, добавляет короткий хэш текущего коммита (напр. '0.0.0-be84763').
    В противном случае (архив без .git) возвращает '0.0.0'.
    """
    if repo_root is None:
        repo_root = Path(__file__).resolve().parent.parent

    version_file = repo_root / "VERSION"
    base_version = "0.0.0"
    if version_file.is_file():
        base_version = version_file.read_text(encoding="utf-8").strip()

    git_dir = repo_root / ".git"
    if not git_dir.exists():
        return base_version

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if result.returncode == 0:
            short_hash = result.stdout.strip()
            if short_hash:
                return f"{base_version}-{short_hash}"
    except (subprocess.SubprocessError, OSError):
        return base_version

    return base_version
