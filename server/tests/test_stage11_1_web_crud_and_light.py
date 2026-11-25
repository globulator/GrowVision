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
from server.db.models import Camera, Capture, EdgeCommand, Installation, User
from server.main import app

CLIENT_KEY = "test-client-secret-key"
EDGE_KEY = "test-edge-secret-key"
ROOT_DIR = Path(__file__).resolve().parent.parent.parent


@pytest.fixture
def test_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "test_stage11_1.db"
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

    yield session_factory, media_dir

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
            login=f"{role}-user",
            role=role,
            is_active=True,
            authorized_at=now,
            last_active_at=now,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return {"gv_session": create_session_cookie(user)}


def test_installations_crud_archive_unarchive_and_delete_only_from_archive(
    client: TestClient, test_env
):
    """CRUD, архивация, разархивация, защита прав биолога и удаление со снимками только из архива."""
    session_factory, media_dir = test_env
    dev_cookies = _create_cookie(session_factory, "developer", "dev-11-1")
    bio_cookies = _create_cookie(session_factory, "biologist", "bio-11-1")

    # 1. Создание установки разработчиком (и запрет для биолога -> 403)
    payload = {
        "name": "Стеллаж Вега",
        "culture_name": "Салат",
        "light_gpio_pin": 17,
        "flash_gpio_pin": 27,
        "aruco_size_mm": 50.0,
        "capture_delay_sec": 2.0,
    }
    r_bio_create = client.post("/api/installations", json=payload, cookies=bio_cookies)
    assert r_bio_create.status_code == 403

    r_create = client.post("/api/installations", json=payload, cookies=dev_cookies)
    assert r_create.status_code == 201
    inst_id = r_create.json()["id"]
    assert r_create.json()["light_state"] is True
    assert r_create.json()["flash_state"] is False

    # 2. Biologist может менять name, culture_name и capture_delay_sec, но не пины/aruco
    r_bio_patch = client.patch(
        f"/api/installations/{inst_id}",
        json={
            "name": "Стеллаж Вега-2",
            "culture_name": "Базилик",
            "capture_delay_sec": 1.2,
        },
        cookies=bio_cookies,
    )
    assert r_bio_patch.status_code == 200
    assert r_bio_patch.json()["name"] == "Стеллаж Вега-2"
    assert r_bio_patch.json()["capture_delay_sec"] == 1.2

    r_bio_forbidden = client.patch(
        f"/api/installations/{inst_id}",
        json={"light_gpio_pin": 18},
        cookies=bio_cookies,
    )
    assert r_bio_forbidden.status_code == 403

    # 3. Привязываем камеру и добавляем снимок
    with session_factory() as db:
        cam = Camera(
            hardware_id="cam-11-1",
            auto_name="Cam 11.1",
            installation_id=inst_id,
            is_online=True,
        )
        db.add(cam)
        db.flush()
        cap = Capture(
            installation_id=inst_id,
            camera_id=cam.id,
            captured_at=datetime.now(UTC),
            is_manual=False,
            raw_image_path=f"media/{inst_id}/raw.jpg",
            annotated_image_path=f"media/{inst_id}/ann.jpg",
        )
        db.add(cap)
        db.commit()
        cam_id = cam.id

    inst_dir = media_dir / str(inst_id)
    inst_dir.mkdir(parents=True, exist_ok=True)
    (inst_dir / "raw.jpg").write_bytes(b"raw")

    # Активную установку со снимками удалить нельзя -> 409
    r_del_active = client.delete(f"/api/installations/{inst_id}", cookies=dev_cookies)
    assert r_del_active.status_code == 409

    # 4. Архивация отвязывает камеры; include_archived=true возвращает архивную
    r_arch = client.post(f"/api/installations/{inst_id}/archive", cookies=dev_cookies)
    assert r_arch.status_code == 200
    assert r_arch.json()["is_archived"] is True

    r_list_active = client.get("/api/installations", cookies=dev_cookies)
    assert all(item["id"] != inst_id for item in r_list_active.json())

    r_list_all = client.get(
        "/api/installations?include_archived=true", cookies=dev_cookies
    )
    assert any(
        item["id"] == inst_id and item["is_archived"] is True
        for item in r_list_all.json()
    )

    # 5. Разархивация и повторная архивация перед удалением
    r_unarch = client.post(
        f"/api/installations/{inst_id}/unarchive", cookies=dev_cookies
    )
    assert r_unarch.status_code == 200
    assert r_unarch.json()["is_archived"] is False

    r_arch2 = client.post(f"/api/installations/{inst_id}/archive", cookies=dev_cookies)
    assert r_arch2.status_code == 200

    # Удаление из архива -> 204, камера отвязана, файлы удалены
    r_del_arch = client.delete(f"/api/installations/{inst_id}", cookies=dev_cookies)
    assert r_del_arch.status_code == 204
    assert not inst_dir.exists()

    with session_factory() as db:
        assert db.get(Installation, inst_id) is None
        assert db.get(Camera, cam_id).installation_id is None


def test_camera_instant_bind_rebind_and_unbind(client: TestClient, test_env):
    """Мгновенная привязка, перепривязка между установками и отвязка камеры через API."""
    session_factory, _ = test_env
    dev_cookies = _create_cookie(session_factory, "developer", "dev-cam-bind")
    bio_cookies = _create_cookie(session_factory, "biologist", "bio-cam-bind")

    with session_factory() as db:
        inst1 = Installation(
            name="Бокс А", culture_name="Салат", light_gpio_pin=4, flash_gpio_pin=5
        )
        inst2 = Installation(
            name="Бокс Б", culture_name="Мята", light_gpio_pin=6, flash_gpio_pin=7
        )
        inst_arch = Installation(
            name="Архив",
            culture_name="Укроп",
            light_gpio_pin=8,
            flash_gpio_pin=9,
            is_archived=True,
        )
        db.add_all([inst1, inst2, inst_arch])
        db.flush()
        cam = Camera(hardware_id="cam-bind-11", auto_name="Cam 11", is_online=True)
        db.add(cam)
        db.commit()
        inst1_id, inst2_id, arch_id, cam_id = inst1.id, inst2.id, inst_arch.id, cam.id

    # Биологу привязка запрещена -> 403
    r_bio = client.post(
        f"/api/cameras/{cam_id}/bind",
        json={"installation_id": inst1_id},
        cookies=bio_cookies,
    )
    assert r_bio.status_code == 403

    # Привязка к Бокс А
    r1 = client.post(
        f"/api/cameras/{cam_id}/bind",
        json={"installation_id": inst1_id},
        cookies=dev_cookies,
    )
    assert r1.status_code == 200
    assert r1.json()["installation_id"] == inst1_id
    assert r1.json()["installation_name"] == "Бокс А"

    # Мгновенная перепривязка на Бокс Б
    r2 = client.post(
        f"/api/cameras/{cam_id}/bind",
        json={"installation_id": inst2_id},
        cookies=dev_cookies,
    )
    assert r2.status_code == 200
    assert r2.json()["installation_id"] == inst2_id
    assert r2.json()["installation_name"] == "Бокс Б"

    # Попытка привязать к архивной установке -> 409
    r_arch = client.post(
        f"/api/cameras/{cam_id}/bind",
        json={"installation_id": arch_id},
        cookies=dev_cookies,
    )
    assert r_arch.status_code == 409

    # Отвязка (installation_id: null)
    r_unbind = client.post(
        f"/api/cameras/{cam_id}/bind",
        json={"installation_id": None},
        cookies=dev_cookies,
    )
    assert r_unbind.status_code == 200
    assert r_unbind.json()["installation_id"] is None
    assert r_unbind.json()["installation_name"] is None


def test_global_light_flash_relay_test_and_delay_settings(
    client: TestClient, test_env, monkeypatch: pytest.MonkeyPatch
):
    """Глобальное управление светом/вспышкой, сохранение состояний после RELAY_TEST и задержка прогрева."""
    session_factory, _ = test_env
    dev_cookies = _create_cookie(session_factory, "developer", "dev-light")
    edge_headers = {"X-Edge-Key": EDGE_KEY}

    with session_factory() as db:
        inst1 = Installation(
            name="Зона 1", culture_name="Салат", light_gpio_pin=17, flash_gpio_pin=27
        )
        inst2 = Installation(
            name="Зона 2", culture_name="Томат", light_gpio_pin=22, flash_gpio_pin=23
        )
        db.add_all([inst1, inst2])
        db.commit()

    monkeypatch.setattr("server.api.control.check_rpi_online", lambda db: None)

    # 1. GET /api/control/relays отдаёт состояния активных установок и capture_delay_sec
    r_relays_init = client.get("/api/control/relays", cookies=dev_cookies)
    assert r_relays_init.status_code == 200
    data_init = r_relays_init.json()
    assert data_init["light_state"] is True
    assert data_init["flash_state"] is False
    assert data_init["capture_delay_sec"] == 1.5
    assert len(data_init["installations"]) == 2
    assert data_init["pins"]["17"] is True
    assert data_init["pins"]["27"] is False

    async def auto_complete_via_edge(cmd_id: int, timeout_sec: float, db: Session):
        claim = client.post("/api/edge/commands/claim", headers=edge_headers)
        assert claim.status_code == 200
        items = claim.json()
        assert len(items) == 1
        c = items[0]
        assert c["id"] == cmd_id

        result_data = None
        if c["command_type"] == "RELAY_TEST":
            init_pins = (c.get("payload") or {}).get("initial_states") or {}
            result_data = {"pin_states": init_pins}

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

    # 2. RELAY_TEST при включённом свете -> свет остаётся ON, pin_states возвращается
    app_state.set_pin_state(17, True)
    app_state.set_pin_state(22, True)
    app_state.set_pin_state(27, False)
    app_state.set_pin_state(23, False)

    r_test_on = client.post(
        "/api/control/relay-test",
        json={"target": "all"},
        cookies=dev_cookies,
    )
    assert r_test_on.status_code == 200
    assert r_test_on.json()["pin_states"]["17"] is True
    assert r_test_on.json()["pin_states"]["22"] is True
    assert r_test_on.json()["pin_states"]["27"] is False
    assert r_test_on.json()["pin_states"]["23"] is False

    # 3. Выключаем основной свет для всех, затем RELAY_TEST -> свет обязан остаться OFF
    r_main_off = client.post(
        "/api/control/light/main",
        json={"target": "all", "state": False},
        cookies=dev_cookies,
    )
    assert r_main_off.status_code == 200
    assert r_main_off.json()["pin_states"]["17"] is False
    assert r_main_off.json()["pin_states"]["22"] is False

    r_test_off = client.post(
        "/api/control/relay-test",
        json={"target": "all"},
        cookies=dev_cookies,
    )
    assert r_test_off.status_code == 200
    assert r_test_off.json()["pin_states"]["17"] is False
    assert r_test_off.json()["pin_states"]["22"] is False

    # 4. Включаем вспышку для всех
    r_flash_on = client.post(
        "/api/control/light/flash",
        json={"target": "all", "state": True},
        cookies=dev_cookies,
    )
    assert r_flash_on.status_code == 200
    assert r_flash_on.json()["pin_states"]["27"] is True
    assert r_flash_on.json()["pin_states"]["23"] is True

    # 5. Изменяем задержку прогрева и проверяем её в GET /api/control/relays
    r_delay = client.patch(
        "/api/system/settings",
        json={"capture_delay_sec": 3.5},
        cookies=dev_cookies,
    )
    assert r_delay.status_code == 200
    assert r_delay.json()["capture_delay_sec"] == 3.5

    r_relays_after = client.get("/api/control/relays", cookies=dev_cookies)
    assert r_relays_after.status_code == 200
    assert r_relays_after.json()["capture_delay_sec"] == 3.5
    assert r_relays_after.json()["light_state"] is False
    assert r_relays_after.json()["flash_state"] is True


def test_web_ui_markup_and_modules_for_stage11_1(client: TestClient):
    """Проверка HTML-разметки, выдачи новых JS-модулей и отсутствия нативных диалогов/секретов."""
    web_dir = ROOT_DIR / "clients" / "web"

    r_index = client.get("/")
    assert r_index.status_code == 200
    html_text = r_index.text

    # Фильтры и сортировка в .sub-toolbar
    for marker in (
        'id="units-filter-status"',
        'data-filter-status="active"',
        'data-filter-status="archived"',
        'id="units-filter-sort"',
        'data-filter-sort="newest"',
        'data-filter-sort="oldest"',
        'id="units-filter-camera"',
        'data-filter-camera="all"',
        'data-filter-camera="with_camera"',
        'data-filter-camera="without_camera"',
    ):
        assert marker in html_text

    # Единая модалка установки (#modal-unit-editor)
    for marker in (
        'id="modal-unit-editor"',
        'id="unit-editor-title"',
        'placeholder="Установка 1"',
        'placeholder="Салат"',
        'id="unit-editor-interval"',
        'id="unit-editor-light-pin"',
        'id="unit-editor-flash-pin"',
        'id="unit-editor-delay"',
        'id="unit-editor-aruco"',
        'id="unit-editor-camera"',
        'id="unit-editor-archive-btn"',
        'id="unit-editor-unarchive-btn"',
        'id="unit-editor-delete-btn"',
        'id="unit-editor-delete-confirm"',
        'id="unit-editor-cancel-btn"',
        'id="unit-editor-save-btn"',
    ):
        assert marker in html_text

    # Кнопка лампочки и модалка управления светом без кнопки «Сохранить»
    for marker in (
        'id="btn-light-menu"',
        'id="modal-light-control"',
        'id="light-mode-main"',
        'id="light-mode-flash"',
        'id="btn-light-relay-test"',
        'id="light-capture-delay-input"',
    ):
        assert marker in html_text

    light_modal_match = re.search(
        r'id="modal-light-control".*?<!-- Модалка: Подтверждение действия',
        html_text,
        re.DOTALL,
    )
    assert light_modal_match is not None
    assert "Сохранить" not in light_modal_match.group(0)

    # Новые JS-модули отдаются сервером
    for js_name in (
        "unit_editor.js",
        "light_control.js",
        "units.js",
        "cameras.js",
        "api.js",
    ):
        r_js = client.get(f"/js/{js_name}")
        assert r_js.status_code == 200, f"Не отдаётся /js/{js_name}"

    # Проверка отсутствия window.confirm/alert и утечек X-Client-Key
    for js_file in (web_dir / "js").glob("*.js"):
        content = js_file.read_text(encoding="utf-8")
        assert "window.confirm" not in content
        assert "x-client-key" not in content.lower()

    units_js = (web_dir / "js" / "units.js").read_text(encoding="utf-8")
    assert "Создать новую установку" in units_js
    assert "filterAndSortUnits" in units_js
    assert "e.stopPropagation()" in units_js

    editor_js = (web_dir / "js" / "unit_editor.js").read_text(encoding="utf-8")
    assert "Создание новой установки" in editor_js
    assert "Редактирование установки:" in editor_js
    assert "Растение ещё не готово к фотосессии..." in editor_js
    assert "След. кадр:" in editor_js
    assert "bindCamera" in editor_js

    cameras_js = (web_dir / "js" / "cameras.js").read_text(encoding="utf-8")
    assert "camera-bind-select" in cameras_js
    assert "Отвязать" in cameras_js
    assert "api.bindCamera" in cameras_js

    light_js = (web_dir / "js" / "light_control.js").read_text(encoding="utf-8")
    assert "api.controlLightMain('all'" in light_js
    assert "api.controlLightFlash('all'" in light_js
    assert "api.controlRelayTest('all')" in light_js
    assert "api.patchSystemSettings" in light_js
