"""Тесты этапа 11.3 (сервер и Web UI): активное сканирование камер, 3-позиционная иерархия света/вспышки и редактор установки."""

import re
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from server.api import edge as edge_api
from server.api.deps import create_session_cookie, get_db
from server.config import get_settings
from server.core.state import app_state
from server.db.database import Base
from server.db.models import Camera, EdgeCommand, SystemSetting, User
from server.main import app

CLIENT_KEY = "test-client-secret-key"
EDGE_KEY = "test-edge-secret-key"
ROOT_DIR = Path(__file__).resolve().parent.parent.parent


@pytest.fixture
def test_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "test_stage11_3.db"
    engine = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(edge_api, "get_media_dir", lambda: media_dir)

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

    yield session_factory

    app.dependency_overrides.clear()
    app_state.clear()
    engine.dispose()


@pytest.fixture
def client(test_env) -> TestClient:
    return TestClient(app)


def _create_cookie(session_factory, role: str, ext_id: str) -> dict[str, str]:
    with session_factory() as db:
        now = datetime.now(UTC)
        user = User(
            client_type="web",
            external_id=ext_id,
            login=f"{role}-11-3",
            role=role,
            is_active=True,
            authorized_at=now,
            last_active_at=now,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return {"gv_session": create_session_cookie(user)}


def _install_auto_edge_completer(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, edge_headers: dict[str, str]
):
    """Автоматически забирает и завершает команду RPi при вызове wait_command_result."""
    captured_commands: list[dict] = []

    async def auto_complete_via_edge(cmd_id: int, timeout_sec: float, db: Session):
        claim = client.post("/api/edge/commands/claim", headers=edge_headers)
        assert claim.status_code == 200
        items = claim.json()
        assert len(items) == 1
        c = items[0]
        assert c["id"] == cmd_id
        captured_commands.append(c)

        result_data = None
        if c["command_type"] == "RELAY_TEST":
            init_pins = (c.get("payload") or {}).get("initial_states") or {}
            result_data = {"pin_states": init_pins}
        elif c["command_type"] in ("LIGHT_MAIN", "LIGHT_FLASH"):
            pin_states = (c.get("payload") or {}).get("pin_states") or {}
            result_data = {"pin_states": pin_states}

        res = client.post(
            f"/api/edge/commands/{cmd_id}/result",
            headers=edge_headers,
            json={
                "status": "done",
                "result_message": "OK",
                "data": result_data,
            },
        )
        assert res.status_code == 200
        db.expire_all()
        return db.query(EdgeCommand).filter(EdgeCommand.id == cmd_id).first()

    monkeypatch.setattr(
        "server.api.control.wait_command_result", auto_complete_via_edge
    )
    return captured_commands


def test_cameras_scan_updates_db_and_offline_fallback(
    client: TestClient, test_env, monkeypatch: pytest.MonkeyPatch
):
    """POST /api/cameras/scan обновляет БД (новые/онлайн/офлайн), а при офлайне отдаёт 503 без потери кэша."""
    session_factory = test_env
    dev_cookies = _create_cookie(session_factory, "developer", "dev-scan-11-3")
    bio_cookies = _create_cookie(session_factory, "biologist", "bio-scan-11-3")
    edge_headers = {"X-Edge-Key": EDGE_KEY}

    with session_factory() as db:
        cam_old = Camera(
            hardware_id="cam-old-offline",
            auto_name="Old Cam",
            is_online=True,
        )
        cam_keep = Camera(
            hardware_id="cam-keep",
            auto_name="Keep Cam Old Name",
            is_online=False,
        )
        db.add_all([cam_old, cam_keep])
        db.commit()

    # 1. Если Edge-агент офлайн -> POST /api/cameras/scan возвращает 503, но GET /api/cameras отдаёт кэш
    r_scan_offline = client.post("/api/cameras/scan", cookies=dev_cookies)
    assert r_scan_offline.status_code == 503

    r_cache = client.get("/api/cameras", cookies=dev_cookies)
    assert r_cache.status_code == 200
    assert {c["hardware_id"] for c in r_cache.json()} == {
        "cam-old-offline",
        "cam-keep",
    }

    # 2. Биологу POST /api/cameras/scan запрещён -> 403, GET /api/cameras доступен
    assert client.post("/api/cameras/scan", cookies=bio_cookies).status_code == 403
    assert client.get("/api/cameras", cookies=bio_cookies).status_code == 200

    # 3. Edge-агент онлайн: имитируем завершение SCAN_CAMERAS через реальный эндпоинт результата
    monkeypatch.setattr("server.api.cameras.check_rpi_online", lambda db: None)

    orig_sleep = __import__("asyncio").sleep

    async def fast_poll_sleep(_delay: float) -> None:
        # На первой итерации забираем SCAN_CAMERAS и отправляем результат
        claim = client.post("/api/edge/commands/claim", headers=edge_headers)
        items = claim.json()
        if items:
            cmd_id = items[0]["id"]
            client.post(
                f"/api/edge/commands/{cmd_id}/result",
                headers=edge_headers,
                json={
                    "status": "done",
                    "result_message": "Найдено 2 камеры",
                    "scanned_cameras": [
                        {"hardware_id": "cam-keep", "auto_name": "Keep Cam Renamed"},
                        {"hardware_id": "cam-new-usb", "auto_name": "New USB Cam"},
                    ],
                },
            )
        await orig_sleep(0.01)

    monkeypatch.setattr("server.api.cameras.asyncio.sleep", fast_poll_sleep)

    r_scan_ok = client.post("/api/cameras/scan", cookies=dev_cookies)
    assert r_scan_ok.status_code == 200
    scan_body = r_scan_ok.json()
    assert scan_body["status"] == "done"
    assert [c["hardware_id"] for c in scan_body["cameras"]] == [
        "cam-keep",
        "cam-new-usb",
    ]
    assert scan_body["cameras"][0]["auto_name"] == "Keep Cam Renamed"
    assert scan_body["cameras"][0]["is_online"] is True

    # Необнаруженная камера cam-old-offline переведена в offline в БД
    r_all_after = client.get("/api/cameras", cookies=dev_cookies)
    by_hw = {c["hardware_id"]: c for c in r_all_after.json()}
    assert by_hw["cam-old-offline"]["is_online"] is False
    assert by_hw["cam-keep"]["is_online"] is True
    assert by_hw["cam-new-usb"]["is_online"] is True


def test_three_state_global_light_flash_hierarchy_and_persistence(
    client: TestClient, test_env, monkeypatch: pytest.MonkeyPatch
):
    """3-позиционная логика общего света/вспышки сохраняет индивидуальные настройки и возвращает их при default."""
    session_factory = test_env
    dev_cookies = _create_cookie(session_factory, "developer", "dev-hier-11-3")
    edge_headers = {"X-Edge-Key": EDGE_KEY}
    monkeypatch.setattr("server.api.control.check_rpi_online", lambda db: None)
    captured_cmds = _install_auto_edge_completer(client, monkeypatch, edge_headers)

    # Создаём две установки: у каждой по умолчанию свет=True, вспышка=False
    r1 = client.post(
        "/api/installations",
        cookies=dev_cookies,
        json={
            "name": "Секция A",
            "culture_name": "Салат",
            "light_gpio_pin": 17,
            "flash_gpio_pin": 27,
        },
    )
    r2 = client.post(
        "/api/installations",
        cookies=dev_cookies,
        json={
            "name": "Секция B",
            "culture_name": "Томат",
            "light_gpio_pin": 22,
            "flash_gpio_pin": 23,
        },
    )
    assert r1.status_code == 201 and r2.status_code == 201
    inst1_id = r1.json()["id"]
    inst2_id = r2.json()["id"]

    # Проверяем начальные поля в GET /api/installations для developer
    assert r1.json()["global_light_mode"] == "default"
    assert r1.json()["global_flash_mode"] == "default"
    assert r1.json()["individual_light_state"] is True
    assert r1.json()["individual_flash_state"] is False
    assert r1.json()["light_locked"] is False
    assert r1.json()["flash_locked"] is False

    # 1. Индивидуально выключаем свет на Секции A и включаем вспышку на Секции B
    r_ind1 = client.post(
        "/api/control/light/main",
        cookies=dev_cookies,
        json={"target": inst1_id, "state": False},
    )
    assert r_ind1.status_code == 200
    assert r_ind1.json()["individual_state"] is False
    assert r_ind1.json()["effective_state"] is False
    assert r_ind1.json()["pin_states"]["17"] is False
    assert r_ind1.json()["pin_states"]["22"] is True

    r_ind2_flash = client.post(
        "/api/control/light/flash",
        cookies=dev_cookies,
        json={"target": inst2_id, "state": True},
    )
    assert r_ind2_flash.status_code == 200
    assert r_ind2_flash.json()["pin_states"]["27"] is False
    assert r_ind2_flash.json()["pin_states"]["23"] is True

    # 2. Общий свет -> "off": все физические пины света гаснут, но individual_light_state Секции B остаётся True
    r_glob_off = client.post(
        "/api/control/light/main",
        cookies=dev_cookies,
        json={"target": "all", "mode": "off"},
    )
    assert r_glob_off.status_code == 200
    assert r_glob_off.json()["global_light_mode"] == "off"
    assert r_glob_off.json()["pin_states"]["17"] is False
    assert r_glob_off.json()["pin_states"]["22"] is False

    r_relays_off = client.get("/api/control/relays", cookies=dev_cookies).json()
    assert r_relays_off["global_light_mode"] == "off"
    by_id = {item["installation_id"]: item for item in r_relays_off["installations"]}
    assert by_id[inst1_id]["individual_light_state"] is False
    assert by_id[inst1_id]["light_state"] is False
    assert by_id[inst1_id]["light_locked"] is True
    assert by_id[inst2_id]["individual_light_state"] is True
    assert by_id[inst2_id]["light_state"] is False
    assert by_id[inst2_id]["light_locked"] is True

    # 3. Общий свет -> "on": все физические пины включаются, но individual_light_state Секции A остаётся False
    r_glob_on = client.post(
        "/api/control/light/main",
        cookies=dev_cookies,
        json={"target": "all", "mode": "on"},
    )
    assert r_glob_on.status_code == 200
    assert r_glob_on.json()["pin_states"]["17"] is True
    assert r_glob_on.json()["pin_states"]["22"] is True

    r_list = client.get("/api/installations", cookies=dev_cookies).json()
    inst_by_id = {item["id"]: item for item in r_list}
    assert inst_by_id[inst1_id]["individual_light_state"] is False
    assert inst_by_id[inst1_id]["light_state"] is True
    assert inst_by_id[inst1_id]["light_locked"] is True
    assert inst_by_id[inst2_id]["individual_light_state"] is True
    assert inst_by_id[inst2_id]["light_state"] is True

    # 4. Пока глобальный свет = "on", индивидуальный запрос для inst2=False сохраняет БД, но пин остаётся True
    r_ind_while_locked = client.post(
        "/api/control/light/main",
        cookies=dev_cookies,
        json={"target": inst2_id, "state": False},
    )
    assert r_ind_while_locked.status_code == 200
    assert r_ind_while_locked.json()["individual_state"] is False
    assert r_ind_while_locked.json()["effective_state"] is True
    assert r_ind_while_locked.json()["locked"] is True
    assert r_ind_while_locked.json()["pin_states"]["22"] is True

    # Возвращаем индивидуальное для inst2 обратно в True, чтобы далее проверить возврат к разным состояниям
    client.post(
        "/api/control/light/main",
        cookies=dev_cookies,
        json={"target": inst2_id, "state": True},
    )

    # 5. Общая вспышка -> "off", затем тест реле (RELAY_TEST) при global_light="on", global_flash="off"
    client.post(
        "/api/control/light/flash",
        cookies=dev_cookies,
        json={"target": "all", "mode": "off"},
    )
    r_relay_test = client.post(
        "/api/control/relay-test",
        cookies=dev_cookies,
        json={"target": "all"},
    )
    assert r_relay_test.status_code == 200
    assert r_relay_test.json()["pin_states"]["17"] is True
    assert r_relay_test.json()["pin_states"]["22"] is True
    assert r_relay_test.json()["pin_states"]["27"] is False
    assert r_relay_test.json()["pin_states"]["23"] is False

    # 6. Возвращаем общий свет и вспышку в "default" -> пины автоматически возвращаются к индивидуальным настройкам!
    r_main_def = client.post(
        "/api/control/light/main",
        cookies=dev_cookies,
        json={"target": "all", "mode": "default"},
    )
    assert r_main_def.status_code == 200
    assert r_main_def.json()["global_light_mode"] == "default"
    assert r_main_def.json()["pin_states"]["17"] is False
    assert r_main_def.json()["pin_states"]["22"] is True

    r_flash_def = client.post(
        "/api/control/light/flash",
        cookies=dev_cookies,
        json={"target": "all", "mode": "default"},
    )
    assert r_flash_def.status_code == 200
    assert r_flash_def.json()["global_flash_mode"] == "default"
    assert r_flash_def.json()["pin_states"]["27"] is False
    assert r_flash_def.json()["pin_states"]["23"] is True

    # Проверяем, что в БД SystemSetting действительно сохранены нужные ключи
    with session_factory() as db:
        keys = {s.key: s.value for s in db.query(SystemSetting).all()}
        assert keys["global_light_mode"] == "default"
        assert keys["global_flash_mode"] == "default"
        assert keys[f"inst_light_state_{inst1_id}"] == "false"
        assert keys[f"inst_light_state_{inst2_id}"] == "true"
        assert keys[f"inst_flash_state_{inst1_id}"] == "false"
        assert keys[f"inst_flash_state_{inst2_id}"] == "true"

    # 7. Heartbeat отдаёт эффективные целевые состояния и глобальные режимы для edge_agent
    r_hb = client.post(
        "/api/edge/heartbeat",
        headers=edge_headers,
        json={
            "software": {
                "edge_version": "0.1.0",
                "python_version": "3.12.0",
                "model_name": "mock",
            },
            "hardware": {
                "board_model": "Mock",
                "os_info": "Linux",
                "uptime_sec": 10,
                "cpu_temp_c": 40.0,
                "cpu_usage_percent": 5.0,
                "ram_used_mb": 100,
                "ram_total_mb": 1024,
                "disk_free_gb": 10.0,
                "gpio_mode": "mock",
            },
            "offline_queue_count": 0,
            "detected_cameras": [],
        },
    )
    assert r_hb.status_code == 200
    hb_data = r_hb.json()
    assert hb_data["global_light_mode"] == "default"
    assert hb_data["global_flash_mode"] == "default"
    hb_by_id = {i["id"]: i for i in hb_data["active_installations"]}
    assert hb_by_id[inst1_id]["light_state"] is False
    assert hb_by_id[inst1_id]["flash_state"] is False
    assert hb_by_id[inst2_id]["light_state"] is True
    assert hb_by_id[inst2_id]["flash_state"] is True
    assert len(captured_cmds) > 0


def test_web_ui_stage11_3_markup_order_and_scripts(client: TestClient):
    """Проверка Web UI: активное сканирование камер, 3-позиционный свет и порядок/скрытие плашек в unit_editor."""
    web_dir = ROOT_DIR / "clients" / "web"
    html_text = client.get("/").text

    # 1. Камеры: индикатор сканирования и JS-логика fallback
    assert 'id="cameras-scan-status"' in html_text
    cameras_js = (web_dir / "js" / "cameras.js").read_text(encoding="utf-8")
    assert "api.scanCameras()" in cameras_js
    assert "Сканирование камер..." in cameras_js
    assert "Edge-агент недоступен" in cameras_js
    assert "openCamerasModal({ scan: false })" in cameras_js
    assert "openCamerasModal({ scan: true })" in cameras_js

    api_js = (web_dir / "js" / "api.js").read_text(encoding="utf-8")
    assert "scanCameras: () => request('/api/cameras/scan'" in api_js
    assert (
        "getInstallation: (instId) => request(`/api/installations/${Number(instId)}`)"
        in api_js
    )

    # 2. Модалка управления светом: 3-позиционные segmented-control
    for mode_id in ('id="light-mode-main"', 'id="light-mode-flash"'):
        assert mode_id in html_text
    assert 'data-mode="on"' in html_text
    assert 'data-mode="default"' in html_text
    assert 'data-mode="off"' in html_text
    assert 'id="light-toggle-main"' not in html_text
    assert 'id="light-toggle-flash"' not in html_text

    # 3. Карточка установки: плашки строго под Название/Культура и над Расписание/Камера, скрыты по умолчанию
    grid_match = re.search(
        r'<div class="unit-editor-grid">(.*?)</div>\s*<div id="unit-editor-error"',
        html_text,
        re.DOTALL,
    )
    assert grid_match is not None
    grid_html = grid_match.group(1)
    idx_name = grid_html.index('id="unit-editor-name"')
    idx_culture = grid_html.index('id="unit-editor-culture"')
    idx_light_card = grid_html.index('id="unit-editor-light-card"')
    idx_flash_card = grid_html.index('id="unit-editor-flash-card"')
    idx_interval = grid_html.index('id="unit-editor-interval"')
    idx_camera = grid_html.index('id="unit-editor-camera"')
    assert (
        idx_name
        < idx_culture
        < idx_light_card
        < idx_flash_card
        < idx_interval
        < idx_camera
    )
    assert 'id="unit-editor-light-card" class="unit-light-card hidden"' in grid_html
    assert 'id="unit-editor-flash-card" class="unit-light-card hidden"' in grid_html
    assert 'id="unit-editor-light-locked"' in grid_html
    assert 'id="unit-editor-flash-locked"' in grid_html
    assert "Заблокировано" in grid_html

    editor_js = (web_dir / "js" / "unit_editor.js").read_text(encoding="utf-8")
    assert "syncUnitLightControls" in editor_js
    assert "individual_light_state" in editor_js
    assert "individual_flash_state" in editor_js
    assert "api.controlLightMain(targetId, nextState)" in editor_js
    assert "api.controlLightFlash(targetId, nextState)" in editor_js
