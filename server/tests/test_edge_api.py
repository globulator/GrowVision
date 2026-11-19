import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from server.api.deps import get_db
from server.config import get_settings
from server.core.state import app_state
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

TEST_EDGE_KEY = "test-edge-secret-key-123"


@pytest.fixture
def test_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Настраивает изолированную БД, медиа-директорию и секретный ключ."""
    db_file = tmp_path / "test_edge_api.db"
    db_url = f"sqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Устанавливаем тестовый ключ Edge API
    monkeypatch.setattr(get_settings(), "edge_api_key", TEST_EDGE_KEY)
    app_state.clear()

    def override_get_db() -> Generator[Session, None, None]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    yield session_factory, engine

    app.dependency_overrides.clear()
    app_state.clear()
    engine.dispose()


@pytest.fixture
def client(test_env) -> TestClient:
    """Возвращает TestClient с тестовым окружением."""
    return TestClient(app)


def test_edge_api_key_auth(client: TestClient):
    """Критерий 2: Неверный или отсутствующий заголовок X-Edge-Key на всех эндпоинтах -> 401."""
    endpoints = [
        ("POST", "/api/edge/heartbeat", {"json": {}}),
        ("POST", "/api/edge/commands/claim", {}),
        (
            "POST",
            "/api/edge/commands/1/result",
            {"json": {"status": "done", "result_message": "ok"}},
        ),
        ("POST", "/api/edge/captures", {}),
    ]

    for method, path, kwargs in endpoints:
        # Без заголовка -> 401
        res_no_key = client.request(method, path, **kwargs)
        assert res_no_key.status_code == 401
        assert "detail" in res_no_key.json()

        # С неверным заголовком -> 401
        res_bad_key = client.request(
            method, path, headers={"X-Edge-Key": "wrong-key"}, **kwargs
        )
        assert res_bad_key.status_code == 401
        assert "detail" in res_bad_key.json()


def test_heartbeat_endpoint(client: TestClient, test_env):
    """Критерий 1: POST /api/edge/heartbeat:

    сохранение телеметрии, обновление камер, active_installations.
    """
    session_factory, _ = test_env
    db = session_factory()

    # 1. Создаем существующую камеру и активную установку
    inst = Installation(
        name="Установка 1",
        culture_name="Салат",
        light_gpio_pin=17,
        flash_gpio_pin=27,
        is_archived=False,
    )
    inst_archived = Installation(
        name="Установка в архиве",
        culture_name="Базилик",
        light_gpio_pin=18,
        flash_gpio_pin=28,
        is_archived=True,
    )
    db.add_all([inst, inst_archived])
    db.commit()

    cam_online = Camera(
        hardware_id="cam_known_1",
        auto_name="Cam 1",
        installation_id=inst.id,
        is_online=False,
    )
    cam_offline = Camera(
        hardware_id="cam_known_2",
        auto_name="Cam 2",
        installation_id=inst.id,
        is_online=True,
    )
    db.add_all([cam_online, cam_offline])
    db.commit()

    # Настройки
    db.add(SystemSetting(key="capture_interval_hours", value="2"))
    db.add(SystemSetting(key="debug_mode", value="true"))
    db.commit()
    inst_id = inst.id
    db.close()

    # 2. Отправляем запрос Heartbeat
    heartbeat_payload = {
        "software": {
            "edge_version": "0.2.0-a3f9c12",
            "python_version": "3.11.2",
            "model_name": "lettuce_seg_v1.onnx",
        },
        "hardware": {
            "board_model": "Raspberry Pi 5 Model B Rev 1.0",
            "os_info": "Debian GNU/Linux 12 (bookworm)",
            "uptime_sec": 84210,
            "cpu_temp_c": 48.5,
            "cpu_usage_percent": 14.2,
            "ram_used_mb": 612,
            "ram_total_mb": 8062,
            "disk_free_gb": 18.4,
            "gpio_mode": "rpi",
        },
        "offline_queue_count": 0,
        "detected_cameras": [
            {"hardware_id": "cam_known_1", "auto_name": "Cam 1"},
            {"hardware_id": "cam_unknown_x", "auto_name": "Unknown"},
        ],
    }

    resp = client.post(
        "/api/edge/heartbeat",
        headers={"X-Edge-Key": TEST_EDGE_KEY},
        json=heartbeat_payload,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["capture_interval_hours"] == 2
    assert data["debug_mode"] is True
    # Только активная установка
    assert len(data["active_installations"]) == 1
    assert data["active_installations"][0]["id"] == inst_id
    assert "cam_known_1" in data["active_installations"][0]["camera_hardware_ids"]

    # 3. Проверяем состояние БД
    db = session_factory()
    # Телеметрия сохранена
    telem = db.query(SystemSetting).filter_by(key="rpi_last_telemetry").first()
    assert telem is not None
    assert "Raspberry Pi 5" in telem.value

    # Статусы камер
    c1 = db.query(Camera).filter_by(hardware_id="cam_known_1").first()
    assert c1.is_online is True
    assert c1.last_seen_at is not None

    c2 = db.query(Camera).filter_by(hardware_id="cam_known_2").first()
    assert c2.is_online is False

    # Неизвестная камера НЕ создана
    c_unknown = db.query(Camera).filter_by(hardware_id="cam_unknown_x").first()
    assert c_unknown is None
    db.close()


def test_commands_claim_lifecycle_and_timeout(client: TestClient, test_env):
    """Критерий 1, 5: POST /api/edge/commands/claim:

    таймаут старых команд (>30 с), claim самой старой pending.
    """
    session_factory, _ = test_env
    db = session_factory()

    inst = Installation(
        name="Установка Claim",
        culture_name="Салат",
        light_gpio_pin=11,
        flash_gpio_pin=12,
        is_archived=False,
    )
    db.add(inst)
    db.commit()

    # 1. Просроченная команда (создана 40 секунд назад)
    expired_time = datetime.now(UTC) - timedelta(seconds=45)
    cmd_expired = EdgeCommand(
        command_type="SNAP",
        installation_id=inst.id,
        is_global=False,
        status="pending",
        created_at=expired_time,
    )

    # 2. Актуальная команда 1 (создана 10 секунд назад)
    time1 = datetime.now(UTC) - timedelta(seconds=10)
    cmd1 = EdgeCommand(
        command_type="LIGHT_MAIN",
        installation_id=inst.id,
        is_global=False,
        payload=json.dumps({"state": True}),
        status="pending",
        created_at=time1,
    )

    # 3. Актуальная команда 2 (создана 5 секунд назад)
    time2 = datetime.now(UTC) - timedelta(seconds=5)
    cmd2 = EdgeCommand(
        command_type="SNAP",
        installation_id=None,
        is_global=True,
        status="pending",
        created_at=time2,
    )
    db.add_all([cmd_expired, cmd1, cmd2])
    db.commit()
    cmd_expired_id = cmd_expired.id
    cmd1_id = cmd1.id
    cmd2_id = cmd2.id
    inst_id = inst.id
    db.close()

    # Вызываем claim первый раз
    resp1 = client.post(
        "/api/edge/commands/claim", headers={"X-Edge-Key": TEST_EDGE_KEY}
    )
    assert resp1.status_code == 200
    claimed = resp1.json()
    assert len(claimed) == 1
    # Должна быть выдана cmd1 (самая старая из непросроченных)
    assert claimed[0]["id"] == cmd1_id
    assert claimed[0]["command_type"] == "LIGHT_MAIN"
    assert claimed[0]["is_global"] is False
    assert claimed[0]["installation"]["id"] == inst_id
    assert claimed[0]["payload"] == {"state": True}

    # Проверяем, что просроченная ушла в timeout
    db = session_factory()
    exp_db = db.get(EdgeCommand, cmd_expired_id)
    assert exp_db.status == "timeout"

    cmd1_db = db.get(EdgeCommand, cmd1_id)
    assert cmd1_db.status == "running"
    assert cmd1_db.attempts == 1
    assert cmd1_db.started_at is not None
    db.close()

    # Вызываем claim второй раз -> должна быть выдана глобальная cmd2
    resp2 = client.post(
        "/api/edge/commands/claim", headers={"X-Edge-Key": TEST_EDGE_KEY}
    )
    assert resp2.status_code == 200
    claimed2 = resp2.json()
    assert len(claimed2) == 1
    assert claimed2[0]["id"] == cmd2_id
    assert claimed2[0]["is_global"] is True
    assert claimed2[0]["installation"] is None
    assert len(claimed2[0]["installations"]) == 1

    # Вызываем claim третий раз -> очередь пуста
    resp3 = client.post(
        "/api/edge/commands/claim", headers={"X-Edge-Key": TEST_EDGE_KEY}
    )
    assert resp3.status_code == 200
    assert resp3.json() == []


def test_command_result_endpoint(client: TestClient, test_env):
    """Критерий 1: POST /api/edge/commands/{command_id}/result.

    404, 409 для не-running, сохранение логов, SCAN_CAMERAS.
    """
    session_factory, _ = test_env
    db = session_factory()

    inst = Installation(
        name="Установка Res",
        culture_name="Салат",
        light_gpio_pin=15,
        flash_gpio_pin=16,
        is_archived=False,
    )
    db.add(inst)
    db.commit()

    cmd_running = EdgeCommand(
        command_type="SNAP",
        installation_id=inst.id,
        is_global=False,
        status="running",
    )
    cmd_timeout = EdgeCommand(
        command_type="SNAP",
        installation_id=inst.id,
        is_global=False,
        status="timeout",
    )
    cmd_scan = EdgeCommand(
        command_type="SCAN_CAMERAS",
        installation_id=None,
        is_global=True,
        status="running",
    )
    db.add_all([cmd_running, cmd_timeout, cmd_scan])
    db.commit()
    cmd_running_id = cmd_running.id
    cmd_timeout_id = cmd_timeout.id
    cmd_scan_id = cmd_scan.id
    db.close()

    # 1. Несуществующая команда -> 404
    r_404 = client.post(
        "/api/edge/commands/9999/result",
        headers={"X-Edge-Key": TEST_EDGE_KEY},
        json={"status": "done", "result_message": "Ok"},
    )
    assert r_404.status_code == 404

    # 2. Не в статусе running -> 409
    r_409 = client.post(
        f"/api/edge/commands/{cmd_timeout_id}/result",
        headers={"X-Edge-Key": TEST_EDGE_KEY},
        json={"status": "done", "result_message": "Ok"},
    )
    assert r_409.status_code == 409

    # 3. Успешный отчет по обычной команде
    r_ok = client.post(
        f"/api/edge/commands/{cmd_running_id}/result",
        headers={"X-Edge-Key": TEST_EDGE_KEY},
        json={
            "status": "done",
            "result_message": "Снимок готов",
            "debug_log_line": "[INFO] Log line 1",
        },
    )
    assert r_ok.status_code == 200
    assert r_ok.json() == {"ok": True}

    db = session_factory()
    cmd_check = db.get(EdgeCommand, cmd_running_id)
    assert cmd_check.status == "done"
    assert cmd_check.result_message == "Снимок готов"
    assert cmd_check.debug_log == "[INFO] Log line 1"
    assert cmd_check.completed_at is not None
    db.close()

    # 4. SCAN_CAMERAS: регистрация новых камер и запись в app_state
    r_scan = client.post(
        f"/api/edge/commands/{cmd_scan_id}/result",
        headers={"X-Edge-Key": TEST_EDGE_KEY},
        json={
            "status": "done",
            "result_message": "Найдено 2 камеры",
            "scanned_cameras": [
                {"hardware_id": "usb_scan_cam_1", "auto_name": "Scan Cam 1"},
                {"hardware_id": "usb_scan_cam_2", "auto_name": "Scan Cam 2"},
            ],
        },
    )
    assert r_scan.status_code == 200

    db = session_factory()
    c1 = db.query(Camera).filter_by(hardware_id="usb_scan_cam_1").first()
    assert c1 is not None
    assert c1.auto_name == "Scan Cam 1"
    assert c1.is_online is True

    # Проверка сохранения в памяти процесса
    cached = app_state.get_scanned_cameras(cmd_scan_id)
    assert cached == ["usb_scan_cam_1", "usb_scan_cam_2"]
    db.close()


def test_captures_upload_and_deduplication(
    client: TestClient, test_env, tmp_path: Path
):
    """Критерии 1, 3, 4, 6: POST /api/edge/captures:

    multipart/form-data, нормализация времени, дедупликация (200),
    404, 409, 422, сохранение файлов и command_id -> capture_id в app_state.
    """
    session_factory, _ = test_env
    db = session_factory()

    inst = Installation(
        name="Установка Capture",
        culture_name="Салат",
        light_gpio_pin=21,
        flash_gpio_pin=22,
        is_archived=False,
    )
    inst_archived = Installation(
        name="Архивная установка",
        culture_name="Томат",
        light_gpio_pin=31,
        flash_gpio_pin=32,
        is_archived=True,
    )
    db.add_all([inst, inst_archived])
    db.commit()

    cam = Camera(
        hardware_id="cam_capture_hw",
        auto_name="Capture Cam",
        installation_id=inst.id,
    )
    db.add(cam)
    db.commit()
    inst_id = inst.id
    archived_id = inst_archived.id
    db.close()

    raw_file = ("raw.jpg", b"fake-raw-jpg-content", "image/jpeg")
    ann_file = ("ann.jpg", b"fake-ann-jpg-content", "image/jpeg")

    # 1. Неизвестная камера -> 409
    meta_bad_cam = json.dumps(
        {
            "installation_id": inst_id,
            "camera_hardware_id": "non_existent_camera",
            "captured_at": "2026-11-18T19:00:00Z",
        }
    )
    r_bad_cam = client.post(
        "/api/edge/captures",
        headers={"X-Edge-Key": TEST_EDGE_KEY},
        data={"metadata": meta_bad_cam},
        files={"raw_image": raw_file, "annotated_image": ann_file},
    )
    assert r_bad_cam.status_code == 409
    assert "не зарегистрирована" in r_bad_cam.json()["detail"]

    # 2. Несуществующая установка -> 404
    meta_bad_inst = json.dumps(
        {
            "installation_id": 9999,
            "camera_hardware_id": "cam_capture_hw",
            "captured_at": "2026-11-18T19:00:00Z",
        }
    )
    r_bad_inst = client.post(
        "/api/edge/captures",
        headers={"X-Edge-Key": TEST_EDGE_KEY},
        data={"metadata": meta_bad_inst},
        files={"raw_image": raw_file, "annotated_image": ann_file},
    )
    assert r_bad_inst.status_code == 404

    # 3. Невалидные метаданные (невалидный JSON или схема) -> 422
    r_422 = client.post(
        "/api/edge/captures",
        headers={"X-Edge-Key": TEST_EDGE_KEY},
        data={"metadata": "not-a-valid-json"},
        files={"raw_image": raw_file, "annotated_image": ann_file},
    )
    assert r_422.status_code == 422

    # 4. Успешная загрузка первого кадра (201 Created)
    valid_meta = {
        "installation_id": inst_id,
        "camera_hardware_id": "cam_capture_hw",
        "captured_at": "2026-11-18T19:05:00.123456Z",  # с миллисекундами, нормализуется до секунд
        "is_manual": True,
        "aruco_area_px": 500.0,
        "command_id": 42,
        "measurements": [{"plant_index": 0, "area_px": 1500.0}],
    }
    r_created = client.post(
        "/api/edge/captures",
        headers={"X-Edge-Key": TEST_EDGE_KEY},
        data={"metadata": json.dumps(valid_meta)},
        files={"raw_image": raw_file, "annotated_image": ann_file},
    )
    assert r_created.status_code == 201
    cap_id = r_created.json()["capture_id"]
    assert cap_id is not None

    # Проверяем связь command_id -> capture_id в памяти процесса (п. 6)
    assert app_state.get_capture_by_command(42) == cap_id

    # Проверяем БД и файлы
    db = session_factory()
    cap = db.get(Capture, cap_id)
    assert cap.is_manual is True
    assert cap.aruco_area_px == 500.0
    # Проверяем нормализацию (микросекунды обнулены)
    assert cap.captured_at.second == 0
    assert len(cap.plant_measurements) == 1
    assert cap.plant_measurements[0].area_px == 1500.0

    # Проверяем наличие файла на диске
    server_media_path = Path("server") / cap.raw_image_path
    assert server_media_path.is_file()
    assert cap.raw_image_path.endswith(f"raw_{cap_id}.jpg")
    assert cap.annotated_image_path.endswith(f"ann_{cap_id}.jpg")
    db.close()

    # 5. Повторная загрузка дубликата (те же installation_id, camera, captured_at) -> 200 OK
    valid_meta["command_id"] = 43  # новая команда, но тот же кадр
    r_dup = client.post(
        "/api/edge/captures",
        headers={"X-Edge-Key": TEST_EDGE_KEY},
        data={"metadata": json.dumps(valid_meta)},
        files={"raw_image": raw_file, "annotated_image": ann_file},
    )
    assert r_dup.status_code == 200
    assert r_dup.json()["capture_id"] == cap_id
    # Связь для новой команды обновилась
    assert app_state.get_capture_by_command(43) == cap_id

    # Проверяем, что в БД не добавился дубль
    db = session_factory()
    total_captures = db.query(Capture).count()
    assert total_captures == 1
    total_meas = db.query(PlantMeasurement).count()
    assert total_meas == 1
    db.close()

    # 6. Загрузка кадра для архивной установки -> успешно принимается (201)
    archived_meta = {
        "installation_id": archived_id,
        "camera_hardware_id": "cam_capture_hw",
        "captured_at": "2026-11-18T20:00:00Z",
        "is_manual": False,
        "measurements": [],
    }
    r_arch = client.post(
        "/api/edge/captures",
        headers={"X-Edge-Key": TEST_EDGE_KEY},
        data={"metadata": json.dumps(archived_meta)},
        files={"raw_image": raw_file, "annotated_image": ann_file},
    )
    assert r_arch.status_code == 201

    # Очистка созданных медиафайлов теста
    import shutil

    shutil.rmtree(Path("server/media") / str(inst_id), ignore_errors=True)
    shutil.rmtree(Path("server/media") / str(archived_id), ignore_errors=True)
