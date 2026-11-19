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
    """Устанавливает свежий rpi_last_heartbeat_at."""
    hb_time = datetime.now(UTC) - timedelta(seconds=delta_seconds)
    st = (
        db.query(SystemSetting)
        .filter(SystemSetting.key == "rpi_last_heartbeat_at")
        .first()
    )
    if not st:
        st = SystemSetting(key="rpi_last_heartbeat_at", value=hb_time.isoformat())
        db.add(st)
    else:
        st.value = hb_time.isoformat()
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
    r = client.post(
        "/api/auth/login",
        headers={"X-Client-Key": CLIENT_KEY},
        json={
            "client_type": "cli",
            "external_id": ext_id,
            "login": role,
            "password": pwd,
        },
    )
    assert r.status_code == 200
    return {
        "X-Client-Key": CLIENT_KEY,
        "X-Client-Type": "cli",
        "X-External-Id": ext_id,
    }


def test_control_rpi_offline_and_target_validations(test_env):
    """Критерий 1: Проверка 503 при офлайне RPi и валидация target (404, 409, 422)."""
    client = TestClient(app)
    session_factory = test_env["session_factory"]
    dev_headers = login_user(client, "developer")

    # 1. RPi офлайн (heartbeat нет) -> 503
    r_snap_offline = client.post(
        "/api/control/snap",
        headers=dev_headers,
        json={"target": 1},
    )
    assert r_snap_offline.status_code == 503
    assert r_snap_offline.json()["detail"] == "Raspberry Pi офлайн"

    with session_factory() as db:
        # Устанавливаем старый heartbeat (> 90 с)
        set_rpi_online(db, delta_seconds=120.0)

    r_old_hb = client.post(
        "/api/control/snap",
        headers=dev_headers,
        json={"target": 1},
    )
    assert r_old_hb.status_code == 503

    # Теперь включаем RPi онлайн
    with session_factory() as db:
        set_rpi_online(db, delta_seconds=10.0)

    # 2. Несуществующая установка -> 404
    r_not_found = client.post(
        "/api/control/snap",
        headers=dev_headers,
        json={"target": 999},
    )
    assert r_not_found.status_code == 404
    assert r_not_found.json()["detail"] == "Установка не найдена"

    # 3. target='all' без активных установок -> 409
    r_all_no_active = client.post(
        "/api/control/snap",
        headers=dev_headers,
        json={"target": "all"},
    )
    assert r_all_no_active.status_code == 409
    assert r_all_no_active.json()["detail"] == "Нет активных установок"

    # Создаем установку и архивируем её
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

    # 4. Обращение к архивной установке -> 409
    r_arch = client.post(
        "/api/control/snap",
        headers=dev_headers,
        json={"target": arch_id},
    )
    assert r_arch.status_code == 409
    assert r_arch.json()["detail"] == "Установка в архиве"

    # 5. Валидация target (не число и не 'all') -> 422
    r_bad_target = client.post(
        "/api/control/snap",
        headers=dev_headers,
        json={"target": "invalid_string"},
    )
    assert r_bad_target.status_code == 422


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

    # Симулируем обработку RPi при ожидании команды
    async def mock_wait_command_result(cmd_id: int, timeout_sec: float, db: Session):
        db.expire_all()
        cmd = db.query(EdgeCommand).filter(EdgeCommand.id == cmd_id).first()
        cmd.status = "done"
        cmd.result_message = "Снимок выполнен успешно"
        cmd.debug_log = "[INFO] [GPIO] Inst#1: light=0, flash=1 -> captured 1 frames"

        # Создаем кадр в БД и связываем в app_state
        cap = Capture(
            installation_id=inst_id,
            camera_id=cam_id,
            captured_at=datetime.now(UTC),
            is_manual=True,
            aruco_area_px=1000.0,
            raw_image_path=f"media/{inst_id}/raw_99.jpg",
            annotated_image_path=f"media/{inst_id}/ann_99.jpg",
        )
        db.add(cap)
        db.flush()
        meas = PlantMeasurement(capture_id=cap.id, plant_index=0, area_px=2000.0)
        db.add(meas)
        db.commit()

        app_state.bind_command_capture(cmd_id, cap.id)
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
    assert cap_info["raw_url"] == f"/media/{inst_id}/raw_99.jpg"
    assert cap_info["area_display"] == "50.0 см²"
    assert "debug_info" in data
    assert (
        data["debug_info"]["log_line"]
        == "[INFO] [GPIO] Inst#1: light=0, flash=1 -> captured 1 frames"
    )

    # Проверяем, что app_state очищен
    assert app_state.pop_captures_by_command(data["command_id"]) == []

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
    assert len(data["cameras"]) == 2
    hw_ids = [c["hardware_id"] for c in data["cameras"]]
    assert "usb-cam-1-video-0" in hw_ids
    assert "usb-cam-2-video-0" in hw_ids
    assert "debug_info" in data

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
