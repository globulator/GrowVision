import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server.api.deps import get_db
from server.config import get_settings
from server.core.state import app_state
from server.db.database import Base
from server.db.models import Camera, EdgeCommand, Installation, SystemSetting
from server.main import app

EDGE_KEY = "test-edge-secret-key"


@pytest.fixture
def edge_test_env(tmp_path: Path, monkeypatch):
    """Изолированное тестовое окружение для проверки Edge API."""
    # 1. Настройка тестового секретного ключа
    settings = get_settings()
    monkeypatch.setattr(settings, "edge_api_key", EDGE_KEY)

    # 2. Изолированная SQLite БД
    db_file = tmp_path / "edge_qa_test.db"
    db_url = f"sqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app_state.clear()

    # 3. Изоляция media каталога в tmp_path
    tmp_media_dir = tmp_path / "media"
    tmp_media_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("server.api.edge.get_media_dir", lambda: tmp_media_dir)

    real_media_root = Path(__file__).resolve().parent.parent.parent / "server" / "media"

    client = TestClient(app)

    yield client, session_factory, tmp_media_dir

    # Очистка
    app.dependency_overrides.clear()
    app_state.clear()
    engine.dispose()

    # Проверка: рабочий server/media не должен содержать посторонних файлов
    non_gitkeep_items = [p for p in real_media_root.iterdir() if p.name != ".gitkeep"]
    assert not non_gitkeep_items, (
        f"Файлы обнаружены в рабочем server/media: {non_gitkeep_items}"
    )


# ==============================================================================
# 1. Авторизация X-Edge-Key и не-ASCII символы (Критерии 1, 2)
# ==============================================================================
def test_qa_edge_auth_all_endpoints(edge_test_env):
    client, _, _ = edge_test_env

    endpoints = [
        ("POST", "/api/edge/heartbeat", {"software": {}, "hardware": {}}),
        ("POST", "/api/edge/commands/claim", None),
        (
            "POST",
            "/api/edge/commands/1/result",
            {"status": "done", "result_message": "ok"},
        ),
    ]

    for method, path, data in endpoints:
        # Без заголовка -> 401
        r_no_key = client.request(method, path, json=data)
        assert r_no_key.status_code == 401, f"Expected 401 on {path} without key"
        assert "detail" in r_no_key.json()

        # С неверным ASCII заголовком -> 401
        r_bad_key = client.request(
            method, path, json=data, headers={"X-Edge-Key": "wrong-key"}
        )
        assert r_bad_key.status_code == 401, f"Expected 401 on {path} with bad key"
        assert "detail" in r_bad_key.json()

        # С не-ASCII заголовком (кириллица, emoji в байтах) -> 401, а не 500 (Критерий 1)
        r_unicode = client.request(
            method,
            path,
            json=data,
            headers={"X-Edge-Key": "неверный-секрет-с-кириллицей-🔑".encode()},
        )
        assert r_unicode.status_code == 401, (
            f"Expected 401 for non-ascii key on {path}, got {r_unicode.status_code}"
        )
        assert "detail" in r_unicode.json()

    # Проверка /api/edge/captures без ключа
    r_cap_no_key = client.post("/api/edge/captures")
    assert r_cap_no_key.status_code == 401


# ==============================================================================
# 2. Heartbeat: сохранение телеметрии и статус камер (Критерий 1.1)
# ==============================================================================
def test_qa_edge_heartbeat_lifecycle(edge_test_env):
    client, session_factory, _ = edge_test_env
    db = session_factory()

    # Создаем установку и известную камеру
    inst = Installation(
        name="Heartbeat Inst",
        culture_name="Салат",
        light_gpio_pin=17,
        flash_gpio_pin=27,
        is_archived=False,
    )
    db.add(inst)
    db.commit()
    inst_id = inst.id

    known_cam = Camera(
        hardware_id="cam_hw_known",
        auto_name="Known Camera",
        installation_id=inst_id,
        is_online=False,
    )
    db.add(known_cam)

    # Настройки
    db.add(SystemSetting(key="capture_interval_hours", value="3"))
    db.add(SystemSetting(key="debug_mode", value="true"))
    db.commit()
    db.close()

    # Запрос heartbeat с известной камерой и неизвестной
    hb_payload = {
        "software": {
            "edge_version": "0.1.0-e2e",
            "python_version": "3.12.0",
            "model_name": "model_v1.onnx",
        },
        "hardware": {
            "board_model": "RPi 5",
            "os_info": "Linux",
            "uptime_sec": 7200,
            "cpu_temp_c": 42.0,
            "cpu_usage_percent": 15.0,
            "ram_used_mb": 400,
            "ram_total_mb": 8000,
            "disk_free_gb": 25.0,
            "gpio_mode": "rpi",
        },
        "offline_queue_count": 0,
        "detected_cameras": [
            {"hardware_id": "cam_hw_known", "auto_name": "Known Camera"},
            {"hardware_id": "cam_hw_unknown_random", "auto_name": "Ghost Camera"},
        ],
    }

    resp = client.post(
        "/api/edge/heartbeat",
        json=hb_payload,
        headers={"X-Edge-Key": EDGE_KEY},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["capture_interval_hours"] == 3
    assert data["debug_mode"] is True
    assert len(data["active_installations"]) == 1
    assert data["active_installations"][0]["id"] == inst_id
    assert "cam_hw_known" in data["active_installations"][0]["camera_hardware_ids"]

    # Проверка БД: известная камера стала online, неизвестная камера НЕ создана
    check_db = session_factory()
    c_known = (
        check_db.query(Camera).filter(Camera.hardware_id == "cam_hw_known").first()
    )
    assert c_known.is_online is True
    assert c_known.last_seen_at is not None

    c_ghost = (
        check_db.query(Camera)
        .filter(Camera.hardware_id == "cam_hw_unknown_random")
        .first()
    )
    assert c_ghost is None, "Неизвестная камера не должна создаваться при heartbeat!"

    # Проверка сохранения телеметрии
    telemetry = (
        check_db.query(SystemSetting)
        .filter(SystemSetting.key == "rpi_last_telemetry")
        .first()
    )
    assert telemetry is not None
    saved_telemetry = json.loads(telemetry.value)
    assert saved_telemetry["software"]["edge_version"] == "0.1.0-e2e"

    # Следующий heartbeat без камеры переводит её в offline
    hb_payload["detected_cameras"] = []
    resp2 = client.post(
        "/api/edge/heartbeat",
        json=hb_payload,
        headers={"X-Edge-Key": EDGE_KEY},
    )
    assert resp2.status_code == 200

    check_db.refresh(c_known)
    assert c_known.is_online is False
    check_db.close()


# ==============================================================================
# 3. Очередь команд: claim, таймауты pending и running (Критерии 1.2, 3)
# ==============================================================================
def test_qa_edge_commands_claim_and_timeout(edge_test_env):
    client, session_factory, _ = edge_test_env
    db = session_factory()

    inst = Installation(
        name="Cmd Inst",
        culture_name="Мята",
        light_gpio_pin=10,
        flash_gpio_pin=11,
        is_archived=False,
    )
    db.add(inst)
    db.commit()
    inst_id = inst.id

    now = datetime.now(UTC)

    # 1. Просроченная pending команда (>30 с)
    cmd_timeout_pending = EdgeCommand(
        command_type="LIGHT_MAIN",
        installation_id=inst_id,
        is_global=False,
        status="pending",
        created_at=now - timedelta(seconds=45),
    )
    # 2. Просроченная running одиночная команда (>60 с)
    cmd_timeout_running = EdgeCommand(
        command_type="LIGHT_FLASH",
        installation_id=inst_id,
        is_global=False,
        status="running",
        started_at=now - timedelta(seconds=70),
    )
    # 3. Активная running одиночная команда (не просрочена, 20 с)
    cmd_active_running = EdgeCommand(
        command_type="RELAY_TEST",
        installation_id=inst_id,
        is_global=False,
        status="running",
        started_at=now - timedelta(seconds=20),
    )
    # 4. Просроченный глобальный SNAP: N=1 -> лимит (15 + 10*1) + 15 = 40 с; started 50 с назад
    cmd_timeout_snap_global = EdgeCommand(
        command_type="SNAP",
        installation_id=None,
        is_global=True,
        status="running",
        started_at=now - timedelta(seconds=50),
    )
    # 5. Свежая pending одиночная команда
    cmd_fresh_single = EdgeCommand(
        command_type="SNAP",
        installation_id=inst_id,
        is_global=False,
        status="pending",
        created_at=now,
    )

    db.add_all(
        [
            cmd_timeout_pending,
            cmd_timeout_running,
            cmd_active_running,
            cmd_timeout_snap_global,
            cmd_fresh_single,
        ]
    )
    db.commit()
    t_pending_id = cmd_timeout_pending.id
    t_running_id = cmd_timeout_running.id
    act_running_id = cmd_active_running.id
    t_snap_id = cmd_timeout_snap_global.id
    fresh_id = cmd_fresh_single.id
    db.close()

    # Запрос claim
    resp = client.post(
        "/api/edge/commands/claim",
        headers={"X-Edge-Key": EDGE_KEY},
    )
    assert resp.status_code == 200
    claimed = resp.json()
    assert len(claimed) == 1
    assert claimed[0]["id"] == fresh_id
    assert claimed[0]["command_type"] == "SNAP"
    assert claimed[0]["is_global"] is False
    assert claimed[0]["installation"]["id"] == inst_id

    # Проверка статусов в БД
    check_db = session_factory()
    assert check_db.get(EdgeCommand, t_pending_id).status == "timeout"
    assert check_db.get(EdgeCommand, t_running_id).status == "timeout"
    assert check_db.get(EdgeCommand, act_running_id).status == "running"
    assert check_db.get(EdgeCommand, t_snap_id).status == "timeout"

    c_f = check_db.get(EdgeCommand, fresh_id)
    assert c_f.status == "running"
    assert c_f.attempts == 1
    assert c_f.started_at is not None

    # Повторный claim: очередь пуста
    resp_empty = client.post(
        "/api/edge/commands/claim",
        headers={"X-Edge-Key": EDGE_KEY},
    )
    assert resp_empty.status_code == 200
    assert resp_empty.json() == []

    # Создаем глобальную команду
    cmd_global = EdgeCommand(
        command_type="SCAN_CAMERAS",
        installation_id=None,
        is_global=True,
        status="pending",
    )
    check_db.add(cmd_global)
    check_db.commit()
    check_db.close()

    # Claim для глобальной команды
    resp_global = client.post(
        "/api/edge/commands/claim",
        headers={"X-Edge-Key": EDGE_KEY},
    )
    assert resp_global.status_code == 200
    claimed_global = resp_global.json()
    assert len(claimed_global) == 1
    assert claimed_global[0]["is_global"] is True
    assert claimed_global[0]["installation"] is None
    assert len(claimed_global[0]["installations"]) == 1


# ==============================================================================
# 4. Отчёт о выполнении команд и пустой список камер (Критерии 1.3, 4)
# ==============================================================================
def test_qa_edge_command_result_and_scan_cameras(edge_test_env):
    client, session_factory, _ = edge_test_env
    db = session_factory()

    # 1. Команда SCAN_CAMERAS с обнаружением новой камеры
    cmd_scan_1 = EdgeCommand(
        command_type="SCAN_CAMERAS",
        is_global=True,
        status="running",
    )
    # 2. Команда SCAN_CAMERAS с пустым списком обнаруженных камер
    cmd_scan_empty = EdgeCommand(
        command_type="SCAN_CAMERAS",
        is_global=True,
        status="running",
    )
    # 3. Команда в статусе pending (не running)
    cmd_pending = EdgeCommand(
        command_type="SNAP",
        installation_id=None,
        is_global=True,
        status="pending",
    )
    db.add_all([cmd_scan_1, cmd_scan_empty, cmd_pending])
    db.commit()
    scan_1_id = cmd_scan_1.id
    scan_empty_id = cmd_scan_empty.id
    pending_id = cmd_pending.id
    db.close()

    # 1. Отчёт для не-running команды -> 409 Conflict
    r_conflict = client.post(
        f"/api/edge/commands/{pending_id}/result",
        json={"status": "done", "result_message": "Err"},
        headers={"X-Edge-Key": EDGE_KEY},
    )
    assert r_conflict.status_code == 409
    assert "running" in r_conflict.json()["detail"]

    # 2. Отчёт для несуществующей команды -> 404 Not Found
    r_404 = client.post(
        "/api/edge/commands/999999/result",
        json={"status": "done", "result_message": "Err"},
        headers={"X-Edge-Key": EDGE_KEY},
    )
    assert r_404.status_code == 404

    # 3. Успешный отчёт SCAN_CAMERAS с обнаружением новой камеры
    scan_result_payload = {
        "status": "done",
        "result_message": "Найдена 1 камера",
        "debug_log_line": "[INFO] scanned v4l devices",
        "scanned_cameras": [
            {"hardware_id": "new_usb_cam_scan_01", "auto_name": "Scan HD Pro"}
        ],
    }
    r_ok = client.post(
        f"/api/edge/commands/{scan_1_id}/result",
        json=scan_result_payload,
        headers={"X-Edge-Key": EDGE_KEY},
    )
    assert r_ok.status_code == 200
    assert r_ok.json() == {"ok": True}

    # Проверка БД: камера создана
    check_db = session_factory()
    created_cam = (
        check_db.query(Camera)
        .filter(Camera.hardware_id == "new_usb_cam_scan_01")
        .first()
    )
    assert created_cam is not None
    assert created_cam.auto_name == "Scan HD Pro"
    assert created_cam.is_online is True

    # Проверка сохранения в памяти процесса
    assert app_state.get_scanned_cameras(scan_1_id) == ["new_usb_cam_scan_01"]

    # 4. Отчёт SCAN_CAMERAS с пустым списком камер (Критерий 4)
    r_empty_scan = client.post(
        f"/api/edge/commands/{scan_empty_id}/result",
        json={
            "status": "done",
            "result_message": "Камеры не найдены",
            "scanned_cameras": [],
        },
        headers={"X-Edge-Key": EDGE_KEY},
    )
    assert r_empty_scan.status_code == 200
    # Результат сохранён в InMemoryState как пустой список []
    assert app_state.get_scanned_cameras(scan_empty_id) == []

    check_db.close()


# ==============================================================================
# 5. Загрузка снимков, невалидная дата 422, изоляция медиа (Критерии 2, 6)
# ==============================================================================
def test_qa_edge_captures_full_flow(edge_test_env):
    client, session_factory, tmp_media_dir = edge_test_env
    db = session_factory()

    inst = Installation(
        name="Capture QA Inst",
        culture_name="Салат",
        light_gpio_pin=18,
        flash_gpio_pin=19,
        is_archived=False,
    )
    db.add(inst)
    db.commit()

    cam = Camera(
        hardware_id="cam_registered_qa",
        auto_name="QA Camera",
        installation_id=inst.id,
        is_online=True,
    )
    db.add(cam)

    archived_inst = Installation(
        name="Archived QA Inst",
        culture_name="Укроп",
        light_gpio_pin=21,
        flash_gpio_pin=22,
        is_archived=True,
    )
    db.add(archived_inst)
    db.commit()

    inst_id = inst.id
    arch_id = archived_inst.id
    db.close()

    raw_file = ("raw.jpg", io.BytesIO(b"fake_raw_jpg_bytes"), "image/jpeg")
    ann_file = ("ann.jpg", io.BytesIO(b"fake_ann_jpg_bytes"), "image/jpeg")

    # 1. Попытка загрузить кадр с неизвестной камеры -> 409
    meta_bad_cam = {
        "installation_id": inst_id,
        "camera_hardware_id": "unknown_ghost_cam",
        "captured_at": "2026-11-19T14:00:00Z",
    }
    r_bad_cam = client.post(
        "/api/edge/captures",
        data={"metadata": json.dumps(meta_bad_cam)},
        files={"raw_image": raw_file, "annotated_image": ann_file},
        headers={"X-Edge-Key": EDGE_KEY},
    )
    assert r_bad_cam.status_code == 409
    assert (
        r_bad_cam.json()["detail"]
        == "Камера не зарегистрирована, выполните сканирование камер"
    )

    # 2. Невалидный captured_at ("not-a-date") -> 422, а не 500 (Критерий 2)
    meta_invalid_date = {
        "installation_id": inst_id,
        "camera_hardware_id": "cam_registered_qa",
        "captured_at": "not-a-date",
        "is_manual": False,
    }
    r_bad_date = client.post(
        "/api/edge/captures",
        data={"metadata": json.dumps(meta_invalid_date)},
        files={"raw_image": raw_file, "annotated_image": ann_file},
        headers={"X-Edge-Key": EDGE_KEY},
    )
    assert r_bad_date.status_code == 422
    assert "detail" in r_bad_date.json()

    # 3. Несуществующая установка -> 404
    meta_bad_inst = {
        "installation_id": 999999,
        "camera_hardware_id": "cam_registered_qa",
        "captured_at": "2026-11-19T14:00:00Z",
    }
    r_bad_inst = client.post(
        "/api/edge/captures",
        data={"metadata": json.dumps(meta_bad_inst)},
        files={
            "raw_image": ("raw.jpg", io.BytesIO(b"raw"), "image/jpeg"),
            "annotated_image": ("ann.jpg", io.BytesIO(b"ann"), "image/jpeg"),
        },
        headers={"X-Edge-Key": EDGE_KEY},
    )
    assert r_bad_inst.status_code == 404

    # 4. Успешная загрузка кадра с command_id и замерами (201 Created)
    meta_valid = {
        "installation_id": inst_id,
        "camera_hardware_id": "cam_registered_qa",
        "captured_at": "2026-11-19T14:00:00.123456Z",
        "is_manual": True,
        "aruco_area_px": 450.0,
        "command_id": 777,
        "measurements": [{"plant_index": 0, "area_px": 14200.0}],
    }
    r_created = client.post(
        "/api/edge/captures",
        data={"metadata": json.dumps(meta_valid)},
        files={
            "raw_image": ("raw.jpg", io.BytesIO(b"raw_bytes_content"), "image/jpeg"),
            "annotated_image": (
                "ann.jpg",
                io.BytesIO(b"ann_bytes_content"),
                "image/jpeg",
            ),
        },
        headers={"X-Edge-Key": EDGE_KEY},
    )
    assert r_created.status_code == 201
    cap_id = r_created.json()["capture_id"]
    assert isinstance(cap_id, int)

    # Проверка связки в памяти процесса InMemoryState
    assert app_state.get_capture_by_command(777) == cap_id

    # Проверка файлов в изолированной тестовой директории
    raw_path = tmp_media_dir / str(inst_id) / f"raw_{cap_id}.jpg"
    ann_path = tmp_media_dir / str(inst_id) / f"ann_{cap_id}.jpg"
    assert raw_path.is_file()
    assert ann_path.is_file()
    assert raw_path.read_bytes() == b"raw_bytes_content"

    # 5. Повторная загрузка дубликата (та же секунда UTC) -> 200 OK с прежним capture_id
    meta_dup = {
        "installation_id": inst_id,
        "camera_hardware_id": "cam_registered_qa",
        "captured_at": "2026-11-19T14:00:00.999999Z",
        "is_manual": True,
        "command_id": 888,
        "measurements": [{"plant_index": 0, "area_px": 99999.0}],
    }
    r_dup = client.post(
        "/api/edge/captures",
        data={"metadata": json.dumps(meta_dup)},
        files={
            "raw_image": ("raw.jpg", io.BytesIO(b"overwriting_attempt"), "image/jpeg"),
            "annotated_image": (
                "ann.jpg",
                io.BytesIO(b"overwriting_attempt"),
                "image/jpeg",
            ),
        },
        headers={"X-Edge-Key": EDGE_KEY},
    )
    assert r_dup.status_code == 200
    assert r_dup.json()["capture_id"] == cap_id

    # Файлы на диске не перезаписались
    assert raw_path.read_bytes() == b"raw_bytes_content"
    # Новая команда связалась с существующим кадром в InMemoryState
    assert app_state.get_capture_by_command(888) == cap_id

    # 6. Загрузка кадра для архивной установки разрешена -> 201 Created
    meta_archived = {
        "installation_id": arch_id,
        "camera_hardware_id": "cam_registered_qa",
        "captured_at": "2026-11-19T14:05:00Z",
    }
    r_arch = client.post(
        "/api/edge/captures",
        data={"metadata": json.dumps(meta_archived)},
        files={
            "raw_image": ("raw.jpg", io.BytesIO(b"arch_raw"), "image/jpeg"),
            "annotated_image": ("ann.jpg", io.BytesIO(b"arch_ann"), "image/jpeg"),
        },
        headers={"X-Edge-Key": EDGE_KEY},
    )
    assert r_arch.status_code == 201
