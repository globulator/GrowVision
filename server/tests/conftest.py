import sys
from pathlib import Path

# Обеспечиваем доступность корневых пакетов server и shared при любом способе вызова pytest
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
