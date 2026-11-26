import time
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from server.api.deps import create_session_cookie, get_db
from server.config import get_settings
from server.db.database import Base
from server.db.models import Camera, EdgeCommand, SystemSetting, User
from server.main import app


@pytest.fixture
def test_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "test_stage8_10_autocalibrate.db"
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
def dev_user(test_env):
    session_factory = test_env
    with session_factory() as db:
        user = User(
            client_type="web",
            external_id="123456",
            login="Dev User",
            role="developer",
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


@pytest.fixture
def bio_user(test_env):
    session_factory = test_env
    with session_factory() as db:
        user = User(
            client_type="web",
            external_id="654321",
            login="Bio User",
            role="biologist",
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def test_autocalibrate_camera_permissions(client, dev_user, bio_user, test_env):
    """POST /api/cameras/{id}/autocalibrate: 401 для гостей, 403 для биологов."""
    session_factory = test_env
    with session_factory() as db:
        cam = Camera(hardware_id="cam-test-hw-1", auto_name="Cam 1", is_online=True)
        db.add(cam)
        db.commit()
        cam_id = cam.id

    # Без куки -> 401
    res = client.post(f"/api/cameras/{cam_id}/autocalibrate")
    assert res.status_code == 401

    # Биолог -> 403
    client.cookies.set("gv_session", create_session_cookie(bio_user))
    res_bio = client.post(f"/api/cameras/{cam_id}/autocalibrate")
    assert res_bio.status_code == 403


def test_autocalibrate_camera_rpi_offline_503(client, dev_user, test_env):
    """Калибровка при оффлайн RPi возвращает 503 Service Unavailable."""
    session_factory = test_env
    with session_factory() as db:
        cam = Camera(hardware_id="cam-offline-hw", auto_name="Cam Off", is_online=True)
        db.add(cam)
        db.commit()
        cam_id = cam.id

    client.cookies.set("gv_session", create_session_cookie(dev_user))
    res = client.post(f"/api/cameras/{cam_id}/autocalibrate")
    assert res.status_code == 503
    assert "Raspberry Pi офлайн" in res.json()["detail"]


def test_autocalibrate_camera_success_flow(client, dev_user, test_env):
    """Успешная автокалибровка камеры: создание команды, ожидание, сохранение профиля и отдача в GET."""
    session_factory = test_env
    now = datetime.now(UTC)

    with session_factory() as db:
        # 1. RPi онлайн (rpi_last_telemetry)
        db.add(
            SystemSetting(
                key="rpi_last_telemetry",
                value="{}",
                updated_at=now,
            )
        )
        # 2. Камера
        cam = Camera(
            hardware_id="cam-calib-hw-42", auto_name="Calib Cam 42", is_online=True
        )
        db.add(cam)
        db.commit()
        cam_id = cam.id

    client.cookies.set("gv_session", create_session_cookie(dev_user))

    # Фоновый воркер-симулятор ответа агента
    import threading

    def complete_command():
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            time.sleep(0.05)
            with session_factory() as db:
                cmd = (
                    db.query(EdgeCommand)
                    .filter(EdgeCommand.command_type == "AUTOCALIBRATE_CAMERA")
                    .first()
                )
                if cmd:
                    cmd.status = "done"
                    cmd.result_message = "1920x1080 (MJPG), автофокус: поддерживается"
                    cmd.debug_log = "[CAM] probe capabilities ok"
                    cmd.completed_at = datetime.now(UTC)
                    db.commit()
                    return

    t = threading.Thread(target=complete_command)
    t.start()

    res = client.post(f"/api/cameras/{cam_id}/autocalibrate")
    t.join()

    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "done"
    assert data["camera_id"] == cam_id
    assert "1920x1080 (MJPG)" in data["summary"]

    # Проверяем сохранение профиля в SystemSetting
    with session_factory() as db:
        setting = (
            db.query(SystemSetting)
            .filter(SystemSetting.key == "camera_profile_cam-calib-hw-42")
            .first()
        )
        assert setting is not None
        assert "1920x1080" in setting.value

    # Проверяем, что GET /api/cameras возвращает calibration_summary
    res_list = client.get("/api/cameras")
    assert res_list.status_code == 200
    cameras = res_list.json()
    assert len(cameras) >= 1
    target_cam = next(c for c in cameras if c["id"] == cam_id)
    assert (
        target_cam["calibration_summary"]
        == "1920x1080 (MJPG), автофокус: поддерживается"
    )
