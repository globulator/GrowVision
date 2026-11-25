import io
import json
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from server.api.deps import create_session_cookie, get_db
from server.config import get_settings
from server.core.state import TEST_SNAPSHOT_TTL_SECONDS, app_state
from server.db.database import Base
from server.db.models import (
    Camera,
    Capture,
    EdgeCommand,
    Installation,
    PlantMeasurement,
    SystemSetting,
    User,
)
from server.main import app

TEST_EDGE_KEY = "test-edge-key-95"
TEST_CLIENT_KEY = "test-client-key-95"


def _make_jpeg_bytes(color: tuple[int, int, int] = (200, 200, 200)) -> bytes:
    img = Image.new("RGB", (32, 32), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def env_95(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Изолированное окружение для тестов эфемерных тестовых снимков (Этап 9.5)."""
    db_file = tmp_path / "test_95.db"
    engine = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("server.api.edge.get_media_dir", lambda: media_dir)
    monkeypatch.setattr("server.api.timelapse.get_media_dir", lambda: media_dir)

    settings = get_settings()
    monkeypatch.setattr(settings, "edge_api_key", TEST_EDGE_KEY)
    monkeypatch.setattr(settings, "client_api_key", TEST_CLIENT_KEY)
    app_state.clear()

    def override_get_db() -> Generator[Session, None, None]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    with session_factory() as db:
        inst = Installation(
            name="Ephemeral Box",
            culture_name="Базилик",
            light_gpio_pin=17,
            flash_gpio_pin=27,
            aruco_size_mm=20.0,
            is_archived=False,
        )
        db.add(inst)
        db.flush()

        cam = Camera(
            hardware_id="cam-95-hw",
            auto_name="Cam 95",
            installation_id=inst.id,
            is_online=True,
        )
        db.add(cam)

        web_user = User(
            client_type="web",
            external_id="1001",
            login="Web Dev",
            role="developer",
            is_active=True,
        )
        tg_user = User(
            client_type="telegram",
            external_id="2002",
            login="TG Bio",
            role="biologist",
            is_active=True,
        )
        db.add_all([web_user, tg_user])
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
        "tg_headers": {
            "X-Client-Key": TEST_CLIENT_KEY,
            "X-Client-Type": "telegram",
            "X-External-Id": "2002",
        },
    }

    app.dependency_overrides.clear()
    app_state.clear()
    engine.dispose()


def test_edge_upload_is_test_saves_only_in_memory_and_applies_rgb(env_95):
    """is_test=True не создаёт записей в БД и файлов в media/, применяет RGB в памяти."""
    client: TestClient = env_95["client"]
    session_factory = env_95["session_factory"]
    media_dir: Path = env_95["media_dir"]
    inst_id: int = env_95["inst_id"]

    # Задаём RGB-профиль (убираем красный и синий каналы)
    with session_factory() as db:
        db.add(
            SystemSetting(
                key="camera_rgb_cam-95-hw",
                value=json.dumps({"r": 0.0, "g": 1.0, "b": 0.0}),
                updated_at=datetime.now(UTC),
            )
        )
        db.commit()

    raw_bytes = _make_jpeg_bytes((200, 150, 180))
    ann_bytes = _make_jpeg_bytes((10, 220, 30))

    meta = {
        "installation_id": inst_id,
        "camera_hardware_id": "cam-95-hw",
        "captured_at": "2026-11-25T10:00:00Z",
        "is_manual": True,
        "is_test": True,
        "aruco_area_px": 400.0,
        "measurements": [{"plant_index": 0, "area_px": 1200.0}],
    }
    resp = client.post(
        "/api/edge/captures",
        headers={"X-Edge-Key": TEST_EDGE_KEY},
        data={"metadata": json.dumps(meta)},
        files={
            "raw_image": ("raw.jpg", io.BytesIO(raw_bytes), "image/jpeg"),
            "annotated_image": ("ann.jpg", io.BytesIO(ann_bytes), "image/jpeg"),
        },
    )
    assert resp.status_code == 201
    payload = resp.json()
    assert payload["ephemeral"] is True
    snap_id = payload["snapshot_id"]
    assert snap_id

    # В БД и на диске 0 записей/файлов
    with session_factory() as db:
        assert db.query(Capture).count() == 0
        assert db.query(PlantMeasurement).count() == 0
    assert list(media_dir.rglob("*")) == []

    # Авторизация: аноним -> 401
    assert client.get(f"/api/control/test-snapshots/{snap_id}/raw").status_code == 401

    # Web по cookie -> 200, JPEG с применённым RGB-профилем (R и B обнулены)
    client.cookies.set("gv_session", env_95["web_cookie"])
    r_raw = client.get(f"/api/control/test-snapshots/{snap_id}/raw")
    assert r_raw.status_code == 200
    assert r_raw.headers["content-type"].startswith("image/jpeg")
    assert "inline;" in r_raw.headers["content-disposition"]
    with Image.open(io.BytesIO(r_raw.content)) as img:
        r_px, g_px, b_px = img.getpixel((16, 16))
        assert r_px < 15
        assert g_px > 120
        assert b_px < 15

    # Скачивание с download=1 -> attachment
    r_ann_dl = client.get(f"/api/control/test-snapshots/{snap_id}/annotated?download=1")
    assert r_ann_dl.status_code == 200
    assert "attachment;" in r_ann_dl.headers["content-disposition"]
    assert r_ann_dl.content == ann_bytes

    # Bot по X-Client-Key + X-Client-Type + X-External-Id -> 200
    client.cookies.clear()
    r_bot = client.get(
        f"/api/control/test-snapshots/{snap_id}/raw",
        headers=env_95["tg_headers"],
    )
    assert r_bot.status_code == 200

    # Проверка latest и совместимости /api/control/captures/{snapshot_id}
    r_latest = client.get(
        f"/api/control/test-snapshots/latest/{inst_id}",
        headers=env_95["tg_headers"],
    )
    assert r_latest.status_code == 200
    assert r_latest.json()["snapshot_id"] == snap_id
    assert r_latest.json()["area_display"] == "12.0 см²"

    r_compat = client.get(
        f"/api/control/captures/{snap_id}",
        headers=env_95["tg_headers"],
    )
    assert r_compat.status_code == 200
    assert r_compat.json()["snapshot_id"] == snap_id


def test_edge_upload_snap_command_without_is_test_still_ephemeral_and_scheduled_persists(
    env_95,
):
    """Команда SNAP (даже без is_test) кэшируется в памяти, а плановый кадр пишется в БД и media/."""
    client: TestClient = env_95["client"]
    session_factory = env_95["session_factory"]
    media_dir: Path = env_95["media_dir"]
    inst_id: int = env_95["inst_id"]

    with session_factory() as db:
        cmd = EdgeCommand(
            command_type="SNAP",
            installation_id=inst_id,
            is_global=False,
            status="running",
        )
        db.add(cmd)
        db.commit()
        cmd_id = cmd.id

    raw_bytes = _make_jpeg_bytes((120, 120, 120))
    ann_bytes = _make_jpeg_bytes((60, 180, 60))

    # 1. Загрузка кадра по команде SNAP без явного is_test -> эфемерно
    meta_snap = {
        "installation_id": inst_id,
        "camera_hardware_id": "cam-95-hw",
        "captured_at": "2026-11-25T10:05:00Z",
        "is_manual": True,
        "command_id": cmd_id,
        "aruco_area_px": 400.0,
        "measurements": [{"plant_index": 0, "area_px": 800.0}],
    }
    r_snap_up = client.post(
        "/api/edge/captures",
        headers={"X-Edge-Key": TEST_EDGE_KEY},
        data={"metadata": json.dumps(meta_snap)},
        files={
            "raw_image": ("raw.jpg", io.BytesIO(raw_bytes), "image/jpeg"),
            "annotated_image": ("ann.jpg", io.BytesIO(ann_bytes), "image/jpeg"),
        },
    )
    assert r_snap_up.status_code == 201
    assert r_snap_up.json()["ephemeral"] is True
    with session_factory() as db:
        assert db.query(Capture).count() == 0
        assert db.query(PlantMeasurement).count() == 0
    assert list(media_dir.rglob("*.jpg")) == []

    # 2. Плановый кадр (is_test=False, is_manual=False) -> сохраняется в БД и на диск!
    meta_plan = {
        "installation_id": inst_id,
        "camera_hardware_id": "cam-95-hw",
        "captured_at": "2026-11-25T11:00:00Z",
        "is_manual": False,
        "is_test": False,
        "aruco_area_px": 400.0,
        "measurements": [{"plant_index": 0, "area_px": 1000.0}],
    }
    r_plan = client.post(
        "/api/edge/captures",
        headers={"X-Edge-Key": TEST_EDGE_KEY},
        data={"metadata": json.dumps(meta_plan)},
        files={
            "raw_image": ("raw.jpg", io.BytesIO(raw_bytes), "image/jpeg"),
            "annotated_image": ("ann.jpg", io.BytesIO(ann_bytes), "image/jpeg"),
        },
    )
    assert r_plan.status_code == 201
    cap_id = r_plan.json()["capture_id"]
    with session_factory() as db:
        assert db.query(Capture).count() == 1
        assert db.query(PlantMeasurement).count() == 1
    assert (media_dir / str(inst_id) / f"raw_{cap_id}.jpg").is_file()
    assert (media_dir / str(inst_id) / f"ann_{cap_id}.jpg").is_file()


def test_ephemeral_snapshot_ttl_expiration_returns_404(
    env_95, monkeypatch: pytest.MonkeyPatch
):
    """После истечения TTL (10 минут) эфемерный снимок удаляется и отдаёт 404."""
    client: TestClient = env_95["client"]
    inst_id: int = env_95["inst_id"]

    fake_now = [1000.0]
    monkeypatch.setattr("server.core.state.time.monotonic", lambda: fake_now[0])

    snap = app_state.save_test_snapshot(
        installation_id=inst_id,
        camera_id=env_95["cam_id"],
        camera_hardware_id="cam-95-hw",
        captured_at="2026-11-25T10:10:00Z",
        raw_bytes=_make_jpeg_bytes(),
        annotated_bytes=_make_jpeg_bytes(),
        area_display="10.0 см²",
    )

    # Пока TTL не истёк (+599 с) -> 200
    fake_now[0] = 1000.0 + TEST_SNAPSHOT_TTL_SECONDS - 1.0
    assert (
        client.get(
            f"/api/control/test-snapshots/{snap.snapshot_id}/raw",
            headers=env_95["tg_headers"],
        ).status_code
        == 200
    )

    # После истечения TTL (+601 с) -> 404 и автоочистка
    fake_now[0] = 1000.0 + TEST_SNAPSHOT_TTL_SECONDS + 1.0
    assert (
        client.get(
            f"/api/control/test-snapshots/{snap.snapshot_id}/raw",
            headers=env_95["tg_headers"],
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/control/test-snapshots/{snap.snapshot_id}/annotated",
            headers=env_95["tg_headers"],
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/control/test-snapshots/latest/{inst_id}",
            headers=env_95["tg_headers"],
        ).status_code
        == 404
    )
    assert app_state.get_test_snapshot(snap.snapshot_id) is None
