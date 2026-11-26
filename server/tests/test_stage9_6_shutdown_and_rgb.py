import asyncio
import io
import json
import signal
import time
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from server.api import edge as edge_api
from server.api.cameras import (
    _mjpeg_generator,
    _probe_ustreamer_online_state,
    _proxy_ustreamer_frames,
)
from server.api.deps import create_session_cookie, get_db
from server.config import get_settings
from server.core.live_manager import live_manager
from server.core.state import app_state
from server.db.database import Base
from server.db.models import Camera, EdgeCommand, Installation, SystemSetting, User
from server.main import app

TEST_EDGE_KEY = "test-edge-key-96"
TEST_CLIENT_KEY = "test-client-key-96"


def _make_jpeg_bytes(color: tuple[int, int, int] = (200, 150, 100)) -> bytes:
    img = Image.new("RGB", (32, 32), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=96)
    return buf.getvalue()


@pytest.fixture
def env_96(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "test_96.db"
    engine = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(edge_api, "get_media_dir", lambda: media_dir)
    monkeypatch.setattr("server.api.timelapse.get_media_dir", lambda: media_dir)

    settings = get_settings()
    monkeypatch.setattr(settings, "edge_api_key", TEST_EDGE_KEY)
    monkeypatch.setattr(settings, "client_api_key", TEST_CLIENT_KEY)
    app_state.clear()
    live_manager.clear()

    def override_get_db() -> Generator[Session, None, None]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    with session_factory() as db:
        inst = Installation(
            name="Бокс 9.6",
            culture_name="Базилик",
            light_gpio_pin=17,
            flash_gpio_pin=27,
            aruco_size_mm=50.0,
            is_archived=False,
        )
        db.add(inst)
        db.flush()

        cam = Camera(
            hardware_id="cam-96-hw",
            auto_name="Cam 96",
            installation_id=inst.id,
            is_online=True,
        )
        db.add(cam)

        web_user = User(
            client_type="web",
            external_id="dev-96",
            login="Web Dev 96",
            role="developer",
            is_active=True,
        )
        db.add(web_user)
        db.commit()
        inst_id = inst.id
        cam_id = cam.id
        web_cookie = create_session_cookie(web_user)

    client = TestClient(app)
    yield {
        "client": client,
        "session_factory": session_factory,
        "media_dir": media_dir,
        "inst_id": inst_id,
        "cam_id": cam_id,
        "web_cookie": web_cookie,
    }

    app.dependency_overrides.clear()
    app_state.clear()
    live_manager.clear()
    engine.dispose()


def test_mjpeg_generator_wakes_immediately_on_shutdown_event():
    """_mjpeg_generator мгновенно завершается при установке shutdown_event, не дожидаясь таймаута."""
    live_manager.clear()
    shut_evt = asyncio.Event()
    session = live_manager.get_or_create("cam-shut-96")
    session.is_active = True
    session.mode = "annotated"

    async def run_test() -> float:
        gen = _mjpeg_generator("cam-shut-96", shutdown_event=shut_evt)

        async def consume_all():
            chunks = []
            async for item in gen:
                chunks.append(item)
            return chunks

        t0 = time.monotonic()
        task = asyncio.create_task(consume_all())
        await asyncio.sleep(0.03)
        shut_evt.set()
        chunks = await asyncio.wait_for(task, timeout=0.3)
        elapsed = time.monotonic() - t0
        assert chunks == []
        return elapsed

    elapsed = asyncio.run(run_test())
    assert elapsed < 0.25
    assert session.active_subscribers == 0


def test_proxy_ustreamer_frames_wakes_immediately_on_shutdown_event():
    """_proxy_ustreamer_frames мгновенно выходит при shutdown_event даже при зависшем сокете."""
    live_manager.clear()
    shut_evt = asyncio.Event()
    session = live_manager.get_or_create("cam-proxy-shut")
    session.is_active = True
    session.mode = "raw"
    session.device_online = True
    session.stream_url = "http://127.0.0.1:8080"

    async def slow_aiter_bytes(chunk_size=32768):
        yield b"\xff\xd8FRAME1\xff\xd9"
        await asyncio.sleep(10.0)
        yield b"\xff\xd8FRAME2\xff\xd9"

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.aiter_bytes = slow_aiter_bytes

    fake_stream_ctx = MagicMock()
    fake_stream_ctx.__aenter__ = AsyncMock(return_value=fake_resp)
    fake_stream_ctx.__aexit__ = AsyncMock(return_value=False)

    fake_client = MagicMock()
    fake_client.stream.return_value = fake_stream_ctx
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)

    async def run_proxy() -> float:
        frames = []

        async def consume():
            async for f in _proxy_ustreamer_frames(session, shutdown_event=shut_evt):
                frames.append(f)

        t0 = time.monotonic()
        with patch("httpx.AsyncClient", return_value=fake_client):
            task = asyncio.create_task(consume())
            await asyncio.sleep(0.03)
            shut_evt.set()
            await asyncio.wait_for(task, timeout=0.3)
        elapsed = time.monotonic() - t0
        assert frames == [b"\xff\xd8FRAME1\xff\xd9"]
        return elapsed

    elapsed = asyncio.run(run_proxy())
    assert elapsed < 0.25


def test_live_manager_request_shutdown_and_probe_startup_grace():
    """request_shutdown выставляет shutdown_event; _probe_ustreamer_online_state не ставит офлайн в первые секунды."""
    live_manager.clear()
    session = live_manager.get_or_create("cam-grace-srv")
    session.is_active = True
    session.stream_url = "http://127.0.0.1:8080"
    session.started_at = time.time()
    session.last_frame_at = 0.0

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {"ok": True, "result": {"source": {"online": False}}}
    fake_http = AsyncMock()
    fake_http.get.return_value = fake_resp
    fake_http.__aenter__.return_value = fake_http
    fake_http.__aexit__.return_value = False

    with patch("httpx.AsyncClient", return_value=fake_http):
        asyncio.run(_probe_ustreamer_online_state(session))
        assert session.device_online is True  # прогрев, первый кадр ещё не пришёл

        # После начала потока (last_frame_at > 0) офлайн фиксируется сразу
        session.last_frame_at = time.time()
        session._last_state_probe_at = 0.0
        asyncio.run(_probe_ustreamer_online_state(session))
        assert session.device_online is False

    live_manager.request_shutdown()
    assert live_manager.is_shutting_down is True
    assert session.is_active is False


def test_start_live_and_heartbeat_sync_saved_rgb_and_no_double_multiply(env_96):
    """START_LIVE и heartbeat передают сохранённый RGB; rgb_applied=True предотвращает двойное умножение на сервере."""
    client: TestClient = env_96["client"]
    session_factory = env_96["session_factory"]
    media_dir: Path = env_96["media_dir"]
    inst_id: int = env_96["inst_id"]
    cam_id: int = env_96["cam_id"]

    with session_factory() as db:
        db.add(
            SystemSetting(
                key="camera_rgb_cam-96-hw",
                value=json.dumps({"r": 0.5, "g": 1.0, "b": 0.5}),
                updated_at=datetime.now(UTC),
            )
        )
        db.commit()

    # 1. START_LIVE содержит rgb в payload
    client.cookies.set("gv_session", env_96["web_cookie"])
    # Делаем RPi онлайн через heartbeat
    hb_resp = client.post(
        "/api/edge/heartbeat",
        headers={"X-Edge-Key": TEST_EDGE_KEY},
        json={
            "software": {
                "edge_version": "0.1.0",
                "python_version": "3.12.8",
                "model_name": "none",
            },
            "hardware": {
                "board_model": "Mock",
                "os_info": "Linux",
                "uptime_sec": 10,
                "cpu_temp_c": 40.0,
                "cpu_usage_percent": 10.0,
                "ram_used_mb": 100,
                "ram_total_mb": 1000,
                "disk_free_gb": 10.0,
                "gpio_mode": "mock",
                "mock_cameras": True,
            },
            "offline_queue_count": 0,
            "detected_cameras": [{"hardware_id": "cam-96-hw", "auto_name": "Cam 96"}],
        },
    )
    assert hb_resp.status_code == 200
    hb_data = hb_resp.json()
    assert hb_data["active_installations"][0]["camera_rgb"]["cam-96-hw"] == {
        "r": 0.5,
        "g": 1.0,
        "b": 0.5,
    }

    r_start = client.post(f"/api/cameras/{cam_id}/live/start")
    assert r_start.status_code == 200
    with session_factory() as db:
        cmd = (
            db.query(EdgeCommand)
            .filter(EdgeCommand.command_type == "START_LIVE")
            .order_by(EdgeCommand.id.desc())
            .first()
        )
        assert cmd is not None
        payload = json.loads(cmd.payload)
        assert payload["rgb"] == {"r": 0.5, "g": 1.0, "b": 0.5}

    # 2. Загрузка тестового снимка с rgb_applied=True не перемножает каналы второй раз
    pre_corrected = _make_jpeg_bytes((100, 200, 100))
    meta_test = {
        "installation_id": inst_id,
        "camera_hardware_id": "cam-96-hw",
        "captured_at": "2026-11-25T12:00:00Z",
        "is_manual": True,
        "is_test": True,
        "rgb_applied": True,
        "aruco_area_px": 400.0,
        "measurements": [{"plant_index": 0, "area_px": 1000.0}],
    }
    up_test = client.post(
        "/api/edge/captures",
        headers={"X-Edge-Key": TEST_EDGE_KEY},
        data={"metadata": json.dumps(meta_test)},
        files={
            "raw_image": ("raw.jpg", pre_corrected, "image/jpeg"),
            "annotated_image": ("ann.jpg", pre_corrected, "image/jpeg"),
        },
    )
    assert up_test.status_code == 201
    snap_id = up_test.json()["snapshot_id"]
    r_snap_raw = client.get(f"/api/control/test-snapshots/{snap_id}/raw")
    assert r_snap_raw.status_code == 200
    assert r_snap_raw.content == pre_corrected

    # 3. Плановый снимок с rgb_applied=True сохраняет raw_... без повторного rgb_raw_...
    meta_plan = {
        "installation_id": inst_id,
        "camera_hardware_id": "cam-96-hw",
        "captured_at": "2026-11-25T13:00:00Z",
        "is_manual": False,
        "rgb_applied": True,
        "aruco_area_px": 400.0,
        "measurements": [{"plant_index": 0, "area_px": 1200.0}],
    }
    up_plan = client.post(
        "/api/edge/captures",
        headers={"X-Edge-Key": TEST_EDGE_KEY},
        data={"metadata": json.dumps(meta_plan)},
        files={
            "raw_image": ("raw.jpg", pre_corrected, "image/jpeg"),
            "annotated_image": ("ann.jpg", pre_corrected, "image/jpeg"),
        },
    )
    assert up_plan.status_code == 201
    cap_id = up_plan.json()["capture_id"]
    assert (
        media_dir / str(inst_id) / f"raw_{cap_id}.jpg"
    ).read_bytes() == pre_corrected
    assert not (media_dir / str(inst_id) / f"rgb_raw_{cap_id}.jpg").exists()


def test_lifespan_signal_handler_calls_original_uvicorn_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """lifespan в главном потоке перехватывает SIGINT/SIGTERM, вызывает request_shutdown и оригинальный обработчик."""
    import server.main as main_mod

    db_file = tmp_path / "lifespan_sig.db"
    engine = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    sf = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(main_mod, "SessionLocal", sf)
    monkeypatch.setattr(main_mod, "init_db", lambda: None)

    orig_called = []

    def fake_orig(signum, frame):
        orig_called.append(signum)

    orig_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, fake_orig)

    async def run_lifespan():
        async with main_mod.lifespan(main_mod.app):
            current_handler = signal.getsignal(signal.SIGINT)
            assert current_handler is not fake_orig
            current_handler(signal.SIGINT, None)
            assert orig_called == [signal.SIGINT]
            assert live_manager.is_shutting_down is True

    try:
        asyncio.run(run_lifespan())
        assert signal.getsignal(signal.SIGINT) is fake_orig
    finally:
        signal.signal(signal.SIGINT, orig_sigint)
        live_manager.clear()
        engine.dispose()


def test_web_ui_stage9_6_live_rgb_grace_and_gallery_double_buffer():
    """Проверяет Web UI 9.6: отключение feColorMatrix в Разметке, стартовое окно Live и double-buffer галереи."""
    web_dir = Path(__file__).resolve().parents[2] / "clients" / "web"
    cameras_js = "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted(
            [web_dir / "js" / "cameras.js", *(web_dir / "js" / "cameras").glob("*.js")]
        )
    )
    gallery_js = (web_dir / "js" / "gallery.js").read_text(encoding="utf-8")
    style_css = (web_dir / "style.css").read_text(encoding="utf-8")

    # 1. cameras.js: отключение SVG-фильтра в 'annotated', throttle sync RGB и защита стартового окна
    assert "currentLiveLayer === 'annotated'" in cameras_js
    assert "scheduleLiveRgbSync" in cameras_js
    assert "LIVE_RGB_THROTTLE_MS" in cameras_js
    assert "LIVE_STARTUP_GRACE_MS" in cameras_js
    assert "inStartupGrace" in cameras_js

    # 2. gallery.js + style.css: предзагрузка окна, decode() и двойная буферизация
    assert "preloadFrameWindow" in gallery_js
    assert "ensureDoubleBufferImgs" in gallery_js
    assert "imgEl.decode" in gallery_js
    assert "gallery-buffer-hidden" in gallery_js
    assert ".gallery-main-img.gallery-buffer-hidden" in style_css
