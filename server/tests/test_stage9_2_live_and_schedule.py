# server/tests/test_stage9_2_live_and_schedule.py
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from server.api.deps import create_session_cookie, get_db
from server.config import get_settings
from server.core.live_manager import live_manager
from server.core.state import app_state
from server.db.database import Base
from server.db.models import Camera, Capture, Installation, SystemSetting, User
from server.main import app
from shared.schedule import compute_next_capture_at

DEV_PASSWORD = "developer-strong-password"
BIO_PASSWORD = "biologist-strong-password"
EDGE_KEY = "test-edge-secret-key"
CLIENT_KEY = "test-client-secret-key"


@pytest.fixture
def test_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "test_stage9_2.db"
    db_url = f"sqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    settings = get_settings()
    monkeypatch.setattr(settings, "client_api_key", CLIENT_KEY)
    monkeypatch.setattr(settings, "edge_api_key", EDGE_KEY)
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
    live_manager.clear()

    yield session_factory, engine

    app.dependency_overrides.clear()
    app_state.clear()
    live_manager.clear()
    engine.dispose()


@pytest.fixture
def client(test_env) -> TestClient:
    return TestClient(app)


def create_user_cookie(
    session_factory, role: str, login_name: str = "TestUser", ext_id: str = "123456"
) -> dict[str, str]:
    db: Session = session_factory()
    now = datetime.now(UTC)
    user = (
        db.query(User)
        .filter(User.client_type == "web", User.external_id == ext_id)
        .first()
    )
    if not user:
        user = User(
            client_type="web",
            external_id=ext_id,
            login=login_name,
            role=role,
            is_active=True,
            authorized_at=now,
            last_active_at=now,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        user.role = role
        user.login = login_name
        user.is_active = True
        db.commit()
        db.refresh(user)

    cookie_val = create_session_cookie(user)
    db.close()
    return {"gv_session": cookie_val}


def test_schedule_alignment_pure_function():
    """Тестирование чистого алгоритма выравнивания расписания по целым часам."""
    # 1. Без прошлых съемок, сейчас 14:23:45 -> следующий ровный час 15:00:00
    now = datetime(2026, 11, 25, 14, 23, 45, tzinfo=UTC)
    next_dt = compute_next_capture_at(None, 2, now=now)
    assert next_dt == datetime(2026, 11, 25, 15, 0, 0, tzinfo=UTC)

    # 2. Прошлая съемка в 21:00, сейчас 22:12, интервал 2 ч -> слот 23:00
    last_dt = datetime(2026, 11, 25, 21, 0, 0, tzinfo=UTC)
    now = datetime(2026, 11, 25, 22, 12, 0, tzinfo=UTC)
    next_dt = compute_next_capture_at(last_dt, 2, now=now)
    assert next_dt == datetime(2026, 11, 25, 23, 0, 0, tzinfo=UTC)

    # 3. Прошлая съемка давно: вчера в 10:00, сейчас 14:15, интервал 2 ч -> ближайший ровный час 15:00
    last_dt = datetime(2026, 11, 24, 10, 0, 0, tzinfo=UTC)
    now = datetime(2026, 11, 25, 14, 15, 0, tzinfo=UTC)
    next_dt = compute_next_capture_at(last_dt, 2, now=now)
    assert next_dt == datetime(2026, 11, 25, 15, 0, 0, tzinfo=UTC)

    # 4. Прошлая съемка ровно в 22:00:00, сейчас ровно 22:00:00 -> следующий через 2 ч в 00:00:00
    last_dt = datetime(2026, 11, 25, 22, 0, 0, tzinfo=UTC)
    now = datetime(2026, 11, 25, 22, 0, 0, tzinfo=UTC)
    next_dt = compute_next_capture_at(last_dt, 2, now=now)
    assert next_dt == datetime(2026, 11, 26, 0, 0, 0, tzinfo=UTC)


def test_installations_next_capture_at_serialization(client: TestClient, test_env):
    """Проверка отдачи поля next_capture_at в GET /api/installations."""
    session_factory, _ = test_env
    db: Session = session_factory()

    inst = Installation(
        name="Установка 1",
        culture_name="Базилик",
        light_gpio_pin=4,
        flash_gpio_pin=17,
        is_archived=False,
    )
    db.add(inst)
    db.commit()
    db.refresh(inst)

    # Запрос без авторизации (для гостей отдается список установок)
    resp = client.get("/api/installations")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    unit = data[0]
    assert unit["name"] == "Установка 1"
    assert "next_capture_at" in unit
    assert unit["next_capture_at"] is not None

    # Добавляем прошлый кадр
    cap_time = datetime.now(UTC) - timedelta(minutes=45)
    cap = Capture(
        installation_id=inst.id,
        captured_at=cap_time,
        raw_image_path="test_raw.jpg",
        annotated_image_path="test_annotated.jpg",
    )
    db.add(cap)
    db.commit()

    resp2 = client.get(f"/api/installations/{inst.id}")
    assert resp2.status_code == 200
    unit2 = resp2.json()
    assert unit2["next_capture_at"] is not None

    # Проверяем архивную установку под разработчиком
    inst.is_archived = True
    db.commit()

    cookies_dev = create_user_cookie(
        session_factory, "developer", "DevArch", ext_id="999"
    )
    resp3 = client.get("/api/installations?include_archived=true", cookies=cookies_dev)
    assert resp3.status_code == 200
    unit3 = next(u for u in resp3.json() if u["id"] == inst.id)
    assert unit3["next_capture_at"] is None
    db.close()


def test_get_cameras_permissions(client: TestClient, test_env):
    """Проверка ролевого доступа к GET /api/cameras."""
    session_factory, _ = test_env

    # 1. Гость -> 401
    resp_guest = client.get("/api/cameras")
    assert resp_guest.status_code == 401

    # 2. Биолог -> 200
    cookies_bio = create_user_cookie(
        session_factory, "biologist", "Биолог Анна", ext_id="111"
    )
    resp_bio = client.get("/api/cameras", cookies=cookies_bio)
    assert resp_bio.status_code == 200
    assert isinstance(resp_bio.json(), list)

    # 3. Разработчик -> 200
    cookies_dev = create_user_cookie(
        session_factory, "developer", "Разработчик Иван", ext_id="222"
    )
    resp_dev = client.get("/api/cameras", cookies=cookies_dev)
    assert resp_dev.status_code == 200
    assert isinstance(resp_dev.json(), list)


def test_camera_live_lifecycle_and_proxy(client: TestClient, test_env):
    """Сквозной тест управления Live-стримом, отправки кадра агентом и snapshot."""
    session_factory, _ = test_env
    db: Session = session_factory()

    cam = Camera(
        hardware_id="cam_live_test_01",
        auto_name="Тестовая Камера",
        is_online=True,
    )
    db.add(cam)
    # Помечаем RPi как онлайн
    telem = SystemSetting(
        key="rpi_last_telemetry",
        value="{}",
        updated_at=datetime.now(UTC),
    )
    db.add(telem)
    db.commit()
    db.refresh(cam)
    cam_id = cam.id
    db.close()

    cookies = create_user_cookie(
        session_factory, "developer", "Разработчик Иван", ext_id="333"
    )

    # 1. Старт стрима
    resp_start = client.post(f"/api/cameras/{cam_id}/live/start", cookies=cookies)
    assert resp_start.status_code == 200
    assert resp_start.json()["status"] == "started"

    # Проверяем статус стрима
    resp_status = client.get(f"/api/cameras/{cam_id}/live/status", cookies=cookies)
    assert resp_status.status_code == 200
    st = resp_status.json()
    assert st["is_active"] is True
    assert st["camera_id"] == cam_id

    # 2. Агент присылает live-frame
    mock_frame = (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00"
        + b"\x00" * 100
        + b"\xff\xd9"
    )
    resp_frame = client.post(
        "/api/edge/cameras/cam_live_test_01/live-frame",
        headers={
            "X-Edge-Key": EDGE_KEY,
            "X-Frame-Timestamp": str(datetime.now(UTC).timestamp()),
            "X-Resolution": "1280x720",
            "X-Paused": "0",
        },
        content=mock_frame,
    )
    assert resp_frame.status_code == 200
    assert resp_frame.json()["status"] == "ok"

    # 3. Скачивание снимка через snapshot
    resp_snap = client.get(f"/api/cameras/{cam_id}/live/snapshot", cookies=cookies)
    assert resp_snap.status_code == 200
    assert resp_snap.headers["content-type"] == "image/jpeg"
    assert resp_snap.content == mock_frame

    # 4. Проверка телеметрии статуса
    resp_status2 = client.get(f"/api/cameras/{cam_id}/live/status", cookies=cookies)
    assert resp_status2.status_code == 200
    st2 = resp_status2.json()
    assert st2["resolution"] == "1280x720"
    assert st2["is_paused"] is False

    # 5. Получение и обновление контролов
    resp_ctrls = client.get(f"/api/cameras/{cam_id}/controls", cookies=cookies)
    assert resp_ctrls.status_code == 200
    ctrls_data = resp_ctrls.json()
    assert "controls" in ctrls_data
    assert "formats" in ctrls_data
    assert "rgb" in ctrls_data

    # Обновление контролов
    resp_patch = client.patch(
        f"/api/cameras/{cam_id}/controls",
        json={
            "controls": {"brightness": 120},
            "rgb": {"r": 1.2, "g": 1.0, "b": 0.9},
        },
        cookies=cookies,
    )
    assert resp_patch.status_code == 200
    patch_res = resp_patch.json()
    assert patch_res["status"] == "updated"

    # 6. Остановка стрима
    resp_stop = client.post(f"/api/cameras/{cam_id}/live/stop", cookies=cookies)
    assert resp_stop.status_code == 200
    assert resp_stop.json()["status"] == "stopped"

    # Проверяем, что стрим стал неактивным
    resp_status3 = client.get(f"/api/cameras/{cam_id}/live/status", cookies=cookies)
    assert resp_status3.status_code == 200
    assert resp_status3.json()["is_active"] is False
