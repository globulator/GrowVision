import io
import json
import threading
import time
from pathlib import Path

import httpx
from PIL import Image

from server.api.deps import create_session_cookie
from server.db.models import Camera, Capture, PlantMeasurement, User
from tests.e2e.conftest import SAMPLE_HEARTBEAT_PAYLOAD

ROOT_DIR = Path(__file__).resolve().parent.parent.parent


def _make_jpeg(color: tuple[int, int, int]) -> bytes:
    img = Image.new("RGB", (32, 32), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_stage9_5_ephemeral_snap_web_and_bot_e2e(e2e_uvicorn_server):
    """Сквозной тест Этапа 9.5:

    1. Web-клиент (cookie gv_session) и Telegram-бот вызывают/получают тестовый снимок через эфемерный кэш.
    2. Оригинал и разметка доступны по /api/control/test-snapshots/{id}/raw и /annotated (в т.ч. ?download=1).
    3. В БД (captures, plant_measurements) и media/ ничего не сохраняется, frames_count и галерея не меняются.
    4. test_snap.js корректно скачивает оба слоя с credentials и проверкой статуса.
    """
    srv = e2e_uvicorn_server
    base_url = srv["base_url"]
    edge_key = srv["edge_key"]
    client_key = srv["client_key"]
    dev_pass = srv["dev_pass"]
    media_dir: Path = srv["media_dir"]
    session_factory = srv["session_factory"]

    dev_cli_headers = {
        "X-Client-Key": client_key,
        "X-Client-Type": "cli",
        "X-External-Id": "e2e_dev_95",
    }
    tg_headers = {
        "X-Client-Key": client_key,
        "X-Client-Type": "telegram",
        "X-External-Id": "77795",
    }

    with httpx.Client(base_url=base_url) as client:
        # 1. Вход CLI и TG-бота (Developer) + регистрация Edge, установки и камеры
        r_login = client.post(
            "/api/auth/login",
            headers=dev_cli_headers,
            json={
                "client_type": "cli",
                "external_id": "e2e_dev_95",
                "password": dev_pass,
                "login": "CLI Dev 95",
            },
        )
        assert r_login.status_code == 200

        r_tg_login = client.post(
            "/api/auth/login",
            headers=tg_headers,
            json={
                "client_type": "telegram",
                "external_id": "77795",
                "password": dev_pass,
                "login": "TG Dev 95",
            },
        )
        assert r_tg_login.status_code == 200

        r_hb = client.post(
            "/api/edge/heartbeat",
            headers={"X-Edge-Key": edge_key},
            json=SAMPLE_HEARTBEAT_PAYLOAD,
        )
        assert r_hb.status_code == 200

        r_inst = client.post(
            "/api/installations",
            headers=dev_cli_headers,
            json={
                "name": "Ephemeral E2E Unit",
                "culture_name": "Рукола",
                "light_gpio_pin": 17,
                "flash_gpio_pin": 27,
                "aruco_size_mm": 20.0,
            },
        )
        assert r_inst.status_code == 201
        inst_id = r_inst.json()["id"]

    # 2. Регистрируем камеру (привязанную к установке) и Web-пользователя (Developer) с cookie gv_session
    with session_factory() as db:
        cam = Camera(
            hardware_id="cam-rpi-01",
            auto_name="RPi CSI Camera 1",
            installation_id=inst_id,
            is_online=True,
        )
        web_user = User(
            client_type="web",
            external_id="99995",
            login="Web Dev 95",
            role="developer",
            is_active=True,
        )
        db.add_all([cam, web_user])
        db.commit()
        db.refresh(web_user)
        cam_hw = cam.hardware_id
        web_cookie = create_session_cookie(web_user)

    with httpx.Client(base_url=base_url) as web_client:
        web_client.cookies.set("gv_session", web_cookie)

        # 3. Фоновый RPi-воркер обрабатывает SNAP с is_test=True
        raw_jpeg = _make_jpeg((180, 210, 170))
        ann_jpeg = _make_jpeg((40, 240, 80))
        stop_ev = threading.Event()

        def rpi_worker():
            with httpx.Client(base_url=base_url) as edge_client:
                while not stop_ev.is_set():
                    r_claim = edge_client.post(
                        "/api/edge/commands/claim",
                        headers={"X-Edge-Key": edge_key},
                    )
                    if r_claim.status_code == 200 and r_claim.json():
                        cmd = r_claim.json()[0]
                        meta = {
                            "installation_id": inst_id,
                            "camera_hardware_id": cam_hw,
                            "captured_at": "2026-11-25T12:00:00Z",
                            "is_manual": True,
                            "is_test": True,
                            "aruco_area_px": 400.0,
                            "command_id": cmd["id"],
                            "measurements": [{"plant_index": 0, "area_px": 1600.0}],
                        }
                        r_up = edge_client.post(
                            "/api/edge/captures",
                            headers={"X-Edge-Key": edge_key},
                            data={"metadata": json.dumps(meta)},
                            files={
                                "raw_image": ("raw.jpg", raw_jpeg, "image/jpeg"),
                                "annotated_image": ("ann.jpg", ann_jpeg, "image/jpeg"),
                            },
                        )
                        assert r_up.status_code == 201
                        assert r_up.json()["ephemeral"] is True
                        edge_client.post(
                            f"/api/edge/commands/{cmd['id']}/result",
                            headers={"X-Edge-Key": edge_key},
                            json={"status": "done", "result_message": "OK"},
                        )
                        return
                    time.sleep(0.05)

        th = threading.Thread(target=rpi_worker, daemon=True)
        th.start()

        try:
            r_snap = web_client.post(
                "/api/control/snap",
                json={"target": inst_id},
                timeout=15.0,
            )
            assert r_snap.status_code == 200
            snap_data = r_snap.json()
            assert snap_data["status"] == "done"
            assert len(snap_data["captures"]) == 1
            cap = snap_data["captures"][0]
            assert cap["ephemeral"] is True
            assert cap["area_display"] == "16.0 см²"
            snap_id = cap["snapshot_id"]
            assert cap["raw_url"] == f"/api/control/test-snapshots/{snap_id}/raw"
            assert (
                cap["annotated_url"]
                == f"/api/control/test-snapshots/{snap_id}/annotated"
            )

            # Web-клиент скачивает оба слоя по cookie gv_session
            r_web_raw = web_client.get(f"{cap['raw_url']}?download=1")
            assert r_web_raw.status_code == 200
            assert "attachment;" in r_web_raw.headers.get("content-disposition", "")
            assert r_web_raw.content == raw_jpeg

            r_web_ann = web_client.get(f"{cap['annotated_url']}?download=1")
            assert r_web_ann.status_code == 200
            assert "attachment;" in r_web_ann.headers.get("content-disposition", "")
            assert r_web_ann.content == ann_jpeg

            # Telegram-бот также может получить метаданные и скачать оба слоя
            with httpx.Client(base_url=base_url, headers=tg_headers) as tg_client:
                r_tg_info = tg_client.get(f"/api/control/test-snapshots/{snap_id}")
                assert r_tg_info.status_code == 200
                r_tg_raw = tg_client.get(cap["raw_url"])
                assert r_tg_raw.status_code == 200
                assert r_tg_raw.content == raw_jpeg
                r_tg_ann = tg_client.get(cap["annotated_url"])
                assert r_tg_ann.status_code == 200
                assert r_tg_ann.content == ann_jpeg

            # Проверяем, что БД и media_dir не изменились, галерея и frames_count пусты
            r_insts = web_client.get("/api/installations")
            assert r_insts.status_code == 200
            unit_row = next(u for u in r_insts.json() if u["id"] == inst_id)
            assert unit_row["frames_count"] == 0
            assert unit_row["last_frame_url"] is None

            r_tl = web_client.get(f"/api/timelapse/{inst_id}/frames")
            assert r_tl.status_code == 200
            assert r_tl.json()["frames"] == []

            with session_factory() as db:
                assert db.query(Capture).count() == 0
                assert db.query(PlantMeasurement).count() == 0
            assert list(media_dir.rglob("*.jpg")) == []
        finally:
            stop_ev.set()
            th.join(timeout=3.0)

    # 4. Проверяем клиентский JS-код test_snap.js + camera_live.js + api.js
    web_js_dir = ROOT_DIR / "clients" / "web" / "js"
    test_snap_js = (web_js_dir / "test_snap.js").read_text(encoding="utf-8")
    camera_live_js = (web_js_dir / "cameras" / "camera_live.js").read_text(
        encoding="utf-8"
    )
    api_js = (web_js_dir / "api.js").read_text(encoding="utf-8")
    assert "showError" in test_snap_js
    assert "downloadSnapImage" in test_snap_js
    assert "download=1" in camera_live_js
    assert "credentials: 'same-origin'" in api_js
    assert "if (!response.ok)" in api_js
