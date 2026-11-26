from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import sessionmaker

from server.core.climate import (
    confirm_esp_command,
    enqueue_manual_command,
    esp_queue,
    get_climate_history,
    get_climate_summary,
    is_esp_online,
    record_ph_reading,
    record_sensor_reading,
    run_climate_scheduler_step,
    set_climate_mode,
    touch_esp_seen,
    update_climate_schedule,
)
from server.db.database import create_db_engine, init_db
from server.db.models import PeripheralLog


@pytest.fixture()
def db_session(tmp_path):
    esp_queue.clear()
    engine = create_db_engine(f"sqlite:///{tmp_path / 'climate_test.db'}")
    init_db(engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()
        esp_queue.clear()


def test_esp_queue_blocks_light_and_formats_protocol(db_session):
    assert esp_queue.peek_payload() == {
        "queue_size": -1,
        "id": 0,
        "device": "None",
        "action": "off",
    }

    with pytest.raises(ValueError, match="water, fan"):
        esp_queue.enqueue("light", "on")

    cmd1 = esp_queue.enqueue("water", "on", source="manual")
    cmd2 = esp_queue.enqueue("fan", "on", source="manual")

    assert esp_queue.peek_payload() == {
        "queue_size": 1,
        "id": cmd1.id,
        "device": "water",
        "action": "on",
    }

    # Неверный id не должен удалять голову очереди
    assert confirm_esp_command(db_session, cmd2.id) is None
    assert esp_queue.size() == 2

    # Верный id подтверждает команду и пишет лог
    acked = confirm_esp_command(db_session, cmd1.id)
    assert acked is not None and acked.id == cmd1.id
    assert esp_queue.peek_payload() == {
        "queue_size": 0,
        "id": cmd2.id,
        "device": "fan",
        "action": "on",
    }

    logs = db_session.query(PeripheralLog).all()
    assert len(logs) == 1
    assert logs[0].device == "water"
    assert logs[0].state == "on"


def test_manual_blocked_in_auto_mode(db_session):
    set_climate_mode(db_session, "auto")
    db_session.commit()

    with pytest.raises(PermissionError):
        enqueue_manual_command(db_session, "water", "on")

    set_climate_mode(db_session, "manual")
    db_session.commit()
    cmd = enqueue_manual_command(db_session, "water", "on")
    assert cmd.device == "water"


def test_scheduler_auto_cycle_and_restart_persistence(db_session):
    t0 = datetime(2026, 11, 26, 10, 0, 0, tzinfo=UTC)
    set_climate_mode(db_session, "auto")
    update_climate_schedule(
        db_session,
        water_interval_hours=4,
        water_duration_minutes=2,
        fan_interval_hours=2,
        fan_duration_minutes=1,
    )
    db_session.commit()

    # Первый тик: ставит water=on и fan=on, запоминает last_*_start в SQLite
    cmds = run_climate_scheduler_step(db_session, now=t0)
    assert {(c.device, c.action) for c in cmds} == {("water", "on"), ("fan", "on")}

    # Анти-спам: через 10 с ничего повторно не ставится
    assert run_climate_scheduler_step(db_session, now=t0 + timedelta(seconds=10)) == []

    # ESP подтверждает обе команды на t0 + 5s
    for c in cmds:
        assert confirm_esp_command(db_session, c.id, now=t0 + timedelta(seconds=5))

    # Через 65 с вентиляция (1 мин) должна выключиться, а полив (2 мин) ещё работает
    off_fan = run_climate_scheduler_step(db_session, now=t0 + timedelta(seconds=65))
    assert [(c.device, c.action) for c in off_fan] == [("fan", "off")]
    confirm_esp_command(db_session, off_fan[0].id, now=t0 + timedelta(seconds=66))

    # Через 130 с полив (2 мин) выключается
    off_water = run_climate_scheduler_step(db_session, now=t0 + timedelta(seconds=130))
    assert [(c.device, c.action) for c in off_water] == [("water", "off")]
    confirm_esp_command(db_session, off_water[0].id, now=t0 + timedelta(seconds=131))

    # Имитация перезапуска сервера (очередь в RAM очищена): через 1 час ложного запуска нет
    esp_queue.clear()
    assert run_climate_scheduler_step(db_session, now=t0 + timedelta(hours=1)) == []

    # Через 2 часа 1 минуту запускается только вентиляция
    fan_again = run_climate_scheduler_step(
        db_session, now=t0 + timedelta(hours=2, minutes=1)
    )
    assert [(c.device, c.action) for c in fan_again] == [("fan", "on")]


def test_sensors_ph_online_and_history(db_session):
    t0 = datetime(2026, 11, 26, 12, 0, 0, tzinfo=UTC)
    assert is_esp_online(db_session, now=t0) is False

    record_sensor_reading(db_session, 24.5, 58.2, now=t0)
    assert is_esp_online(db_session, now=t0 + timedelta(seconds=30)) is True
    assert is_esp_online(db_session, now=t0 + timedelta(seconds=65)) is False

    touch_esp_seen(db_session, now=t0 + timedelta(seconds=70))
    db_session.commit()
    assert is_esp_online(db_session, now=t0 + timedelta(seconds=90)) is True

    with pytest.raises(ValueError, match="pH"):
        record_ph_reading(db_session, 14.5, recorded_at=t0)

    record_ph_reading(db_session, 6.2, recorded_at=t0)

    summary = get_climate_summary(db_session, now=t0 + timedelta(seconds=90))
    assert summary["esp_online"] is True
    assert summary["temperature"] == 24.5
    assert summary["humidity"] == 58.2
    assert summary["ph"] == 6.2

    history = get_climate_history(
        db_session, period="24h", now=t0 + timedelta(minutes=5)
    )
    assert len(history["temperature"]) == 1
    assert len(history["ph"]) == 1
