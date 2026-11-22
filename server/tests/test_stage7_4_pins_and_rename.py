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
from server.db.models import EdgeCommand, User
from server.main import app

CLIENT_KEY = "test-client-secret-key"


@pytest.fixture
def test_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "test_stage7_4.db"
    db_url = f"sqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    settings = get_settings()
    monkeypatch.setattr(settings, "client_api_key", CLIENT_KEY)

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


@pytest.fixture
def client(test_env) -> TestClient:
    return TestClient(app)


def create_user(session_factory, login: str, role: str, external_id: str) -> int:
    db = session_factory()
    user = User(
        client_type="telegram",
        external_id=external_id,
        login=login,
        role=role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    user_id = user.id
    db.close()
    return user_id


def auth_headers(external_id: str = "dev-user") -> dict[str, str]:
    return {
        "X-Client-Key": CLIENT_KEY,
        "X-Client-Type": "telegram",
        "X-External-Id": external_id,
    }


def test_shared_gpio_pins_creation_and_warnings(client: TestClient, test_env):
    """Критерий 1 и 3: Создание установок с общими пинами и получение предупреждений."""
    session_factory, _ = test_env
    create_user(session_factory, "Dev", "developer", "dev-user")
    hdrs = auth_headers("dev-user")

    # 1. Первая установка с пинами 4 и 5
    r1 = client.post(
        "/api/installations",
        headers=hdrs,
        json={
            "name": "Установка 1",
            "culture_name": "Томат",
            "light_gpio_pin": 4,
            "flash_gpio_pin": 5,
        },
    )
    assert r1.status_code == 201
    data1 = r1.json()
    assert data1["warnings"] == []

    # 2. Вторая установка: light=4 (общий с Установкой 1), flash=6
    r2 = client.post(
        "/api/installations",
        headers=hdrs,
        json={
            "name": "Установка 2",
            "culture_name": "Огурец",
            "light_gpio_pin": 4,
            "flash_gpio_pin": 6,
        },
    )
    assert r2.status_code == 201
    data2 = r2.json()
    assert len(data2["warnings"]) == 1
    assert (
        "⚠️ Пин 4 уже занят («Установка 1»). Реле будут общими." in data2["warnings"][0]
    )

    # 3. Третья установка: light=4 (занят в 1 и 2)
    r3 = client.post(
        "/api/installations",
        headers=hdrs,
        json={
            "name": "Установка 3",
            "culture_name": "Перец",
            "light_gpio_pin": 4,
            "flash_gpio_pin": 7,
        },
    )
    assert r3.status_code == 201
    data3 = r3.json()
    assert len(data3["warnings"]) == 1
    w = data3["warnings"][0]
    assert "⚠️ Пин 4 уже занят" in w
    assert "«Установка 1»" in w and "«Установка 2»" in w

    # 4. Попытка создать установку с совпадающими light и flash внутри одной установки -> 409
    r_bad = client.post(
        "/api/installations",
        headers=hdrs,
        json={
            "name": "Неверные пины",
            "culture_name": "Салат",
            "light_gpio_pin": 8,
            "flash_gpio_pin": 8,
        },
    )
    assert r_bad.status_code == 409
    assert "Пины света и вспышки не могут совпадать" in r_bad.json()["detail"]

    # 5. Редактирование пинов (PATCH) возвращает warning
    inst2_id = data2["id"]
    r_patch = client.patch(
        f"/api/installations/{inst2_id}",
        headers=hdrs,
        json={"flash_gpio_pin": 5},  # 5 занят в Установке 1
    )
    assert r_patch.status_code == 200
    patch_data = r_patch.json()
    assert any("⚠️ Пин 5 уже занят («Установка 1»)" in w for w in patch_data["warnings"])


def test_relay_software_state_sync_by_pin(client: TestClient, test_env, monkeypatch):
    """Критерий 2: Программная синхронизация состояния реле по номеру GPIO-пина."""
    session_factory, _ = test_env
    create_user(session_factory, "Dev", "developer", "dev-user")
    hdrs = auth_headers("dev-user")
    app_state.clear()

    # Мокируем check_rpi_online, чтобы не требовался реальный heartbeat RPi
    monkeypatch.setattr("server.api.control.check_rpi_online", lambda db: None)

    # Создаем 2 установки с общим пином света 12
    r1 = client.post(
        "/api/installations",
        headers=hdrs,
        json={
            "name": "Синхро 1",
            "culture_name": "Клубника",
            "light_gpio_pin": 12,
            "flash_gpio_pin": 13,
        },
    )
    inst1_id = r1.json()["id"]

    r2 = client.post(
        "/api/installations",
        headers=hdrs,
        json={
            "name": "Синхро 2",
            "culture_name": "Базилик",
            "light_gpio_pin": 12,
            "flash_gpio_pin": 14,
        },
    )
    inst2_id = r2.json()["id"]

    # Изначально свет выключен
    assert app_state.get_pin_state(12) is False

    # Мокируем wait_command_result, чтобы сразу возвращалась завершённая команда
    async def fake_wait_command(cmd_id, timeout_sec, db):
        cmd = db.query(EdgeCommand).filter(EdgeCommand.id == cmd_id).first()
        cmd.status = "done"
        cmd.result_message = "OK"
        db.commit()
        return cmd

    monkeypatch.setattr("server.api.control.wait_command_result", fake_wait_command)

    # Включаем свет на Установке 1
    resp = client.post(
        "/api/control/light/main",
        headers=hdrs,
        json={"target": inst1_id, "state": True},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "done"
    assert resp.json()["pin_states"]["12"] is True

    # Проверяем, что состояние пина 12 обновилось для Установки 2
    r_get2 = client.get(f"/api/installations/{inst2_id}", headers=hdrs)
    assert r_get2.status_code == 200
    assert r_get2.json()["light_state"] is True

    # Проверяем эндпоинт GET /api/control/relays/{id}
    r_relays2 = client.get(f"/api/control/relays/{inst2_id}", headers=hdrs)
    assert r_relays2.status_code == 200
    assert r_relays2.json()["light_state"] is True
    assert r_relays2.json()["light_gpio_pin"] == 12

    # Выключаем свет через Установку 2
    resp_off = client.post(
        "/api/control/light/main",
        headers=hdrs,
        json={"target": inst2_id, "state": False},
    )
    assert resp_off.status_code == 200
    assert resp_off.json()["status"] == "done"
    assert resp_off.json()["pin_states"]["12"] is False

    # Установка 1 теперь тоже видит свет выключенным
    r_get1 = client.get(f"/api/installations/{inst1_id}", headers=hdrs)
    assert r_get1.status_code == 200
    assert r_get1.json()["light_state"] is False


def test_user_rename_me_and_sessions(client: TestClient, test_env):
    """Критерий 4: Переименование пользователей."""
    session_factory, _ = test_env
    dev_id = create_user(session_factory, "Dev User", "developer", "dev-user")
    bio_id = create_user(session_factory, "Анна Биолог", "biologist", "bio-user")

    dev_hdrs = auth_headers("dev-user")
    bio_hdrs = auth_headers("bio-user")

    # 1. Биолог переименовывает себя через PATCH /api/auth/me
    r_me = client.patch(
        "/api/auth/me",
        headers=bio_hdrs,
        json={"login": "Анна Сидорова"},
    )
    assert r_me.status_code == 200
    assert r_me.json()["login"] == "Анна Сидорова"

    # Проверяем GET /api/auth/me
    r_check = client.get("/api/auth/me", headers=bio_hdrs)
    assert r_check.json()["login"] == "Анна Сидорова"

    # 2. Биолог пытается переименовать другого пользователя через PATCH /api/auth/sessions/{id} -> 403
    r_bio_forbidden = client.patch(
        f"/api/auth/sessions/{dev_id}",
        headers=bio_hdrs,
        json={"login": "Взломщик"},
    )
    assert r_bio_forbidden.status_code == 403

    # 3. Разработчик переименовывает биолога через PATCH /api/auth/sessions/{id}
    r_dev_rename = client.patch(
        f"/api/auth/sessions/{bio_id}",
        headers=dev_hdrs,
        json={"login": "Анна Профессор"},
    )
    assert r_dev_rename.status_code == 200
    assert r_dev_rename.json()["login"] == "Анна Профессор"

    # 4. Валидация: пустое имя -> 422
    r_empty = client.patch(
        "/api/auth/me",
        headers=bio_hdrs,
        json={"login": "   "},
    )
    assert r_empty.status_code == 422

    # Длинное имя (>64 символов) -> 422
    r_long = client.patch(
        "/api/auth/me",
        headers=bio_hdrs,
        json={"login": "A" * 65},
    )
    assert r_long.status_code == 422
