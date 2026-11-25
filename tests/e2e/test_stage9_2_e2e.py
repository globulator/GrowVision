# tests/e2e/test_stage9_2_e2e.py
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx

from server.db.models import Camera, Installation, SystemSetting, User


def test_stage9_2_web_markup_and_invariants():
    """Проверка DOM-разметки элементов Live-стрима и инвариантов Web UI (0 эмодзи)."""
    web_dir = Path(__file__).resolve().parent.parent.parent / "clients" / "web"
    index_html = (web_dir / "index.html").read_text(encoding="utf-8")
    style_css = (web_dir / "style.css").read_text(encoding="utf-8")
    cameras_js = (web_dir / "js" / "cameras.js").read_text(encoding="utf-8")

    # 1. Проверка кнопки меню камер в шапке
    assert 'id="btn-cameras-menu"' in index_html
    # 2. Проверка модального окна списка камер
    assert 'id="modal-cameras-menu"' in index_html
    assert 'id="cameras-list-container"' in index_html

    # 3. Проверка модального окна Live-стрима и новых оверлеев
    assert 'id="modal-camera-live"' in index_html
    assert 'id="live-stream-img"' in index_html
    assert 'id="live-loading-overlay"' in index_html
    assert 'id="live-error-overlay"' in index_html
    assert 'id="live-paused-overlay"' in index_html
    assert "Стрим на паузе" in index_html
    assert 'id="live-settings-drawer"' in index_html
    assert 'id="live-settings-content"' in index_html
    assert "btn-cam-settings" not in cameras_js

    # 4. Проверка 4 категорий настроек и новой верхней панели в cameras.js
    assert "id: 'format'" in cameras_js
    assert "id: 'exposure'" in cameras_js
    assert "id: 'color'" in cameras_js
    assert "id: 'focus'" in cameras_js
    assert "Фиксированный фокус" in cameras_js
    assert "URL.createObjectURL" in cameras_js

    # 5. Проверка ползунков RGB и телеметрии
    assert "slider-rgb-" in cameras_js
    assert "id: 'r'" in cameras_js
    assert "id: 'g'" in cameras_js
    assert "id: 'b'" in cameras_js
    assert 'id="live-fps"' in index_html
    assert 'id="live-latency"' in index_html
    assert 'id="live-res"' in index_html
    assert 'id="btn-live-download-snapshot"' in index_html

    # 6. Инвариант: 0 Unicode-эмодзи в Web UI
    emoji_pattern = re.compile(
        r"[\U00010000-\U0010ffff]|[\u2600-\u27bf]|[\u2300-\u23ff]|[\u2b50-\u2b55]"
    )
    for name, content in [
        ("index.html", index_html),
        ("style.css", style_css),
        ("cameras.js", cameras_js),
    ]:
        matches = emoji_pattern.findall(content)
        assert not matches, f"В файле {name} обнаружены запрещенные эмодзи: {matches}"


def test_stage9_2_live_and_schedule_e2e(e2e_uvicorn_server):
    """Сквозной E2E тест расписания по ровным часам, Live-стрима и настроек камеры."""
    server_data = e2e_uvicorn_server
    base_url = server_data["base_url"]
    dev_pass = server_data["dev_pass"]
    edge_key = server_data["edge_key"]
    session_factory = server_data["session_factory"]

    dev_tg_id = "55667788"
    now = datetime.now(UTC)

    with session_factory() as db:
        user = User(
            client_type="web",
            external_id=dev_tg_id,
            login="E2E Live Dev",
            role="developer",
            is_active=True,
            authorized_at=now,
            last_active_at=now,
        )
        db.add(user)

        # RPi онлайн
        db.add(
            SystemSetting(
                key="rpi_last_telemetry",
                value='{"hardware": {"board_model": "Raspberry Pi 5"}}',
                updated_at=now,
            )
        )

        # Активная установка
        inst = Installation(
            name="E2E Установка Live",
            culture_name="Розмарин",
            light_gpio_pin=18,
            flash_gpio_pin=23,
            is_archived=False,
        )
        db.add(inst)
        db.flush()

        # Камера
        cam = Camera(
            hardware_id="cam-live-e2e-01",
            auto_name="Камера Live E2E",
            installation_id=inst.id,
            is_online=True,
        )
        db.add(cam)
        db.commit()
        cam_id = cam.id
        inst_id = inst.id

    with httpx.Client(base_url=base_url) as client:
        # 1. Логин разработчика
        r_login = client.post(
            "/api/auth/web/login",
            json={"telegram": dev_tg_id, "password": dev_pass},
        )
        assert r_login.status_code == 200

        # 2. Проверка выравнивания расписания: наличие next_capture_at
        r_insts = client.get("/api/installations")
        assert r_insts.status_code == 200
        insts = r_insts.json()
        target_inst = next((u for u in insts if u["id"] == inst_id), None)
        assert target_inst is not None
        assert "next_capture_at" in target_inst
        assert target_inst["next_capture_at"] is not None

        # 3. Старт Live-стрима
        r_start = client.post(f"/api/cameras/{cam_id}/live/start")
        assert r_start.status_code == 200
        assert r_start.json()["status"] == "started"

        # 4. Проверка статуса стрима
        r_status = client.get(f"/api/cameras/{cam_id}/live/status")
        assert r_status.status_code == 200
        st = r_status.json()
        assert st["is_active"] is True
        assert st["camera_id"] == cam_id

        # 5. Агент передает живой кадр
        mock_frame = (
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00"
            + b"\xaa" * 128
            + b"\xff\xd9"
        )
        r_frame = client.post(
            "/api/edge/cameras/cam-live-e2e-01/live-frame",
            headers={
                "X-Edge-Key": edge_key,
                "X-Frame-Timestamp": str(datetime.now(UTC).timestamp()),
                "X-Resolution": "1280x720",
                "X-Paused": "0",
            },
            content=mock_frame,
        )
        assert r_frame.status_code == 200
        assert r_frame.json()["status"] == "ok"

        # 6. Скачивание тестового снимка напрямую
        r_snap = client.get(f"/api/cameras/{cam_id}/live/snapshot")
        assert r_snap.status_code == 200
        assert r_snap.headers["content-type"] == "image/jpeg"
        assert r_snap.content == mock_frame

        # 7. Получение и изменение настроек контролов камеры
        r_ctrls = client.get(f"/api/cameras/{cam_id}/controls")
        assert r_ctrls.status_code == 200
        ctrls_body = r_ctrls.json()
        assert "controls" in ctrls_body
        assert "formats" in ctrls_body
        assert "rgb" in ctrls_body

        r_patch = client.patch(
            f"/api/cameras/{cam_id}/controls",
            json={
                "controls": {"brightness": 20},
                "rgb": {"r": 1.2, "g": 1.0, "b": 0.8},
                "format": "MJPG",
                "resolution": "1280x720",
            },
        )
        assert r_patch.status_code == 200
        assert r_patch.json()["status"] == "updated"

        # 8. Остановка стрима
        r_stop = client.post(f"/api/cameras/{cam_id}/live/stop")
        assert r_stop.status_code == 200
        assert r_stop.json()["status"] == "stopped"

        r_status_after = client.get(f"/api/cameras/{cam_id}/live/status")
        assert r_status_after.status_code == 200
        assert r_status_after.json()["is_active"] is False
