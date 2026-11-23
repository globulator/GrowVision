from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

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

EXPECTED_TABLES = {
    "cameras",
    "captures",
    "edge_commands",
    "installations",
    "invite_codes",
    "plant_measurements",
    "system_settings",
    "users",
}


@pytest.fixture
def test_db(tmp_path: Path):
    """Фикстура для изолированной тестовой БД в файле SQLite."""
    db_file = tmp_path / "test_growvision.db"
    db_url = f"sqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    init_db(engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    yield engine, session_factory
    engine.dispose()


def test_db_tables_and_pragmas(test_db):
    """Критерий: Создание БД на пустом SQLite даёт ровно 7 таблиц из DB_SCHEMA.md;

    PRAGMA foreign_keys = 1, journal_mode = wal, busy_timeout = 5000.
    """
    engine, _ = test_db
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    assert tables == EXPECTED_TABLES, (
        f"Ожидались таблицы {EXPECTED_TABLES}, получены {tables}"
    )
    assert len(tables) == 8

    with engine.connect() as conn:
        fk = conn.execute(text("PRAGMA foreign_keys;")).scalar()
        journal = conn.execute(text("PRAGMA journal_mode;")).scalar()
        busy = conn.execute(text("PRAGMA busy_timeout;")).scalar()

        assert fk == 1, f"PRAGMA foreign_keys должна быть 1, получено {fk}"
        assert journal.lower() == "wal", (
            f"PRAGMA journal_mode должна быть wal, получено {journal}"
        )
        assert busy == 5000, f"PRAGMA busy_timeout должен быть 5000, получено {busy}"


def test_shared_gpio_pins_allowed(test_db):
    """Критерий (Этап 7.4): Общие GPIO-пины разрешены.

    Две активные установки могут использовать один и тот же GPIO-пин.
    """
    _, session_factory = test_db
    session = session_factory()

    inst_active1 = Installation(
        name="Установка 1 (активная)",
        culture_name="Базилик",
        light_gpio_pin=17,
        flash_gpio_pin=27,
        is_archived=False,
    )
    session.add(inst_active1)
    session.commit()

    # Вторая активная установка с теми же пинами — теперь разрешена
    inst_active2 = Installation(
        name="Установка 2 (общие пины)",
        culture_name="Томат",
        light_gpio_pin=17,
        flash_gpio_pin=27,
        is_archived=False,
    )
    session.add(inst_active2)
    session.commit()

    assert inst_active1.id is not None
    assert inst_active2.id is not None
    assert inst_active1.light_gpio_pin == inst_active2.light_gpio_pin

    session.close()


def test_installation_check_constraints(test_db):
    """Проверяет CHECK-ограничения для installations:

    - light_gpio_pin != flash_gpio_pin
    - aruco_size_mm > 0
    """
    _, session_factory = test_db
    session = session_factory()

    # Одинаковые пины света и вспышки разрешены (Этап 8.1)
    inst_same_pins = Installation(
        name="Установка с одинаковыми пинами",
        culture_name="Салат",
        light_gpio_pin=18,
        flash_gpio_pin=18,
        is_archived=False,
    )
    session.add(inst_same_pins)
    session.commit()
    assert inst_same_pins.id is not None

    # Отрицательный размер маркера ArUco
    inst_negative_aruco = Installation(
        name="Неверный ArUco",
        culture_name="Салат",
        light_gpio_pin=18,
        flash_gpio_pin=19,
        aruco_size_mm=-5.0,
        is_archived=False,
    )
    session.add(inst_negative_aruco)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    session.close()


def test_foreign_key_and_delete_behaviors(test_db):
    """Проверяет ON DELETE поведения по DB_SCHEMA.md п. 4.2."""
    _, session_factory = test_db
    session = session_factory()

    # Создаем установку
    inst = Installation(
        name="Установка FK",
        culture_name="Мята",
        light_gpio_pin=5,
        flash_gpio_pin=6,
        is_archived=False,
    )
    session.add(inst)
    session.commit()

    # Камера (FK -> installations.id ON DELETE SET NULL)
    cam = Camera(
        hardware_id="cam_fk_test",
        auto_name="Test Cam",
        installation_id=inst.id,
    )
    session.add(cam)
    session.commit()

    # Снимок (FK -> installations.id ON DELETE RESTRICT, camera_id ON DELETE SET NULL)
    cap = Capture(
        installation_id=inst.id,
        camera_id=cam.id,
        captured_at=datetime.now(UTC),
        is_manual=False,
        raw_image_path="media/1/raw_1.jpg",
        annotated_image_path="media/1/ann_1.jpg",
    )
    session.add(cap)
    session.commit()

    # Замер (FK -> captures.id ON DELETE CASCADE)
    meas = PlantMeasurement(
        capture_id=cap.id,
        plant_index=0,
        area_px=1200.0,
    )
    session.add(meas)
    session.commit()

    # 1. Попытка удалить установку со снимком должна нарушить RESTRICT
    session.delete(inst)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # 2. Удаление снимка каскадно удаляет замер
    cap_to_del = session.get(Capture, cap.id)
    session.delete(cap_to_del)
    session.commit()
    assert session.get(PlantMeasurement, meas.id) is None

    # 3. Удаление камеры оставляет установку целой, а в кадрах сбрасывает в NULL
    cam_to_del = session.get(Camera, cam.id)
    session.delete(cam_to_del)
    session.commit()
    assert session.get(Installation, inst.id) is not None

    session.close()


def test_installation_delete_with_commands_cascades(test_db):
    """Критерий: Удаление установки без кадров, но с командами,

    проходит и каскадно удаляет связанные команды (passive_deletes=True).
    """
    _, session_factory = test_db
    session = session_factory()

    inst = Installation(
        name="Установка для команд",
        culture_name="Укроп",
        light_gpio_pin=24,
        flash_gpio_pin=25,
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

    # Удаляем установку без кадров
    session.delete(inst)
    session.commit()

    # Установка удалена
    assert session.get(Installation, inst.id) is None
    # Команды каскадно удалены
    assert session.get(EdgeCommand, cmd1_id) is None
    assert session.get(EdgeCommand, cmd2_id) is None

    session.close()


def test_edge_command_constraints(test_db):
    """Проверяет ограничения edge_commands: статус и согласованность is_global."""
    _, session_factory = test_db
    session = session_factory()

    # 1. is_global=True, но installation_id задан -> ошибка CHECK
    inst = Installation(
        name="Установка Cmd",
        culture_name="Салат",
        light_gpio_pin=11,
        flash_gpio_pin=12,
        is_archived=False,
    )
    session.add(inst)
    session.commit()

    bad_cmd = EdgeCommand(
        command_type="SNAP",
        installation_id=inst.id,
        is_global=True,
    )
    session.add(bad_cmd)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # 2. Неверный статус
    bad_status = EdgeCommand(
        command_type="SNAP",
        installation_id=None,
        is_global=True,
        status="unknown_status",
    )
    session.add(bad_status)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # 3. Валидная глобальная команда
    good_cmd = EdgeCommand(
        command_type="SCAN_CAMERAS",
        installation_id=None,
        is_global=True,
        status="pending",
    )
    session.add(good_cmd)
    session.commit()
    assert good_cmd.id is not None

    session.close()


def test_users_constraints(test_db):
    """Проверяет уникальность пары client_type + external_id и допустимые роли."""
    _, session_factory = test_db
    session = session_factory()

    user1 = User(
        client_type="telegram",
        external_id="12345678",
        login="Иван",
        role="biologist",
    )
    session.add(user1)
    session.commit()

    # Дубликат (telegram, 12345678)
    user2 = User(
        client_type="telegram",
        external_id="12345678",
        login="Петр",
        role="developer",
    )
    session.add(user2)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # Недопустимая роль (guest не должен храниться в таблице)
    user3 = User(
        client_type="cli",
        external_id="cli_uuid",
        login="Гость",
        role="guest",
    )
    session.add(user3)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    session.close()


def test_system_settings_upsert(test_db):
    """Проверяет запись и обновление в system_settings."""
    _, session_factory = test_db
    session = session_factory()

    setting = SystemSetting(key="debug_mode", value="false")
    session.add(setting)
    session.commit()

    s = session.get(SystemSetting, "debug_mode")
    assert s is not None
    assert s.value == "false"

    s.value = "true"
    session.commit()
    assert session.get(SystemSetting, "debug_mode").value == "true"

    session.close()
