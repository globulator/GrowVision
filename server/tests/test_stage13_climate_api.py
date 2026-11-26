from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from server.api.deps import get_db
from server.core.climate import esp_queue
from server.db.database import create_db_engine, init_db
from server.db.models import User
from server.main import app


@pytest.fixture()
def climate_client(tmp_path, monkeypatch):
    esp_queue.clear()
    engine = create_db_engine(f"sqlite:///{tmp_path / 'climate_api_test.db'}")
    init_db(engine)
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Создаём тестового биолога
    db = TestSession()
    db.add(
        User(
            client_type="cli",
            external_id="bio-test-1",
            login="bio_tester",
            role="biologist",
            is_active=True,
        )
    )
    db.commit()
    db.close()

    def override_get_db():
        s = TestSession()
        try:
            yield s
        finally:
            s.close()

    fake_settings = SimpleNamespace(
        client_api_key="test-client-key",
        developer_password="dev",
        edge_api_key="test-edge-key",
    )
    monkeypatch.setattr("server.api.deps.get_settings", lambda: fake_settings)
    monkeypatch.setattr("server.main.init_db", lambda *a, **kw: None)
    monkeypatch.setattr("server.main.SessionLocal", TestSession)
    app.dependency_overrides[get_db] = override_get_db

    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
        esp_queue.clear()


BIO_HEADERS = {
    "X-Client-Key": "test-client-key",
    "X-Client-Type": "cli",
    "X-External-Id": "bio-test-1",
}


def test_esp_protocol_ping_sensors_and_commands(climate_client):
    # Изначально ESP офлайн
    s0 = climate_client.get("/api/climate/summary")
    assert s0.status_code == 200
    assert s0.json()["esp_online"] is False

    # GET /ping обновляет онлайн ESP
    ping = climate_client.get("/ping")
    assert ping.status_code == 200

    # Пустая очередь в формате прошивки
    empty_cmd = climate_client.get("/api/esp/command")
    assert empty_cmd.status_code == 200
    assert empty_cmd.json() == {
        "queue_size": -1,
        "id": 0,
        "device": "None",
        "action": "off",
    }

    # Приём датчиков от ESP
    sens = climate_client.post(
        "/api/esp/sensors",
        json={"temperature": 23.4, "humidity": 61.5},
    )
    assert sens.status_code == 200

    # Ставим команду полива от биолога
    ctrl = climate_client.post(
        "/api/climate/control",
        json={"device": "water", "action": "on"},
        headers=BIO_HEADERS,
    )
    assert ctrl.status_code == 200
    cmd_id = ctrl.json()["command_id"]

    # ESP забирает команду
    got = climate_client.get("/api/esp/command")
    assert got.status_code == 200
    assert got.json() == {
        "queue_size": 0,
        "id": cmd_id,
        "device": "water",
        "action": "on",
    }

    # Неверный id отклоняется 409
    bad_ack = climate_client.post("/api/esp/command", json={"id": cmd_id + 99})
    assert bad_ack.status_code == 409

    # Верный id подтверждает команду и обновляет состояние
    ack = climate_client.post("/api/esp/command", json={"id": cmd_id})
    assert ack.status_code == 200

    summary = climate_client.get("/api/climate/summary").json()
    assert summary["esp_online"] is True
    assert summary["temperature"] == 23.4
    assert summary["humidity"] == 61.5
    assert summary["water_state"] == "on"


def test_climate_permissions_auto_lock_schedule_and_ph(climate_client):
    # Гость не может управлять периферией
    guest_ctrl = climate_client.post(
        "/api/climate/control",
        json={"device": "fan", "action": "on"},
    )
    assert guest_ctrl.status_code == 401

    # Команда light на ESP запрещена валидацией (422)
    light_resp = climate_client.post(
        "/api/climate/control",
        json={"device": "light", "action": "on"},
        headers=BIO_HEADERS,
    )
    assert light_resp.status_code == 422

    # Переключаем в auto -> ручное управление даёт 409 Conflict
    mode_resp = climate_client.post(
        "/api/climate/mode",
        json={"mode": "auto"},
        headers=BIO_HEADERS,
    )
    assert mode_resp.status_code == 200
    assert mode_resp.json()["mode"] == "auto"

    blocked = climate_client.post(
        "/api/climate/control",
        json={"device": "fan", "action": "on"},
        headers=BIO_HEADERS,
    )
    assert blocked.status_code == 409

    # Обновляем расписание
    sched = climate_client.post(
        "/api/climate/schedule",
        json={
            "water_interval_hours": 6,
            "water_duration_minutes": 2,
            "fan_interval_hours": 3,
            "fan_duration_minutes": 4,
        },
        headers=BIO_HEADERS,
    )
    assert sched.status_code == 200
    assert sched.json()["schedule"] == {
        "water_interval_hours": 6,
        "water_duration_minutes": 2,
        "fan_interval_hours": 3,
        "fan_duration_minutes": 4,
    }

    # Ввод pH: за пределами 0..14 -> 422, валидное -> 200
    bad_ph = climate_client.post(
        "/api/climate/ph",
        json={"level": 15.0},
        headers=BIO_HEADERS,
    )
    assert bad_ph.status_code == 422

    ok_ph = climate_client.post(
        "/api/climate/ph",
        json={"level": 6.4},
        headers=BIO_HEADERS,
    )
    assert ok_ph.status_code == 200

    hist = climate_client.get("/api/climate/history?period=24h")
    assert hist.status_code == 200
    assert hist.json()["ph"][0]["value"] == 6.4
