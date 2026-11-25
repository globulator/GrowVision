"""Тесты этапа 12: 1/3 уникальных GPIO-пинов, память HEX-цвета, строгая валидация 422 и Web UI."""

import json
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from server.api.deps import get_db
from server.config import get_settings
from server.core.state import app_state
from server.db.database import Base
from server.db.models import EdgeCommand, Installation, User
from server.main import app
from shared.pins import (
    DEFAULT_COLOR_HEX,
    format_gpio_pins,
    hex_to_rgb01,
    hex_to_rgb255,
    hsv_to_hex,
    normalize_hex_color,
    parse_gpio_pins,
    rainbow_hex_steps,
    rgb01_to_hex,
    validate_light_flash_disjoint,
)

CLIENT_KEY = "test-client-key-stage12"
EDGE_KEY = "test-edge-key-stage12"


@pytest.fixture
def test_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "test_stage12.db"
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

    app.dependency_overrides.clear()
    app_state.clear()
    engine.dispose()


@pytest.fixture
def client(test_env) -> TestClient:
    return TestClient(app)


def _create_dev(session_factory, external_id: str = "dev-12") -> dict[str, str]:
    db = session_factory()
    db.add(
        User(
            client_type="telegram",
            external_id=external_id,
            login="Dev12",
            role="developer",
            is_active=True,
        )
    )
    db.commit()
    db.close()
    return {
        "X-Client-Key": CLIENT_KEY,
        "X-Client-Type": "telegram",
        "X-External-Id": external_id,
    }


def test_shared_pins_and_hex_utilities():
    """Парсинг 1/3 пинов, отклонение ошибок, нормализация HEX и конвертация в RGB/радугу."""
    assert parse_gpio_pins(0) == [0]
    assert parse_gpio_pins(53) == [53]
    assert parse_gpio_pins("17") == [17]
    assert parse_gpio_pins(" 17 , 27 , 22 ") == [17, 27, 22]
    assert format_gpio_pins([17]) == 17
    assert format_gpio_pins([17, 27, 22]) == "17,27,22"

    for bad in (
        True,
        False,
        -1,
        54,
        "",
        "17,",
        "17,27",
        "17,27,22,5",
        "17,abc,22",
        "17,54,22",
        "17,17,22",
        [1, 2],
        [1, 1, 2],
    ):
        with pytest.raises((ValueError, TypeError)):
            parse_gpio_pins(bad)

    validate_light_flash_disjoint([17, 27, 22], [5, 6, 13])
    with pytest.raises(ValueError, match="пересекаться"):
        validate_light_flash_disjoint([17, 27, 22], [22, 23, 24])

    assert normalize_hex_color("ff8800") == "#FF8800"
    assert normalize_hex_color("#00aaff") == "#00AAFF"
    assert normalize_hex_color(None, default=DEFAULT_COLOR_HEX) == "#FFFFFF"
    for bad_hex in ("", "#FFF", "#12345", "#GGGGGG", "1234567"):
        with pytest.raises(ValueError):
            normalize_hex_color(bad_hex)

    assert hex_to_rgb255("#FF8000") == (255, 128, 0)
    r, g, b = hex_to_rgb01("#FF8000")
    assert r == 1.0 and 0.5 <= g <= 0.51 and b == 0.0
    assert rgb01_to_hex(1.0, 0.0, 0.5) == "#FF0080"
    assert hsv_to_hex(0.0, 1.0, 1.0) == "#FF0000"

    spectrum = rainbow_hex_steps(12)
    assert len(spectrum) == 12
    assert spectrum[0] == "#FF0000"
    assert spectrum[-1] == "#FF00FF"
    assert len(set(spectrum)) == 12


def test_installations_rgb_validation_and_serialization(client: TestClient, test_env):
    """CRUD с 1 и 3 пинами, запрет дублей/пересечений (422), частичный PATCH и обратная совместимость."""
    session_factory, _ = test_env
    hdrs = _create_dev(session_factory)

    # 1. Создание установки с RGB-светом и RGB-вспышкой
    r_ok = client.post(
        "/api/installations",
        headers=hdrs,
        json={
            "name": "RGB Бокс",
            "culture_name": "Базилик",
            "light_gpio_pin": "17, 27, 22",
            "flash_gpio_pin": "5,6,13",
            "light_color": "ff8800",
        },
    )
    assert r_ok.status_code == 201
    data = r_ok.json()
    assert data["light_gpio_pin"] == "17,27,22"
    assert data["flash_gpio_pin"] == "5,6,13"
    assert data["light_pins"] == [17, 27, 22]
    assert data["flash_pins"] == [5, 6, 13]
    assert data["light_is_rgb"] is True
    assert data["flash_is_rgb"] is True
    assert data["light_color"] == "#FF8800"
    assert data["flash_color"] == "#FFFFFF"

    inst_id = data["id"]

    # 2. В SQLite-таблице сохранены int первых пинов (обратная совместимость БД)
    db = session_factory()
    inst_db = db.get(Installation, inst_id)
    assert inst_db.light_gpio_pin == 17
    assert inst_db.flash_gpio_pin == 5
    db.close()

    # 3. Ошибки 422 при создании: 2 пина, дубли внутри RGB, пересечение света и вспышки
    for bad_light, bad_flash in [
        ("17,27", 19),
        ("17,17,22", 19),
        ("17,27,22", 22),
        ("17,27,22", "10,27,11"),
        ("17,27,99", 19),
    ]:
        r_bad = client.post(
            "/api/installations",
            headers=hdrs,
            json={
                "name": f"Bad-{bad_light}-{bad_flash}",
                "culture_name": "Салат",
                "light_gpio_pin": bad_light,
                "flash_gpio_pin": bad_flash,
            },
        )
        assert r_bad.status_code == 422

    # 4. Частичный PATCH одного поля пинов сверяется со вторым каналом -> 422 при пересечении
    r_patch_overlap = client.patch(
        f"/api/installations/{inst_id}",
        headers=hdrs,
        json={"flash_gpio_pin": "12,22,19"},
    )
    assert r_patch_overlap.status_code == 422
    assert "пересекаться" in r_patch_overlap.json()["detail"]

    # Валидный частичный PATCH на 1-пиновую вспышку
    r_patch_ok = client.patch(
        f"/api/installations/{inst_id}",
        headers=hdrs,
        json={"flash_gpio_pin": 26},
    )
    assert r_patch_ok.status_code == 200
    assert r_patch_ok.json()["flash_gpio_pin"] == 26
    assert r_patch_ok.json()["flash_pins"] == [26]
    assert r_patch_ok.json()["flash_is_rgb"] is False
    assert r_patch_ok.json()["light_is_rgb"] is True


def test_hex_memory_persistence_and_control_endpoints(
    client: TestClient, test_env, monkeypatch: pytest.MonkeyPatch
):
    """Память HEX при off->on и глобальных режимах, color без state и payload RELAY_TEST/Edge."""
    session_factory, _ = test_env
    hdrs = _create_dev(session_factory)
    monkeypatch.setattr("server.api.control.check_rpi_online", lambda db: None)

    created_cmds: list[tuple[str, dict]] = []

    async def fake_wait_command(cmd_id, timeout_sec, db):
        cmd = db.get(EdgeCommand, cmd_id)
        payload = json.loads(cmd.payload) if cmd.payload else {}
        created_cmds.append((cmd.command_type, payload))
        cmd.status = "done"
        cmd.result_message = "OK"
        db.commit()
        return cmd

    monkeypatch.setattr("server.api.control.wait_command_result", fake_wait_command)

    r_create = client.post(
        "/api/installations",
        headers=hdrs,
        json={
            "name": "RGB Память",
            "culture_name": "Мята",
            "light_gpio_pin": "17,27,22",
            "flash_gpio_pin": "5,6,13",
        },
    )
    assert r_create.status_code == 201
    inst_id = r_create.json()["id"]

    # 1. Изменение только цвета на включённом канале (state=None, light_state=True)
    r_color_on = client.post(
        "/api/control/light/main",
        headers=hdrs,
        json={"target": inst_id, "color": "00ffaa"},
    )
    assert r_color_on.status_code == 200
    assert r_color_on.json()["color"] == "#00FFAA"
    assert r_color_on.json()["effective_state"] is True
    assert created_cmds[-1][0] == "LIGHT_MAIN"
    assert created_cmds[-1][1]["color"] == "#00FFAA"
    assert created_cmds[-1][1]["pins"] == [17, 27, 22]

    # 2. Изменение только цвета на выключенном канале (вспышка по умолчанию OFF):
    # сохраняется в БД без отправки команды включения!
    cmds_before = len(created_cmds)
    r_color_off = client.post(
        "/api/control/light/flash",
        headers=hdrs,
        json={"target": inst_id, "color": "#FF4488"},
    )
    assert r_color_off.status_code == 200
    assert r_color_off.json()["color"] == "#FF4488"
    assert r_color_off.json()["effective_state"] is False
    assert len(created_cmds) == cmds_before

    # 3. Глобальный off -> глобальный on -> глобальный default: запомненные цвета сохраняются!
    r_glob_off = client.post(
        "/api/control/light/main",
        headers=hdrs,
        json={"target": "all", "mode": "off"},
    )
    assert r_glob_off.status_code == 200

    r_glob_on = client.post(
        "/api/control/light/main",
        headers=hdrs,
        json={"target": "all", "mode": "on"},
    )
    assert r_glob_on.status_code == 200
    inst_entry = created_cmds[-1][1]["installation_states"][str(inst_id)]
    assert inst_entry["light_color"] == "#00FFAA"
    assert inst_entry["light_pins"] == [17, 27, 22]

    r_glob_def = client.post(
        "/api/control/light/main",
        headers=hdrs,
        json={"target": "all", "mode": "default"},
    )
    assert r_glob_def.status_code == 200

    # 4. GET /api/control/relays и /relays/{id} отдают все RGB-поля и цвета
    r_relays = client.get("/api/control/relays", headers=hdrs)
    assert r_relays.status_code == 200
    rel_item = r_relays.json()["installations"][0]
    assert rel_item["light_is_rgb"] is True
    assert rel_item["flash_is_rgb"] is True
    assert rel_item["light_color"] == "#00FFAA"
    assert rel_item["flash_color"] == "#FF4488"

    r_rel_one = client.get(f"/api/control/relays/{inst_id}", headers=hdrs)
    assert r_rel_one.status_code == 200
    assert r_rel_one.json()["light_color"] == "#00FFAA"
    assert r_rel_one.json()["flash_color"] == "#FF4488"

    # 5. RELAY_TEST передаёт полные каналы и цвета в payload
    r_test = client.post(
        "/api/control/relay-test",
        headers=hdrs,
        json={"target": inst_id},
    )
    assert r_test.status_code == 200
    assert created_cmds[-1][0] == "RELAY_TEST"
    channels = created_cmds[-1][1]["channels"]
    assert len(channels) == 2
    assert channels[0]["pins"] == [17, 27, 22]
    assert channels[0]["color"] == "#00FFAA"
    assert channels[1]["pins"] == [5, 6, 13]
    assert channels[1]["color"] == "#FF4488"

    # 6. Heartbeat для edge отдаёт RGB-пины и цвета
    r_hb = client.post(
        "/api/edge/heartbeat",
        headers={"X-Edge-Key": EDGE_KEY},
        json={
            "software": {
                "edge_version": "1.0.0",
                "python_version": "3.12",
                "model_name": "mock",
            },
            "hardware": {
                "board_model": "Mock",
                "os_info": "Linux",
                "uptime_sec": 10,
                "cpu_temp_c": 40.0,
                "cpu_usage_percent": 5.0,
                "ram_used_mb": 100,
                "ram_total_mb": 1000,
                "disk_free_gb": 10.0,
                "gpio_mode": "mock",
            },
        },
    )
    assert r_hb.status_code == 200
    hb_inst = r_hb.json()["active_installations"][0]
    assert hb_inst["light_gpio_pin"] == "17,27,22"
    assert hb_inst["light_pins"] == [17, 27, 22]
    assert hb_inst["light_color"] == "#00FFAA"
    assert hb_inst["flash_color"] == "#FF4488"


def test_web_ui_rgb_markup_and_no_color_in_global_panel():
    """Web UI: панель цвета только в модалке установки, 5 ползунков, 2D-палитра и HEX."""
    root = Path(__file__).resolve().parents[2]
    html = (root / "clients/web/index.html").read_text(encoding="utf-8")
    css = (root / "clients/web/style.css").read_text(encoding="utf-8")
    unit_js = (root / "clients/web/js/unit_editor.js").read_text(encoding="utf-8")
    light_js = (root / "clients/web/js/light_control.js").read_text(encoding="utf-8")

    for required_id in (
        "unit-editor-color-panel",
        "unit-color-r",
        "unit-color-g",
        "unit-color-b",
        "unit-color-v",
        "unit-color-s",
        "unit-color-sv-canvas",
        "unit-color-hue-canvas",
        "unit-color-hex-input",
        "unit-color-preview",
        "unit-editor-light-swatch",
        "unit-editor-flash-swatch",
    ):
        assert required_id in html, f"Не найден элемент #{required_id} в index.html"

    # В модалке #modal-light-control выбора цвета быть не должно
    light_modal_part = html.split('id="modal-light-control"')[1].split("<!-- Модалка")[
        0
    ]
    assert "unit-color" not in light_modal_part
    assert 'type="color"' not in light_modal_part
    assert "color-picker" not in light_js

    assert ".unit-color-panel" in css
    assert "grid-column: 1 / -1" in css

    for fn_name in (
        "parseGpioPinsInput",
        "rgbToHsv",
        "hsvToRgb",
        "rgbToHex",
        "hexToRgb",
        "scheduleColorApiSend",
    ):
        assert fn_name in unit_js
