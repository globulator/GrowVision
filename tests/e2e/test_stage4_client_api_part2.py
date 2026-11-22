import asyncio
import inspect
import io
import json
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from server.api.deps import get_db
from server.api.timelapse import generate_timelapse_video
from server.config import get_settings
from server.core.rate_limit import login_limiter
from server.core.state import app_state
from server.core.system_status import is_rpi_online
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
    db_file = tmp_path / "stage4_e2e.db"
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


def _set_rpi_online_manual(session_factory, delta_seconds: float = 5.0) -> None:
    """Устанавливает статус онлайн через запись rpi_last_telemetry.updated_at."""
    with session_factory() as db:
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

        db.query(SystemSetting).filter(
            SystemSetting.key == "rpi_last_telemetry"
        ).update(
            {"value": "{}", "updated_at": hb_time},
            synchronize_session=False,
        )
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
    ext_id = f"e2e-{role}-ext-id"
    if role == "developer":
        r = client.post(
            "/api/auth/login",
            headers={"X-Client-Key": CLIENT_KEY},
            json={
                "client_type": "cli",
                "external_id": ext_id,
                "login": role,
                "password": DEV_PASS,
            },
        )
        assert r.status_code == 200, f"Failed to login as {role}: {r.text}"
        return {
            "X-Client-Key": CLIENT_KEY,
            "X-Client-Type": "cli",
            "X-External-Id": ext_id,
        }
    else:
        dev_h = _login(client, "developer")
        inv_res = client.post(
            "/api/auth/invites",
            headers=dev_h,
            json={"role": "biologist"},
        )
        assert inv_res.status_code == 200, f"Failed to create invite: {inv_res.text}"
        code = inv_res.json()["code"]
        r = client.post(
            "/api/auth/login",
            headers={"X-Client-Key": CLIENT_KEY},
            json={
                "client_type": "telegram",
                "external_id": ext_id,
                "login": role,
                "password": code,
            },
        )
        assert r.status_code == 200, f"Failed to login as {role}: {r.text}"
        return {
            "X-Client-Key": CLIENT_KEY,
            "X-Client-Type": "telegram",
            "X-External-Id": ext_id,
        }


# ==============================================================================
# 1. Проверка прав, порядка проверок (роль -> тело -> target -> 503) и target
# ==============================================================================
def test_qa_control_permissions_and_check_order(stage4_test_env):
    """Критерии 1, 2, 3: Роли, валидация target (422 bool), порядок (404/409 до 503)."""
    client = stage4_test_env["client"]
    session_factory = stage4_test_env["session_factory"]

    dev_headers = _login(client, "developer")
    bio_headers = _login(client, "biologist")

    # 1. Без авторизации -> 401
    for ep in [
        "/api/control/snap",
        "/api/control/light/main",
        "/api/control/light/flash",
        "/api/control/relay-test",
    ]:
        r_guest = client.post(ep, json={"target": 1})
        assert r_guest.status_code == 401, f"{ep} должен требовать авторизацию"

    # 2. Недостаточно роли (Biologist к реле и свету) -> 403
    for ep in ["/api/control/light/main", "/api/control/light/flash"]:
        r_bio_light = client.post(
            ep, headers=bio_headers, json={"target": 1, "state": True}
        )
        assert r_bio_light.status_code == 403

    r_bio_relay = client.post(
        "/api/control/relay-test", headers=bio_headers, json={"target": 1}
    )
    assert r_bio_relay.status_code == 403

    # 3. Валидация тела (422) до проверок target и онлайна
    # Boolean в target (True, False) должен вызывать 422 во всех эндпоинтах
    for b in [True, False]:
        for ep, payload in [
            ("/api/control/snap", {"target": b}),
            ("/api/control/light/main", {"target": b, "state": True}),
            ("/api/control/light/flash", {"target": b, "state": False}),
            ("/api/control/relay-test", {"target": b}),
        ]:
            r_bool = client.post(ep, headers=dev_headers, json=payload)
            assert r_bool.status_code == 422, (
                f"{ep} target={b} должен возвращать 422, получен {r_bool.status_code}"
            )
            assert "target не может быть boolean" in r_bool.json()["detail"]

    # Отрицательные, нулевые и строковые ID -> 422
    for bad_target in [0, -5, "not_an_id"]:
        r_bad = client.post(
            "/api/control/snap", headers=dev_headers, json={"target": bad_target}
        )
        assert r_bad.status_code == 422

    # 4. Проверка СТРОГОГО порядка проверок: target (404/409) ПЕРЕД проверкой онлайна (503)!
    # В данный момент RPi ОФЛАЙН (heartbeat не поступал)
    with session_factory() as db:
        online, _ = is_rpi_online(db)
        assert not online, "RPi должен быть офлайн в начале теста"

        # Создаем архивную установку
        arch_inst = Installation(
            name="Archived Box Order",
            culture_name="Салат",
            light_gpio_pin=10,
            flash_gpio_pin=11,
            is_archived=True,
        )
        db.add(arch_inst)
        db.commit()
        arch_id = arch_inst.id

    # Несуществующая установка -> 404 (не 503!)
    r_404 = client.post(
        "/api/control/snap", headers=dev_headers, json={"target": 99999}
    )
    assert r_404.status_code == 404
    assert r_404.json()["detail"] == "Установка не найдена"

    # Архивная установка -> 409 (не 503!)
    r_409_arch = client.post(
        "/api/control/snap", headers=dev_headers, json={"target": arch_id}
    )
    assert r_409_arch.status_code == 409
    assert r_409_arch.json()["detail"] == "Установка в архиве"

    # target="all" без активных установок -> 409 (не 503!)
    r_409_all = client.post(
        "/api/control/snap", headers=dev_headers, json={"target": "all"}
    )
    assert r_409_all.status_code == 409
    assert r_409_all.json()["detail"] == "Нет активных установок"

    # Теперь создаем АКТИВНУЮ установку: target валиден, но RPi офлайн -> 503!
    with session_factory() as db:
        active_inst = Installation(
            name="Active Box Order",
            culture_name="Томат",
            light_gpio_pin=12,
            flash_gpio_pin=13,
            is_archived=False,
        )
        db.add(active_inst)
        db.commit()
        active_id = active_inst.id

    r_503 = client.post(
        "/api/control/snap", headers=dev_headers, json={"target": active_id}
    )
    assert r_503.status_code == 503
    assert r_503.json()["detail"] == "Raspberry Pi офлайн"


# ==============================================================================
# 2. Мгновенный 504 при статусе timeout команды в wait_command_result
# ==============================================================================
def test_qa_control_immediate_504_on_timeout(stage4_test_env, monkeypatch):
    """Критерий 4: wait_command_result немедленно возвращает 504 при статусе timeout."""
    client = stage4_test_env["client"]
    session_factory = stage4_test_env["session_factory"]
    dev_headers = _login(client, "developer")

    _set_rpi_online_manual(session_factory, delta_seconds=5.0)

    with session_factory() as db:
        inst = Installation(
            name="Timeout Box",
            culture_name="Мята",
            light_gpio_pin=14,
            flash_gpio_pin=15,
            is_archived=False,
        )
        db.add(inst)
        db.commit()
        inst_id = inst.id

    # При первом опросе команды переводим ее статус в timeout в БД
    original_sleep = asyncio.sleep

    async def fake_sleep_make_timeout(sec):
        with session_factory() as db:
            cmd = db.query(EdgeCommand).filter(EdgeCommand.status == "pending").first()
            if cmd:
                cmd.status = "timeout"
                db.commit()
        await original_sleep(0.01)

    monkeypatch.setattr("asyncio.sleep", fake_sleep_make_timeout)

    start_t = datetime.now(UTC)
    r = client.post(
        "/api/control/relay-test",
        headers=dev_headers,
        json={"target": inst_id},
    )
    elapsed = (datetime.now(UTC) - start_t).total_seconds()

    assert r.status_code == 504
    assert r.json()["detail"] == "Превышено время ожидания ответа Raspberry Pi"
    # Ожидание завершилось мгновенно (не ждало 10 секунд!)
    assert elapsed < 2.0


# ==============================================================================
# 3. Очистка app_state при ошибке SNAP и debug_info
# ==============================================================================
def test_qa_control_snap_error_cleans_app_state(stage4_test_env, monkeypatch):
    """Критерии 1, 6: SNAP error удаляет запись из app_state, debug_info только Developer."""
    client = stage4_test_env["client"]
    session_factory = stage4_test_env["session_factory"]

    dev_headers = _login(client, "developer")
    bio_headers = _login(client, "biologist")

    _set_rpi_online_manual(session_factory, delta_seconds=5.0)
    _set_debug_mode(session_factory, True)

    with session_factory() as db:
        inst = Installation(
            name="Error Inst",
            culture_name="Базилик",
            light_gpio_pin=24,
            flash_gpio_pin=25,
            is_archived=False,
        )
        db.add(inst)
        db.commit()
        inst_id = inst.id

    async def mock_wait_command_error(cmd_id: int, timeout_sec: float, db: Session):
        db.expire_all()
        cmd = db.query(EdgeCommand).filter(EdgeCommand.id == cmd_id).first()
        cmd.status = "error"
        cmd.result_message = "Ошибка инициализации сенсора камеры"
        cmd.debug_log = "[ERR] sensor fail on /dev/video0"
        db.commit()
        # Помещаем временную запись в app_state
        app_state.bind_command_capture(cmd_id, 9999)
        return cmd

    monkeypatch.setattr(
        "server.api.control.wait_command_result", mock_wait_command_error
    )

    # 1. Developer при debug_mode=True
    r_dev = client.post(
        "/api/control/snap", headers=dev_headers, json={"target": inst_id}
    )
    assert r_dev.status_code == 200
    data_dev = r_dev.json()
    assert data_dev["status"] == "error"
    assert data_dev["result_message"] == "Ошибка инициализации сенсора камеры"
    assert "debug_info" in data_dev
    assert data_dev["debug_info"]["log_line"] == "[ERR] sensor fail on /dev/video0"

    # Запись в app_state ДОЛЖНА БЫТЬ УДАЛЕНА даже при ошибке!
    cmd_id = data_dev["command_id"]
    assert app_state.get_capture_by_command(cmd_id) is None

    # 2. Biologist при debug_mode=True не видит debug_info
    r_bio = client.post(
        "/api/control/snap", headers=bio_headers, json={"target": inst_id}
    )
    assert r_bio.status_code == 200
    data_bio = r_bio.json()
    assert data_bio["status"] == "error"
    assert "debug_info" not in data_bio


# ==============================================================================
# 4. Сквозной сценарий: RPi онлайн через heartbeat -> control -> captures -> video
# ==============================================================================
def test_qa_e2e_full_hardware_flow(stage4_test_env):
    """Требование 2: Heartbeat делает RPi online -> light, snap, scan, captures upload, ffmpeg video."""
    client = stage4_test_env["client"]
    session_factory = stage4_test_env["session_factory"]

    dev_headers = _login(client, "developer")

    with session_factory() as db:
        inst = Installation(
            name="Hardware Flow Inst",
            culture_name="Томат Черри",
            light_gpio_pin=20,
            flash_gpio_pin=21,
            aruco_size_mm=40.0,
            is_archived=False,
        )
        db.add(inst)
        db.commit()
        inst_id = inst.id

        cam = Camera(
            hardware_id="cam-hw-01",
            auto_name="Hardware Cam 01",
            installation_id=inst_id,
        )
        db.add(cam)
        db.commit()

    # Шаг 1: До heartbeat RPi офлайн -> запрос света возвращает 503
    r_light_offline = client.post(
        "/api/control/light/main",
        headers=dev_headers,
        json={"target": inst_id, "state": True},
    )
    assert r_light_offline.status_code == 503
    assert r_light_offline.json()["detail"] == "Raspberry Pi офлайн"

    # Шаг 2: RPi отправляет POST /api/edge/heartbeat и становится ОНЛАЙН!
    hb_payload = {
        "software": {
            "edge_version": "0.1.0",
            "python_version": "3.12.0",
            "model_name": "model_test.onnx",
        },
        "hardware": {
            "board_model": "Raspberry Pi 5 Model B",
            "os_info": "Debian GNU/Linux 12 (bookworm)",
            "uptime_sec": 1000,
            "cpu_temp_c": 46.2,
            "cpu_usage_percent": 10.0,
            "ram_used_mb": 512,
            "ram_total_mb": 4096,
            "disk_free_gb": 15.5,
            "gpio_mode": "bcm",
        },
        "offline_queue_count": 0,
        "detected_cameras": [
            {"hardware_id": "cam-hw-01", "auto_name": "Hardware Cam 01"}
        ],
    }
    r_hb = client.post(
        "/api/edge/heartbeat",
        headers={"X-Edge-Key": EDGE_KEY},
        json=hb_payload,
    )
    assert r_hb.status_code == 200

    # Проверяем, что в GET /api/system/status связь online
    r_status = client.get("/api/system/status", headers=dev_headers)
    assert r_status.status_code == 200
    assert r_status.json()["rpi_connection"] == "online"
    assert r_status.json()["rpi_last_heartbeat_at"] is not None

    # Вспомогательный эмулятор фонового RPi агента в отдельном потоке
    def emulate_rpi_worker(on_command_callback):
        def worker():
            start_t = time.monotonic()
            while time.monotonic() - start_t < 5.0:
                time.sleep(0.05)
                r_claim = client.post(
                    "/api/edge/commands/claim", headers={"X-Edge-Key": EDGE_KEY}
                )
                if r_claim.status_code == 200 and r_claim.json():
                    cmd = r_claim.json()[0]
                    on_command_callback(cmd)
                    return

        th = threading.Thread(target=worker, daemon=True)
        th.start()
        return th

    # Шаг 3: Команда света LIGHT_MAIN с эмуляцией ответа RPi
    def handle_light(cmd):
        client.post(
            f"/api/edge/commands/{cmd['id']}/result",
            headers={"X-Edge-Key": EDGE_KEY},
            json={"status": "done", "result_message": "Light ON OK"},
        )

    t_light = emulate_rpi_worker(handle_light)
    r_light = client.post(
        "/api/control/light/main",
        headers=dev_headers,
        json={"target": inst_id, "state": True},
    )
    t_light.join(timeout=3.0)
    assert r_light.status_code == 200
    assert r_light.json()["status"] == "done"

    # Шаг 4: Сканирование камер POST /api/cameras/scan с ответом RPi
    def handle_scan(cmd):
        client.post(
            f"/api/edge/commands/{cmd['id']}/result",
            headers={"X-Edge-Key": EDGE_KEY},
            json={
                "status": "done",
                "result_message": "Found 2 cams",
                "scanned_cameras": [
                    {
                        "hardware_id": "cam-hw-01",
                        "auto_name": "Hardware Cam 01",
                    },
                    {
                        "hardware_id": "cam-hw-02",
                        "auto_name": "Hardware Cam 02",
                    },
                ],
            },
        )

    t_scan = emulate_rpi_worker(handle_scan)
    r_scan = client.post("/api/cameras/scan", headers=dev_headers)
    t_scan.join(timeout=3.0)
    assert r_scan.status_code == 200
    assert r_scan.json()["status"] == "done"
    assert len(r_scan.json()["cameras"]) == 2

    # Шаг 5: SNAP команда -> RPi делает claim -> заливает кадр через /api/edge/captures -> result done
    def handle_snap(cmd):
        cap_meta = {
            "installation_id": inst_id,
            "camera_hardware_id": "cam-hw-01",
            "captured_at": "2026-11-20T12:00:00Z",
            "is_manual": True,
            "aruco_area_px": 1600.0,
            "command_id": cmd["id"],
            "measurements": [{"plant_index": 0, "area_px": 3200.0}],
        }
        r_cap = client.post(
            "/api/edge/captures",
            data={"metadata": json.dumps(cap_meta)},
            files={
                "raw_image": (
                    "raw_1.jpg",
                    io.BytesIO(TINY_JPEG),
                    "image/jpeg",
                ),
                "annotated_image": (
                    "ann_1.jpg",
                    io.BytesIO(TINY_JPEG),
                    "image/jpeg",
                ),
            },
            headers={"X-Edge-Key": EDGE_KEY},
        )
        assert r_cap.status_code == 201

        client.post(
            f"/api/edge/commands/{cmd['id']}/result",
            headers={"X-Edge-Key": EDGE_KEY},
            json={"status": "done", "result_message": "Snap completed"},
        )

    t_snap = emulate_rpi_worker(handle_snap)
    r_snap = client.post(
        "/api/control/snap",
        headers=dev_headers,
        json={"target": inst_id},
    )
    t_snap.join(timeout=3.0)
    assert r_snap.status_code == 200
    assert r_snap.json()["status"] == "done"
    assert len(r_snap.json()["captures"]) == 1
    assert r_snap.json()["captures"][0]["area_display"] == "32.0 см²"

    # Шаг 6: Загружаем 2 ПЛАНОВЫХ кадра через POST /api/edge/captures для сборки видео
    for idx, cap_time in enumerate(
        ["2026-11-20T13:00:00Z", "2026-11-20T14:00:00Z"], start=1
    ):
        meta_plan = {
            "installation_id": inst_id,
            "camera_hardware_id": "cam-hw-01",
            "captured_at": cap_time,
            "is_manual": False,
            "aruco_area_px": 1600.0,
            "measurements": [{"plant_index": 0, "area_px": 3200.0 + idx * 500}],
        }
        r_up = client.post(
            "/api/edge/captures",
            data={"metadata": json.dumps(meta_plan)},
            files={
                "raw_image": (
                    f"plan_raw_{idx}.jpg",
                    io.BytesIO(TINY_JPEG),
                    "image/jpeg",
                ),
                "annotated_image": (
                    f"plan_ann_{idx}.jpg",
                    io.BytesIO(TINY_JPEG),
                    "image/jpeg",
                ),
            },
            headers={"X-Edge-Key": EDGE_KEY},
        )
        assert r_up.status_code == 201

    # Шаг 7: Сборка видео через POST /api/timelapse/{inst_id}/video настоящим ffmpeg
    r_video = client.post(
        f"/api/timelapse/{inst_id}/video",
        headers=dev_headers,
        json={},
    )
    assert r_video.status_code == 200
    assert r_video.headers["content-type"].startswith("video/mp4")
    assert (
        f'filename="timelapse_{inst_id}.mp4"' in r_video.headers["content-disposition"]
    )
    assert len(r_video.content) > 100
    assert b"ftyp" in r_video.content[:32]


# ==============================================================================
# 5. Тест на 2 камеры: конфликт 422 без camera_id, успех 200 с camera_id
# ==============================================================================
def test_qa_two_cameras_conflict_and_resolution(stage4_test_env):
    """Требование 3: 2 камеры одновременно: без camera_id -> 422 со списком камер, с camera_id -> 200."""
    client = stage4_test_env["client"]
    session_factory = stage4_test_env["session_factory"]
    media_dir = stage4_test_env["media_dir"]
    dev_headers = _login(client, "developer")

    with session_factory() as db:
        inst = Installation(
            name="Two Cams Box",
            culture_name="Салат Романо",
            light_gpio_pin=26,
            flash_gpio_pin=27,
            is_archived=False,
        )
        db.add(inst)
        db.flush()
        inst_id = inst.id

        cam1 = Camera(
            hardware_id="cam-logitech",
            auto_name="Logitech C270",
            installation_id=inst_id,
        )
        cam2 = Camera(
            hardware_id="cam-digma",
            auto_name="Digma DiCam 300",
            installation_id=inst_id,
        )
        db.add_all([cam1, cam2])
        db.commit()
        c1_id = cam1.id
        c2_id = cam2.id

    # Создаем реальные файлы кадров на диске
    inst_media_dir = media_dir / str(inst_id)
    inst_media_dir.mkdir(parents=True, exist_ok=True)
    (inst_media_dir / "c1_f1.jpg").write_bytes(TINY_JPEG)
    (inst_media_dir / "c1_f2.jpg").write_bytes(TINY_JPEG)
    (inst_media_dir / "c2_f1.jpg").write_bytes(TINY_JPEG)

    # Кадры обеих камер сняты с разницей в 3 минуты (<= 10 минут)
    base_t = datetime(2026, 11, 20, 15, 0, 0, tzinfo=UTC)
    with session_factory() as db:
        cap_c1_1 = Capture(
            installation_id=inst_id,
            camera_id=c1_id,
            captured_at=base_t,
            is_manual=False,
            raw_image_path=f"media/{inst_id}/c1_f1.jpg",
            annotated_image_path=f"media/{inst_id}/c1_f1.jpg",
        )
        cap_c2_1 = Capture(
            installation_id=inst_id,
            camera_id=c2_id,
            captured_at=base_t + timedelta(minutes=3),
            is_manual=False,
            raw_image_path=f"media/{inst_id}/c2_f1.jpg",
            annotated_image_path=f"media/{inst_id}/c2_f1.jpg",
        )
        cap_c1_2 = Capture(
            installation_id=inst_id,
            camera_id=c1_id,
            captured_at=base_t + timedelta(hours=1),
            is_manual=False,
            raw_image_path=f"media/{inst_id}/c1_f2.jpg",
            annotated_image_path=f"media/{inst_id}/c1_f2.jpg",
        )
        db.add_all([cap_c1_1, cap_c2_1, cap_c1_2])
        db.commit()

    # 1. Запрос без camera_id -> 422 со списком обеих камер
    r_no_cam = client.post(
        f"/api/timelapse/{inst_id}/video",
        headers=dev_headers,
        json={},
    )
    assert r_no_cam.status_code == 422
    err_detail = r_no_cam.json()["detail"]
    assert "У установки несколько камер, укажите camera_id:" in err_detail
    assert f"{c1_id} (Logitech C270)" in err_detail
    assert f"{c2_id} (Digma DiCam 300)" in err_detail

    # 2. Запрос с camera_id=c1_id -> 200 MP4 (у cam1 2 кадра)
    r_cam1 = client.post(
        f"/api/timelapse/{inst_id}/video",
        headers=dev_headers,
        json={"camera_id": c1_id},
    )
    assert r_cam1.status_code == 200
    assert r_cam1.headers["content-type"].startswith("video/mp4")
    assert b"ftyp" in r_cam1.content[:32]

    # 3. Запрос с camera_id=c2_id -> 200 JPEG (у cam2 1 кадр)
    r_cam2 = client.post(
        f"/api/timelapse/{inst_id}/video",
        headers=dev_headers,
        json={"camera_id": c2_id},
    )
    assert r_cam2.status_code == 200
    assert r_cam2.headers["content-type"].startswith("image/jpeg")
    assert r_cam2.content == TINY_JPEG


# ==============================================================================
# 6. Пропуск отсутствующих файлов и чистый 500 при сбоях ffmpeg
# ==============================================================================
def test_qa_timelapse_missing_files_and_ffmpeg_errors(stage4_test_env):
    """Критерий 5: Пропуск отсутствующих кадров, 404 при 0, чистый 500 без stderr при ошибках ffmpeg."""
    client = stage4_test_env["client"]
    session_factory = stage4_test_env["session_factory"]
    media_dir = stage4_test_env["media_dir"]
    dev_headers = _login(client, "developer")

    with session_factory() as db:
        inst = Installation(
            name="Missing Files Inst",
            culture_name="Огурец",
            light_gpio_pin=28,
            flash_gpio_pin=29,
            is_archived=False,
        )
        db.add(inst)
        db.flush()
        inst_id = inst.id

        cam = Camera(
            hardware_id="cam-missing",
            auto_name="Missing Cam",
            installation_id=inst_id,
        )
        db.add(cam)
        db.commit()
        cam_id = cam.id

    # 1. В БД есть кадры, но файлы на диске отсутствуют -> 404
    base_t = datetime(2026, 11, 20, 16, 0, 0, tzinfo=UTC)
    with session_factory() as db:
        cap1 = Capture(
            installation_id=inst_id,
            camera_id=cam_id,
            captured_at=base_t,
            is_manual=False,
            raw_image_path=f"media/{inst_id}/non_existent_1.jpg",
            annotated_image_path=f"media/{inst_id}/non_existent_1.jpg",
        )
        db.add(cap1)
        db.commit()

    r_no_file = client.post(
        f"/api/timelapse/{inst_id}/video",
        headers=dev_headers,
        json={},
    )
    assert r_no_file.status_code == 404
    assert r_no_file.json()["detail"] == "Плановые снимки не найдены"

    # 2. Создаем битые файлы (не валидные JPEG), вызывающие сбой ffmpeg
    inst_media_dir = media_dir / str(inst_id)
    inst_media_dir.mkdir(parents=True, exist_ok=True)
    corrupted_1 = inst_media_dir / "bad_1.jpg"
    corrupted_2 = inst_media_dir / "bad_2.jpg"
    corrupted_1.write_bytes(b"NOT_A_JPEG_HEADER_CORRUPTED_DATA_1111")
    corrupted_2.write_bytes(b"NOT_A_JPEG_HEADER_CORRUPTED_DATA_2222")

    with session_factory() as db:
        cap_bad_1 = Capture(
            installation_id=inst_id,
            camera_id=cam_id,
            captured_at=base_t + timedelta(hours=1),
            is_manual=False,
            raw_image_path=f"media/{inst_id}/bad_1.jpg",
            annotated_image_path=f"media/{inst_id}/bad_1.jpg",
        )
        cap_bad_2 = Capture(
            installation_id=inst_id,
            camera_id=cam_id,
            captured_at=base_t + timedelta(hours=2),
            is_manual=False,
            raw_image_path=f"media/{inst_id}/bad_2.jpg",
            annotated_image_path=f"media/{inst_id}/bad_2.jpg",
        )
        db.add_all([cap_bad_1, cap_bad_2])
        db.commit()

    # ffmpeg упадет с ненулевым кодом -> клиент должен получить чистый 500
    r_ffmpeg_fail = client.post(
        f"/api/timelapse/{inst_id}/video",
        headers=dev_headers,
        json={},
    )
    assert r_ffmpeg_fail.status_code == 500
    data_err = r_ffmpeg_fail.json()
    assert data_err["detail"] == "Ошибка кодирования видео"
    # Никаких внутренних путей и stderr не утекает!
    assert "ffmpeg" not in data_err["detail"]
    assert "Traceback" not in data_err["detail"]
    assert "/tmp" not in data_err["detail"]


# ==============================================================================
# 7. Предотвращение утечек app_state: cleanup_stale_entries
# ==============================================================================
def test_qa_app_state_stale_cleanup(stage4_test_env):
    """Критерий 6: Периодическая очистка устаревших записей в памяти воркера."""
    session_factory = stage4_test_env["session_factory"]
    now = datetime.now(UTC)

    with session_factory() as db:
        # 1. Завершённая команда больше 5 минут назад (6 минут)
        cmd_stale_done = EdgeCommand(
            command_type="SNAP",
            is_global=True,
            status="done",
            completed_at=now - timedelta(minutes=6),
            created_at=now - timedelta(minutes=7),
        )
        # 2. Свежая завершённая команда (2 минуты назад)
        cmd_fresh_done = EdgeCommand(
            command_type="SNAP",
            is_global=True,
            status="done",
            completed_at=now - timedelta(minutes=2),
            created_at=now - timedelta(minutes=3),
        )
        db.add_all([cmd_stale_done, cmd_fresh_done])
        db.commit()

        c_stale_id = cmd_stale_done.id
        c_fresh_id = cmd_fresh_done.id

    # Привязываем снимки и сканирования в память
    app_state.bind_command_capture(c_stale_id, 101)
    app_state.save_scanned_cameras(c_stale_id, ["hw-old"])
    app_state.bind_command_capture(c_fresh_id, 102)
    app_state.save_scanned_cameras(c_fresh_id, ["hw-fresh"])

    with session_factory() as db:
        cleaned = app_state.cleanup_stale_entries(db, max_age_seconds=300.0)
        assert cleaned >= 1

    # Устаревшая удалена
    assert app_state.get_capture_by_command(c_stale_id) is None
    assert app_state.get_scanned_cameras(c_stale_id) is None

    # Свежая осталась
    assert app_state.get_capture_by_command(c_fresh_id) == 102
    assert app_state.get_scanned_cameras(c_fresh_id) == ["hw-fresh"]


# ==============================================================================
# 8. Логика таймаутов в server/core/timeouts.py
# ==============================================================================
def test_qa_core_timeouts_expiration_logic(stage4_test_env):
    """Критерий 3: Логика истечения таймаутов: pending >30с, running >60с, snap-all."""
    session_factory = stage4_test_env["session_factory"]
    now = datetime(2026, 11, 20, 12, 0, 0, tzinfo=UTC)

    with session_factory() as db:
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

        cmd_p_fresh = EdgeCommand(
            command_type="SNAP",
            installation_id=i1.id,
            status="pending",
            created_at=now - timedelta(seconds=10),
        )
        cmd_p_stale = EdgeCommand(
            command_type="SNAP",
            installation_id=i1.id,
            status="pending",
            created_at=now - timedelta(seconds=35),
        )
        cmd_r_fresh = EdgeCommand(
            command_type="LIGHT_MAIN",
            installation_id=i1.id,
            status="running",
            started_at=now - timedelta(seconds=40),
            created_at=now - timedelta(seconds=45),
        )
        cmd_r_stale = EdgeCommand(
            command_type="LIGHT_MAIN",
            installation_id=i1.id,
            status="running",
            started_at=now - timedelta(seconds=65),
            created_at=now - timedelta(seconds=70),
        )
        cmd_snap_all_fresh = EdgeCommand(
            command_type="SNAP",
            is_global=True,
            status="running",
            started_at=now - timedelta(seconds=45),
            created_at=now - timedelta(seconds=50),
        )
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

        expired_count = expire_stale_commands(db, now=now)
        assert expired_count == 3

        assert cmd_p_fresh.status == "pending"
        assert cmd_p_stale.status == "timeout"
        assert cmd_r_fresh.status == "running"
        assert cmd_r_stale.status == "timeout"
        assert cmd_snap_all_fresh.status == "running"
        assert cmd_snap_all_stale.status == "timeout"


# ==============================================================================
# 9. GET /api/timelapse/{id}/frames (публичный, сортировка, area_display)
# ==============================================================================
def test_qa_timelapse_frames_public_and_ordering(stage4_test_env):
    """Критерий 5: Плановые кадры, сортировка ASC, Гость, фильтр camera_id, area_display."""
    client = stage4_test_env["client"]
    session_factory = stage4_test_env["session_factory"]

    # 404 для несуществующей установки
    r_404 = client.get("/api/timelapse/99999/frames")
    assert r_404.status_code == 404

    # Неверный X-Client-Key -> 401
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
            hardware_id="cam-tl-1",
            auto_name="TL Cam 1",
            installation_id=inst_id,
        )
        cam2 = Camera(
            hardware_id="cam-tl-2",
            auto_name="TL Cam 2",
            installation_id=inst_id,
        )
        db.add_all([cam1, cam2])
        db.flush()

        base_time = datetime(2026, 11, 20, 10, 0, 0, tzinfo=UTC)

        cap1 = Capture(
            installation_id=inst_id,
            camera_id=cam1.id,
            captured_at=base_time,
            is_manual=False,
            aruco_area_px=900.0,
            raw_image_path=f"media/{inst_id}/raw_1.jpg",
            annotated_image_path=f"media/{inst_id}/ann_1.jpg",
        )
        cap2 = Capture(
            installation_id=inst_id,
            camera_id=cam2.id,
            captured_at=base_time + timedelta(hours=1),
            is_manual=False,
            aruco_area_px=900.0,
            raw_image_path=f"media/{inst_id}/raw_2.jpg",
            annotated_image_path=f"media/{inst_id}/ann_2.jpg",
        )
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

        m1 = PlantMeasurement(capture_id=cap1.id, plant_index=0, area_px=1800.0)
        db.add(m1)
        db.commit()

        cam1_id = cam1.id
        cam2_id = cam2.id

    r_guest = client.get(f"/api/timelapse/{inst_id}/frames")
    assert r_guest.status_code == 200
    data = r_guest.json()
    assert len(data["frames"]) == 2
    assert data["frames"][0]["captured_at"] < data["frames"][1]["captured_at"]
    assert data["frames"][0]["area_display"] == "18.0 см²"
    assert data["frames"][1]["area_display"] is None

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
# 10. Раздача статики и приоритет API роутеров
# ==============================================================================
def test_qa_static_files_and_api_priority(stage4_test_env):
    """Критерий 7: Раздача статики /media и clients/web/, API роутеры не перекрыты."""
    client = stage4_test_env["client"]

    # Синхронность POST video эндпоинта
    assert not inspect.iscoroutinefunction(generate_timelapse_video)

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
