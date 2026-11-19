from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from server.config import Settings, load_settings
from server.db.database import init_db
from server.db.models import (
    Camera,
    Capture,
    EdgeCommand,
    Installation,
    PlantMeasurement,
    SystemSetting,
    User,
)
from shared.logger import UTCFormatter, setup_logger
from shared.schemas import (
    CaptureMeta,
    CommandItem,
    CommandResult,
    HeartbeatRequest,
    HeartbeatResponse,
    PlantMeasurementMeta,
)
from shared.version import get_app_version


# ==============================================================================
# 1. Структура репозитория и базовые файлы (Критерии 0, 1)
# ==============================================================================
def test_repo_structure_and_gitkeeps():
    repo_root = Path(__file__).resolve().parent.parent.parent

    # Проверка VERSION
    version_file = repo_root / "VERSION"
    assert version_file.is_file(), "VERSION file missing"
    assert version_file.read_text(encoding="utf-8").strip() == "0.0.0"

    # Проверка requirements-dev.txt
    req_dev_file = repo_root / "requirements-dev.txt"
    assert req_dev_file.is_file(), "requirements-dev.txt missing"
    req_dev_content = req_dev_file.read_text(encoding="utf-8")
    for package in ["pytest", "pytest-asyncio", "httpx", "ruff"]:
        assert package in req_dev_content, f"{package} missing in requirements-dev.txt"

    # Проверка server/requirements.txt
    server_req_file = repo_root / "server" / "requirements.txt"
    assert server_req_file.is_file(), "server/requirements.txt missing"
    server_req_content = server_req_file.read_text(encoding="utf-8")
    for package in [
        "fastapi",
        "uvicorn",
        "sqlalchemy",
        "python-multipart",
        "python-dotenv",
    ]:
        assert package in server_req_content, (
            f"{package} missing in server/requirements.txt"
        )

    # Проверка server/.env.example
    env_example = repo_root / "server" / ".env.example"
    assert env_example.is_file(), "server/.env.example missing"
    env_content = env_example.read_text(encoding="utf-8")
    for key in [
        "SERVER_HOST",
        "SERVER_PORT",
        "DATABASE_URL",
        "EDGE_API_KEY",
        "CLIENT_API_KEY",
        "BIOLOGIST_PASSWORD",
        "DEVELOPER_PASSWORD",
        "VIDEO_MAX_FPS",
        "VIDEO_MIN_DURATION_SEC",
    ]:
        assert key in env_content, f"{key} missing in server/.env.example"

    # Проверка .gitkeep в обязательных каталогах каркаса
    expected_gitkeeps = [
        "edge_agent/models/.gitkeep",
        "edge_agent/tests/.gitkeep",
        "server/media/.gitkeep",
        "clients/cli/tests/.gitkeep",
        "clients/telegram_bot/tests/.gitkeep",
        "clients/web/.gitkeep",
    ]
    for rel_path in expected_gitkeeps:
        target = repo_root / rel_path
        assert target.is_file(), f"Missing .gitkeep at {rel_path}"


# ==============================================================================
# 2. Версионирование (Критерий 2)
# ==============================================================================
def test_version_git_and_fallback(tmp_path: Path):
    repo_root = Path(__file__).resolve().parent.parent.parent

    # В репозитории с Git: должно быть '0.0.0-<хэш>'
    version_git = get_app_version(repo_root)
    assert version_git.startswith("0.0.0-"), f"Unexpected git version: {version_git}"
    assert len(version_git.split("-")) == 2

    # Во временном каталоге без Git: должно быть '0.0.0'
    fake_repo = tmp_path / "fake_repo"
    fake_repo.mkdir()
    (fake_repo / "VERSION").write_text("0.0.0\n", encoding="utf-8")
    assert get_app_version(fake_repo) == "0.0.0"

    # С изменённой версией без Git
    (fake_repo / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    assert get_app_version(fake_repo) == "1.2.3"


# ==============================================================================
# 3. Логгер (Критерий 3)
# ==============================================================================
def test_shared_logger_levels_and_formatter():
    log_info = setup_logger("qa_test_info", debug_mode=False)
    assert log_info.level == 20  # logging.INFO

    log_debug = setup_logger("qa_test_debug", debug_mode=True)
    assert log_debug.level == 10  # logging.DEBUG

    formatter = UTCFormatter()
    fmt_time = formatter.formatTime(type("Record", (), {"created": 1700000000.0})())
    assert fmt_time.endswith("Z")
    assert "T" in fmt_time


# ==============================================================================
# 4. Pydantic-схемы (Критерий 4, API_CONTRACT п. 3)
# ==============================================================================
def test_schemas_heartbeat_roundtrip():
    payload = {
        "software": {
            "edge_version": "0.1.0-test",
            "python_version": "3.12.0",
            "model_name": "lettuce_seg_v1.onnx",
        },
        "hardware": {
            "board_model": "Raspberry Pi 5 Model B Rev 1.0",
            "os_info": "Debian GNU/Linux 12",
            "uptime_sec": 3600,
            "cpu_temp_c": 45.2,
            "cpu_usage_percent": 12.5,
            "ram_used_mb": 512,
            "ram_total_mb": 4096,
            "disk_free_gb": 16.5,
            "gpio_mode": "rpi",
        },
        "offline_queue_count": 3,
        "detected_cameras": [{"hardware_id": "usb-cam-1", "auto_name": "Cam One"}],
    }

    hb = HeartbeatRequest.model_validate(payload)
    assert hb.software.edge_version == "0.1.0-test"
    assert hb.hardware.uptime_sec == 3600
    assert len(hb.detected_cameras) == 1
    assert hb.detected_cameras[0].hardware_id == "usb-cam-1"

    # HeartbeatResponse
    resp_data = {
        "capture_interval_hours": 2,
        "debug_mode": True,
        "active_installations": [
            {
                "id": 1,
                "light_gpio_pin": 17,
                "flash_gpio_pin": 27,
                "camera_hardware_ids": ["usb-cam-1"],
            }
        ],
    }
    resp = HeartbeatResponse.model_validate(resp_data)
    assert resp.capture_interval_hours == 2
    assert resp.debug_mode is True
    assert len(resp.active_installations) == 1


def test_schemas_command_and_result_validation():
    # Одиночная команда
    cmd_data = {
        "id": 101,
        "command_type": "SNAP",
        "is_global": False,
        "installation": {
            "id": 1,
            "light_gpio_pin": 17,
            "flash_gpio_pin": 27,
            "camera_hardware_ids": ["usb-cam-1"],
        },
        "payload": {"state": True},
    }
    cmd = CommandItem.model_validate(cmd_data)
    assert cmd.id == 101
    assert cmd.installation is not None
    assert cmd.installation.id == 1

    # Результат команды
    res_ok = CommandResult.model_validate(
        {
            "status": "done",
            "result_message": "Success",
            "debug_log_line": "log trace",
            "scanned_cameras": [{"hardware_id": "cam-1", "auto_name": "C1"}],
        }
    )
    assert res_ok.status == "done"

    # Негативный сценарий: неверный статус
    with pytest.raises(ValidationError):
        CommandResult.model_validate(
            {"status": "in_progress", "result_message": "fail"}
        )


def test_schemas_capture_meta_validation():
    meta_data = {
        "installation_id": 1,
        "camera_hardware_id": "usb-cam-1",
        "captured_at": "2026-11-19T10:00:00Z",
        "is_manual": True,
        "aruco_area_px": 500.5,
        "command_id": 42,
        "measurements": [{"plant_index": 0, "area_px": 12500.0}],
    }
    meta = CaptureMeta.model_validate(meta_data)
    assert meta.installation_id == 1
    assert meta.command_id == 42
    assert len(meta.measurements) == 1

    # Негативный сценарий: отсутствует обязательный installation_id
    with pytest.raises(ValidationError):
        CaptureMeta.model_validate(
            {
                "camera_hardware_id": "usb-cam-1",
                "captured_at": "2026-11-19T10:00:00Z",
            }
        )


def test_schemas_bounds_validation_e2e():
    """Требование 3: PlantMeasurementMeta.area_px ge=0, CaptureMeta.aruco_area_px gt=0.

    Значения -1 и 0 для aruco_area_px и -1 для area_px вызывают ValidationError.
    """
    # PlantMeasurementMeta: 0 разрешено, -1 вызывает ValidationError
    m0 = PlantMeasurementMeta(plant_index=0, area_px=0.0)
    assert m0.area_px == 0.0

    with pytest.raises(ValidationError):
        PlantMeasurementMeta(plant_index=0, area_px=-1.0)

    # CaptureMeta: aruco_area_px=None разрешено, >0 разрешено
    c_none = CaptureMeta(
        installation_id=1,
        camera_hardware_id="usb-cam-1",
        captured_at="2026-11-19T10:00:00Z",
        aruco_area_px=None,
    )
    assert c_none.aruco_area_px is None

    c_valid = CaptureMeta(
        installation_id=1,
        camera_hardware_id="usb-cam-1",
        captured_at="2026-11-19T10:00:00Z",
        aruco_area_px=100.5,
    )
    assert c_valid.aruco_area_px == 100.5

    # 0 и -1 для aruco_area_px вызывают ValidationError
    with pytest.raises(ValidationError):
        CaptureMeta(
            installation_id=1,
            camera_hardware_id="usb-cam-1",
            captured_at="2026-11-19T10:00:00Z",
            aruco_area_px=0.0,
        )

    with pytest.raises(ValidationError):
        CaptureMeta(
            installation_id=1,
            camera_hardware_id="usb-cam-1",
            captured_at="2026-11-19T10:00:00Z",
            aruco_area_px=-1.0,
        )


# ==============================================================================
# 5. Конфигурация сервера (Критерий 5)
# ==============================================================================
def test_server_config_loading_and_validation(tmp_path: Path):
    env_file = tmp_path / ".env.test"
    env_file.write_text(
        "SERVER_HOST=127.0.0.1\n"
        "SERVER_PORT=9000\n"
        "DATABASE_URL=sqlite:///./test.db\n"
        "EDGE_API_KEY=edge_secret\n"
        "CLIENT_API_KEY=client_secret\n"
        "BIOLOGIST_PASSWORD=bio_pass\n"
        "DEVELOPER_PASSWORD=dev_pass\n"
        "VIDEO_MAX_FPS=15\n"
        "VIDEO_MIN_DURATION_SEC=5\n",
        encoding="utf-8",
    )
    settings = load_settings(env_file)
    assert settings.server_host == "127.0.0.1"
    assert settings.server_port == 9000
    assert settings.edge_api_key == "edge_secret"
    assert settings.video_max_fps == 15
    assert settings.video_min_duration_sec == 5

    # Валидация ограничений ge=1
    with pytest.raises(ValidationError):
        Settings(VIDEO_MAX_FPS=0)

    with pytest.raises(ValidationError):
        Settings(VIDEO_MIN_DURATION_SEC=-1)


# ==============================================================================
# 6. База данных: схема, ограничения, жизненный цикл (Критерии 6, 7, 8)
# ==============================================================================
@pytest.fixture
def qa_isolated_db(tmp_path: Path):
    db_file = tmp_path / "qa_test.db"
    db_url = f"sqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    init_db(engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    yield engine, session_factory
    engine.dispose()


def test_qa_db_pragmas_and_tables(qa_isolated_db):
    engine, _ = qa_isolated_db
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    assert len(tables) == 7
    assert tables == {
        "installations",
        "cameras",
        "captures",
        "plant_measurements",
        "users",
        "edge_commands",
        "system_settings",
    }

    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys;")).scalar() == 1
        assert conn.execute(text("PRAGMA journal_mode;")).scalar().lower() == "wal"
        assert conn.execute(text("PRAGMA busy_timeout;")).scalar() == 5000


def test_qa_lifecycle_installation_and_pin_uniqueness(qa_isolated_db):
    _, session_factory = qa_isolated_db
    session = session_factory()

    # Жизненный цикл: Создание установки
    inst1 = Installation(
        name="QA Inst 1",
        culture_name="Салат",
        light_gpio_pin=17,
        flash_gpio_pin=27,
        aruco_size_mm=30.0,
        is_archived=False,
    )
    session.add(inst1)
    session.commit()
    assert inst1.id is not None

    # Чтение
    inst_read = session.get(Installation, inst1.id)
    assert inst_read.name == "QA Inst 1"

    # Изменение
    inst_read.culture_name = "Базилик"
    session.commit()
    assert session.get(Installation, inst1.id).culture_name == "Базилик"

    # Конфликт уникальности имени
    inst_dup_name = Installation(
        name="QA Inst 1",
        culture_name="Мята",
        light_gpio_pin=22,
        flash_gpio_pin=23,
    )
    session.add(inst_dup_name)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # Конфликт уникальности пина света среди активных
    inst_dup_light = Installation(
        name="QA Inst 2",
        culture_name="Мята",
        light_gpio_pin=17,
        flash_gpio_pin=23,
        is_archived=False,
    )
    session.add(inst_dup_light)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # Архивация первой установки
    inst1.is_archived = True
    session.commit()

    # Теперь установка с light_gpio_pin=17 разрешена!
    inst_reuse_light = Installation(
        name="QA Inst 2 (новая)",
        culture_name="Мята",
        light_gpio_pin=17,
        flash_gpio_pin=23,
        is_archived=False,
    )
    session.add(inst_reuse_light)
    session.commit()
    assert inst_reuse_light.id is not None

    session.close()


def test_qa_negative_installation_constraints(qa_isolated_db):
    _, session_factory = qa_isolated_db
    session = session_factory()

    # light_gpio_pin == flash_gpio_pin
    inst_same_pin = Installation(
        name="Invalid Same Pin",
        culture_name="Салат",
        light_gpio_pin=20,
        flash_gpio_pin=20,
    )
    session.add(inst_same_pin)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # aruco_size_mm <= 0
    inst_zero_aruco = Installation(
        name="Invalid Aruco Zero",
        culture_name="Салат",
        light_gpio_pin=20,
        flash_gpio_pin=21,
        aruco_size_mm=0.0,
    )
    session.add(inst_zero_aruco)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    session.close()


def test_qa_camera_and_capture_relationships(qa_isolated_db):
    _, session_factory = qa_isolated_db
    session = session_factory()

    # 1. Создаем установку и камеру
    inst = Installation(
        name="Inst Relations",
        culture_name="Укроп",
        light_gpio_pin=4,
        flash_gpio_pin=14,
    )
    session.add(inst)
    session.commit()

    cam = Camera(
        hardware_id="v4l-cam-qa-test",
        auto_name="QA Camera",
        installation_id=inst.id,
    )
    session.add(cam)
    session.commit()

    # 2. Создаем кадр
    now_utc = datetime.now(UTC)
    cap = Capture(
        installation_id=inst.id,
        camera_id=cam.id,
        captured_at=now_utc,
        raw_image_path="media/1/raw_1.jpg",
        annotated_image_path="media/1/ann_1.jpg",
    )
    session.add(cap)
    session.commit()

    # 3. Создаем замер растения
    meas = PlantMeasurement(
        capture_id=cap.id,
        plant_index=0,
        area_px=5432.1,
    )
    session.add(meas)
    session.commit()

    # Требование 2: Попытка удалить установку при наличии кадров -> RESTRICT IntegrityError
    session.delete(inst)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # Дубликат кадра uq_captures_inst_cam_time (installation_id, camera_id, captured_at)
    cap_dup = Capture(
        installation_id=inst.id,
        camera_id=cam.id,
        captured_at=now_utc,
        raw_image_path="media/1/raw_1_dup.jpg",
        annotated_image_path="media/1/ann_1_dup.jpg",
    )
    session.add(cap_dup)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # Проверка SET NULL для captures.camera_id при удалении камеры
    session.delete(cam)
    session.commit()
    reloaded_cap = session.get(Capture, cap.id)
    assert reloaded_cap is not None
    assert reloaded_cap.camera_id is None
    assert session.get(Installation, inst.id) is not None

    # Удаление кадра -> каскадное удаление замера
    session.delete(reloaded_cap)
    session.commit()
    assert session.get(PlantMeasurement, meas.id) is None

    session.close()


def test_qa_installation_delete_cascades_commands(qa_isolated_db):
    """Требование 1: Удаление установки без кадров с командами удаляет команды."""
    _, session_factory = qa_isolated_db
    session = session_factory()

    inst = Installation(
        name="Inst Cmd Cascades",
        culture_name="Салат",
        light_gpio_pin=28,
        flash_gpio_pin=29,
        is_archived=False,
    )
    session.add(inst)
    session.commit()

    cmd1 = EdgeCommand(
        command_type="SNAP",
        installation_id=inst.id,
        is_global=False,
        status="pending",
    )
    cmd2 = EdgeCommand(
        command_type="LIGHT_MAIN",
        installation_id=inst.id,
        is_global=False,
        status="running",
    )
    session.add_all([cmd1, cmd2])
    session.commit()
    cmd1_id, cmd2_id = cmd1.id, cmd2.id

    # Удаляем установку
    session.delete(inst)
    session.commit()

    # Установка и связанные команды удалены
    assert session.get(Installation, inst.id) is None
    assert session.get(EdgeCommand, cmd1_id) is None
    assert session.get(EdgeCommand, cmd2_id) is None

    session.close()


def test_qa_users_edge_commands_and_settings(qa_isolated_db):
    _, session_factory = qa_isolated_db
    session = session_factory()

    # User: допустимые роли и типы клиентов
    user = User(
        client_type="cli",
        external_id="cli_user_1",
        login="QA Tester",
        role="developer",
    )
    session.add(user)
    session.commit()

    # Недопустимый client_type
    bad_client = User(
        client_type="web",
        external_id="web_1",
        login="QA Web",
        role="developer",
    )
    session.add(bad_client)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # EdgeCommand: проверка is_global vs installation_id
    inst = Installation(
        name="Inst For Cmd",
        culture_name="Салат",
        light_gpio_pin=8,
        flash_gpio_pin=9,
    )
    session.add(inst)
    session.commit()

    # Локальная команда без installation_id -> нарушение CHECK
    bad_cmd = EdgeCommand(
        command_type="SNAP",
        is_global=False,
        installation_id=None,
    )
    session.add(bad_cmd)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # Корректная локальная команда
    good_cmd = EdgeCommand(
        command_type="SNAP",
        is_global=False,
        installation_id=inst.id,
        status="pending",
    )
    session.add(good_cmd)
    session.commit()
    assert good_cmd.id is not None

    # SystemSetting: проверка создания и обновления
    setting = SystemSetting(key="capture_interval_hours", value="2")
    session.add(setting)
    session.commit()
    assert session.get(SystemSetting, "capture_interval_hours").value == "2"

    session.close()
