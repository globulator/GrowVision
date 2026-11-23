from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from server.api.deps import get_db
from server.config import get_settings
from server.core.state import app_state
from server.db.database import Base
from server.main import app

DEV_PASSWORD = "developer-strong-password"
BIO_PASSWORD = "biologist-strong-password"
CLIENT_KEY = "test-client-secret-key"


@pytest.fixture
def test_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "test_unarchive.db"
    db_url = f"sqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    settings = get_settings()
    monkeypatch.setattr(settings, "client_api_key", CLIENT_KEY)
    monkeypatch.setattr(settings, "developer_password", DEV_PASSWORD)
    monkeypatch.setattr(settings, "biologist_password", BIO_PASSWORD)

    def override_get_db() -> Generator[Session, None, None]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app_state.clear()

    yield session_factory, engine

    app.dependency_overrides.clear()
    app_state.clear()
    engine.dispose()


def test_unarchive_installation_flow(test_env):
    """Проверка эндпоинта POST /api/installations/{id}/unarchive:

    - права доступа (только Developer);
    - статус is_archived = False;
    - свет по умолчанию ВКЛ, вспышка ВЫКЛ;
    - проверка и возвращение warnings о пересечении пинов.
    """
    dev_client = TestClient(app)
    dev_client.headers.update(
        {
            "X-Client-Key": CLIENT_KEY,
            "X-Client-Type": "cli",
            "X-External-Id": "cli_dev",
        }
    )
    # Авторизуем разработчика
    r_login = dev_client.post(
        "/api/auth/login",
        json={
            "client_type": "cli",
            "external_id": "cli_dev",
            "password": DEV_PASSWORD,
            "login": "Разработчик CLI",
        },
    )
    assert r_login.status_code == 200

    # 1. Создаем две установки
    r_inst1 = dev_client.post(
        "/api/installations",
        json={
            "name": "Установка 1",
            "culture_name": "Томат",
            "light_gpio_pin": 17,
            "flash_gpio_pin": 27,
        },
    )
    assert r_inst1.status_code == 201
    inst1_id = r_inst1.json()["id"]

    r_inst2 = dev_client.post(
        "/api/installations",
        json={
            "name": "Установка 2",
            "culture_name": "Базилик",
            "light_gpio_pin": 17,  # Общий пин света
            "flash_gpio_pin": 22,
        },
    )
    assert r_inst2.status_code == 201
    assert "id" in r_inst2.json()

    # 2. Архивируем Установку 1
    r_arch = dev_client.post(f"/api/installations/{inst1_id}/archive")
    assert r_arch.status_code == 200
    assert r_arch.json()["is_archived"] is True

    # 3. Попытка разархивации гостем (без авторизации) -> 401
    guest_client = TestClient(app)
    guest_client.headers.update(
        {
            "X-Client-Key": CLIENT_KEY,
            "X-Client-Type": "telegram",
            "X-External-Id": "tg_guest",
        }
    )
    r_unarch_guest = guest_client.post(f"/api/installations/{inst1_id}/unarchive")
    assert r_unarch_guest.status_code == 401

    # 4. Попытка разархивации биологом -> 403
    bio_client = TestClient(app)
    bio_client.headers.update(
        {
            "X-Client-Key": CLIENT_KEY,
            "X-Client-Type": "telegram",
            "X-External-Id": "tg_bio",
        }
    )
    # Создаем инвайт разработчиком для биолога
    r_inv = dev_client.post("/api/auth/invites")
    inv_code = r_inv.json()["code"]
    bio_client.post(
        "/api/auth/login",
        json={
            "client_type": "telegram",
            "external_id": "tg_bio",
            "password": inv_code,
            "login": "Биолог Тест",
        },
    )
    r_unarch_bio = bio_client.post(f"/api/installations/{inst1_id}/unarchive")
    assert r_unarch_bio.status_code == 403

    # 5. Разархивация разработчиком
    r_unarch_dev = dev_client.post(f"/api/installations/{inst1_id}/unarchive")
    assert r_unarch_dev.status_code == 200
    data = r_unarch_dev.json()
    assert data["id"] == inst1_id
    assert data["is_archived"] is False
    assert data["light_state"] is True
    assert data["flash_state"] is False
    # Так как пин 17 занят активной Установкой 2, должно быть предупреждение
    assert any("⚠️ Пин 17 уже занят" in w for w in data.get("warnings", []))

    # 6. Несуществующая установка -> 404
    r_404 = dev_client.post("/api/installations/999999/unarchive")
    assert r_404.status_code == 404
