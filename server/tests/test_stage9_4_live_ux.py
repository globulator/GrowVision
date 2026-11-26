import asyncio
import io
import json
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
from server.api.cameras import _mjpeg_generator, _probe_ustreamer_online_state
from server.api.deps import create_session_cookie, get_db
from server.config import get_settings
from server.core.live_manager import live_manager
from server.core.state import app_state
from server.db.database import Base
from server.db.models import Camera, Installation, User
from server.main import app

DEV_PASSWORD = "developer-strong-password"
EDGE_KEY = "test-edge-secret-key"
CLIENT_KEY = "test-client-secret-key"


def _make_solid_jpeg(rgb: tuple[int, int, int] = (200, 160, 100)) -> bytes:
    img = Image.new("RGB", (24, 24), color=rgb)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=98)
    return buf.getvalue()


def _read_center_rgb(jpeg_bytes: bytes) -> tuple[int, int, int]:
    with Image.open(io.BytesIO(jpeg_bytes)) as img:
        return img.convert("RGB").getpixel((12, 12))


@pytest.fixture
def test_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "test_stage9_4.db"
    db_url = f"sqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(edge_api, "get_media_dir", lambda: media_dir)
    monkeypatch.setattr("server.api.timelapse.get_media_dir", lambda: media_dir)

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

    yield session_factory, media_dir

    app.dependency_overrides.clear()
    app_state.clear()
    live_manager.clear()
    engine.dispose()


@pytest.fixture
def client(test_env) -> TestClient:
    return TestClient(app)


def create_dev_cookie(session_factory, ext_id: str = "dev-94") -> dict[str, str]:
    db: Session = session_factory()
    now = datetime.now(UTC)
    user = User(
        client_type="web",
        external_id=ext_id,
        login="Dev 9.4",
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


def test_live_device_offline_via_edge_header_and_probe(client: TestClient, test_env):
    """Статус device_online=false от агента или /state ustreamer отражается в /live/status."""
    session_factory, _ = test_env
    db: Session = session_factory()
    cam = Camera(hardware_id="cam-off-94", auto_name="Cam Off", is_online=True)
    db.add(cam)
    db.commit()
    db.refresh(cam)
    cam_id = cam.id
    db.close()

    cookies = create_dev_cookie(session_factory, "dev-94-off")

    # 1. Агент сообщает об отключении физической камеры без кадра (X-Device-Online: 0)
    r_off = client.post(
        "/api/edge/cameras/cam-off-94/live-frame",
        headers={"X-Edge-Key": EDGE_KEY, "X-Device-Online": "0"},
        content=b"",
    )
    assert r_off.status_code == 200
    assert r_off.json()["status"] == "ok"

    st1 = client.get(f"/api/cameras/{cam_id}/live/status", cookies=cookies).json()
    assert st1["device_online"] is False
    assert st1["stream_error"] == "Камера отключена"
    assert st1["fps"] == 0.0

    # 2. Камера вернулась онлайн с кадром
    jpeg = _make_solid_jpeg()
    r_on = client.post(
        "/api/edge/cameras/cam-off-94/live-frame",
        headers={"X-Edge-Key": EDGE_KEY, "X-Device-Online": "1"},
        content=jpeg,
    )
    assert r_on.status_code == 200
    st2 = client.get(f"/api/cameras/{cam_id}/live/status", cookies=cookies).json()
    assert st2["device_online"] is True
    assert st2["stream_error"] is None

    # 3. Прямая проверка /state ustreamer через _probe_ustreamer_online_state
    session = live_manager.get("cam-off-94")
    assert session is not None
    session.stream_url = "http://127.0.0.1:8080"
    session._last_state_probe_at = 0.0

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {"ok": True, "result": {"source": {"online": False}}}
    fake_http = AsyncMock()
    fake_http.get.return_value = fake_resp
    fake_http.__aenter__.return_value = fake_http
    fake_http.__aexit__.return_value = False

    with patch("httpx.AsyncClient", return_value=fake_http):
        asyncio.run(_probe_ustreamer_online_state(session))
    assert session.device_online is False


def test_live_mode_switch_raw_and_annotated(client: TestClient, test_env):
    """Переключение Live между 'raw' и 'annotated' управляет потоком и метриками."""
    session_factory, _ = test_env
    db: Session = session_factory()
    cam = Camera(hardware_id="cam-mode-94", auto_name="Cam Mode", is_online=True)
    db.add(cam)
    db.commit()
    db.refresh(cam)
    cam_id = cam.id
    db.close()

    cookies = create_dev_cookie(session_factory, "dev-94-mode")
    raw_jpg = _make_solid_jpeg((10, 200, 10))
    ann_jpg = _make_solid_jpeg((200, 20, 200))

    # 1. Переключение в 'annotated'
    r_mode = client.post(
        f"/api/cameras/{cam_id}/live/mode",
        json={"mode": "annotated"},
        cookies=cookies,
    )
    assert r_mode.status_code == 200
    assert r_mode.json()["mode"] == "annotated"

    # 2. Запоздалый raw-кадр не перезаписывает annotated-поток, а в ответе агенту уходит mode='annotated'
    r_raw_push = client.post(
        "/api/edge/cameras/cam-mode-94/live-frame",
        headers={"X-Edge-Key": EDGE_KEY, "X-Frame-Mode": "raw"},
        content=raw_jpg,
    )
    assert r_raw_push.status_code == 200
    assert r_raw_push.json()["mode"] == "annotated"

    # 3. Размеченный кадр принимается и обновляет latest_annotated_frame
    r_ann_push = client.post(
        "/api/edge/cameras/cam-mode-94/live-frame",
        headers={
            "X-Edge-Key": EDGE_KEY,
            "X-Frame-Mode": "annotated",
            "X-Latency-Ms": "85",
        },
        content=ann_jpg,
    )
    assert r_ann_push.status_code == 200
    st = client.get(f"/api/cameras/{cam_id}/live/status", cookies=cookies).json()
    assert st["mode"] == "annotated"
    assert st["latency_ms"] >= 85

    # 4. В режиме 'annotated' _mjpeg_generator отдаёт ann_jpg, не проксируя ustreamer
    sess = live_manager.get("cam-mode-94")
    assert sess is not None
    sess.stream_url = "http://127.0.0.1:8080"

    async def read_one_annotated_chunk():
        with patch("server.api.cameras._proxy_ustreamer_frames") as proxy_mock:
            gen = _mjpeg_generator("cam-mode-94")
            chunk = await anext(gen)
            proxy_mock.assert_not_called()
            await gen.aclose()
            return chunk

    chunk = asyncio.run(read_one_annotated_chunk())
    assert ann_jpg in chunk

    # 5. Обратное переключение на 'raw'
    r_back = client.post(
        f"/api/cameras/{cam_id}/live/mode",
        json={"mode": "raw"},
        cookies=cookies,
    )
    assert r_back.status_code == 200
    assert r_back.json()["mode"] == "raw"


def test_live_snapshot_rgb_filtering_and_range(client: TestClient, test_env):
    """GET /live/snapshot применяет r/g/b из диапазона 0.0-1.0 к JPEG и отклоняет выход за пределы."""
    session_factory, _ = test_env
    db: Session = session_factory()
    cam = Camera(hardware_id="cam-rgb-94", auto_name="Cam RGB", is_online=True)
    db.add(cam)
    db.commit()
    db.refresh(cam)
    cam_id = cam.id
    db.close()

    cookies = create_dev_cookie(session_factory, "dev-94-rgb")
    src_jpeg = _make_solid_jpeg((200, 160, 100))

    sess = live_manager.get_or_create("cam-rgb-94", camera_id=cam_id)
    sess.is_active = True
    sess.latest_frame = src_jpeg
    sess.last_frame_at = datetime.now(UTC).timestamp()

    # Проверка фильтрации R=0.5, G=1.0, B=0.2
    resp = client.get(
        f"/api/cameras/{cam_id}/live/snapshot?r=0.50&g=1.00&b=0.20",
        cookies=cookies,
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    r_val, g_val, b_val = _read_center_rgb(resp.content)
    assert abs(r_val - 100) <= 5
    assert abs(g_val - 160) <= 5
    assert abs(b_val - 20) <= 5

    # Выход за диапазон [0.0, 1.0] -> 422
    bad_resp = client.get(
        f"/api/cameras/{cam_id}/live/snapshot?r=1.5",
        cookies=cookies,
    )
    assert bad_resp.status_code == 422


def test_save_controls_next_frame_vs_all_gallery_preserves_originals(
    client: TestClient, test_env
):
    """Сохранение 'next_frame' и 'all_gallery' применяет RGB, не разрушая исходные оригиналы raw_...jpg."""
    session_factory, media_dir = test_env
    db: Session = session_factory()
    inst = Installation(
        name="Грядка RGB",
        culture_name="Базилик",
        light_gpio_pin=4,
        flash_gpio_pin=17,
        is_archived=False,
    )
    db.add(inst)
    db.flush()
    cam = Camera(
        hardware_id="cam-save-94",
        auto_name="Cam Save",
        is_online=True,
        installation_id=inst.id,
    )
    db.add(cam)
    db.commit()
    db.refresh(inst)
    db.refresh(cam)
    inst_id, cam_id = inst.id, cam.id
    db.close()

    cookies = create_dev_cookie(session_factory, "dev-94-save")
    orig_jpeg = _make_solid_jpeg((200, 200, 200))

    # 1. Загружаем первый плановый снимок (до смены RGB)
    meta1 = {
        "installation_id": inst_id,
        "camera_hardware_id": "cam-save-94",
        "captured_at": "2026-11-25T08:00:00Z",
        "is_manual": False,
        "aruco_area_px": 100.0,
        "measurements": [{"plant_index": 0, "area_px": 500.0}],
    }
    up1 = client.post(
        "/api/edge/captures",
        headers={"X-Edge-Key": EDGE_KEY},
        data={"metadata": json.dumps(meta1)},
        files={
            "raw_image": ("raw.jpg", orig_jpeg, "image/jpeg"),
            "annotated_image": ("ann.jpg", orig_jpeg, "image/jpeg"),
        },
    )
    assert up1.status_code == 201
    cap1_id = up1.json()["capture_id"]

    # 2. Сохраняем RGB с режимом "next_frame" (Со следующего кадра)
    save_next = client.post(
        f"/api/cameras/{cam_id}/controls/save",
        json={"scope": "next_frame", "rgb": {"r": 0.5, "g": 1.0, "b": 0.5}},
        cookies=cookies,
    )
    assert save_next.status_code == 200
    assert save_next.json()["updated_captures"] == 0

    frames_after_next = client.get(f"/api/timelapse/{inst_id}/frames").json()["frames"]
    assert frames_after_next[0]["raw_url"] == f"/media/{inst_id}/raw_{cap1_id}.jpg"

    # 3. Загружаем второй плановый снимок (после смены RGB) -> у него появляется rgb_raw_...jpg
    meta2 = {
        "installation_id": inst_id,
        "camera_hardware_id": "cam-save-94",
        "captured_at": "2026-11-25T09:00:00Z",
        "is_manual": False,
        "aruco_area_px": 100.0,
        "measurements": [{"plant_index": 0, "area_px": 600.0}],
    }
    up2 = client.post(
        "/api/edge/captures",
        headers={"X-Edge-Key": EDGE_KEY},
        data={"metadata": json.dumps(meta2)},
        files={
            "raw_image": ("raw.jpg", orig_jpeg, "image/jpeg"),
            "annotated_image": ("ann.jpg", orig_jpeg, "image/jpeg"),
        },
    )
    assert up2.status_code == 201
    cap2_id = up2.json()["capture_id"]

    frames_with_new = client.get(f"/api/timelapse/{inst_id}/frames").json()["frames"]
    assert frames_with_new[0]["raw_url"] == f"/media/{inst_id}/raw_{cap1_id}.jpg"
    assert frames_with_new[1]["raw_url"] == f"/media/{inst_id}/rgb_raw_{cap2_id}.jpg"
    rgb_file_2 = media_dir / str(inst_id) / f"rgb_raw_{cap2_id}.jpg"
    r2, g2, b2 = _read_center_rgb(rgb_file_2.read_bytes())
    assert abs(r2 - 100) <= 6 and abs(g2 - 200) <= 6 and abs(b2 - 100) <= 6

    # 4. Сохраняем новый RGB со scope="all_gallery" (Для всей галереи)
    save_all = client.post(
        f"/api/cameras/{cam_id}/controls/save",
        json={"scope": "all_gallery", "rgb": {"r": 0.25, "g": 0.5, "b": 1.0}},
        cookies=cookies,
    )
    assert save_all.status_code == 200
    assert save_all.json()["updated_captures"] == 2

    # Исходные оригиналы raw_...jpg остались побайтово нетронутыми!
    orig_path_1 = media_dir / str(inst_id) / f"raw_{cap1_id}.jpg"
    orig_path_2 = media_dir / str(inst_id) / f"raw_{cap2_id}.jpg"
    assert orig_path_1.read_bytes() == orig_jpeg
    assert orig_path_2.read_bytes() == orig_jpeg

    # Отфильтрованные копии созданы для обоих кадров и отдаются в галерее
    frames_all = client.get(f"/api/timelapse/{inst_id}/frames").json()["frames"]
    assert frames_all[0]["raw_url"] == f"/media/{inst_id}/rgb_raw_{cap1_id}.jpg"
    assert frames_all[1]["raw_url"] == f"/media/{inst_id}/rgb_raw_{cap2_id}.jpg"
    r1, g1, b1 = _read_center_rgb(
        (media_dir / str(inst_id) / f"rgb_raw_{cap1_id}.jpg").read_bytes()
    )
    assert abs(r1 - 50) <= 6 and abs(g1 - 100) <= 6 and abs(b1 - 200) <= 6

    # 5. Сброс RGB на (1.0, 1.0, 1.0) для всей галереи восстанавливает оригиналы
    save_reset = client.post(
        f"/api/cameras/{cam_id}/controls/save",
        json={"scope": "all_gallery", "rgb": {"r": 1.0, "g": 1.0, "b": 1.0}},
        cookies=cookies,
    )
    assert save_reset.status_code == 200
    frames_reset = client.get(f"/api/timelapse/{inst_id}/frames").json()["frames"]
    assert frames_reset[0]["raw_url"] == f"/media/{inst_id}/raw_{cap1_id}.jpg"
    assert frames_reset[1]["raw_url"] == f"/media/{inst_id}/raw_{cap2_id}.jpg"
    assert not (media_dir / str(inst_id) / f"rgb_raw_{cap1_id}.jpg").exists()


def test_web_ui_stage9_4_markup_and_styles():
    """Проверяет ключевые элементы этапа 9.4 в Web SPA (без регрессий)."""
    web_dir = Path(__file__).resolve().parents[2] / "clients" / "web"
    html = (web_dir / "index.html").read_text(encoding="utf-8")
    cameras_js = "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted(
            [web_dir / "js" / "cameras.js", *(web_dir / "js" / "cameras").glob("*.js")]
        )
    )
    units_js = (web_dir / "js" / "units.js").read_text(encoding="utf-8")
    style_css = (web_dir / "style.css").read_text(encoding="utf-8")

    # Переключатель Оригинал/Разметка, SVG feColorMatrix, диалог несохраненных изменений, единый лоадер
    assert 'id="live-layer-raw"' in html
    assert 'id="live-layer-ann"' in html
    assert 'id="live-rgb-filter"' in html
    assert "<feColorMatrix" in html
    assert 'id="modal-live-unsaved"' in html
    assert 'class="live-spinner"' not in html

    # В units.js подпись кнопки сокращена до 'Снимок'
    assert "<span>Снимок</span>" in units_js
    assert "<span>Тестовый снимок</span>" not in units_js

    # В style.css кнопки карточек переносятся через flex-wrap
    assert "flex-wrap: wrap;" in style_css

    # В cameras.js есть toggle-кнопки, единицы измерения, RGB 0..1, сброс default и проверка изменений
    assert "getLoadingSpinnerHTML" in cameras_js
    assert "live-toggle-btn" in cameras_js
    assert "exposure_time_absolute" in cameras_js and "мс" in cameras_js
    assert "white_balance_temperature" in cameras_js and "К" in cameras_js
    assert "power_line_frequency" in cameras_js and "Гц" in cameras_js
    assert "hasUnsavedLiveChanges" in cameras_js
    assert "Камера отключена" in cameras_js
