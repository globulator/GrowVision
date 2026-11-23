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
from server.core.rate_limit import login_limiter
from server.db.database import Base
from server.db.models import (
    Camera,
    Capture,
    Installation,
    PlantMeasurement,
    SystemSetting,
    User,
)
from server.main import app

CLIENT_KEY = "test-client-secret-key"
EDGE_KEY = "test-edge-secret-key"
BIO_PASS = "bio-password-123"
DEV_PASS = "dev-password-456"


@pytest.fixture
def test_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Изолирует окружение: временную БД, секреты, ключи и лимиты."""
    db_file = tmp_path / "test_client_api.db"
    db_url = f"sqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    test_media_dir = tmp_path / "media"
    test_media_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("server.api.edge.get_media_dir", lambda: test_media_dir)

    settings = get_settings()
    monkeypatch.setattr(settings, "client_api_key", CLIENT_KEY)
    monkeypatch.setattr(settings, "edge_api_key", EDGE_KEY)
    monkeypatch.setattr(settings, "biologist_password", BIO_PASS)
    monkeypatch.setattr(settings, "developer_password", DEV_PASS)

    login_limiter.clear()

    def override_get_db() -> Generator[Session, None, None]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    yield session_factory, engine

    app.dependency_overrides.clear()
    login_limiter.clear()
    engine.dispose()


@pytest.fixture
def client(test_env) -> TestClient:
    return TestClient(app)


def auth_headers(
    client_type: str = "cli", external_id: str = "user-1"
) -> dict[str, str]:
    return {
        "X-Client-Key": CLIENT_KEY,
        "X-Client-Type": client_type,
        "X-External-Id": external_id,
    }


def create_session(
    session_factory,
    role: str = "developer",
    client_type: str = "cli",
    external_id: str = "user-1",
    login: str = "Тестовый Пользователь",
) -> None:
    db = session_factory()
    user = User(
        client_type=client_type,
        external_id=external_id,
        login=login,
        role=role,
    )
    db.add(user)
    db.commit()
    db.close()


# ---------------- 1. Аутентификация и сессии ----------------


def test_auth_login_success_and_update(client: TestClient, test_env):
    """POST /api/auth/login: вход biolgist и developer, обновление сессии."""
    session_factory, _ = test_env

    # 1. Вход Biologist
    r_bio = client.post(
        "/api/auth/login",
        headers={"X-Client-Key": CLIENT_KEY},
        json={
            "client_type": "telegram",
            "external_id": "tg-100",
            "login": "Анна Биолог",
            "password": BIO_PASS,
        },
    )
    assert r_bio.status_code == 200
    assert r_bio.json() == {"role": "biologist", "login": "Анна Биолог"}

    db = session_factory()
    u = db.query(User).filter_by(client_type="telegram", external_id="tg-100").first()
    assert u is not None
    assert u.role == "biologist"
    db.close()

    # 2. Повторный вход тем же пользователем с паролем Developer обновляет роль
    r_dev = client.post(
        "/api/auth/login",
        headers={"X-Client-Key": CLIENT_KEY},
        json={
            "client_type": "telegram",
            "external_id": "tg-100",
            "login": "Анна Админ",
            "password": DEV_PASS,
        },
    )
    assert r_dev.status_code == 200
    assert r_dev.json() == {"role": "developer", "login": "Анна Админ"}

    db = session_factory()
    u2 = db.query(User).filter_by(client_type="telegram", external_id="tg-100").first()
    assert u2.role == "developer"
    assert u2.login == "Анна Админ"
    db.close()


def test_auth_login_invalid_password_and_key(client: TestClient):
    """POST /api/auth/login: неверный пароль -> 401, неверный ключ -> 401."""
    # Неверный ключ клиента
    r_bad_key = client.post(
        "/api/auth/login",
        headers={"X-Client-Key": "wrong-key"},
        json={
            "client_type": "cli",
            "external_id": "u1",
            "login": "Иван",
            "password": BIO_PASS,
        },
    )
    assert r_bad_key.status_code == 401

    # Неверный пароль
    r_bad_pwd = client.post(
        "/api/auth/login",
        headers={"X-Client-Key": CLIENT_KEY},
        json={
            "client_type": "cli",
            "external_id": "u1",
            "login": "Иван",
            "password": "incorrect-password",
        },
    )
    assert r_bad_pwd.status_code == 401
    assert "Неверный пароль" in r_bad_pwd.json()["detail"]


def test_auth_login_rate_limiting(client: TestClient):
    """Лимит входа: не более 5 попыток в минуту на пару (client_type, external_id); 6-я -> 429."""
    for _ in range(5):
        resp = client.post(
            "/api/auth/login",
            headers={"X-Client-Key": CLIENT_KEY},
            json={
                "client_type": "cli",
                "external_id": "rate-limit-user",
                "login": "Тест",
                "password": "bad-password",
            },
        )
        assert resp.status_code == 401

    # 6-я попытка должна вернуть 429 Too Many Requests
    r_blocked = client.post(
        "/api/auth/login",
        headers={"X-Client-Key": CLIENT_KEY},
        json={
            "client_type": "cli",
            "external_id": "rate-limit-user",
            "login": "Тест",
            "password": "bad-password",
        },
    )
    assert r_blocked.status_code == 429
    assert "Retry-After" in r_blocked.headers
    retry_sec = int(r_blocked.headers["Retry-After"])
    assert 1 <= retry_sec <= 60


def test_auth_logout_and_me(client: TestClient, test_env):
    """POST /api/auth/logout и GET /api/auth/me."""
    session_factory, _ = test_env
    create_session(
        session_factory,
        role="biologist",
        client_type="cli",
        external_id="me-user",
        login="Мария",
    )
    hdrs = auth_headers(client_type="cli", external_id="me-user")

    # GET /api/auth/me
    r_me = client.get("/api/auth/me", headers=hdrs)
    assert r_me.status_code == 200
    data = r_me.json()
    assert data["login"] == "Мария"
    assert data["role"] == "biologist"
    assert data["authorized_at"].endswith("Z")
    assert data["last_active_at"].endswith("Z")

    # POST /api/auth/logout
    r_logout = client.post("/api/auth/logout", headers=hdrs)
    assert r_logout.status_code == 200
    assert r_logout.json() == {"ok": True}

    # Повторный GET /api/auth/me без сессии -> 401
    r_after = client.get("/api/auth/me", headers=hdrs)
    assert r_after.status_code == 401


# ---------------- 2. Установки (installations) ----------------


def test_installations_public_and_role_projections(client: TestClient, test_env):
    """GET /api/installations и last_area_display для ролей Гость, Biologist, Developer."""
    session_factory, _ = test_env
    db = session_factory()

    inst1 = Installation(
        name="Установка 1",
        culture_name="Салат",
        light_gpio_pin=17,
        flash_gpio_pin=27,
        aruco_size_mm=50.0,
        is_archived=False,
    )
    inst2_archived = Installation(
        name="Архивная установка",
        culture_name="Мята",
        light_gpio_pin=18,
        flash_gpio_pin=28,
        is_archived=True,
    )
    db.add_all([inst1, inst2_archived])
    db.commit()

    cam1 = Camera(
        hardware_id="cam_hw_1",
        auto_name="Digma Cam",
        installation_id=inst1.id,
        is_online=True,
    )
    db.add(cam1)
    db.commit()

    # Добавляем плановый снимок с маркером и замером для расчета площади
    cap_time = datetime.now(UTC) - timedelta(hours=1)
    cap = Capture(
        installation_id=inst1.id,
        camera_id=cam1.id,
        captured_at=cap_time,
        is_manual=False,
        aruco_area_px=1000.0,
        raw_image_path="media/1/raw_1.jpg",
        annotated_image_path="media/1/ann_1.jpg",
    )
    db.add(cap)
    db.flush()

    meas = PlantMeasurement(capture_id=cap.id, plant_index=0, area_px=2000.0)
    db.add(meas)
    db.commit()
    inst1_id = inst1.id
    db.close()

    # 1. Запрос Гостя (без заголовков)
    r_guest = client.get("/api/installations")
    assert r_guest.status_code == 200
    items_guest = r_guest.json()
    assert len(items_guest) == 1  # по умолчанию архивная скрыта
    g1 = items_guest[0]
    assert g1["id"] == inst1_id
    assert g1["name"] == "Установка 1"
    # Площадь: 2000 * (50/10)^2 / 1000 = 2000 * 25 / 1000 = 50.0 см²
    assert g1["last_area_display"] == "50.0 см²"
    # У Гостя нет пинов и камер
    assert "light_gpio_pin" not in g1
    assert "cameras" not in g1
    assert "aruco_size_mm" not in g1

    # Запрос Гостя с include_archived=true: по критерию этапа 8.1 Гость всегда получает только активные
    r_guest_all = client.get("/api/installations?include_archived=true")
    assert len(r_guest_all.json()) == 1

    # 2. Запрос Biologist
    create_session(session_factory, role="biologist", external_id="bio-usr")
    r_bio = client.get(
        f"/api/installations/{inst1_id}",
        headers=auth_headers(external_id="bio-usr"),
    )
    assert r_bio.status_code == 200
    b_data = r_bio.json()
    assert b_data["aruco_size_mm"] == 50.0
    assert "created_at" in b_data
    assert len(b_data["cameras"]) == 1
    assert b_data["cameras"][0]["auto_name"] == "Digma Cam"
    # У Biologist нет hardware_id и пинов
    assert "hardware_id" not in b_data["cameras"][0]
    assert "light_gpio_pin" not in b_data

    # 3. Запрос Developer
    create_session(session_factory, role="developer", external_id="dev-usr")
    r_dev = client.get(
        f"/api/installations/{inst1_id}",
        headers=auth_headers(external_id="dev-usr"),
    )
    assert r_dev.status_code == 200
    d_data = r_dev.json()
    assert d_data["light_gpio_pin"] == 17
    assert d_data["flash_gpio_pin"] == 27
    assert d_data["cameras"][0]["hardware_id"] == "cam_hw_1"


def test_installation_crud_and_pin_validations(client: TestClient, test_env):
    """POST, PATCH, ARCHIVE, DELETE для installations."""
    session_factory, _ = test_env
    create_session(session_factory, role="developer", external_id="dev-user")
    create_session(session_factory, role="biologist", external_id="bio-user")
    dev_hdrs = auth_headers(external_id="dev-user")
    bio_hdrs = auth_headers(external_id="bio-user")

    # 1. Создание первой установки
    r_create = client.post(
        "/api/installations",
        headers=dev_hdrs,
        json={
            "name": "Новая Установка",
            "culture_name": "Томат",
            "light_gpio_pin": 10,
            "flash_gpio_pin": 11,
            "aruco_size_mm": 40.0,
        },
    )
    assert r_create.status_code == 201
    inst_id = r_create.json()["id"]

    # 2. Попытка создания с тем же именем -> 409
    r_dup_name = client.post(
        "/api/installations",
        headers=dev_hdrs,
        json={
            "name": "Новая Установка",
            "culture_name": "Перец",
            "light_gpio_pin": 20,
            "flash_gpio_pin": 21,
        },
    )
    assert r_dup_name.status_code == 409

    # 3. Совпадение пинов света и вспышки внутри одной установки разрешено (201) с предупреждением (Этап 8.1)
    r_same_pins = client.post(
        "/api/installations",
        headers=dev_hdrs,
        json={
            "name": "Другая-1",
            "culture_name": "Перец",
            "light_gpio_pin": 21,
            "flash_gpio_pin": 21,
        },
    )
    assert r_same_pins.status_code == 201
    assert any("совпадают" in w for w in r_same_pins.json().get("warnings", []))

    # Использование пина другой установки разрешено (201) с предупреждением (Этап 7.4)
    r_shared_pin = client.post(
        "/api/installations",
        headers=dev_hdrs,
        json={
            "name": "Другая",
            "culture_name": "Перец",
            "light_gpio_pin": 11,  # занят как flash в первой установке
            "flash_gpio_pin": 21,
        },
    )
    assert r_shared_pin.status_code == 201
    assert any(
        "⚠️ Пин 11 уже занят" in w for w in r_shared_pin.json().get("warnings", [])
    )

    # 4. PATCH: Biologist правит только name и culture_name
    r_bio_ok = client.patch(
        f"/api/installations/{inst_id}",
        headers=bio_hdrs,
        json={"name": "Имя от Биолога", "culture_name": "Базилик"},
    )
    assert r_bio_ok.status_code == 200
    assert r_bio_ok.json()["name"] == "Имя от Биолога"

    # Biologist пытается изменить пины -> 403
    r_bio_forbidden = client.patch(
        f"/api/installations/{inst_id}",
        headers=bio_hdrs,
        json={"light_gpio_pin": 12},
    )
    assert r_bio_forbidden.status_code == 403

    # 5. Привязка камеры и архивация (камеры должны отвязаться)
    db = session_factory()
    cam = Camera(
        hardware_id="cam_bind_test",
        auto_name="Cam",
        installation_id=inst_id,
        is_online=True,
    )
    db.add(cam)
    db.commit()
    cam_id = cam.id
    db.close()

    r_arch = client.post(f"/api/installations/{inst_id}/archive", headers=dev_hdrs)
    assert r_arch.status_code == 200
    assert r_arch.json()["is_archived"] is True

    db = session_factory()
    cam_db = db.get(Camera, cam_id)
    assert cam_db.installation_id is None
    db.close()

    # 6. DELETE: удаление установки без снимков -> 204
    r_del = client.delete(f"/api/installations/{inst_id}", headers=dev_hdrs)
    assert r_del.status_code == 204

    # Удаление установки со снимками -> 409
    db = session_factory()
    inst_with_cap = Installation(
        name="Inst With Cap",
        culture_name="C",
        light_gpio_pin=10,
        flash_gpio_pin=11,
    )
    db.add(inst_with_cap)
    db.commit()
    cap = Capture(
        installation_id=inst_with_cap.id,
        captured_at=datetime.now(UTC),
        is_manual=False,
        raw_image_path="1",
        annotated_image_path="2",
    )
    db.add(cap)
    db.commit()
    inst_cap_id = inst_with_cap.id
    db.close()

    r_del_blocked = client.delete(f"/api/installations/{inst_cap_id}", headers=dev_hdrs)
    assert r_del_blocked.status_code == 409
    assert "У установки есть снимки" in r_del_blocked.json()["detail"]


# ---------------- 3. Камеры (cameras) ----------------


def test_cameras_list_and_bind(client: TestClient, test_env):
    """GET /api/cameras и POST /api/cameras/{id}/bind."""
    session_factory, _ = test_env
    create_session(session_factory, role="developer", external_id="dev-usr")
    dev_hdrs = auth_headers(external_id="dev-usr")

    db = session_factory()
    inst = Installation(
        name="Установка Камер",
        culture_name="Салат",
        light_gpio_pin=14,
        flash_gpio_pin=15,
        is_archived=False,
    )
    archived_inst = Installation(
        name="Архив",
        culture_name="Салат",
        light_gpio_pin=24,
        flash_gpio_pin=25,
        is_archived=True,
    )
    db.add_all([inst, archived_inst])
    db.commit()

    cam = Camera(
        hardware_id="cam_bind_hw",
        auto_name="Camera 1",
        installation_id=None,
        is_online=True,
    )
    db.add(cam)
    db.commit()
    inst_id = inst.id
    arch_id = archived_inst.id
    cam_id = cam.id
    db.close()

    # 1. GET /api/cameras
    r_list = client.get("/api/cameras", headers=dev_hdrs)
    assert r_list.status_code == 200
    c_data = r_list.json()
    assert len(c_data) == 1
    assert c_data[0]["installation_id"] is None
    assert c_data[0]["installation_name"] is None

    # 2. Привязка к активной установке
    r_bind = client.post(
        f"/api/cameras/{cam_id}/bind",
        headers=dev_hdrs,
        json={"installation_id": inst_id},
    )
    assert r_bind.status_code == 200
    assert r_bind.json()["installation_id"] == inst_id
    assert r_bind.json()["installation_name"] == "Установка Камер"

    # 3. Попытка привязать к архивной установке -> 409
    r_arch_bind = client.post(
        f"/api/cameras/{cam_id}/bind",
        headers=dev_hdrs,
        json={"installation_id": arch_id},
    )
    assert r_arch_bind.status_code == 409

    # 4. Отвязка камеры (installation_id: null) -> 200
    r_unbind = client.post(
        f"/api/cameras/{cam_id}/bind",
        headers=dev_hdrs,
        json={"installation_id": None},
    )
    assert r_unbind.status_code == 200
    assert r_unbind.json()["installation_id"] is None


# ---------------- 4. Версия и система (system) ----------------


def test_system_endpoints(client: TestClient, test_env):
    """GET /api/version, GET /api/system/status, PATCH /api/system/settings."""
    session_factory, _ = test_env
    create_session(session_factory, role="developer", external_id="dev-usr")
    dev_hdrs = auth_headers(external_id="dev-usr")

    # 1. GET /api/version доступен Гостю без заголовков
    r_ver = client.get("/api/version")
    assert r_ver.status_code == 200
    assert "server_version" in r_ver.json()
    assert r_ver.json()["server_version"].startswith("0.")

    # 2. GET /api/system/status
    db = session_factory()
    db.add(
        SystemSetting(
            key="rpi_last_telemetry",
            value=json.dumps({"test": "data"}),
            updated_at=datetime.now(UTC) - timedelta(seconds=10),
        )
    )
    db.commit()
    db.close()

    r_status = client.get("/api/system/status", headers=dev_hdrs)
    assert r_status.status_code == 200
    status_data = r_status.json()
    assert status_data["rpi_connection"] == "online"
    assert status_data["settings"]["capture_interval_hours"] == 1
    assert status_data["settings"]["debug_mode"] is False

    # 3. PATCH /api/system/settings: смена интервала добавляет warning
    r_patch = client.patch(
        "/api/system/settings",
        headers=dev_hdrs,
        json={"capture_interval_hours": 3, "debug_mode": True},
    )
    assert r_patch.status_code == 200
    patch_data = r_patch.json()
    assert patch_data["capture_interval_hours"] == 3
    assert patch_data["debug_mode"] is True
    assert "warning" in patch_data
    assert "неравномерным" in patch_data["warning"]

    # 4. PATCH с невалидным дробным интервалом -> 422
    r_patch_invalid = client.patch(
        "/api/system/settings",
        headers=dev_hdrs,
        json={"capture_interval_hours": 1.5},
    )
    assert r_patch_invalid.status_code == 422


def test_system_settings_capture_delay_sec(client: TestClient, test_env):
    """Проверка чтения и изменения capture_delay_sec в system_settings."""
    session_factory, _ = test_env
    create_session(session_factory, role="developer", external_id="dev-usr-delay")
    dev_hdrs = auth_headers(external_id="dev-usr-delay")

    # 1. По умолчанию в статусе capture_delay_sec == 1.5
    r_status = client.get("/api/system/status", headers=dev_hdrs)
    assert r_status.status_code == 200
    assert r_status.json()["settings"]["capture_delay_sec"] == 1.5

    # 2. Изменение capture_delay_sec (валидный диапазон 0.0..30.0)
    r_patch = client.patch(
        "/api/system/settings",
        headers=dev_hdrs,
        json={"capture_delay_sec": 3.5},
    )
    assert r_patch.status_code == 200
    assert r_patch.json()["capture_delay_sec"] == 3.5

    # 3. Невалидные значения (<0.0 или >30.0) -> 422
    r_bad_low = client.patch(
        "/api/system/settings",
        headers=dev_hdrs,
        json={"capture_delay_sec": -1.0},
    )
    assert r_bad_low.status_code == 422

    r_bad_high = client.patch(
        "/api/system/settings",
        headers=dev_hdrs,
        json={"capture_delay_sec": 35.0},
    )
    assert r_bad_high.status_code == 422


def test_installation_capture_delay_sec_crud(client: TestClient, test_env):
    """Проверка установки capture_delay_sec при создании, редактировании и чтении."""
    session_factory, _ = test_env
    create_session(session_factory, role="developer", external_id="dev-usr-inst")
    dev_hdrs = auth_headers(external_id="dev-usr-inst")
    create_session(session_factory, role="biologist", external_id="bio-usr-inst")
    bio_hdrs = auth_headers(external_id="bio-usr-inst")
    guest_hdrs = {}

    # 1. Создание с capture_delay_sec
    r_create = client.post(
        "/api/installations",
        headers=dev_hdrs,
        json={
            "name": "Инст с задержкой",
            "culture_name": "Базилик",
            "light_gpio_pin": 11,
            "flash_gpio_pin": 12,
            "capture_delay_sec": 2.5,
        },
    )
    assert r_create.status_code == 201
    inst_data = r_create.json()
    assert inst_data["capture_delay_sec"] == 2.5
    inst_id = inst_data["id"]

    # 2. Чтение карточки биологом -> capture_delay_sec видно
    r_get_bio = client.get(f"/api/installations/{inst_id}", headers=bio_hdrs)
    assert r_get_bio.status_code == 200
    assert r_get_bio.json()["capture_delay_sec"] == 2.5

    # Чтение гостем -> capture_delay_sec скрыто
    r_get_guest = client.get(f"/api/installations/{inst_id}", headers=guest_hdrs)
    assert r_get_guest.status_code == 200
    assert "capture_delay_sec" not in r_get_guest.json()

    # 3. Изменение биологом capture_delay_sec
    r_patch_bio = client.patch(
        f"/api/installations/{inst_id}",
        headers=bio_hdrs,
        json={"capture_delay_sec": 5.0},
    )
    assert r_patch_bio.status_code == 200
    assert r_patch_bio.json()["capture_delay_sec"] == 5.0

    # 4. Сброс capture_delay_sec в null (использовать общую)
    r_patch_null = client.patch(
        f"/api/installations/{inst_id}",
        headers=bio_hdrs,
        json={"capture_delay_sec": None},
    )
    assert r_patch_null.status_code == 200
    assert r_patch_null.json()["capture_delay_sec"] is None


def test_delete_archived_installation_with_captures_and_files(
    client: TestClient, test_env
):
    """Критерий 9: удаление архивной установки с кадрами разрешено, активной — запрещено (409)."""
    session_factory, _ = test_env
    create_session(session_factory, role="developer", external_id="dev-usr-del")
    dev_hdrs = auth_headers(external_id="dev-usr-del")
    db = session_factory()

    # Создаем установку с камерой и кадром
    inst = Installation(
        name="Inst for Delete",
        culture_name="Салат",
        light_gpio_pin=13,
        flash_gpio_pin=14,
        is_archived=False,
    )
    db.add(inst)
    db.commit()

    cam = Camera(
        hardware_id="cam_del_test",
        auto_name="Cam Del Test",
        installation_id=inst.id,
        is_online=True,
    )
    db.add(cam)
    db.commit()

    cap = Capture(
        installation_id=inst.id,
        camera_id=cam.id,
        captured_at=datetime.now(UTC),
        is_manual=False,
        raw_image_path=f"media/{inst.id}/raw_del.jpg",
        annotated_image_path=f"media/{inst.id}/ann_del.jpg",
    )
    db.add(cap)
    db.commit()
    inst_id = inst.id
    cam_id = cam.id

    # Создаем имитацию файлов на диске
    from server.api.edge import get_media_dir

    inst_media_dir = get_media_dir() / str(inst_id)
    inst_media_dir.mkdir(parents=True, exist_ok=True)
    (inst_media_dir / "raw_del.jpg").write_bytes(b"raw-data")
    (inst_media_dir / "ann_del.jpg").write_bytes(b"ann-data")

    # 1. Попытка удалить активную установку с кадрами -> 409 Conflict
    r_del_active = client.delete(f"/api/installations/{inst_id}", headers=dev_hdrs)
    assert r_del_active.status_code == 409
    assert "У установки есть снимки" in r_del_active.json()["detail"]

    # 2. Переводим в архив
    r_archive = client.post(f"/api/installations/{inst_id}/archive", headers=dev_hdrs)
    assert r_archive.status_code == 200

    # 3. Удаление архивной установки с кадрами -> 204 No Content
    r_del_archived = client.delete(f"/api/installations/{inst_id}", headers=dev_hdrs)
    assert r_del_archived.status_code == 204

    # 4. Проверяем БД: установка и кадры удалены, камера отвязана
    db.expire_all()
    assert db.get(Installation, inst_id) is None
    assert db.query(Capture).filter(Capture.installation_id == inst_id).count() == 0
    refreshed_cam = db.get(Camera, cam_id)
    assert refreshed_cam is not None
    assert refreshed_cam.installation_id is None

    # 5. Проверяем файловую систему: каталог удалён
    assert not inst_media_dir.exists()
    db.close()
