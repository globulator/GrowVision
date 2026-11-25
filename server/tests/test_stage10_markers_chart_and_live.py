import io
import json
import subprocess
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from server.api import edge as edge_api
from server.api.deps import create_session_cookie, get_db
from server.config import get_settings
from server.core.live_manager import live_manager
from server.core.state import app_state
from server.db.database import Base, init_db
from server.db.models import Camera, Capture, Installation, PlantMeasurement, User
from server.main import app

DEV_PASSWORD = "developer-strong-password"
EDGE_KEY = "test-edge-secret-key"
CLIENT_KEY = "test-client-secret-key"


def _make_jpeg() -> bytes:
    img = Image.new("RGB", (32, 32), color=(100, 180, 90))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


@pytest.fixture
def test_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "test_stage10.db"
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

    yield session_factory, media_dir, engine

    app.dependency_overrides.clear()
    app_state.clear()
    live_manager.clear()
    engine.dispose()


@pytest.fixture
def client(test_env) -> TestClient:
    return TestClient(app)


def _create_user_cookie(session_factory, role: str = "developer") -> dict[str, str]:
    db: Session = session_factory()
    now = datetime.now(UTC)
    user = User(
        client_type="web",
        external_id=f"u10-{role}",
        login=f"User {role}",
        role=role,
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


def test_db_migration_and_plant_measurement_metric_fields(
    client: TestClient, test_env, tmp_path: Path
):
    """Безопасная миграция plant_measurements и сохранение width_px/height_px/width_mm/height_mm/area_cm2."""
    # 1. Проверяем миграцию на старой БД без новых колонок
    legacy_db = tmp_path / "legacy_stage10.db"
    legacy_engine = create_engine(
        f"sqlite:///{legacy_db}", connect_args={"check_same_thread": False}
    )
    with legacy_engine.connect() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE plant_measurements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    capture_id INTEGER NOT NULL,
                    plant_index INTEGER NOT NULL DEFAULT 0,
                    area_px FLOAT NOT NULL
                );
                """
            )
        )
        conn.commit()
    init_db(legacy_engine)
    with legacy_engine.connect() as conn:
        cols = {
            row[1]
            for row in conn.execute(
                text("PRAGMA table_info(plant_measurements);")
            ).fetchall()
        }
    assert {"width_px", "height_px", "width_mm", "height_mm", "area_cm2"} <= cols
    legacy_engine.dispose()

    # 2. Сохранение через POST /api/edge/captures
    session_factory, _, _ = test_env
    with session_factory() as db:
        inst = Installation(
            name="Бокс Метрика",
            culture_name="Томат",
            light_gpio_pin=4,
            flash_gpio_pin=17,
            aruco_size_mm=50.0,
        )
        db.add(inst)
        db.flush()
        cam = Camera(
            hardware_id="cam-m10",
            auto_name="Cam M10",
            is_online=True,
            installation_id=inst.id,
        )
        db.add(cam)
        db.commit()
        inst_id = inst.id

    jpg = _make_jpeg()
    meta = {
        "installation_id": inst_id,
        "camera_hardware_id": "cam-m10",
        "captured_at": "2026-11-25T10:00:00Z",
        "is_manual": False,
        "aruco_area_px": 1000.0,
        "measurements": [
            {
                "plant_index": 0,
                "area_px": 4000.0,
                "width_px": 80.0,
                "height_px": 60.0,
                "width_mm": 100.0,
                "height_mm": 75.0,
                "area_cm2": 75.0,
            }
        ],
    }
    resp = client.post(
        "/api/edge/captures",
        headers={"X-Edge-Key": EDGE_KEY},
        data={"metadata": json.dumps(meta)},
        files={
            "raw_image": ("raw.jpg", jpg, "image/jpeg"),
            "annotated_image": ("ann.jpg", jpg, "image/jpeg"),
        },
    )
    assert resp.status_code == 201
    cap_id = resp.json()["capture_id"]

    with session_factory() as db:
        pm = (
            db.query(PlantMeasurement)
            .filter(PlantMeasurement.capture_id == cap_id)
            .one()
        )
        assert pm.area_px == 4000.0
        assert pm.width_px == 80.0
        assert pm.height_px == 60.0
        assert pm.width_mm == 100.0
        assert pm.height_mm == 75.0
        assert pm.area_cm2 == 75.0


def test_timelapse_frames_chart_fields_and_units(client: TestClient, test_env):
    """GET /api/timelapse/{id}/frames отдаёт поля графика и корректно разделяет калиброванные/некалиброванные кадры."""
    session_factory, _, _ = test_env
    with session_factory() as db:
        inst = Installation(
            name="Бокс График",
            culture_name="Салат",
            light_gpio_pin=4,
            flash_gpio_pin=17,
            aruco_size_mm=50.0,
        )
        db.add(inst)
        db.flush()
        cam = Camera(
            hardware_id="cam-chart-10",
            auto_name="Cam Chart",
            is_online=True,
            installation_id=inst.id,
        )
        db.add(cam)
        db.flush()

        # Кадр 1: с ArUco и 2 кустами
        cap1 = Capture(
            installation_id=inst.id,
            camera_id=cam.id,
            captured_at=datetime(2026, 11, 25, 8, 0, tzinfo=UTC),
            is_manual=False,
            aruco_area_px=1000.0,
            raw_image_path=f"media/{inst.id}/raw_1.jpg",
            annotated_image_path=f"media/{inst.id}/ann_1.jpg",
        )
        db.add(cap1)
        db.flush()
        db.add_all(
            [
                PlantMeasurement(
                    capture_id=cap1.id, plant_index=0, area_px=2000.0, area_cm2=50.0
                ),
                PlantMeasurement(
                    capture_id=cap1.id, plant_index=1, area_px=1000.0, area_cm2=25.0
                ),
            ]
        )

        # Кадр 2: без ArUco
        cap2 = Capture(
            installation_id=inst.id,
            camera_id=cam.id,
            captured_at=datetime(2026, 11, 25, 9, 0, tzinfo=UTC),
            is_manual=False,
            aruco_area_px=None,
            raw_image_path=f"media/{inst.id}/raw_2.jpg",
            annotated_image_path=f"media/{inst.id}/ann_2.jpg",
        )
        db.add(cap2)
        db.flush()
        db.add(PlantMeasurement(capture_id=cap2.id, plant_index=0, area_px=3500.0))
        db.commit()
        inst_id = inst.id

    res = client.get(f"/api/timelapse/{inst_id}/frames")
    assert res.status_code == 200
    frames = res.json()["frames"]
    assert len(frames) == 2

    f1, f2 = frames[0], frames[1]
    assert f1["plants_count"] == 2
    assert f1["total_area_px"] == 3000.0
    assert f1["avg_area_px"] == 1500.0
    assert f1["has_aruco"] is True
    assert f1["total_area_cm2"] == 75.0
    assert f1["avg_area_cm2"] == 37.5
    assert f1["area_display"] == "50.0 см²"

    assert f2["plants_count"] == 1
    assert f2["total_area_px"] == 3500.0
    assert f2["avg_area_px"] == 3500.0
    assert f2["has_aruco"] is False
    assert f2["total_area_cm2"] is None
    assert f2["avg_area_cm2"] is None


def test_marker_svg_endpoints(client: TestClient, test_env):
    """Эндпоинты /api/cameras/markers/aruco.svg и color.svg отдают печатные SVG с единицами mm."""
    session_factory, _, _ = test_env
    cookies = _create_user_cookie(session_factory, "biologist")

    r_aruco = client.get("/api/cameras/markers/aruco.svg?size_mm=40", cookies=cookies)
    assert r_aruco.status_code == 200
    assert "image/svg+xml" in r_aruco.headers["content-type"]
    assert 'width="60.00mm"' in r_aruco.text
    assert 'id="scale-ruler"' in r_aruco.text
    assert "DICT_4X4_50" in r_aruco.text

    r_color = client.get("/api/cameras/markers/color.svg?size_mm=50", cookies=cookies)
    assert r_color.status_code == 200
    assert "image/svg+xml" in r_color.headers["content-type"]
    assert 'width="120.00mm"' in r_color.text
    assert "#FF0000" in r_color.text
    assert "#00FF00" in r_color.text
    assert "#0000FF" in r_color.text
    assert "#FFFFFF" in r_color.text
    assert "#808080" in r_color.text


def test_live_color_marker_status_and_auto_color_calibrate_endpoint(
    client: TestClient, test_env
):
    """Live status передаёт color_marker_detected, а POST /auto-color-calibrate возвращает вычисленные RGB."""
    session_factory, _, _ = test_env
    with session_factory() as db:
        from server.db.models import SystemSetting

        db.add(
            SystemSetting(
                key="rpi_last_telemetry",
                value="{}",
                updated_at=datetime.now(UTC),
            )
        )
        cam = Camera(hardware_id="cam-ac-10", auto_name="Cam AC", is_online=True)
        db.add(cam)
        db.commit()
        db.refresh(cam)
        cam_id = cam.id

    cookies = _create_user_cookie(session_factory, "developer")
    jpg = _make_jpeg()

    # 1. Агент шлёт кадр с X-Color-Marker-Detected: 1
    r1 = client.post(
        "/api/edge/cameras/cam-ac-10/live-frame",
        headers={"X-Edge-Key": EDGE_KEY, "X-Color-Marker-Detected": "1"},
        content=jpg,
    )
    assert r1.status_code == 200
    st1 = client.get(f"/api/cameras/{cam_id}/live/status", cookies=cookies).json()
    assert st1["color_marker_detected"] is True

    # 2. Агент шлёт X-Color-Marker-Detected: 0
    client.post(
        "/api/edge/cameras/cam-ac-10/live-frame",
        headers={"X-Edge-Key": EDGE_KEY, "X-Color-Marker-Detected": "0"},
        content=jpg,
    )
    st2 = client.get(f"/api/cameras/{cam_id}/live/status", cookies=cookies).json()
    assert st2["color_marker_detected"] is False

    # 3. Имитация выполнения команды AUTO_COLOR_CALIBRATE в фоне
    import threading
    import time

    def edge_worker():
        for _ in range(40):
            claimed = client.post(
                "/api/edge/commands/claim", headers={"X-Edge-Key": EDGE_KEY}
            ).json()
            if claimed:
                cmd_id = claimed[0]["id"]
                assert claimed[0]["command_type"] == "AUTO_COLOR_CALIBRATE"
                client.post(
                    f"/api/edge/commands/{cmd_id}/result",
                    headers={"X-Edge-Key": EDGE_KEY},
                    json={
                        "status": "done",
                        "result_message": "OK",
                        "data": {"rgb": {"r": 0.62, "g": 1.0, "b": 0.74}},
                    },
                )
                return
            time.sleep(0.05)

    t = threading.Thread(target=edge_worker, daemon=True)
    t.start()
    resp_cal = client.post(
        f"/api/cameras/{cam_id}/auto-color-calibrate", cookies=cookies
    )
    t.join(timeout=3.0)
    assert resp_cal.status_code == 200
    body = resp_cal.json()
    assert body["status"] == "done"
    assert body["rgb"] == {"r": 0.62, "g": 1.0, "b": 0.74}

    # 4. Режим предпросмотра (preview=True): возвращает ROI-кроп и recommended_rgb, НЕ перезаписывая сохранённый профиль
    with session_factory() as db:
        from server.db.models import SystemSetting

        existing_rgb = (
            db.query(SystemSetting)
            .filter(SystemSetting.key == "camera_rgb_cam-ac-10")
            .first()
        )
        assert existing_rgb is not None
        existing_rgb.value = json.dumps({"r": 0.9, "g": 0.9, "b": 0.9})
        db.commit()

    def edge_preview_worker():
        for _ in range(40):
            claimed = client.post(
                "/api/edge/commands/claim", headers={"X-Edge-Key": EDGE_KEY}
            ).json()
            if claimed:
                cmd_id = claimed[0]["id"]
                assert claimed[0]["command_type"] == "AUTO_COLOR_CALIBRATE"
                assert claimed[0]["payload"]["preview"] is True
                client.post(
                    f"/api/edge/commands/{cmd_id}/result",
                    headers={"X-Edge-Key": EDGE_KEY},
                    json={
                        "status": "done",
                        "result_message": "Preview OK",
                        "data": {
                            "rgb": {"r": 0.55, "g": 0.98, "b": 0.72},
                            "recommended_rgb": {"r": 0.55, "g": 0.98, "b": 0.72},
                            "preview": True,
                            "roi_image_base64": "ZmFrZS1qcGVnLXJvaQ==",
                        },
                    },
                )
                return
            time.sleep(0.05)

    t_prev = threading.Thread(target=edge_preview_worker, daemon=True)
    t_prev.start()
    resp_prev = client.post(
        f"/api/cameras/{cam_id}/auto-color-calibrate",
        cookies=cookies,
        json={"preview": True},
    )
    t_prev.join(timeout=3.0)
    assert resp_prev.status_code == 200
    prev_body = resp_prev.json()
    assert prev_body["preview"] is True
    assert prev_body["recommended_rgb"] == {"r": 0.55, "g": 0.98, "b": 0.72}
    assert prev_body["roi_image_base64"] == "ZmFrZS1qcGVnLXJvaQ=="

    # Профиль в БД не должен был измениться при preview=True
    with session_factory() as db:
        from server.db.models import SystemSetting

        setting_after_prev = (
            db.query(SystemSetting)
            .filter(SystemSetting.key == "camera_rgb_cam-ac-10")
            .first()
        )
        assert setting_after_prev is not None
        assert json.loads(setting_after_prev.value) == {"r": 0.9, "g": 0.9, "b": 0.9}

    # 5. Подтверждение через PATCH /controls применяет коэффициенты в БД
    resp_apply = client.patch(
        f"/api/cameras/{cam_id}/controls",
        cookies=cookies,
        json={"rgb": prev_body["recommended_rgb"]},
    )
    assert resp_apply.status_code == 200
    with session_factory() as db:
        from server.db.models import SystemSetting

        setting_after_apply = (
            db.query(SystemSetting)
            .filter(SystemSetting.key == "camera_rgb_cam-ac-10")
            .first()
        )
        assert setting_after_apply is not None
        assert json.loads(setting_after_apply.value) == {
            "r": 0.55,
            "g": 0.98,
            "b": 0.72,
        }


def test_web_ui_stage10_markup_and_chart_scale_consistency():
    """Проверяет отсутствие #btn-live-retry, предпросмотр автоцветокоррекции, кнопки SVG и режим см²/px графика."""
    web_dir = Path(__file__).resolve().parents[2] / "clients" / "web"
    html = (web_dir / "index.html").read_text(encoding="utf-8")
    cameras_js = (web_dir / "js" / "cameras.js").read_text(encoding="utf-8")
    gallery_js = (web_dir / "js" / "gallery.js").read_text(encoding="utf-8")

    # 1. Нет кнопки "Повторить"
    assert "btn-live-retry" not in html
    assert "btn-live-retry" not in cameras_js

    # 2. Стабильная заглушка отключения до первого /live/status и при offline
    assert "hasFirstLiveStatus" in cameras_js
    assert "deviceOfflineShown" in cameras_js

    # 3. Автоцветокоррекция с предпросмотром и скачивание маркеров
    assert "btn-live-auto-color" in cameras_js
    assert "colorMarkerDetected" in cameras_js
    assert "autoColorCalibrate" in cameras_js
    assert 'id="live-auto-color-preview"' in html
    assert 'id="live-auto-color-roi-img"' in html
    assert 'id="btn-live-auto-color-apply"' in html
    assert 'id="btn-live-auto-color-cancel"' in html
    assert "Скачать ArUco-маркер" in html
    assert "Скачать цветовой маркер" in html

    # 4. График роста в галерее: ровно две кнопки ("В см²" и "В px") без "Авто"
    assert 'id="gallery-btn-chart"' in html
    assert 'id="gallery-chart-panel"' in html
    assert 'data-chart-mode="auto"' not in html
    assert 'data-chart-mode="cm2"' in html
    assert 'data-chart-mode="px"' in html
    assert ">В см²<" in html
    assert ">В px<" in html
    assert "prepareGrowthChartSeries" in gallery_js
    assert "resolveDefaultGrowthChartMode" in gallery_js

    # 5. Если доступен node, проверяем JS-логику выбора cm2/px и prepareGrowthChartSeries
    import shutil

    if shutil.which("node"):
        script = f"""
        import {{ prepareGrowthChartSeries, resolveDefaultGrowthChartMode }} from {json.dumps(str(web_dir / "js" / "gallery.js"))};
        const mixed = [
          {{ captured_at: '2026-11-25T08:00:00Z', total_area_px: 1000, avg_area_px: 500, total_area_cm2: 20, avg_area_cm2: 10, plants_count: 2 }},
          {{ captured_at: '2026-11-25T09:00:00Z', total_area_px: 1500, avg_area_px: 750, total_area_cm2: null, avg_area_cm2: null, plants_count: 2 }},
        ];
        if (resolveDefaultGrowthChartMode(mixed) !== 'cm2') throw new Error('mixed must default to cm2');
        const defMixed = prepareGrowthChartSeries(mixed);
        if (defMixed.unit !== 'cm2' || defMixed.points.length !== 1) throw new Error('default mixed must use calibrated cm2');
        const pxMixed = prepareGrowthChartSeries(mixed, 'px');
        if (pxMixed.unit !== 'px' || pxMixed.points.length !== 2) throw new Error('px mode must include all frames');
        const uncal = [
          {{ captured_at: '2026-11-25T08:00:00Z', total_area_px: 1000, avg_area_px: 500, total_area_cm2: null, avg_area_cm2: null, plants_count: 2 }},
          {{ captured_at: '2026-11-25T09:00:00Z', total_area_px: 1500, avg_area_px: 750, total_area_cm2: null, avg_area_cm2: null, plants_count: 2 }},
        ];
        if (resolveDefaultGrowthChartMode(uncal) !== 'px') throw new Error('uncalibrated must default to px');
        const defUncal = prepareGrowthChartSeries(uncal);
        if (defUncal.unit !== 'px' || defUncal.points.length !== 2) throw new Error('default uncal must use px');
        const cm2Uncal = prepareGrowthChartSeries(uncal, 'cm2');
        if (cm2Uncal.unit !== 'cm2' || cm2Uncal.points.length !== 0) throw new Error('explicit cm2 with no calibrated must be empty');
        """
        res = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0, res.stderr
