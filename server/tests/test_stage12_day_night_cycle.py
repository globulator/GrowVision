"""Тесты этапа 12.1: цикл дня и ночи, 5-уровневая иерархия, планировщик, API и Web UI."""

import json
from collections.abc import Generator
from datetime import UTC, datetime, time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from server.api.deps import get_db
from server.config import get_settings
from server.core.light_state import (
    get_installation_channel_color,
    get_installation_light_snapshot,
    set_global_day_night_settings,
    set_global_light_mode,
    set_individual_light_state,
    set_installation_day_night_settings,
    sync_day_night_phases_step,
)
from server.core.state import app_state
from server.db.database import Base
from server.db.models import EdgeCommand, Installation, SystemSetting, User
from server.main import app
from shared.light_cycle import (
    get_day_night_phase,
    is_day_phase,
    normalize_hhmm,
    validate_day_night_window,
)

CLIENT_KEY = "test-client-key-stage12-dn"
EDGE_KEY = "test-edge-key-stage12-dn"


@pytest.fixture
def test_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "test_stage12_dn.db"
    engine = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    settings = get_settings()
    monkeypatch.setattr(settings, "client_api_key", CLIENT_KEY)
    monkeypatch.setattr(settings, "edge_api_key", EDGE_KEY)

    def override_get_db() -> Generator[Session, None, None]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app_state.clear()

    yield session_factory, engine

    app_state.clear()
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture
def client(test_env) -> TestClient:
    return TestClient(app)


def _create_user(session_factory, role: str, external_id: str) -> dict[str, str]:
    db = session_factory()
    try:
        user = User(
            client_type="telegram",
            external_id=external_id,
            login=f"user_{role}_{external_id}",
            role=role,
            is_active=True,
        )
        db.add(user)
        db.commit()
    finally:
        db.close()
    return {
        "X-Client-Key": CLIENT_KEY,
        "X-Client-Type": "telegram",
        "X-External-Id": external_id,
    }


def _mark_rpi_online(db: Session) -> None:
    now = datetime.now(UTC)
    row = (
        db.query(SystemSetting)
        .filter(SystemSetting.key == "rpi_last_telemetry")
        .first()
    )
    if row:
        row.value = "{}"
        row.updated_at = now
    else:
        db.add(SystemSetting(key="rpi_last_telemetry", value="{}", updated_at=now))
    db.commit()


def _patch_now_time(monkeypatch: pytest.MonkeyPatch, target_time: time) -> None:
    class _FixedDatetime:
        @staticmethod
        def now():
            return datetime.combine(datetime(2026, 11, 25).date(), target_time)

    monkeypatch.setattr("shared.light_cycle.datetime", _FixedDatetime)


def test_light_cycle_validation_and_phase_calculation():
    """Проверка валидации HH:MM, запрета start == end и расчёта обычного/полуночного интервала."""
    assert normalize_hhmm("8:05") == "08:05"
    assert normalize_hhmm(" 23:59 ") == "23:59"
    assert normalize_hhmm("00:00") == "00:00"

    for invalid in ("24:00", "08:60", "08:5", "abc", "", "12-00", True, 800):
        with pytest.raises((ValueError, TypeError)):
            normalize_hhmm(invalid)

    with pytest.raises(ValueError, match="совпадать"):
        validate_day_night_window("08:00", "8:00")

    with pytest.raises(ValueError, match="совпадать"):
        is_day_phase("10:00", "10:00", time(12, 0))

    # Обычный интервал 08:00–22:00: True при start <= now < end
    assert is_day_phase("08:00", "22:00", time(7, 59)) is False
    assert is_day_phase("08:00", "22:00", time(8, 0)) is True
    assert is_day_phase("08:00", "22:00", time(14, 30)) is True
    assert is_day_phase("08:00", "22:00", time(21, 59)) is True
    assert is_day_phase("08:00", "22:00", time(22, 0)) is False
    assert get_day_night_phase("08:00", "22:00", time(22, 0)) == "night"

    # Переход через полночь 22:00–06:00: True при now >= start or now < end
    assert is_day_phase("22:00", "06:00", time(21, 59)) is False
    assert is_day_phase("22:00", "06:00", time(22, 0)) is True
    assert is_day_phase("22:00", "06:00", time(23, 59)) is True
    assert is_day_phase("22:00", "06:00", time(0, 0)) is True
    assert is_day_phase("22:00", "06:00", time(5, 59)) is True
    assert is_day_phase("22:00", "06:00", time(6, 0)) is False
    assert get_day_night_phase("22:00", "06:00", time(2, 0)) == "day"


def test_5_level_light_hierarchy_and_color_preservation(
    client: TestClient, test_env, monkeypatch: pytest.MonkeyPatch
):
    """5-уровневая иерархия основного света не трогает вспышку, сохраняет цвета и нижние настройки."""
    session_factory, _ = test_env
    hdrs = _create_user(session_factory, "developer", "dev-dn-7001")

    r1 = client.post(
        "/api/installations",
        headers=hdrs,
        json={
            "name": "RGB Секция",
            "culture_name": "Салат",
            "light_gpio_pin": "17,27,22",
            "flash_gpio_pin": 23,
            "light_color": "#FF6600",
        },
    )
    assert r1.status_code == 201
    inst_id = r1.json()["id"]

    db = session_factory()
    try:
        inst = db.query(Installation).filter(Installation.id == inst_id).first()
        assert inst is not None

        # Уровень 5: индивидуальный тумблер False, вспышка True
        set_individual_light_state(db, inst_id, False, is_flash=False)
        set_individual_light_state(db, inst_id, True, is_flash=True)
        db.commit()

        snap5 = get_installation_light_snapshot(db, inst, now_time=time(12, 0))
        assert snap5["light_state"] is False
        assert snap5["flash_state"] is True
        assert snap5["light_locked"] is False
        assert snap5["light_lock_reason"] is None
        assert snap5["day_night_locked"] is False

        # Уровень 4: индивидуальный цикл (08:00-22:00) -> днём свет ON, хотя тумблер False!
        set_installation_day_night_settings(
            db, inst_id, enabled=True, day_start="08:00", day_end="22:00"
        )
        db.commit()
        snap4_day = get_installation_light_snapshot(db, inst, now_time=time(12, 0))
        assert snap4_day["light_state"] is True
        assert snap4_day["flash_state"] is True
        assert snap4_day["light_locked"] is True
        assert snap4_day["light_lock_reason"] == "Световой день"
        assert snap4_day["day_night_locked"] is False
        assert snap4_day["individual_light_state"] is False

        snap4_night = get_installation_light_snapshot(db, inst, now_time=time(23, 0))
        assert snap4_night["light_state"] is False
        assert snap4_night["flash_state"] is True
        assert snap4_night["day_night_phase"] == "night"

        # Уровень 3: общий ручной свет "off" перекрывает уровень 4 (даже днём)
        set_global_light_mode(db, "off", is_flash=False)
        db.commit()
        snap3_off = get_installation_light_snapshot(db, inst, now_time=time(12, 0))
        assert snap3_off["light_state"] is False
        assert snap3_off["flash_state"] is True
        assert snap3_off["light_locked"] is True
        assert snap3_off["light_lock_reason"] == "Заблокировано"
        assert snap3_off["day_night_locked"] is True

        # Уровень 2: общий цикл (22:00-06:00) перекрывает уровень 3 ("off"), уровень 4 и 5
        set_global_day_night_settings(
            db, enabled=True, day_start="22:00", day_end="06:00"
        )
        db.commit()
        snap2_night_window_day = get_installation_light_snapshot(
            db, inst, now_time=time(23, 30)
        )
        assert snap2_night_window_day["global_light_locked"] is True
        assert snap2_night_window_day["global_day_night_phase"] == "day"
        assert snap2_night_window_day["light_state"] is True
        assert snap2_night_window_day["flash_state"] is True
        assert snap2_night_window_day["light_lock_reason"] == "Заблокировано"
        assert snap2_night_window_day["day_night_locked"] is True

        # Возвращаем верхние уровни: цвет #FF6600 и индивидуальные настройки не затёрты
        set_global_day_night_settings(db, enabled=False)
        set_global_light_mode(db, "default", is_flash=False)
        set_installation_day_night_settings(db, inst_id, enabled=False)
        db.commit()
        snap_restored = get_installation_light_snapshot(db, inst, now_time=time(12, 0))
        assert snap_restored["light_state"] is False
        assert snap_restored["individual_light_state"] is False
        assert get_installation_channel_color(db, inst_id, is_flash=False) == "#FF6600"
    finally:
        db.close()


def test_day_night_api_offline_online_validation_and_edge_sync(
    client: TestClient, test_env, monkeypatch: pytest.MonkeyPatch
):
    """POST /api/control/day-night, PATCH /installations, /relays, /edge/heartbeat и /claim."""
    session_factory, _ = test_env
    dev_hdrs = _create_user(session_factory, "developer", "dev-dn-7002")
    bio_hdrs = _create_user(session_factory, "biologist", "bio-dn-7003")
    edge_hdrs = {"X-Edge-Key": EDGE_KEY}

    _patch_now_time(monkeypatch, time(23, 0))

    r_create = client.post(
        "/api/installations",
        headers=dev_hdrs,
        json={
            "name": "Бокс 1",
            "culture_name": "Базилик",
            "light_gpio_pin": "17,27,22",
            "flash_gpio_pin": 24,
            "light_color": "#00AAFF",
        },
    )
    assert r_create.status_code == 201
    inst_id = r_create.json()["id"]

    # Валидация start == end (в т.ч. при частичном обновлении одного из концов) -> 422
    r_same = client.post(
        "/api/control/day-night",
        headers=dev_hdrs,
        json={"target": "all", "day_start": "08:00", "day_end": "08:00"},
    )
    assert r_same.status_code == 422

    r_partial_same = client.post(
        "/api/control/day-night",
        headers=dev_hdrs,
        json={"target": inst_id, "day_start": "22:00"},
    )
    assert r_partial_same.status_code == 422

    r_bad_time = client.post(
        "/api/control/day-night",
        headers=dev_hdrs,
        json={"target": "all", "day_start": "25:00"},
    )
    assert r_bad_time.status_code == 422

    # При офлайн RPi настройки сохраняются в БД (не падает с 503!) и app_state обновляется
    r_dn_offline = client.post(
        "/api/control/day-night",
        headers=dev_hdrs,
        json={
            "target": inst_id,
            "enabled": True,
            "day_start": "08:00",
            "day_end": "22:00",
        },
    )
    assert r_dn_offline.status_code == 200
    data_off = r_dn_offline.json()
    assert data_off["delivery_status"] == "offline"
    assert data_off["command_id"] is None
    assert data_off["day_night_enabled"] is True
    assert data_off["day_night_phase"] == "night"
    assert data_off["light_state"] is False
    assert data_off["light_locked"] is True
    assert data_off["light_lock_reason"] == "Световой день"
    assert app_state.get_pin_state(17) is False

    # Heartbeat при выходе агента в онлайн получает light_state=False и сохраняет light_color
    hb_body = {
        "software": {
            "edge_version": "1.0.0",
            "python_version": "3.12",
            "model_name": "mock",
        },
        "hardware": {
            "board_model": "RPi4",
            "os_info": "Linux",
            "uptime_sec": 100,
            "cpu_temp_c": 40.0,
            "cpu_usage_percent": 10.0,
            "ram_used_mb": 200,
            "ram_total_mb": 4096,
            "disk_free_gb": 20.0,
            "gpio_mode": "mock",
        },
        "offline_queue_count": 0,
        "detected_cameras": [],
    }
    r_hb = client.post("/api/edge/heartbeat", headers=edge_hdrs, json=hb_body)
    assert r_hb.status_code == 200
    hb_inst = r_hb.json()["active_installations"][0]
    assert hb_inst["light_state"] is False
    assert hb_inst["light_color"] == "#00AAFF"

    # Теперь RPi онлайн: включаем глобальный цикл с переходом через полночь 22:00–06:00 (23:00 -> day!)
    r_glob_on = client.post(
        "/api/control/day-night",
        headers=dev_hdrs,
        json={
            "target": "all",
            "enabled": True,
            "day_start": "22:00",
            "day_end": "06:00",
        },
    )
    assert r_glob_on.status_code == 200
    g_data = r_glob_on.json()
    assert g_data["delivery_status"] == "queued"
    assert g_data["command_id"] is not None
    assert g_data["global_day_night_enabled"] is True
    assert g_data["global_day_night_phase"] == "day"
    assert g_data["global_light_locked"] is True
    assert app_state.get_pin_state(17) is True

    # GET /api/control/relays, /relays/{id}, /installations/{id}
    r_relays = client.get("/api/control/relays", headers=dev_hdrs)
    assert r_relays.status_code == 200
    rel = r_relays.json()
    assert rel["global_day_night_enabled"] is True
    assert rel["global_light_locked"] is True
    assert rel["installations"][0]["light_lock_reason"] == "Заблокировано"
    assert rel["installations"][0]["day_night_locked"] is True

    r_rel_one = client.get(f"/api/control/relays/{inst_id}", headers=dev_hdrs)
    assert r_rel_one.status_code == 200
    assert r_rel_one.json()["day_night_locked"] is True
    assert r_rel_one.json()["light_lock_reason"] == "Заблокировано"

    r_inst = client.get(f"/api/installations/{inst_id}", headers=dev_hdrs)
    assert r_inst.status_code == 200
    assert r_inst.json()["global_day_night_phase"] == "day"
    assert r_inst.json()["light_lock_reason"] == "Заблокировано"

    # claim возвращает команду с light_state=True
    r_claim = client.post("/api/edge/commands/claim", headers=edge_hdrs)
    assert r_claim.status_code == 200
    claimed = r_claim.json()
    assert len(claimed) == 1
    assert claimed[0]["command_type"] == "LIGHT_MAIN"
    assert claimed[0]["installations"][0]["light_state"] is True

    # PATCH /api/installations/{id} с day_night полями: biologist -> 403, developer -> 200 и 422 при start==end
    r_bio = client.patch(
        f"/api/installations/{inst_id}",
        headers=bio_hdrs,
        json={"day_night_enabled": False},
    )
    assert r_bio.status_code == 403

    r_patch_bad = client.patch(
        f"/api/installations/{inst_id}",
        headers=dev_hdrs,
        json={"day_start": "22:00"},
    )
    assert r_patch_bad.status_code == 422

    r_patch_ok = client.patch(
        f"/api/installations/{inst_id}",
        headers=dev_hdrs,
        json={"day_start": "09:00", "day_end": "21:00"},
    )
    assert r_patch_ok.status_code == 200
    assert r_patch_ok.json()["day_start"] == "09:00"
    assert r_patch_ok.json()["day_end"] == "21:00"


def test_sync_day_night_phases_step_no_spam_and_creates_on_transition(test_env):
    """Фоновый шаг sync_day_night_phases_step не спамит команды и реагирует только на смену фазы."""
    session_factory, _ = test_env
    db = session_factory()
    try:
        inst = Installation(
            name="Планировщик",
            culture_name="Салат",
            light_gpio_pin=17,
            flash_gpio_pin=27,
            is_archived=False,
        )
        db.add(inst)
        db.commit()
        db.refresh(inst)

        set_installation_day_night_settings(
            db, inst.id, enabled=True, day_start="08:00", day_end="22:00"
        )
        _mark_rpi_online(db)
        app_state.set_pin_state(17, True)

        # 1. Днём (12:00): состояние уже True -> изменений нет, команда не создаётся
        res1 = sync_day_night_phases_step(db, now_time=time(12, 0))
        assert res1["changed"] is False
        assert res1["command_id"] is None
        assert db.query(EdgeCommand).count() == 0

        # 2. Переход в ночь (22:05): True -> False -> обновляет app_state и создаёт LIGHT_MAIN
        res2 = sync_day_night_phases_step(db, now_time=time(22, 5))
        assert res2["changed"] is True
        assert res2["command_id"] is not None
        assert res2["changed_pins"] == {17: False}
        assert app_state.get_pin_state(17) is False
        assert db.query(EdgeCommand).count() == 1

        # 3. Повторный тик ночью (22:10): изменений нет -> команда не спамится
        res3 = sync_day_night_phases_step(db, now_time=time(22, 10))
        assert res3["changed"] is False
        assert db.query(EdgeCommand).count() == 1

        # 4. Переход обратно в день (08:00): False -> True -> создаётся вторая LIGHT_MAIN
        res4 = sync_day_night_phases_step(db, now_time=time(8, 0))
        assert res4["changed"] is True
        assert res4["changed_pins"] == {17: True}
        assert app_state.get_pin_state(17) is True
        assert db.query(EdgeCommand).count() == 2

        cmd_last = db.query(EdgeCommand).order_by(EdgeCommand.id.desc()).first()
        assert cmd_last is not None
        payload = json.loads(cmd_last.payload or "{}")
        assert payload["pin_states"] == {"17": True}
    finally:
        db.close()


def test_web_ui_contains_day_night_controls_and_logic():
    """Проверка наличия необходимых элементов и контрактов светового дня в Web UI."""
    root = Path(__file__).resolve().parent.parent.parent
    html_text = (root / "clients" / "web" / "index.html").read_text(encoding="utf-8")
    api_js = (root / "clients" / "web" / "js" / "api.js").read_text(encoding="utf-8")
    light_js = (root / "clients" / "web" / "js" / "light_control.js").read_text(
        encoding="utf-8"
    )
    editor_js = (root / "clients" / "web" / "js" / "unit_editor.js").read_text(
        encoding="utf-8"
    )
    css_text = (root / "clients" / "web" / "style.css").read_text(encoding="utf-8")

    # Панель «Управление светом и реле»
    assert "Световой день (все установки)" in html_text
    assert 'id="light-day-night-toggle"' in html_text
    assert 'id="light-day-night-start"' in html_text
    assert 'id="light-day-night-end"' in html_text
    assert 'id="light-day-night-phase"' in html_text
    assert "Цикл дня/ночи" in html_text
    assert "/api/control/day-night" in api_js
    assert "api.controlDayNight" in light_js

    # Карточка редактирования установки
    assert 'id="unit-editor-day-night-card"' in html_text
    assert "Настроить световой день" in html_text
    assert 'id="unit-editor-day-night-toggle"' in html_text
    assert 'id="unit-editor-day-night-start"' in html_text
    assert 'id="unit-editor-day-night-end"' in html_text
    assert "Световой день" in editor_js
    assert "Заблокировано" in editor_js
    assert ".unit-day-night-card" in css_text
    assert ".unit-day-night-panel" in css_text
