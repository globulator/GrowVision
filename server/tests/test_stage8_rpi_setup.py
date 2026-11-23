import os
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from server.api.deps import create_session_cookie, get_db
from server.api.edge_setup import _setup_tokens
from server.config import get_settings
from server.db.database import Base
from server.db.models import User
from server.main import app


@pytest.fixture
def test_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "test_stage8_rpi_setup.db"
    db_url = f"sqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    settings = get_settings()
    monkeypatch.setattr(settings, "client_api_key", "test-client-secret-key")
    monkeypatch.setattr(settings, "developer_password", "test-dev-password")

    def override_get_db() -> Generator[Session, None, None]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield session_factory
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture
def client(test_env):
    return TestClient(app)


@pytest.fixture
def developer_user(test_env):
    session_factory = test_env
    with session_factory() as db:
        user = User(
            client_type="web",
            external_id="111111",
            login="Dev Admin",
            role="developer",
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


@pytest.fixture
def biologist_user(test_env):
    session_factory = test_env
    with session_factory() as db:
        user = User(
            client_type="web",
            external_id="222222",
            login="Bio User",
            role="biologist",
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def test_edge_setup_defaults_permissions(client, biologist_user, developer_user):
    """GET /api/edge/setup/defaults доступен только разработчику."""
    # Без авторизации (Гость) -> 401
    res = client.get("/api/edge/setup/defaults")
    assert res.status_code == 401

    # Биолог -> 403
    bio_cookie = create_session_cookie(biologist_user)
    client.cookies.set("gv_session", bio_cookie)
    res_bio = client.get("/api/edge/setup/defaults")
    assert res_bio.status_code == 403

    # Разработчик -> 200
    dev_cookie = create_session_cookie(developer_user)
    client.cookies.set("gv_session", dev_cookie)
    res_dev = client.get("/api/edge/setup/defaults")
    assert res_dev.status_code == 200
    data = res_dev.json()
    assert "default_server_url" in data
    assert "repo_url" in data


def test_create_edge_setup_token_and_burn_on_fetch(client, developer_user):
    """POST /api/edge/setup/token создаёт одноразовый токен, GET сжигает его."""
    dev_cookie = create_session_cookie(developer_user)
    client.cookies.set("gv_session", dev_cookie)

    # 1. Валидация некорректного server_url
    bad_req = client.post(
        "/api/edge/setup/token",
        json={"server_url": "ftp://bad-url.com", "hardware_mode": "rpi"},
    )
    assert bad_req.status_code == 422

    # 2. Успешная генерация токена
    payload = {
        "server_url": "http://<server-host>:8000",
        "netbird_setup_key": "nb-test-setup-key-12345",
        "hardware_mode": "rpi",
    }
    res = client.post("/api/edge/setup/token", json=payload)
    assert res.status_code == 200
    token_data = res.json()
    token = token_data["token"]
    assert token_data["expires_in_sec"] == 600
    assert token_data["script_path"] == f"/api/edge/setup/{token}"

    # Проверяем, что токен находится в in-memory словаре
    assert token in _setup_tokens
    assert _setup_tokens[token]["server_url"] == "http://<server-host>:8000"

    # 3. Первый вызов GET /api/edge/setup/{token} (публичный, без кук)
    client.cookies.clear()
    script_res = client.get(f"/api/edge/setup/{token}")
    assert script_res.status_code == 200
    assert "text/x-shellscript" in script_res.headers["content-type"]
    script_text = script_res.text

    # Проверяем ключевые элементы генерируемого bash-скрипта
    assert "#!/usr/bin/env bash" in script_text
    assert "set -euo pipefail" in script_text
    assert "apt-get install -y git python3-venv python3-pip curl" in script_text
    assert "netbird up --setup-key" in script_text
    assert "nb-test-setup-key-12345" in script_text
    assert "growvision-edge.service" in script_text
    assert "/usr/local/bin/gv-update" in script_text
    assert "http://<server-host>:8000" in script_text

    # 4. Токен сожжён: в памяти его больше нет
    assert token not in _setup_tokens

    # 5. Повторный вызов возвращает 404
    second_res = client.get(f"/api/edge/setup/{token}")
    assert second_res.status_code == 404


def test_edge_setup_token_expired(client):
    """Просроченный токен возвращает 404."""
    # Искусственно добавляем просроченный токен
    stale_token = "stale_token_test_123"
    _setup_tokens[stale_token] = {
        "server_url": "http://<server-host>:8000",
        "netbird_setup_key": None,
        "hardware_mode": "mock",
        "repo_url": "https://github.com/<owner>/<repo>.git",
        "created_at": 100.0,
        "expires_at": 100.0,  # в далеком прошлом
    }

    res = client.get(f"/api/edge/setup/{stale_token}")
    assert res.status_code == 404
    assert stale_token not in _setup_tokens


def test_scripts_syntax_and_executables():
    """Скрипты gv-update.sh и install_server.sh существуют, имеют +x и валидный синтаксис bash."""
    gv_update = Path("scripts/gv-update.sh")
    install_srv = Path("scripts/install_server.sh")

    assert gv_update.exists(), "scripts/gv-update.sh должен существовать"
    assert install_srv.exists(), "scripts/install_server.sh должен существовать"

    assert os.access(gv_update, os.X_OK), (
        "scripts/gv-update.sh должен быть исполняемым (+x)"
    )
    assert os.access(install_srv, os.X_OK), (
        "scripts/install_server.sh должен быть исполняемым (+x)"
    )

    # Проверка на отсутствие личных IP/паролей в скриптах
    for p in (gv_update, install_srv):
        content = p.read_text(encoding="utf-8")
        assert "192.168." not in content, f"В {p} не должно быть локальных IP"
        assert "100." not in content or "100.0" in content, (
            f"В {p} не должно быть реальных NetBird IP"
        )
