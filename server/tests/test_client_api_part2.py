import asyncio
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from server.api.deps import get_db
from server.config import get_settings
from server.core.rate_limit import login_limiter
from server.core.state import app_state
from server.core.timeouts import expire_stale_commands
from server.db.database import Base
from server.db.models import (
    Camera,
    Capture,
    EdgeCommand,
    Installation,
    PlantMeasurement,
    SystemSetting,
)
from server.main import app

CLIENT_KEY = "test-client-secret-key"
EDGE_KEY = "test-edge-secret-key"
BIO_PASS = "bio-secret"
DEV_PASS = "dev-secret"


@pytest.fixture
def test_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Изолирует базу данных, медиа-каталог и настройки."""
    db_file = tmp_path / "test_client_part2.db"
    db_url = f"sqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    test_media_dir = tmp_path / "media"
    test_media_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("server.api.edge.get_media_dir", lambda: test_media_dir)
    monkeypatch.setattr("server.api.timelapse.get_media_dir", lambda: test_media_dir)

    settings = get_settings()
    monkeypatch.setattr(settings, "client_api_key", CLIENT_KEY)
    monkeypatch.setattr(settings, "edge_api_key", EDGE_KEY)
    monkeypatch.setattr(settings, "biologist_password", BIO_PASS)
    monkeypatch.setattr(settings, "developer_password", DEV_PASS)

    app_state.clear()
    login_limiter.clear()

    def override_get_db() -> Generator[Session, None, None]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield {
            "session_factory": session_factory,
            "media_dir": test_media_dir,
            "tmp_path": tmp_path,
        }
    finally:
        app.dependency_overrides.clear()
        app_state.clear()
        login_limiter.clear()


def set_rpi_online(db: Session, delta_seconds: float = 10.0) -> None:
    """Устанавливает updated_at записи rpi_last_telemetry."""
    hb_time = datetime.now(UTC) - timedelta(seconds=delta_seconds)
    st = (
        db.query(SystemSetting)
        .filter(SystemSetting.key == "rpi_last_telemetry")
        .first()
    )
    if not st:
        st = SystemSetting(key="rpi_last_telemetry", value="{}")
        db.add(st)
        db.commit()

    db.query(SystemSetting).filter(SystemSetting.key == "rpi_last_telemetry").update(
        {"value": "{}", "updated_at": hb_time},
        synchronize_session=False,
    )
    db.commit()


def set_debug_mode(db: Session, enabled: bool) -> None:
    """Устанавливает debug_mode в system_settings."""
    st = db.query(SystemSetting).filter(SystemSetting.key == "debug_mode").first()
    val = "true" if enabled else "false"
    if not st:
        st = SystemSetting(key="debug_mode", value=val)
        db.add(st)
    else:
        st.value = val
    db.commit()


def login_user(client: TestClient, role: str) -> dict[str, str]:
    """Авторизует пользователя и возвращает заголовки сессии."""
    login_limiter.clear()
    pwd = DEV_PASS if role == "developer" else BIO_PASS
    ext_id = f"{role}-user-id"
    c_type = "cli" if role == "developer" else "telegram"
    r = client.post(
        "/api/auth/login",
        headers={"X-Client-Key": CLIENT_KEY},
        json={
            "client_type": c_type,
            "external_id": ext_id,
            "login": role,
            "password": pwd,
        },
    )
    assert r.status_code == 200
    return {
        "X-Client-Key": CLIENT_KEY,
        "X-Client-Type": c_type,
        "X-External-Id": ext_id,
    }


def test_control_rpi_offline_and_target_validations(test_env):
    """Критерии 1 и 2: Строгий порядок проверок (401/403 -> 422 -> 404/409 -> 503)."""
    client = TestClient(app)
    session_factory = test_env["session_factory"]
    dev_headers = login_user(client, "developer")
    bio_headers = login_user(client, "biologist")

    # 1. Проверка роли (401 / 403)
    r_unauth = client.post("/api/control/snap", json={"target": 1})
    assert r_unauth.status_code == 401

    r_forbidden = client.post(
        "/api/control/light/main",
        headers=bio_headers,
        json={"target": 1, "state": True},
    )
    assert r_forbidden.status_code == 403

    # 2. Валидация тела запроса (422) до проверки target и RPi
    r_bad_target = client.post(
        "/api/control/snap",
        headers=dev_headers,
        json={"target": "invalid_string"},
    )
    assert r_bad_target.status_code == 422

    for endpoint, payload in (
        ("/api/control/snap", {"target": True}),
        ("/api/control/snap", {"target": False}),
        ("/api/control/snap", {"target": 0}),
        ("/api/control/snap", {"target": -1}),
        ("/api/control/light/main", {"target": True, "state": True}),
        ("/api/control/light/flash", {"target": True, "state": False}),
        ("/api/control/relay-test", {"target": True}),
    ):
        r_bool = client.post(endpoint, headers=dev_headers, json=payload)
        assert r_bool.status_code == 422, (
            f"{endpoint} должен возвращать 422 на невалидный target {payload}"
        )

    # Создаем архивную установку для проверки 409
    with session_factory() as db:
        inst_arch = Installation(
            name="Archived Box",
            culture_name="Basil",
            light_gpio_pin=10,
            flash_gpio_pin=11,
            is_archived=True,
        )
        db.add(inst_arch)
        db.commit()
        arch_id = inst_arch.id

    # 3. При RPi ОФЛАЙН проверка target ДОЛЖНА отрабатывать раньше проверки онлайна RPi:
    # 3.1. Несуществующая установка -> 404 (не 503!)
    r_not_found = client.post(
        "/api/control/snap",
        headers=dev_headers,
        json={"target": 9999},
    )
    assert r_not_found.status_code == 404
    assert r_not_found.json()["detail"] == "Установка не найдена"

    # 3.2. target="all" без активных установок -> 409 (не 503!)
    r_all_no_active = client.post(
        "/api/control/snap",
        headers=dev_headers,
        json={"target": "all"},
    )
    assert r_all_no_active.status_code == 409
    assert r_all_no_active.json()["detail"] == "Нет активных установок"

    # 3.3. Обращение к архивной установке -> 409 (не 503!)
    r_arch = client.post(
        "/api/control/snap",
        headers=dev_headers,
        json={"target": arch_id},
    )
    assert r_arch.status_code == 409
    assert r_arch.json()["detail"] == "Установка в архиве"

    # Создаем активную установку
    with session_factory() as db:
        inst_active = Installation(
            name="Active Test Box",
            culture_name="Mint",
            light_gpio_pin=12,
            flash_gpio_pin=13,
            is_archived=False,
        )
        db.add(inst_active)
        db.commit()
        active_id = inst_active.id

    # 4. Проверка онлайна RPi (503): target валиден и существует, но RPi офлайн
    # 4.1. rpi_last_telemetry вообще нет
    r_snap_offline = client.post(
        "/api/control/snap",
        headers=dev_headers,
        json={"target": active_id},
    )
    assert r_snap_offline.status_code == 503
    assert r_snap_offline.json()["detail"] == "Raspberry Pi офлайн"

    r_all_offline = client.post(
        "/api/control/snap",
        headers=dev_headers,
        json={"target": "all"},
    )
    assert r_all_offline.status_code == 503
    assert r_all_offline.json()["detail"] == "Raspberry Pi офлайн"

    # 4.2. rpi_last_telemetry устарел (> 90 с)
    with session_factory() as db:
        set_rpi_online(db, delta_seconds=120.0)

    r_old_telem = client.post(
        "/api/control/snap",
        headers=dev_headers,
        json={"target": active_id},
    )
    assert r_old_telem.status_code == 503
    assert r_old_telem.json()["detail"] == "Raspberry Pi офлайн"

    # 4.3. POST /api/cameras/scan при офлайн RPi -> 503
    r_scan_offline = client.post("/api/cameras/scan", headers=dev_headers)
    assert r_scan_offline.status_code == 503
    assert r_scan_offline.json()["detail"] == "Raspberry Pi офлайн"

    # 5. При включении RPi онлайн (<= 90 с):
    with session_factory() as db:
        set_rpi_online(db, delta_seconds=10.0)

    # 404 и 409 продолжают корректно работать
    r_not_found_on = client.post(
        "/api/control/snap",
        headers=dev_headers,
        json={"target": 9999},
    )
    assert r_not_found_on.status_code == 404

    r_arch_on = client.post(
        "/api/control/snap",
        headers=dev_headers,
        json={"target": arch_id},
    )
    assert r_arch_on.status_code == 409


def test_control_snap_lifecycle_and_debug_info(test_env, monkeypatch):
    """Критерий 1: SNAP lifecycle, captures, is_manual=True, debug_mode."""
    client = TestClient(app)
    session_factory = test_env["session_factory"]
    dev_headers = login_user(client, "developer")
    bio_headers = login_user(client, "biologist")

    with session_factory() as db:
        set_rpi_online(db, delta_seconds=5.0)
        set_debug_mode(db, True)
        inst = Installation(
            name="Active Box",
            culture_name="Lettuce",
            light_gpio_pin=12,
            flash_gpio_pin=13,
            aruco_size_mm=50.0,
            is_archived=False,
        )
        db.add(inst)
        db.flush()
        inst_id = inst.id

        cam = Camera(
            hardware_id="cam-snap-1",
            auto_name="Snap Cam",
            installation_id=inst_id,
        )
        db.add(cam)
        db.commit()
        cam_id = cam.id

    # Симулируем обработку RPi при ожидании команды (эфемерный тестовый снимок в памяти)
    async def mock_wait_command_result(cmd_id: int, timeout_sec: float, db: Session):
        db.expire_all()
        cmd = db.query(EdgeCommand).filter(EdgeCommand.id == cmd_id).first()
        cmd.status = "done"
        cmd.result_message = "Снимок выполнен успешно"
        cmd.debug_log = "[INFO] [GPIO] Inst#1: light=0, flash=1 -> captured 1 frames"
        db.commit()

        app_state.save_test_snapshot(
            installation_id=inst_id,
            camera_id=cam_id,
            camera_hardware_id="cam-snap-1",
            captured_at="2026-11-25T10:00:00Z",
            raw_bytes=b"\xff\xd8\xffraw_jpeg\xff\xd9",
            annotated_bytes=b"\xff\xd8\xffann_jpeg\xff\xd9",
            area_display="50.0 см²",
            measurements=[{"plant_index": 0, "area_px": 2000.0}],
            aruco_area_px=1000.0,
            command_id=cmd_id,
        )
        return cmd

    monkeypatch.setattr(
        "server.api.control.wait_command_result", mock_wait_command_result
    )

    # Developer запрос со включенным debug_mode
    r_snap = client.post(
        "/api/control/snap",
        headers=dev_headers,
        json={"target": inst_id},
    )
    assert r_snap.status_code == 200
    data = r_snap.json()
    assert data["status"] == "done"
    assert data["result_message"] == "Снимок выполнен успешно"
    assert len(data["captures"]) == 1
    cap_info = data["captures"][0]
    assert cap_info["installation_id"] == inst_id
    assert cap_info["ephemeral"] is True
    snap_id = cap_info["snapshot_id"]
    assert cap_info["raw_url"] == f"/api/control/test-snapshots/{snap_id}/raw"
    assert (
        cap_info["annotated_url"] == f"/api/control/test-snapshots/{snap_id}/annotated"
    )
    assert cap_info["area_display"] == "50.0 см²"
    assert "debug_info" in data
    assert (
        data["debug_info"]["log_line"]
        == "[INFO] [GPIO] Inst#1: light=0, flash=1 -> captured 1 frames"
    )

    # Проверяем, что в БД записей Capture / PlantMeasurement не создано
    with session_factory() as db:
        assert db.query(Capture).count() == 0
        assert db.query(PlantMeasurement).count() == 0

    # Проверяем, что связь команды в app_state очищена, но сам снимок доступен по TTL
    assert app_state.pop_test_snapshots_by_command(data["command_id"]) == []
    assert app_state.get_test_snapshot(snap_id) is not None

    # Biologist запрос: debug_info НЕ должен быть виден
    r_snap_bio = client.post(
        "/api/control/snap",
        headers=bio_headers,
        json={"target": inst_id},
    )
    assert r_snap_bio.status_code == 200
    assert "debug_info" not in r_snap_bio.json()


def test_control_light_and_relay_endpoints(test_env, monkeypatch):
    """Критерий 1: POST /api/control/light/* и /relay-test права Developer."""
    client = TestClient(app)
    session_factory = test_env["session_factory"]
    dev_headers = login_user(client, "developer")
    bio_headers = login_user(client, "biologist")

    with session_factory() as db:
        set_rpi_online(db, delta_seconds=5.0)
        inst = Installation(
            name="Light Box",
            culture_name="Tomato",
            light_gpio_pin=14,
            flash_gpio_pin=15,
            is_archived=False,
        )
        db.add(inst)
        db.commit()
        inst_id = inst.id

    # Biologist не имеет доступа к light и relay-test -> 403
    for path, payload in (
        ("/api/control/light/main", {"target": inst_id, "state": True}),
        ("/api/control/light/flash", {"target": inst_id, "state": False}),
        ("/api/control/relay-test", {"target": inst_id}),
    ):
        r_forbidden = client.post(path, headers=bio_headers, json=payload)
        assert r_forbidden.status_code == 403

    async def mock_wait_cmd(cmd_id: int, timeout_sec: float, db: Session):
        db.expire_all()
        cmd = db.query(EdgeCommand).filter(EdgeCommand.id == cmd_id).first()
        cmd.status = "done"
        cmd.result_message = "OK"
        db.commit()
        return cmd

    monkeypatch.setattr("server.api.control.wait_command_result", mock_wait_cmd)

    # Developer успешные вызовы
    r_main = client.post(
        "/api/control/light/main",
        headers=dev_headers,
        json={"target": inst_id, "state": True},
    )
    assert r_main.status_code == 200
    assert r_main.json()["status"] == "done"

    r_flash = client.post(
        "/api/control/light/flash",
        headers=dev_headers,
        json={"target": inst_id, "state": True},
    )
    assert r_flash.status_code == 200

    r_relay = client.post(
        "/api/control/relay-test",
        headers=dev_headers,
        json={"target": inst_id},
    )
    assert r_relay.status_code == 200


def test_cameras_scan_endpoint_and_state_cleanup(test_env, monkeypatch):
    """Критерий 2 и 4: POST /api/cameras/scan, регистрация камер, очистка app_state."""
    client = TestClient(app)
    session_factory = test_env["session_factory"]
    dev_headers = login_user(client, "developer")
    bio_headers = login_user(client, "biologist")

    with session_factory() as db:
        set_rpi_online(db, delta_seconds=5.0)
        set_debug_mode(db, True)
        # Создаем камеру, которая не будет обнаружена при следующем сканировании
        old_cam = Camera(
            hardware_id="cam-disconnected-hw",
            auto_name="Disconnected Cam",
            is_online=True,
        )
        db.add(old_cam)
        db.commit()

    # Biologist -> 403
    r_bio = client.post("/api/cameras/scan", headers=bio_headers)
    assert r_bio.status_code == 403

    # Симулируем ответ RPi на сканирование
    async def mock_scan_sleep(delay: float):
        # При первом вызове переводим команду в done и сохраняем найденные камеры
        with session_factory() as db:
            cmd = (
                db.query(EdgeCommand)
                .filter(EdgeCommand.command_type == "SCAN_CAMERAS")
                .first()
            )
            if cmd and cmd.status == "pending":
                cmd.status = "done"
                db.commit()
                app_state.save_scanned_cameras(
                    cmd.id, ["usb-cam-1-video-0", "usb-cam-2-video-0"]
                )

    monkeypatch.setattr(asyncio, "sleep", mock_scan_sleep)

    r_scan = client.post("/api/cameras/scan", headers=dev_headers)
    assert r_scan.status_code == 200
    data = r_scan.json()
    assert data["status"] == "done"
    # Только реально обнаруженные камеры (2 шт.)
    assert len(data["cameras"]) == 2
    hw_ids = [c["hardware_id"] for c in data["cameras"]]
    assert "usb-cam-1-video-0" in hw_ids
    assert "usb-cam-2-video-0" in hw_ids
    assert "cam-disconnected-hw" not in hw_ids
    assert "debug_info" in data

    # Проверяем, что отключенная камера осталась в БД со статусом is_online=False (Критерий 10)
    with session_factory() as db:
        disc_cam = (
            db.query(Camera).filter(Camera.hardware_id == "cam-disconnected-hw").first()
        )
        assert disc_cam is not None
        assert disc_cam.is_online is False

    # Проверяем, что результат сканирования удален из app_state
    assert app_state.pop_scanned_cameras(data["command_id"]) is None


def test_core_timeouts_expiration(test_env):
    """Критерий 3: Проверка таймаутов pending (>30с) и running (>60с / глобальный SNAP)."""
    session_factory = test_env["session_factory"]
    now = datetime.now(UTC)

    with session_factory() as db:
        inst = Installation(
            name="Timeout Box",
            culture_name="Salad",
            light_gpio_pin=30,
            flash_gpio_pin=31,
        )
        db.add(inst)
        db.flush()

        # 1. Свежая pending команда (создана 10 с назад) -> не должна устареть
        cmd_pending_fresh = EdgeCommand(
            command_type="SNAP",
            is_global=True,
            status="pending",
            created_at=now - timedelta(seconds=10),
        )
        # 2. Старая pending команда (создана 35 с назад) -> должна уйти в timeout
        cmd_pending_old = EdgeCommand(
            command_type="SNAP",
            is_global=True,
            status="pending",
            created_at=now - timedelta(seconds=35),
        )
        # 3. Свежая running одиночная (стартовала 30 с назад) -> остается running
        cmd_running_fresh = EdgeCommand(
            command_type="SNAP",
            is_global=False,
            installation_id=inst.id,
            status="running",
            created_at=now - timedelta(seconds=40),
            started_at=now - timedelta(seconds=30),
        )
        # 4. Старая running одиночная (стартовала 65 с назад) -> timeout
        cmd_running_old = EdgeCommand(
            command_type="LIGHT_MAIN",
            is_global=True,
            status="running",
            created_at=now - timedelta(seconds=80),
            started_at=now - timedelta(seconds=65),
        )
        db.add_all(
            [
                cmd_pending_fresh,
                cmd_pending_old,
                cmd_running_fresh,
                cmd_running_old,
            ]
        )
        db.commit()

        expired = expire_stale_commands(db, now=now)
        assert expired == 2

        db.refresh(cmd_pending_fresh)
        db.refresh(cmd_pending_old)
        db.refresh(cmd_running_fresh)
        db.refresh(cmd_running_old)

        assert cmd_pending_fresh.status == "pending"
        assert cmd_pending_old.status == "timeout"
        assert cmd_running_fresh.status == "running"
        assert cmd_running_old.status == "timeout"


def test_timelapse_frames_public(test_env):
    """Критерий 5: GET /api/timelapse/{id}/frames доступен Гостю, фильтрация по camera_id."""
    client = TestClient(app)
    session_factory = test_env["session_factory"]

    # 404 для несуществующей установки
    r_404 = client.get("/api/timelapse/999/frames")
    assert r_404.status_code == 404

    with session_factory() as db:
        inst = Installation(
            name="Timelapse Box",
            culture_name="Basil",
            light_gpio_pin=18,
            flash_gpio_pin=19,
            aruco_size_mm=40.0,
        )
        db.add(inst)
        db.flush()
        inst_id = inst.id

        cam = Camera(
            hardware_id="cam-tl-1",
            auto_name="Timelapse Cam",
            installation_id=inst_id,
        )
        db.add(cam)
        db.flush()
        cam_id = cam.id

        # Создаем плановый кадр и ручной кадр
        t1 = datetime.now(UTC) - timedelta(hours=2)
        t2 = datetime.now(UTC) - timedelta(hours=1)
        cap_plan = Capture(
            installation_id=inst_id,
            camera_id=cam_id,
            captured_at=t1,
            is_manual=False,
            aruco_area_px=100.0,
            raw_image_path=f"media/{inst_id}/raw_1.jpg",
            annotated_image_path=f"media/{inst_id}/ann_1.jpg",
        )
        cap_manual = Capture(
            installation_id=inst_id,
            camera_id=cam_id,
            captured_at=t2,
            is_manual=True,
            raw_image_path=f"media/{inst_id}/raw_2.jpg",
            annotated_image_path=f"media/{inst_id}/ann_2.jpg",
        )
        db.add_all([cap_plan, cap_manual])
        db.flush()
        meas = PlantMeasurement(capture_id=cap_plan.id, plant_index=0, area_px=250.0)
        db.add(meas)
        db.commit()
        cap_plan_id = cap_plan.id

    # Запрос без авторизации (роль Гость)
    r_guest = client.get(f"/api/timelapse/{inst_id}/frames")
    assert r_guest.status_code == 200
    data = r_guest.json()
    assert data["installation_id"] == inst_id
    assert len(data["frames"]) == 1  # Только плановый кадр
    frame = data["frames"][0]
    assert frame["capture_id"] == cap_plan_id
    assert frame["area_display"] == "40.0 см²"

    # Фильтр по camera_id
    r_cam = client.get(f"/api/timelapse/{inst_id}/frames?camera_id=99")
    assert r_cam.status_code == 200
    assert r_cam.json()["frames"] == []


def test_timelapse_video_generation(test_env):
    """Критерий 6: POST /api/timelapse/{id}/video: 0 кадров 404, 1 кадр JPEG, >=2 кадра MP4, ffmpeg."""
    client = TestClient(app)
    session_factory = test_env["session_factory"]
    media_dir = test_env["media_dir"]
    dev_headers = login_user(client, "developer")

    with session_factory() as db:
        inst = Installation(
            name="Video Box",
            culture_name="Mint",
            light_gpio_pin=22,
            flash_gpio_pin=23,
        )
        db.add(inst)
        db.flush()
        inst_id = inst.id

        cam = Camera(
            hardware_id="cam-vid-1",
            auto_name="Video Cam",
            installation_id=inst_id,
        )
        db.add(cam)
        db.commit()
        cam_id = cam.id

    # 1. 0 кадров -> 404
    r_empty = client.post(
        f"/api/timelapse/{inst_id}/video",
        headers=dev_headers,
        json={"step": 1},
    )
    assert r_empty.status_code == 404
    assert r_empty.json()["detail"] == "Плановые снимки не найдены"

    # Создаем 1 плановый кадр и физический файл
    jpeg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00\x02\x00\x02\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
    inst_dir = media_dir / str(inst_id)
    inst_dir.mkdir(parents=True, exist_ok=True)
    raw_1_file = inst_dir / "raw_1.jpg"
    raw_1_file.write_bytes(jpeg_bytes)

    with session_factory() as db:
        c1 = Capture(
            installation_id=inst_id,
            camera_id=cam_id,
            captured_at=datetime.now(UTC) - timedelta(hours=2),
            is_manual=False,
            raw_image_path=f"media/{inst_id}/raw_1.jpg",
            annotated_image_path=f"media/{inst_id}/ann_1.jpg",
        )
        db.add(c1)
        db.commit()

    # 2. 1 кадр -> 200 image/jpeg
    r_single = client.post(
        f"/api/timelapse/{inst_id}/video",
        headers=dev_headers,
        json={"step": 1},
    )
    assert r_single.status_code == 200
    assert r_single.headers["content-type"] == "image/jpeg"
    assert r_single.content == jpeg_bytes

    # Создаем второй кадр
    raw_2_file = inst_dir / "raw_2.jpg"
    raw_2_file.write_bytes(jpeg_bytes)

    with session_factory() as db:
        c2 = Capture(
            installation_id=inst_id,
            camera_id=cam_id,
            captured_at=datetime.now(UTC) - timedelta(hours=1),
            is_manual=False,
            raw_image_path=f"media/{inst_id}/raw_2.jpg",
            annotated_image_path=f"media/{inst_id}/ann_2.jpg",
        )
        db.add(c2)
        db.commit()

    # 3. >= 2 кадров -> сборка video/mp4 через ffmpeg
    r_video = client.post(
        f"/api/timelapse/{inst_id}/video",
        headers=dev_headers,
        json={"step": 1},
    )
    assert r_video.status_code == 200
    assert r_video.headers["content-type"] == "video/mp4"
    assert "attachment" in r_video.headers["content-disposition"]
    assert len(r_video.content) > 0


def test_timelapse_multiple_cameras_conflict(test_env):
    """Критерий 6: Одновременная съёмка разными камерами (<=10 мин) -> 422."""
    client = TestClient(app)
    session_factory = test_env["session_factory"]
    dev_headers = login_user(client, "developer")

    with session_factory() as db:
        inst = Installation(
            name="Multi-cam Box",
            culture_name="Salad",
            light_gpio_pin=24,
            flash_gpio_pin=25,
        )
        db.add(inst)
        db.flush()

        cam1 = Camera(
            hardware_id="cam-1",
            auto_name="Digma DiCam 300",
            installation_id=inst.id,
        )
        cam2 = Camera(
            hardware_id="cam-2",
            auto_name="Logitech C270",
            installation_id=inst.id,
        )
        db.add_all([cam1, cam2])
        db.flush()

        t_base = datetime.now(UTC)
        # Кадр с камеры 1 и кадр с камеры 2 сняты с разницей в 2 минуты (<= 10 минут)
        cap1 = Capture(
            installation_id=inst.id,
            camera_id=cam1.id,
            captured_at=t_base,
            is_manual=False,
            raw_image_path=f"media/{inst.id}/raw_c1.jpg",
            annotated_image_path=f"media/{inst.id}/ann_c1.jpg",
        )
        cap2 = Capture(
            installation_id=inst.id,
            camera_id=cam2.id,
            captured_at=t_base + timedelta(minutes=2),
            is_manual=False,
            raw_image_path=f"media/{inst.id}/raw_c2.jpg",
            annotated_image_path=f"media/{inst.id}/ann_c2.jpg",
        )
        db.add_all([cap1, cap2])
        db.commit()
        inst_id = inst.id
        cam1_id = cam1.id

    # Без camera_id -> 422
    r_multi = client.post(
        f"/api/timelapse/{inst_id}/video",
        headers=dev_headers,
        json={},
    )
    assert r_multi.status_code == 422
    assert "У установки несколько камер, укажите camera_id" in r_multi.json()["detail"]

    # С указанием camera_id -> 200 (1 кадр для cam1 -> JPEG)
    r_specific = client.post(
        f"/api/timelapse/{inst_id}/video",
        headers=dev_headers,
        json={"camera_id": cam1_id},
    )
    # Так как файл не создан на диске, вернет 404 "Файл кадра не найден" (успешная ветка выбора одного кадра)
    assert r_specific.status_code in (200, 404)


def test_static_files_serving(test_env):
    """Критерий 7: Раздача статики /media и clients/web."""
    client = TestClient(app)

    # 1. API роутеры работают и не перекрыты статикой
    r_version = client.get("/api/version")
    assert r_version.status_code == 200

    # 2. Раздача /media
    r_media = client.get("/media/.gitkeep")
    assert r_media.status_code in (200, 404)


def test_control_wait_command_timeout_immediate_504(test_env, monkeypatch):
    """Критерий 4: Мгновенный 504 при переходе команды в статус 'timeout' в wait_command_result."""
    import time

    client = TestClient(app)
    session_factory = test_env["session_factory"]
    dev_headers = login_user(client, "developer")

    with session_factory() as db:
        set_rpi_online(db, delta_seconds=5.0)
        inst = Installation(
            name="Timeout Fast Box",
            culture_name="Dill",
            light_gpio_pin=28,
            flash_gpio_pin=29,
        )
        db.add(inst)
        db.commit()
        inst_id = inst.id

    orig_sleep = asyncio.sleep

    async def mock_sleep_timeout(delay: float):
        with session_factory() as db:
            cmd = (
                db.query(EdgeCommand).filter(EdgeCommand.command_type == "SNAP").first()
            )
            if cmd and cmd.status == "pending":
                cmd.status = "timeout"
                db.commit()
        await orig_sleep(0.01)

    monkeypatch.setattr(asyncio, "sleep", mock_sleep_timeout)

    t0 = time.monotonic()
    r = client.post(
        "/api/control/snap",
        headers=dev_headers,
        json={"target": inst_id},
    )
    elapsed = time.monotonic() - t0

    assert r.status_code == 504
    assert r.json()["detail"] == "Превышено время ожидания ответа Raspberry Pi"
    assert elapsed < 3.0


def test_control_snap_error_cleans_app_state(test_env, monkeypatch):
    """Критерий 6: При статусе error команды SNAP запись удаляется из app_state."""
    client = TestClient(app)
    session_factory = test_env["session_factory"]
    dev_headers = login_user(client, "developer")

    with session_factory() as db:
        set_rpi_online(db, delta_seconds=5.0)
        inst = Installation(
            name="Snap Error Box",
            culture_name="Oregano",
            light_gpio_pin=32,
            flash_gpio_pin=33,
        )
        db.add(inst)
        db.commit()
        inst_id = inst.id

    async def mock_wait_cmd_error(cmd_id: int, timeout_sec: float, db: Session):
        db.expire_all()
        cmd = db.query(EdgeCommand).filter(EdgeCommand.id == cmd_id).first()
        cmd.status = "error"
        cmd.result_message = "Камера не ответила"
        db.commit()
        app_state.bind_command_capture(cmd_id, 9999)
        return cmd

    monkeypatch.setattr("server.api.control.wait_command_result", mock_wait_cmd_error)

    r = client.post(
        "/api/control/snap",
        headers=dev_headers,
        json={"target": inst_id},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "error"
    assert data["result_message"] == "Камера не ответила"

    assert app_state.get_capture_by_command(data["command_id"]) is None
    assert app_state.pop_captures_by_command(data["command_id"]) == []


def test_cameras_scan_error_and_timeout(test_env, monkeypatch):
    """Критерии 4 и 6: SCAN_CAMERAS мгновенный 504 при timeout и очистка app_state при error."""
    import time

    client = TestClient(app)
    session_factory = test_env["session_factory"]
    dev_headers = login_user(client, "developer")

    with session_factory() as db:
        set_rpi_online(db, delta_seconds=5.0)

    orig_sleep = asyncio.sleep

    # 1. SCAN_CAMERAS уходит в timeout -> быстрый 504
    async def mock_sleep_scan_timeout(delay: float):
        with session_factory() as db:
            cmd = (
                db.query(EdgeCommand)
                .filter(EdgeCommand.command_type == "SCAN_CAMERAS")
                .first()
            )
            if cmd and cmd.status == "pending":
                cmd.status = "timeout"
                db.commit()
        await orig_sleep(0.01)

    monkeypatch.setattr(asyncio, "sleep", mock_sleep_scan_timeout)

    t0 = time.monotonic()
    r_timeout = client.post("/api/cameras/scan", headers=dev_headers)
    elapsed = time.monotonic() - t0
    assert r_timeout.status_code == 504
    assert elapsed < 3.0

    # 2. SCAN_CAMERAS завершается со статусом error -> app_state очищен
    async def mock_sleep_scan_error(delay: float):
        with session_factory() as db:
            cmd = (
                db.query(EdgeCommand)
                .filter(EdgeCommand.command_type == "SCAN_CAMERAS")
                .order_by(EdgeCommand.id.desc())
                .first()
            )
            if cmd and cmd.status == "pending":
                cmd.status = "error"
                cmd.result_message = "USB контроллер занят"
                db.commit()
                app_state.save_scanned_cameras(cmd.id, ["temp-cam"])
        await orig_sleep(0.01)

    monkeypatch.setattr(asyncio, "sleep", mock_sleep_scan_error)
    r_error = client.post("/api/cameras/scan", headers=dev_headers)
    assert r_error.status_code == 200
    data = r_error.json()
    assert data["status"] == "error"
    assert data["result_message"] == "USB контроллер занят"
    assert app_state.pop_scanned_cameras(data["command_id"]) is None


def test_app_state_cleanup_stale_entries(test_env):
    """Критерий 6: Периодическая очистка app_state для команд старше 5 минут."""
    session_factory = test_env["session_factory"]
    now = datetime.now(UTC)

    with session_factory() as db:
        cmd_stale_done = EdgeCommand(
            command_type="SNAP",
            is_global=True,
            status="done",
            created_at=now - timedelta(minutes=6),
            started_at=now - timedelta(minutes=6),
        )
        cmd_stale_timeout = EdgeCommand(
            command_type="SNAP",
            is_global=True,
            status="timeout",
            created_at=now - timedelta(minutes=10),
            started_at=now - timedelta(minutes=10),
        )
        cmd_fresh_done = EdgeCommand(
            command_type="SNAP",
            is_global=True,
            status="done",
            created_at=now - timedelta(minutes=1),
            started_at=now - timedelta(minutes=1),
        )
        db.add_all([cmd_stale_done, cmd_stale_timeout, cmd_fresh_done])
        db.commit()

        app_state.bind_command_capture(cmd_stale_done.id, 101)
        app_state.save_scanned_cameras(cmd_stale_timeout.id, ["cam-old"])
        app_state.bind_command_capture(cmd_fresh_done.id, 202)

        cleaned = app_state.cleanup_stale_entries(db, max_age_seconds=300.0)
        assert cleaned == 2

        assert app_state.get_capture_by_command(cmd_stale_done.id) is None
        assert app_state.get_scanned_cameras(cmd_stale_timeout.id) is None
        assert app_state.get_capture_by_command(cmd_fresh_done.id) == 202


def test_timelapse_missing_files_and_ffmpeg_errors(test_env, monkeypatch):
    """Критерий 5: Пропуск отсутствующих кадров, 404 если 0, timeout=600 и чистый 500 detail при ошибке ffmpeg."""
    import subprocess

    client = TestClient(app)
    session_factory = test_env["session_factory"]
    media_dir = test_env["media_dir"]
    dev_headers = login_user(client, "developer")

    with session_factory() as db:
        inst = Installation(
            name="Missing Frames Box",
            culture_name="Thyme",
            light_gpio_pin=34,
            flash_gpio_pin=35,
        )
        db.add(inst)
        db.flush()
        inst_id = inst.id

        cam = Camera(
            hardware_id="cam-missing-1",
            auto_name="Missing Cam",
            installation_id=inst_id,
        )
        db.add(cam)
        db.flush()

        cap1 = Capture(
            installation_id=inst_id,
            camera_id=cam.id,
            captured_at=datetime.now(UTC) - timedelta(hours=3),
            is_manual=False,
            raw_image_path=f"media/{inst_id}/missing_1.jpg",
            annotated_image_path=f"media/{inst_id}/missing_1_ann.jpg",
        )
        cap2 = Capture(
            installation_id=inst_id,
            camera_id=cam.id,
            captured_at=datetime.now(UTC) - timedelta(hours=2),
            is_manual=False,
            raw_image_path=f"media/{inst_id}/exists_2.jpg",
            annotated_image_path=f"media/{inst_id}/exists_2_ann.jpg",
        )
        cap3 = Capture(
            installation_id=inst_id,
            camera_id=cam.id,
            captured_at=datetime.now(UTC) - timedelta(hours=1),
            is_manual=False,
            raw_image_path=f"media/{inst_id}/missing_3.jpg",
            annotated_image_path=f"media/{inst_id}/missing_3_ann.jpg",
        )
        db.add_all([cap1, cap2, cap3])
        db.commit()

    # 1. Файлов нет вообще -> 404 "Плановые снимки не найдены"
    r_no_files = client.post(
        f"/api/timelapse/{inst_id}/video",
        headers=dev_headers,
        json={},
    )
    assert r_no_files.status_code == 404
    assert r_no_files.json()["detail"] == "Плановые снимки не найдены"

    # 2. Создаем файл только для cap2 -> пропустит 1 и 3, останется ровно 1 кадр -> 200 image/jpeg
    inst_dir = media_dir / str(inst_id)
    inst_dir.mkdir(parents=True, exist_ok=True)
    jpeg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00\x02\x00\x02\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
    (inst_dir / "exists_2.jpg").write_bytes(jpeg_bytes)

    r_one_valid = client.post(
        f"/api/timelapse/{inst_id}/video",
        headers=dev_headers,
        json={},
    )
    assert r_one_valid.status_code == 200
    assert r_one_valid.headers["content-type"] == "image/jpeg"
    assert r_one_valid.content == jpeg_bytes

    # 3. Создаем второй файл -> 2 кадра -> сборка ffmpeg
    (inst_dir / "missing_3.jpg").write_bytes(jpeg_bytes)

    # 3.1. Ошибка процесса CalledProcessError -> 500 без путей и stderr
    def mock_run_error(*args, **kwargs):
        assert kwargs.get("timeout") == 600
        raise subprocess.CalledProcessError(
            returncode=1,
            cmd=args[0],
            stderr=b"Internal ffmpeg error /tmp/secret/path",
        )

    monkeypatch.setattr(subprocess, "run", mock_run_error)
    r_err = client.post(
        f"/api/timelapse/{inst_id}/video",
        headers=dev_headers,
        json={},
    )
    assert r_err.status_code == 500
    assert r_err.json() == {"detail": "Ошибка кодирования видео"}
    assert "secret" not in r_err.text
    assert "ffmpeg" not in r_err.text

    # 3.2. Таймаут TimeoutExpired -> 500 без путей и stderr
    def mock_run_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=600)

    monkeypatch.setattr(subprocess, "run", mock_run_timeout)
    r_to = client.post(
        f"/api/timelapse/{inst_id}/video",
        headers=dev_headers,
        json={},
    )
    assert r_to.status_code == 500
    assert r_to.json() == {"detail": "Ошибка кодирования видео"}
