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


def test_live_snapshot_unavailable_returns_503_not_500(client: TestClient, test_env):
    """При недоступном стриме и отсутствии кадров /live/snapshot возвращает 503 без HTTP 500."""
    session_factory, _ = test_env
    db: Session = session_factory()
    cam = Camera(
        hardware_id="cam_no_frame_01",
        auto_name="Камера без кадров",
        is_online=True,
    )
    db.add(cam)
    db.commit()
    db.refresh(cam)
    cam_id = cam.id
    db.close()

    cookies = create_user_cookie(
        session_factory, "developer", "Разработчик", ext_id="444"
    )

    # 1. Сессии вообще нет -> 503 (не 500)
    resp1 = client.get(f"/api/cameras/{cam_id}/live/snapshot", cookies=cookies)
    assert resp1.status_code == 503

    # 2. Есть stream_url, но ustreamer недоступен -> 503 (не 500)
    session = live_manager.get_or_create("cam_no_frame_01", camera_id=cam_id)
    session.is_active = True
    session.stream_url = "http://127.0.0.1:59999"
    resp2 = client.get(f"/api/cameras/{cam_id}/live/snapshot", cookies=cookies)
    assert resp2.status_code == 503


def test_live_session_fps_and_latency_calculation():
    """Расчёт реального FPS и latency_ms при частых кадрах и рассинхроне часов."""
    import time

    from server.core.live_manager import LiveCameraSession

    session = LiveCameraSession("cam_fps_test", camera_id=1)
    # Имитируем рассинхрон часов: frame_timestamp в будущем + переданный X-Latency-Ms=24
    future_ts = time.time() + 10.0
    session.push_frame(b"frame1", frame_timestamp=future_ts, latency_ms=24)
    time.sleep(0.06)
    session.push_frame(b"frame2", frame_timestamp=future_ts + 0.06, latency_ms=26)

    st = session.to_status_dict()
    assert st["fps"] > 0.0
    assert st["latency_ms"] == 26


def test_rpi_controls_state_sync_and_no_fake_controls(client: TestClient, test_env):
    """В режиме rpi сервер отдаёт реальные контролы от агента и обновляет auto_name в heartbeat."""
    import json

    session_factory, _ = test_env
    db: Session = session_factory()
    cam = Camera(
        hardware_id="usb-046d_0825_1122-video-index0",
        auto_name="usb-046d_0825_1122-video-index0",
        is_online=True,
    )
    db.add(cam)
    db.add(
        SystemSetting(
            key="rpi_last_telemetry",
            value=json.dumps({"hardware_mode": "rpi", "is_raspberry_pi": True}),
            updated_at=datetime.now(UTC),
        )
    )
    db.commit()
    db.refresh(cam)
    cam_id = cam.id
    hw_id = cam.hardware_id
    db.close()

    # 1. Edge-агент присылает реальные форматы и контролы C270 (без автофокуса и без 1920x1080)
    real_formats = [
        {"format": "MJPG", "resolutions": ["1280x960", "1280x720", "640x480"]}
    ]
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
        },
        {
            "name": "white_balance_automatic",
            "id": "0x0098090c",
            "type": "bool",
            "min": 0,
            "max": 1,
            "step": 1,
            "default": 1,
            "value": 1,
            "is_inactive": False,
            "category": "color",
            "menu_items": [],
        },
        {
            "name": "white_balance_temperature",
            "id": "0x0098091a",
            "type": "int",
            "min": 2800,
            "max": 6500,
            "step": 1,
            "default": 4000,
            "value": 4000,
            "is_inactive": True,
            "category": "color",
            "menu_items": [],
        },
    ]
    resp_state = client.post(
        f"/api/edge/cameras/{hw_id}/controls-state",
        headers={"X-Edge-Key": EDGE_KEY},
        json={
            "formats": real_formats,
            "controls": real_controls,
            "current_format": "MJPG",
            "current_resolution": "1280x960",
        },
    )
    assert resp_state.status_code == 200

    cookies = create_user_cookie(
        session_factory, "developer", "Разработчик", ext_id="555"
    )
    resp_get = client.get(f"/api/cameras/{cam_id}/controls", cookies=cookies)
    assert resp_get.status_code == 200
    body = resp_get.json()
    assert body["formats"] == real_formats
    assert all(c["name"] != "focus_automatic_continuous" for c in body["controls"])
    assert body["current_resolution"] == "1280x960"

    # 2. Переключаем white_balance_automatic=0 -> white_balance_temperature разблокируется
    resp_patch = client.patch(
        f"/api/cameras/{cam_id}/controls",
        json={"controls": {"white_balance_automatic": 0}},
        cookies=cookies,
    )
    assert resp_patch.status_code == 200
    patched_ctrls = {c["name"]: c for c in resp_patch.json()["controls"]}
    assert patched_ctrls["white_balance_temperature"]["is_inactive"] is False

    # 3. Heartbeat обновляет человекочитаемое имя камеры
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
                "board_model": "Raspberry Pi 5",
                "os_info": "Linux",
                "uptime_sec": 100,
                "cpu_temp_c": 45.0,
                "cpu_usage_percent": 5.0,
                "ram_used_mb": 512,
                "ram_total_mb": 4096,
                "disk_free_gb": 20.0,
                "gpio_mode": "rpi",
            },
            "offline_queue_count": 0,
            "detected_cameras": [
                {"hardware_id": hw_id, "auto_name": "UVC Camera (046d:0825)"}
            ],
        },
    )
    assert resp_hb.status_code == 200
    resp_cams = client.get("/api/cameras", cookies=cookies)
    assert resp_cams.status_code == 200
    cam_item = next(c for c in resp_cams.json() if c["id"] == cam_id)
    assert cam_item["auto_name"] == "UVC Camera (046d:0825)"


def test_live_session_proxied_frame_prevents_push_fps_doubling():
    """При активном проксировании push_frame не удваивает fps и не затирает latest_frame/latency_ms."""
    import time

    from server.core.live_manager import LiveCameraSession

    session = LiveCameraSession("cam_proxy_guard", camera_id=7)
    session.latest_frame = b"proxied_jpeg_1"
    session.record_proxied_frame(latency_ms=12)
    time.sleep(0.08)
    session.latest_frame = b"proxied_jpeg_2"
    session.record_proxied_frame(latency_ms=14)

    count_before = len(session._frame_times)
    fps_before = session.fps
    assert count_before == 2
    assert fps_before > 0.0

    # Параллельный push от агента (во время активного проксирования < 1.5 с)
    session.push_frame(
        b"pushed_jpeg",
        is_paused=True,
        resolution="1280x960",
        latency_ms=99,
        stream_url="http://100.64.0.2:8080",
    )

    assert len(session._frame_times) == count_before
    assert session.fps == fps_before
    assert session.latest_frame == b"proxied_jpeg_2"
    assert session.latency_ms == 14
    assert session.is_paused is True
    assert session.resolution == "1280x960"
    assert session.stream_url == "http://100.64.0.2:8080"

    # Остановка сбрасывает stream_url и last_proxied_at
    live_manager._sessions["cam_proxy_guard"] = session
    live_manager.stop("cam_proxy_guard")
    assert session.stream_url is None
    assert session.last_proxied_at == 0.0


def test_edge_command_result_normalizes_loopback_stream_url():
    """_normalize_stream_url заменяет 127.0.0.1 на IP клиента из request.client.host."""
    from types import SimpleNamespace

    from server.api.edge import _normalize_stream_url

    req_remote = SimpleNamespace(client=SimpleNamespace(host="100.64.0.15"))
    assert (
        _normalize_stream_url("http://127.0.0.1:8080", req_remote)
        == "http://100.64.0.15:8080"
    )

    req_local = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    assert (
        _normalize_stream_url("http://127.0.0.1:8080", req_local)
        == "http://127.0.0.1:8080"
    )
    assert _normalize_stream_url(None, req_remote) is None
