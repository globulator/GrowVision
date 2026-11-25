import asyncio
import json
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from server.api.cameras import _is_real_cameras_mode, _mjpeg_generator
from server.api.deps import create_session_cookie, get_db
from server.config import get_settings
from server.core.live_manager import live_manager
from server.core.state import app_state
from server.db.database import Base
from server.db.models import Camera, EdgeCommand, SystemSetting, User
from server.main import app

DEV_PASSWORD = "developer-strong-password"
EDGE_KEY = "test-edge-secret-key"
CLIENT_KEY = "test-client-secret-key"


@pytest.fixture
def test_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "test_stage9_3.db"
    db_url = f"sqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    settings = get_settings()
    monkeypatch.setattr(settings, "client_api_key", CLIENT_KEY)
    monkeypatch.setattr(settings, "edge_api_key", EDGE_KEY)
    monkeypatch.setattr(settings, "developer_password", DEV_PASSWORD)

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


def create_dev_cookie(session_factory, ext_id: str = "dev-93") -> dict[str, str]:
    db: Session = session_factory()
    now = datetime.now(UTC)
    user = User(
        client_type="web",
        external_id=ext_id,
        login="Dev 9.3",
        role="developer",
        is_active=True,
        authorized_at=now,
        last_active_at=now,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    cookie_val = create_session_cookie(user)
    db.close()
    return {"gv_session": cookie_val}


def test_hybrid_mode_heartbeat_enables_real_controls_and_preserves_cache(
    client: TestClient, test_env
):
    """В гибридном режиме (gpio_mode=mock, mock_cameras=False) сервер опрашивает контролы и не затирает кэш пустым []."""
    session_factory, _ = test_env

    # 1. Edge-агент присылает heartbeat в гибридном режиме (gpio_mode='mock', mock_cameras=False)
    hw_id = "usb-Chicony_Cam-video-index0"
    resp_hb = client.post(
        "/api/edge/heartbeat",
        headers={"X-Edge-Key": EDGE_KEY},
        json={
            "software": {
                "edge_version": "0.7.0",
                "python_version": "3.12.0",
                "model_name": "mock",
            },
            "hardware": {
                "board_model": "Laptop",
                "os_info": "Linux",
                "uptime_sec": 100,
                "cpu_temp_c": 45.0,
                "cpu_usage_percent": 5.0,
                "ram_used_mb": 1024,
                "ram_total_mb": 8192,
                "disk_free_gb": 50.0,
                "gpio_mode": "mock",
                "mock_cameras": False,
            },
            "offline_queue_count": 0,
            "detected_cameras": [
                {"hardware_id": hw_id, "auto_name": "Integrated Camera"}
            ],
        },
    )
    assert resp_hb.status_code == 200

    db: Session = session_factory()
    assert _is_real_cameras_mode(db) is True
    cam = Camera(hardware_id=hw_id, auto_name="Integrated Camera", is_online=True)
    db.add(cam)
    db.commit()
    db.refresh(cam)
    cam_id = cam.id
    db.close()

    # 2. Агент присылает реальные форматы и контролы
    real_formats = [{"format": "MJPG", "resolutions": ["640x480", "320x240"]}]
    real_controls = [
        {
            "name": "brightness",
            "id": "0x00980900",
            "type": "int",
            "min": 0,
            "max": 255,
            "step": 1,
            "default": 128,
            "value": 128,
            "is_inactive": False,
            "category": "exposure",
            "menu_items": [],
        }
    ]
    r_state = client.post(
        f"/api/edge/cameras/{hw_id}/controls-state",
        headers={"X-Edge-Key": EDGE_KEY},
        json={"formats": real_formats, "controls": real_controls},
    )
    assert r_state.status_code == 200

    # 3. Временный сбой v4l2-ctl: агент присылает пустые списки -> кэш НЕ должен затереться!
    r_empty = client.post(
        f"/api/edge/cameras/{hw_id}/controls-state",
        headers={"X-Edge-Key": EDGE_KEY},
        json={"formats": [], "controls": []},
    )
    assert r_empty.status_code == 200

    cookies = create_dev_cookie(session_factory, ext_id="dev-93-1")
    r_get = client.get(f"/api/cameras/{cam_id}/controls", cookies=cookies)
    assert r_get.status_code == 200
    body = r_get.json()
    assert body["formats"] == real_formats
    assert body["controls"] == real_controls
    # И реальное разрешение из форматов камеры (а не 1280x720)
    assert body["current_resolution"] == "640x480"

    # 4. Проверяем, что при mock_cameras=True _is_real_cameras_mode становится False
    r_hb_mock = client.post(
        "/api/edge/heartbeat",
        headers={"X-Edge-Key": EDGE_KEY},
        json={
            "software": {
                "edge_version": "0.7.0",
                "python_version": "3.12.0",
                "model_name": "mock",
            },
            "hardware": {
                "board_model": "Laptop",
                "os_info": "Linux",
                "uptime_sec": 100,
                "cpu_temp_c": 45.0,
                "cpu_usage_percent": 5.0,
                "ram_used_mb": 1024,
                "ram_total_mb": 8192,
                "disk_free_gb": 50.0,
                "gpio_mode": "mock",
                "mock_cameras": True,
            },
            "offline_queue_count": 0,
            "detected_cameras": [],
        },
    )
    assert r_hb_mock.status_code == 200
    db2: Session = session_factory()
    assert _is_real_cameras_mode(db2) is False
    db2.close()


def test_start_camera_live_uses_real_camera_resolution_from_formats_and_profile(
    client: TestClient, test_env
):
    """start_camera_live выставляет session.resolution из реальных форматов/профиля камеры, а не 1280x720."""
    session_factory, _ = test_env
    db: Session = session_factory()
    now = datetime.now(UTC)

    db.add(
        SystemSetting(
            key="rpi_last_telemetry",
            value=json.dumps({"hardware_mode": "mock", "mock_cameras": False}),
            updated_at=now,
        )
    )
    cam = Camera(hardware_id="cam-res-real-1", auto_name="Real Cam", is_online=True)
    db.add(cam)
    db.add(
        SystemSetting(
            key="camera_formats_cam-res-real-1",
            value=json.dumps(
                [{"format": "YUYV", "resolutions": ["800x600", "640x480"]}]
            ),
            updated_at=now,
        )
    )
    db.commit()
    db.refresh(cam)
    cam_id = cam.id
    db.close()

    cookies = create_dev_cookie(session_factory, ext_id="dev-93-2")
    r_start = client.post(f"/api/cameras/{cam_id}/live/start", cookies=cookies)
    assert r_start.status_code == 200
    assert r_start.json()["resolution"] == "800x600"
    assert r_start.json()["format"] == "YUYV"

    r_status = client.get(f"/api/cameras/{cam_id}/live/status", cookies=cookies)
    assert r_status.status_code == 200
    assert r_status.json()["resolution"] == "800x600"

    # Проверяем, что в EdgeCommand START_LIVE ушли реальные width=800, height=600, pixel_format=YUYV
    db2: Session = session_factory()
    cmd = (
        db2.query(EdgeCommand)
        .filter(EdgeCommand.command_type == "START_LIVE")
        .order_by(EdgeCommand.id.desc())
        .first()
    )
    assert cmd is not None and cmd.payload is not None
    payload = json.loads(cmd.payload)
    assert payload["width"] == 800
    assert payload["height"] == 600
    assert payload["pixel_format"] == "YUYV"
    db2.close()


def test_mjpeg_generator_retries_proxy_after_initial_ustreamer_startup_failure():
    """_mjpeg_generator не ставит вечный proxy_failed=True, а повторяет подключение к ustreamer."""
    live_manager.clear()
    session = live_manager.get_or_create("cam-retry-test", camera_id=1)
    session.is_active = True
    session.stream_url = "http://127.0.0.1:8080"

    attempts = 0
    fake_jpeg = b"\xff\xd8\xff\xe0TEST_FRAME\xff\xd9"

    async def fake_proxy(_sess):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionRefusedError("ustreamer still starting")
        yield fake_jpeg
        _sess.is_active = False

    async def run_test():
        chunks = []
        with patch(
            "server.api.cameras._proxy_ustreamer_frames", side_effect=fake_proxy
        ):
            async for chunk in _mjpeg_generator("cam-retry-test"):
                chunks.append(chunk)
        return chunks

    chunks = asyncio.run(run_test())
    assert attempts == 2
    assert len(chunks) == 1
    assert fake_jpeg in chunks[0]
    live_manager.clear()
