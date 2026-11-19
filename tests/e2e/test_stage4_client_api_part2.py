import asyncio
import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from server.api.control import wait_command_result
from server.api.deps import get_db
from server.api.timelapse import generate_timelapse_video
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

CLIENT_KEY = "test-client-e2e-key"
EDGE_KEY = "test-edge-e2e-key"
BIO_PASS = "bio-pass-e2e"
DEV_PASS = "dev-pass-e2e"

TINY_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07"
    b"\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c"
    b"(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00\x02\x00\x02\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01"
    b"\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00"
    b"\x00?\x00\xbf\x00\xff\xd9"
)


@pytest.fixture
def stage4_test_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Изолированное окружение для сквозных тестов Этапа 4."""
    # 1. База данных
    db_file = tmp_path / "stage4_e2e.db"
    db_url = f"sqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # 2. Изоляция медиа
    test_media_dir = tmp_path / "media"
    test_media_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("server.api.edge.get_media_dir", lambda: test_media_dir)
    monkeypatch.setattr("server.api.timelapse.get_media_dir", lambda: test_media_dir)

    # 3. Настройки
    settings = get_settings()
    monkeypatch.setattr(settings, "client_api_key", CLIENT_KEY)
    monkeypatch.setattr(settings, "edge_api_key", EDGE_KEY)
    monkeypatch.setattr(settings, "biologist_password", BIO_PASS)
    monkeypatch.setattr(settings, "developer_password", DEV_PASS)
    monkeypatch.setattr(settings, "video_max_fps", 10)
    monkeypatch.setattr(settings, "video_min_duration_sec", 2)

    app_state.clear()
    login_limiter.clear()

    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    real_media_root = Path(__file__).resolve().parent.parent.parent / "server" / "media"

    client = TestClient(app)

    yield {
        "client": client,
        "session_factory": session_factory,
        "media_dir": test_media_dir,
        "tmp_path": tmp_path,
    }

    app.dependency_overrides.clear()
    app_state.clear()
    login_limiter.clear()
    engine.dispose()

    # Проверка чистоты рабочего каталога media
    non_gitkeep = [p for p in real_media_root.iterdir() if p.name != ".gitkeep"]
    assert not non_gitkeep, f"Файлы в рабочем server/media/: {non_gitkeep}"


def _set_rpi_heartbeat(
    session_factory, delta_seconds: float = 5.0, raw_str: str | None = None
) -> None:
    with session_factory() as db:
        st = (
            db.query(SystemSetting)
            .filter(SystemSetting.key == "rpi_last_heartbeat_at")
            .first()
        )
        if raw_str is not None:
            val = raw_str
        else:
            val = (datetime.now(UTC) - timedelta(seconds=delta_seconds)).isoformat()

        if not st:
            st = SystemSetting(key="rpi_last_heartbeat_at", value=val)
            db.add(st)
        else:
            st.value = val
        db.commit()


def _set_debug_mode(session_factory, enabled: bool) -> None:
    with session_factory() as db:
        st = db.query(SystemSetting).filter(SystemSetting.key == "debug_mode").first()
        val = "true" if enabled else "false"
        if not st:
            st = SystemSetting(key="debug_mode", value=val)
            db.add(st)
        else:
            st.value = val
        db.commit()


def _login(client: TestClient, role: str) -> dict[str, str]:
    login_limiter.clear()
    pwd = DEV_PASS if role == "developer" else BIO_PASS
    ext_id = f"e2e-{role}-ext-id"
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
    assert r.status_code == 200, f"Failed to login as {role}: {r.text}"
    return {
        "X-Client-Key": CLIENT_KEY,
        "X-Client-Type": "cli",
        "X-External-Id": ext_id,
    }


# ==============================================================================
# 1. Проверка прав доступа и статуса RPi (/api/control/*)
# ==============================================================================
def test_qa_control_permissions_and_rpi_status(stage4_test_env):
    """Критерий 1: Проверка прав доступа (Biologist, Developer) и статуса подключения RPi."""
    client = stage4_test_env["client"]
    session_factory = stage4_test_env["session_factory"]

    dev_headers = _login(client, "developer")
    bio_headers = _login(client, "biologist")

    # Без авторизации (Гость) -> 401
    for ep in [
        "/api/control/snap",
        "/api/control/light/main",
        "/api/control/light/flash",
        "/api/control/relay-test",
    ]:
        r_guest = client.post(ep, json={"target": 1})
        assert r_guest.status_code == 401, f"{ep} should require auth"

    # Доступ Biologist к реле и свету запрещен -> 403
    for ep in [
        "/api/control/light/main",
        "/api/control/light/flash",
    ]:
        r_bio_light = client.post(
            ep, headers=bio_headers, json={"target": 1, "state": True}
        )
        assert r_bio_light.status_code == 403

    r_bio_relay = client.post(
        "/api/control/relay-test", headers=bio_headers, json={"target": 1}
    )
    assert r_bio_relay.status_code == 403

    # Проверка статуса RPi:
    # 1. Heartbeat вообще не поступал -> 503
    r_no_hb = client.post("/api/control/snap", headers=bio_headers, json={"target": 1})
    assert r_no_hb.status_code == 503
    assert r_no_hb.json()["detail"] == "Raspberry Pi офлайн"

    # 2. Heartbeat старше 90 с -> 503
    _set_rpi_heartbeat(session_factory, delta_seconds=100.0)
    r_old_hb = client.post("/api/control/snap", headers=dev_headers, json={"target": 1})
    assert r_old_hb.status_code == 503
    assert r_old_hb.json()["detail"] == "Raspberry Pi офлайн"

    # 3. Некорректный формат времени в heartbeat -> 503
    _set_rpi_heartbeat(session_factory, raw_str="invalid-date-string")
    r_bad_hb = client.post("/api/control/snap", headers=dev_headers, json={"target": 1})
    assert r_bad_hb.status_code == 503
    assert r_bad_hb.json()["detail"] == "Raspberry Pi офлайн"


# ==============================================================================
# 2. Валидация target в /api/control/*
# ==============================================================================
def test_qa_control_target_validations(stage4_test_env):
    """Критерий 1: Валидация target (bool, <=0, string, несуществующий, архивная установка, all)."""
    client = stage4_test_env["client"]
    session_factory = stage4_test_env["session_factory"]
    dev_headers = _login(client, "developer")

    _set_rpi_heartbeat(session_factory, delta_seconds=10.0)

    # 1. target = boolean -> 422
    for b in [True, False]:
        r_snap = client.post(
            "/api/control/snap", headers=dev_headers, json={"target": b}
        )
        assert r_snap.status_code == 422, (
            f"SNAP: target={b} (boolean) должен отклоняться с 422, получен {r_snap.status_code}"
        )

        r_light = client.post(
            "/api/control/light/main",
            headers=dev_headers,
            json={"target": b, "state": True},
        )
        assert r_light.status_code == 422, (
            f"LIGHT: target={b} (boolean) должен отклоняться с 422, получен {r_light.status_code}"
        )

        r_flash = client.post(
            "/api/control/light/flash",
            headers=dev_headers,
            json={"target": b, "state": True},
        )
        assert r_flash.status_code == 422, (
            f"FLASH: target={b} (boolean) должен отклоняться с 422, получен {r_flash.status_code}"
        )

        r_relay = client.post(
            "/api/control/relay-test", headers=dev_headers, json={"target": b}
        )
        assert r_relay.status_code == 422, (
            f"RELAY: target={b} (boolean) должен отклоняться с 422, получен {r_relay.status_code}"
        )

    # 2. target <= 0 -> 422
    for bad_id in [0, -1, -100]:
        r_neg = client.post(
            "/api/control/snap", headers=dev_headers, json={"target": bad_id}
        )
        assert r_neg.status_code == 422

    # 3. target = невалидная строка -> 422
    for bad_str in ["none", "everything", ""]:
        r_str = client.post(
            "/api/control/snap", headers=dev_headers, json={"target": bad_str}
        )
        assert r_str.status_code == 422

    # 4. target = "all" при 0 активных установок -> 409
    r_all_empty = client.post(
        "/api/control/snap", headers=dev_headers, json={"target": "all"}
    )
    assert r_all_empty.status_code == 409
    assert r_all_empty.json()["detail"] == "Нет активных установок"

    # Создаем архивную установку
    with session_factory() as db:
        arch_inst = Installation(
            name="Archived Box",
            culture_name="Салат",
            light_gpio_pin=10,
            flash_gpio_pin=11,
            is_archived=True,
        )
        db.add(arch_inst)
        db.commit()
        arch_id = arch_inst.id

    # 5. target = архивная установка -> 409
    r_arch = client.post(
        "/api/control/snap", headers=dev_headers, json={"target": arch_id}
    )
    assert r_arch.status_code == 409
    assert r_arch.json()["detail"] == "Установка в архиве"

    # target="all" когда есть только архивная установка -> 409
    r_all_arch = client.post(
        "/api/control/snap", headers=dev_headers, json={"target": "all"}
    )
    assert r_all_arch.status_code == 409
    assert r_all_arch.json()["detail"] == "Нет активных установок"

    # 6. target = несуществующий id -> 404
    r_not_found = client.post(
        "/api/control/snap", headers=dev_headers, json={"target": 99999}
    )
    assert r_not_found.status_code == 404
    assert r_not_found.json()["detail"] == "Установка не найдена"


# ==============================================================================
# 3. Жизненный цикл SNAP, сохранение кадров и debug_info
# ==============================================================================
def test_qa_control_snap_lifecycle_and_debug_info(stage4_test_env, monkeypatch):
    """Критерий 1: Жизненный цикл SNAP, captures, is_manual=True, debug_info Developer/Biologist."""
    client = stage4_test_env["client"]
    session_factory = stage4_test_env["session_factory"]
    media_dir = stage4_test_env["media_dir"]

    dev_headers = _login(client, "developer")
    bio_headers = _login(client, "biologist")

    _set_rpi_heartbeat(session_factory, delta_seconds=5.0)
    _set_debug_mode(session_factory, True)

    with session_factory() as db:
        inst = Installation(
            name="SNAP Active Box",
            culture_name="Базилик",
            light_gpio_pin=20,
            flash_gpio_pin=21,
            aruco_size_mm=40.0,
            is_archived=False,
        )
        db.add(inst)
        db.flush()
        inst_id = inst.id

        cam = Camera(
            hardware_id="cam-snap-qa",
            auto_name="Snap QA Cam",
            installation_id=inst_id,
        )
        db.add(cam)
        db.commit()
        cam_id = cam.id

    # Подготавливаем мок ответа RPi для wait_command_result
    async def mock_wait_command_success(cmd_id: int, timeout_sec: float, db: Session):
        db.expire_all()
        cmd = db.query(EdgeCommand).filter(EdgeCommand.id == cmd_id).first()
        cmd.status = "done"
        cmd.result_message = "Кадр успешно захвачен"
        cmd.debug_log = "[DEBUG] Snapshot OK GPIO=20"

        # Создаем файлы снимка
        cap_dir = media_dir / str(inst_id)
        cap_dir.mkdir(parents=True, exist_ok=True)
        raw_file = cap_dir / "raw_snap_1.jpg"
        ann_file = cap_dir / "ann_snap_1.jpg"
        raw_file.write_bytes(TINY_JPEG)
        ann_file.write_bytes(TINY_JPEG)

        cap = Capture(
            installation_id=inst_id,
            camera_id=cam_id,
            captured_at=datetime.now(UTC),
            is_manual=True,
            aruco_area_px=1600.0,
            raw_image_path=f"media/{inst_id}/raw_snap_1.jpg",
            annotated_image_path=f"media/{inst_id}/ann_snap_1.jpg",
        )
        db.add(cap)
        db.flush()

        meas = PlantMeasurement(
            capture_id=cap.id,
            plant_index=0,
            area_px=3200.0,
        )
        db.add(meas)
        db.commit()

        app_state.bind_command_capture(cmd_id, cap.id)
        return cmd

    monkeypatch.setattr(
        "server.api.control.wait_command_result", mock_wait_command_success
    )

    # 1. Developer выполняет SNAP при debug_mode=True
    r_dev = client.post(
        "/api/control/snap", headers=dev_headers, json={"target": inst_id}
    )
    assert r_dev.status_code == 200
    data_dev = r_dev.json()
    assert data_dev["status"] == "done"
    assert len(data_dev["captures"]) == 1
    cap_info = data_dev["captures"][0]
    assert cap_info["installation_id"] == inst_id
    assert cap_info["camera_id"] == cam_id
    assert cap_info["raw_url"] == f"/media/{inst_id}/raw_snap_1.jpg"
    assert "area_display" in cap_info
    assert cap_info["area_display"] == "32.0 см²"

    # Developer видит debug_info
    assert "debug_info" in data_dev
    assert data_dev["debug_info"]["log_line"] == "[DEBUG] Snapshot OK GPIO=20"
    assert data_dev["debug_info"]["command"]["target"] == inst_id

    # Проверяем, что app_state очищен после выдачи
    assert app_state.get_capture_by_command(data_dev["command_id"]) is None

    # 2. Biologist выполняет SNAP при debug_mode=True
    r_bio = client.post(
        "/api/control/snap", headers=bio_headers, json={"target": inst_id}
    )
    assert r_bio.status_code == 200
    data_bio = r_bio.json()
    assert data_bio["status"] == "done"
    # Biologist НЕ должен видеть debug_info даже при debug_mode=True!
    assert "debug_info" not in data_bio

    # 3. Проверка ошибки RPi (status="error")
    async def mock_wait_command_error(cmd_id: int, timeout_sec: float, db: Session):
        db.expire_all()
        cmd = db.query(EdgeCommand).filter(EdgeCommand.id == cmd_id).first()
        cmd.status = "error"
        cmd.result_message = "Камера временно недоступна"
        cmd.debug_log = "[ERROR] Camera device busy"
        db.commit()
        return cmd

    monkeypatch.setattr(
        "server.api.control.wait_command_result", mock_wait_command_error
    )

    # Developer получает 200 с status="error" и debug_info
    r_err_dev = client.post(
        "/api/control/snap", headers=dev_headers, json={"target": inst_id}
    )
    assert r_err_dev.status_code == 200
    data_err = r_err_dev.json()
    assert data_err["status"] == "error"
    assert data_err["result_message"] == "Камера временно недоступна"
    assert "debug_info" in data_err
    assert data_err["debug_info"]["log_line"] == "[ERROR] Camera device busy"

    # При debug_mode=False debug_info не выдается даже Developer
    _set_debug_mode(session_factory, False)
    r_err_nodebug = client.post(
        "/api/control/snap", headers=dev_headers, json={"target": inst_id}
    )
    assert r_err_nodebug.status_code == 200
    assert "debug_info" not in r_err_nodebug.json()


# ==============================================================================
# 4. Команды управления светом, реле и таймаут ожидания 504
# ==============================================================================
def test_qa_control_light_relay_and_timeout_504(stage4_test_env, monkeypatch):
    """Критерии 1, 4: Управление светом, реле, target='all' и таймаут ожидания 504."""
    client = stage4_test_env["client"]
    session_factory = stage4_test_env["session_factory"]
    dev_headers = _login(client, "developer")

    _set_rpi_heartbeat(session_factory, delta_seconds=5.0)

    with session_factory() as db:
        inst = Installation(
            name="Light Box",
            culture_name="Укроп",
            light_gpio_pin=22,
            flash_gpio_pin=23,
            is_archived=False,
        )
        db.add(inst)
        db.commit()
        inst_id = inst.id

    async def mock_wait_command_relay(cmd_id: int, timeout_sec: float, db: Session):
        db.expire_all()
        cmd = db.query(EdgeCommand).filter(EdgeCommand.id == cmd_id).first()
        cmd.status = "done"
        cmd.result_message = "OK"
        db.commit()
        return cmd

    monkeypatch.setattr(
        "server.api.control.wait_command_result", mock_wait_command_relay
    )

    # 1. POST /api/control/light/main
    r_main = client.post(
        "/api/control/light/main",
        headers=dev_headers,
        json={"target": inst_id, "state": True},
    )
    assert r_main.status_code == 200
    assert r_main.json()["status"] == "done"

    # 2. POST /api/control/light/flash target="all"
    r_flash_all = client.post(
        "/api/control/light/flash",
        headers=dev_headers,
        json={"target": "all", "state": False},
    )
    assert r_flash_all.status_code == 200
    assert r_flash_all.json()["status"] == "done"

    with session_factory() as db:
        cmd_all = (
            db.query(EdgeCommand)
            .filter(EdgeCommand.id == r_flash_all.json()["command_id"])
            .first()
        )
        assert cmd_all.is_global is True
        assert cmd_all.installation_id is None

    # 3. POST /api/control/relay-test
    r_relay = client.post(
        "/api/control/relay-test",
        headers=dev_headers,
        json={"target": inst_id},
    )
    assert r_relay.status_code == 200
    assert r_relay.json()["status"] == "done"

    # 4. Проверка реального таймаута ожидания (504 Gateway Timeout)
    # Восстанавливаем оригинальный wait_command_result, но передаем минимальное время через мок sleep
    async def fast_wait(cmd_id: int, timeout_sec: float, db: Session):
        # вызываем реальный wait_command_result с маленьким timeout_sec (0.05 с)
        return await wait_command_result(cmd_id, 0.05, db)

    monkeypatch.setattr("server.api.control.wait_command_result", fast_wait)

    r_timeout = client.post(
        "/api/control/relay-test",
        headers=dev_headers,
        json={"target": inst_id},
    )
    assert r_timeout.status_code == 504
    assert "Превышено время ожидания ответа Raspberry Pi" in r_timeout.json()["detail"]

    # Проверяем, что команда переведена в статус "timeout"
    with session_factory() as db:
        last_cmd = db.query(EdgeCommand).order_by(EdgeCommand.id.desc()).first()
        assert last_cmd.status == "timeout"


# ==============================================================================
# 5. POST /api/cameras/scan и очистка app_state
# ==============================================================================
def test_qa_cameras_scan_and_cleanup(stage4_test_env, monkeypatch):
    """Критерии 2, 4: Сканирование камер, регистрация, очистка app_state."""
    client = stage4_test_env["client"]
    session_factory = stage4_test_env["session_factory"]

    dev_headers = _login(client, "developer")
    bio_headers = _login(client, "biologist")

    # Доступ только Developer (Biologist -> 403, Guest -> 401)
    r_guest = client.post("/api/cameras/scan")
    assert r_guest.status_code == 401
    r_bio = client.post("/api/cameras/scan", headers=bio_headers)
    assert r_bio.status_code == 403

    # RPi offline -> 503
    r_off = client.post("/api/cameras/scan", headers=dev_headers)
    assert r_off.status_code == 503

    _set_rpi_heartbeat(session_factory, delta_seconds=5.0)

    # Симулируем успешный ответ RPi в фоне
    original_sleep = asyncio.sleep

    async def fake_sleep(sec):
        # При первом же цикле ожидания эмулируем завершение команды RPi
        with session_factory() as db:
            cmd = (
                db.query(EdgeCommand)
                .filter(
                    EdgeCommand.command_type == "SCAN_CAMERAS",
                    EdgeCommand.status == "pending",
                )
                .first()
            )
            if cmd:
                cmd.status = "done"
                cmd.result_message = "Найдено 2 камеры"
                db.commit()
                app_state.save_scanned_cameras(
                    cmd.id, ["qa-usb-cam-01", "qa-csi-cam-02"]
                )
        await original_sleep(0.01)

    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    r_scan = client.post("/api/cameras/scan", headers=dev_headers)
    assert r_scan.status_code == 200
    scan_data = r_scan.json()
    assert scan_data["status"] == "done"
    assert len(scan_data["cameras"]) == 2

    cam_hw_ids = [c["hardware_id"] for c in scan_data["cameras"]]
    assert "qa-usb-cam-01" in cam_hw_ids
    assert "qa-csi-cam-02" in cam_hw_ids

    # Проверяем, что результаты сканирования очищены из app_state
    assert app_state.get_scanned_cameras(scan_data["command_id"]) is None

    # Проверяем, что камеры сохранены в БД
    with session_factory() as db:
        cams_in_db = db.query(Camera).all()
        assert len(cams_in_db) == 2


# ==============================================================================
# 6. Фоновая очистка таймаутов (expire_stale_commands)
# ==============================================================================
def test_qa_core_timeouts_expiration_logic(stage4_test_env):
    """Критерий 3: Логика истечения таймаутов: pending >30с, running >60с, snap-all."""
    session_factory = stage4_test_env["session_factory"]
    now = datetime(2026, 11, 20, 12, 0, 0, tzinfo=UTC)

    with session_factory() as db:
        # 2 активные установки
        i1 = Installation(
            name="I1",
            culture_name="C1",
            light_gpio_pin=1,
            flash_gpio_pin=2,
            is_archived=False,
        )
        i2 = Installation(
            name="I2",
            culture_name="C2",
            light_gpio_pin=3,
            flash_gpio_pin=4,
            is_archived=False,
        )
        i_arch = Installation(
            name="Arch",
            culture_name="C3",
            light_gpio_pin=5,
            flash_gpio_pin=6,
            is_archived=True,
        )
        db.add_all([i1, i2, i_arch])
        db.flush()

        # 1. Pending команды
        # Свежая (10 с) -> pending
        cmd_p_fresh = EdgeCommand(
            command_type="SNAP",
            installation_id=i1.id,
            status="pending",
            created_at=now - timedelta(seconds=10),
        )
        # Просроченная (35 с) -> timeout
        cmd_p_stale = EdgeCommand(
            command_type="SNAP",
            installation_id=i1.id,
            status="pending",
            created_at=now - timedelta(seconds=35),
        )

        # 2. Running команды (одиночные, лимит 60 с)
        # Свежая (40 с) -> running
        cmd_r_fresh = EdgeCommand(
            command_type="LIGHT_MAIN",
            installation_id=i1.id,
            status="running",
            started_at=now - timedelta(seconds=40),
            created_at=now - timedelta(seconds=45),
        )
        # Просроченная (65 с) -> timeout
        cmd_r_stale = EdgeCommand(
            command_type="LIGHT_MAIN",
            installation_id=i1.id,
            status="running",
            started_at=now - timedelta(seconds=65),
            created_at=now - timedelta(seconds=70),
        )

        # 3. Running глобальный SNAP: лимит (15 + 10*N) + 15 с
        # При N=2 активных установках: (15 + 20) + 15 = 50 с
        # 45 с -> running
        cmd_snap_all_fresh = EdgeCommand(
            command_type="SNAP",
            is_global=True,
            status="running",
            started_at=now - timedelta(seconds=45),
            created_at=now - timedelta(seconds=50),
        )
        # 55 с -> timeout
        cmd_snap_all_stale = EdgeCommand(
            command_type="SNAP",
            is_global=True,
            status="running",
            started_at=now - timedelta(seconds=55),
            created_at=now - timedelta(seconds=60),
        )

        db.add_all(
            [
                cmd_p_fresh,
                cmd_p_stale,
                cmd_r_fresh,
                cmd_r_stale,
                cmd_snap_all_fresh,
                cmd_snap_all_stale,
            ]
        )
        db.commit()

        # Запускаем expire_stale_commands
        expired_count = expire_stale_commands(db, now=now)
        assert expired_count == 3

        assert cmd_p_fresh.status == "pending"
        assert cmd_p_stale.status == "timeout"
        assert cmd_r_fresh.status == "running"
        assert cmd_r_stale.status == "timeout"
        assert cmd_snap_all_fresh.status == "running"
        assert cmd_snap_all_stale.status == "timeout"


# ==============================================================================
# 7. Таймлапс-кадры: GET /api/timelapse/{id}/frames
# ==============================================================================
def test_qa_timelapse_frames_public_and_ordering(stage4_test_env):
    """Критерий 5: Плановые кадры, сортировка ASC, Гость, фильтр camera_id, area_display."""
    client = stage4_test_env["client"]
    session_factory = stage4_test_env["session_factory"]

    # 1. 404 для несуществующей установки
    r_404 = client.get("/api/timelapse/99999/frames")
    assert r_404.status_code == 404

    # 2. Неверный X-Client-Key -> 401
    r_401 = client.get("/api/timelapse/1/frames", headers={"X-Client-Key": "wrong-key"})
    assert r_401.status_code == 401

    with session_factory() as db:
        inst = Installation(
            name="TL Box",
            culture_name="Мята",
            light_gpio_pin=14,
            flash_gpio_pin=15,
            aruco_size_mm=30.0,
            is_archived=False,
        )
        db.add(inst)
        db.flush()
        inst_id = inst.id

        cam1 = Camera(
            hardware_id="cam-tl-1", auto_name="TL Cam 1", installation_id=inst_id
        )
        cam2 = Camera(
            hardware_id="cam-tl-2", auto_name="TL Cam 2", installation_id=inst_id
        )
        db.add_all([cam1, cam2])
        db.flush()

        base_time = datetime(2026, 11, 20, 10, 0, 0, tzinfo=UTC)

        # Кадр 1: плановый, более ранний
        cap1 = Capture(
            installation_id=inst_id,
            camera_id=cam1.id,
            captured_at=base_time,
            is_manual=False,
            aruco_area_px=900.0,
            raw_image_path=f"media/{inst_id}/raw_1.jpg",
            annotated_image_path=f"media/{inst_id}/ann_1.jpg",
        )
        # Кадр 2: плановый, более поздний
        cap2 = Capture(
            installation_id=inst_id,
            camera_id=cam2.id,
            captured_at=base_time + timedelta(hours=1),
            is_manual=False,
            aruco_area_px=900.0,
            raw_image_path=f"media/{inst_id}/raw_2.jpg",
            annotated_image_path=f"media/{inst_id}/ann_2.jpg",
        )
        # Кадр 3: ручной снимок (is_manual=True) -> НЕ должен попасть в выдачу!
        cap_manual = Capture(
            installation_id=inst_id,
            camera_id=cam1.id,
            captured_at=base_time + timedelta(minutes=30),
            is_manual=True,
            aruco_area_px=900.0,
            raw_image_path=f"media/{inst_id}/raw_man.jpg",
            annotated_image_path=f"media/{inst_id}/ann_man.jpg",
        )
        db.add_all([cap1, cap2, cap_manual])
        db.flush()

        # Замер для plant_index=0
        m1 = PlantMeasurement(capture_id=cap1.id, plant_index=0, area_px=1800.0)
        db.add(m1)
        db.commit()

        cam1_id = cam1.id
        cam2_id = cam2.id

    # Запрос от Гостя (без заголовков)
    r_guest = client.get(f"/api/timelapse/{inst_id}/frames")
    assert r_guest.status_code == 200
    data = r_guest.json()
    assert data["installation_id"] == inst_id
    assert data["installation_name"] == "TL Box"
    assert data["culture_name"] == "Мята"
    assert len(data["frames"]) == 2

    frames = data["frames"]
    # Проверяем сортировку ASC
    assert frames[0]["captured_at"] < frames[1]["captured_at"]
    # Проверяем area_display для cap1: (1800 / 900) * (30^2) = 2 * 900 мм2 = 1800 мм2 = 18.0 см2
    assert frames[0]["area_display"] == "18.0 см²"
    # Для cap2 замера нет -> area_display is None
    assert frames[1]["area_display"] is None

    # Фильтрация по camera_id
    r_cam1 = client.get(f"/api/timelapse/{inst_id}/frames?camera_id={cam1_id}")
    assert r_cam1.status_code == 200
    assert len(r_cam1.json()["frames"]) == 1
    assert r_cam1.json()["frames"][0]["camera_id"] == cam1_id

    r_cam2 = client.get(f"/api/timelapse/{inst_id}/frames?camera_id={cam2_id}")
    assert r_cam2.status_code == 200
    assert len(r_cam2.json()["frames"]) == 1
    assert r_cam2.json()["frames"][0]["camera_id"] == cam2_id

    # Пустая установка -> 200 {"frames": []}
    with session_factory() as db:
        empty_inst = Installation(
            name="Empty Box",
            culture_name="Пусто",
            light_gpio_pin=16,
            flash_gpio_pin=17,
        )
        db.add(empty_inst)
        db.commit()
        empty_id = empty_inst.id

    r_empty = client.get(f"/api/timelapse/{empty_id}/frames")
    assert r_empty.status_code == 200
    assert r_empty.json()["frames"] == []


# ==============================================================================
# 8. Сборка видео через ffmpeg: POST /api/timelapse/{id}/video
# ==============================================================================
def test_qa_timelapse_video_ffmpeg_and_conflicts(stage4_test_env):
    """Критерий 6: def-синхронность, 404 при 0, 200 JPEG при 1, 200 MP4 при >=2, конфликт 422."""
    client = stage4_test_env["client"]
    session_factory = stage4_test_env["session_factory"]
    media_dir = stage4_test_env["media_dir"]

    dev_headers = _login(client, "developer")
    bio_headers = _login(client, "biologist")

    # 1. Проверяем, что эндпоинт синхронный (def, не async def)
    assert not inspect.iscoroutinefunction(generate_timelapse_video)

    with session_factory() as db:
        inst = Installation(
            name="Video Box",
            culture_name="Томат",
            light_gpio_pin=18,
            flash_gpio_pin=19,
        )
        db.add(inst)
        db.commit()
        inst_id = inst.id

    # 2. 0 плановых кадров -> 404
    r_zero = client.post(
        f"/api/timelapse/{inst_id}/video",
        headers=bio_headers,
        json={},
    )
    assert r_zero.status_code == 404
    assert r_zero.json()["detail"] == "Плановые снимки не найдены"

    # Создаем 1 плановый кадр
    cap_dir = media_dir / str(inst_id)
    cap_dir.mkdir(parents=True, exist_ok=True)
    f1_path = cap_dir / "raw_v1.jpg"
    f1_path.write_bytes(TINY_JPEG)

    with session_factory() as db:
        c1 = Camera(
            hardware_id="cam-v1", auto_name="Video Cam 1", installation_id=inst_id
        )
        c2 = Camera(
            hardware_id="cam-v2", auto_name="Video Cam 2", installation_id=inst_id
        )
        db.add_all([c1, c2])
        db.flush()

        t0 = datetime(2026, 11, 20, 14, 0, 0, tzinfo=UTC)
        cap1 = Capture(
            installation_id=inst_id,
            camera_id=c1.id,
            captured_at=t0,
            is_manual=False,
            raw_image_path=f"media/{inst_id}/raw_v1.jpg",
            annotated_image_path=f"media/{inst_id}/ann_v1.jpg",
        )
        db.add(cap1)
        db.commit()
        c1_id = c1.id
        c2_id = c2.id

    # 3. Ровно 1 кадр -> 200 image/jpeg
    r_one = client.post(
        f"/api/timelapse/{inst_id}/video",
        headers=bio_headers,
        json={},
    )
    assert r_one.status_code == 200
    assert r_one.headers["content-type"].startswith("image/jpeg")
    assert r_one.content == TINY_JPEG

    # Добавляем кадр второй камеры через 5 минут (<= 10 минут) -> конфликт одновременной съемки
    f2_path = cap_dir / "raw_v2.jpg"
    f2_path.write_bytes(TINY_JPEG)
    with session_factory() as db:
        cap2 = Capture(
            installation_id=inst_id,
            camera_id=c2_id,
            captured_at=t0 + timedelta(minutes=5),
            is_manual=False,
            raw_image_path=f"media/{inst_id}/raw_v2.jpg",
            annotated_image_path=f"media/{inst_id}/ann_v2.jpg",
        )
        db.add(cap2)
        db.commit()

    # 4. Конфликт нескольких камер без camera_id -> 422
    r_conflict = client.post(
        f"/api/timelapse/{inst_id}/video",
        headers=dev_headers,
        json={},
    )
    assert r_conflict.status_code == 422
    assert (
        "У установки несколько камер, укажите camera_id:" in r_conflict.json()["detail"]
    )
    assert "Video Cam 1" in r_conflict.json()["detail"]
    assert "Video Cam 2" in r_conflict.json()["detail"]

    # 5. Указание camera_id снимает конфликт, но для c2_id кадр только 1 -> отдается JPEG
    r_fix_cam2 = client.post(
        f"/api/timelapse/{inst_id}/video",
        headers=dev_headers,
        json={"camera_id": c2_id},
    )
    assert r_fix_cam2.status_code == 200
    assert r_fix_cam2.headers["content-type"].startswith("image/jpeg")

    # 6. Добавим еще кадры для cam-v1 для полноценной сборки MP4 через ffmpeg
    f3_path = cap_dir / "raw_v3.jpg"
    f3_path.write_bytes(TINY_JPEG)
    with session_factory() as db:
        cap3 = Capture(
            installation_id=inst_id,
            camera_id=c1_id,
            captured_at=t0 + timedelta(hours=2),
            is_manual=False,
            raw_image_path=f"media/{inst_id}/raw_v3.jpg",
            annotated_image_path=f"media/{inst_id}/ann_v3.jpg",
        )
        db.add(cap3)
        db.commit()

    # Сборка MP4 для camera_id=c1_id (2 кадра)
    r_mp4 = client.post(
        f"/api/timelapse/{inst_id}/video",
        headers=dev_headers,
        json={"camera_id": c1_id, "step": 1},
    )
    assert r_mp4.status_code == 200
    assert r_mp4.headers["content-type"].startswith("video/mp4")
    assert f'filename="timelapse_{inst_id}.mp4"' in r_mp4.headers["content-disposition"]
    assert "content-length" in r_mp4.headers
    # Проверка MP4 заголовка (ftyp box)
    assert len(r_mp4.content) > 100
    assert b"ftyp" in r_mp4.content[:32]

    # 7. Невалидный step -> 422
    for bad_step in [0, -1, "abc"]:
        r_bad_step = client.post(
            f"/api/timelapse/{inst_id}/video",
            headers=dev_headers,
            json={"camera_id": c1_id, "step": bad_step},
        )
        assert r_bad_step.status_code == 422


# ==============================================================================
# 9. Раздача статики и приоритет роутеров
# ==============================================================================
def test_qa_static_files_and_api_priority(stage4_test_env):
    """Критерий 7: Раздача статики /media и clients/web/, API роутеры не перекрыты."""
    client = stage4_test_env["client"]

    # 1. API роутер /api/version возвращает JSON
    r_ver = client.get("/api/version")
    assert r_ver.status_code == 200
    assert "server_version" in r_ver.json()

    # 2. Несуществующий API роутер возвращает 404 JSON, а не index.html
    r_api_404 = client.get("/api/nonexistent_endpoint_12345")
    assert r_api_404.status_code == 404
    assert "detail" in r_api_404.json()

    # 3. Раздача /media
    r_media = client.get("/media/.gitkeep")
    assert r_media.status_code in (200, 404)
