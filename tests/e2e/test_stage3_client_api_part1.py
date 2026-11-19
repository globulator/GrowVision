from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

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
BIO_PASS = "bio-secret"
DEV_PASS = "dev-secret"


@pytest.fixture
def client_api_test_env(tmp_path: Path, monkeypatch):
    """Изолированное окружение для тестирования Client API."""
    settings = get_settings()
    monkeypatch.setattr(settings, "client_api_key", CLIENT_KEY)
    monkeypatch.setattr(settings, "biologist_password", BIO_PASS)
    monkeypatch.setattr(settings, "developer_password", DEV_PASS)

    # Изолированная SQLite БД
    db_file = tmp_path / "client_qa_test.db"
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
    login_limiter.clear()

    # Изоляция media
    tmp_media_dir = tmp_path / "media"
    tmp_media_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("server.api.edge.get_media_dir", lambda: tmp_media_dir)

    real_media_root = Path(__file__).resolve().parent.parent.parent / "server" / "media"

    client = TestClient(app)

    yield client, session_factory

    app.dependency_overrides.clear()
    login_limiter.clear()
    engine.dispose()

    # Проверка чистоты рабочей директории media
    non_gitkeep = [p for p in real_media_root.iterdir() if p.name != ".gitkeep"]
    assert not non_gitkeep, f"Файлы в рабочем server/media/: {non_gitkeep}"


def _make_headers(role_user: tuple[str, str] | None = None) -> dict[str, str]:
    """Формирует заголовки клиента: (client_type, external_id) или без сессии."""
    headers = {"X-Client-Key": CLIENT_KEY}
    if role_user:
        headers["X-Client-Type"] = role_user[0]
        headers["X-External-Id"] = role_user[1]
    return headers


# ==============================================================================
# 1. Аутентификация, Rate Limit (429) и сессии (Критерий 2)
# ==============================================================================
def test_qa_auth_login_rate_limiting_and_logout(client_api_test_env):
    client, _ = client_api_test_env

    # 1. Попытка входа без X-Client-Key -> 401
    r_no_key = client.post(
        "/api/auth/login",
        json={
            "client_type": "telegram",
            "external_id": "111",
            "login": "Tester",
            "password": BIO_PASS,
        },
    )
    assert r_no_key.status_code == 401

    # 2. Неверный пароль -> 401
    r_bad_pw = client.post(
        "/api/auth/login",
        headers=_make_headers(),
        json={
            "client_type": "telegram",
            "external_id": "111",
            "login": "Tester",
            "password": "wrong-password",
        },
    )
    assert r_bad_pw.status_code == 401

    # 3. Проверка Rate Limit: 5 попыток разрешены, 6-я -> 429 с Retry-After
    login_limiter.clear()
    for _ in range(5):
        r = client.post(
            "/api/auth/login",
            headers=_make_headers(),
            json={
                "client_type": "telegram",
                "external_id": "rate_user",
                "login": "Rate Tester",
                "password": "wrong",
            },
        )
        assert r.status_code == 401

    # 6-я попытка -> 429
    r_limit = client.post(
        "/api/auth/login",
        headers=_make_headers(),
        json={
            "client_type": "telegram",
            "external_id": "rate_user",
            "login": "Rate Tester",
            "password": BIO_PASS,
        },
    )
    assert r_limit.status_code == 429
    assert "Retry-After" in r_limit.headers
    assert "Превышен лимит попыток входа" in r_limit.json()["detail"]

    # Другой пользователь не заблокирован
    r_other = client.post(
        "/api/auth/login",
        headers=_make_headers(),
        json={
            "client_type": "cli",
            "external_id": "cli_other",
            "login": "Other User",
            "password": BIO_PASS,
        },
    )
    assert r_other.status_code == 200
    assert r_other.json()["role"] == "biologist"

    # 4. Проверка /api/auth/me и /api/auth/logout
    cli_headers = _make_headers(("cli", "cli_other"))
    r_me = client.get("/api/auth/me", headers=cli_headers)
    assert r_me.status_code == 200
    assert r_me.json()["login"] == "Other User"
    assert r_me.json()["role"] == "biologist"

    # Logout
    r_logout = client.post("/api/auth/logout", headers=cli_headers)
    assert r_logout.status_code == 200
    assert r_logout.json() == {"ok": True}

    # После logout сессия не существует -> 401
    r_me_after = client.get("/api/auth/me", headers=cli_headers)
    assert r_me_after.status_code == 401


# ==============================================================================
# 2. Ролевая модель и проекции полей установок (Критерии 1, 3)
# ==============================================================================
def test_qa_installations_role_projections_and_last_area(client_api_test_env):
    client, session_factory = client_api_test_env
    db = session_factory()

    # 1. Создаем пользователей в БД
    user_bio = User(
        client_type="telegram",
        external_id="bio_tg",
        login="Биолог Анна",
        role="biologist",
    )
    user_dev = User(
        client_type="cli",
        external_id="dev_cli",
        login="Разработчик Макс",
        role="developer",
    )
    db.add_all([user_bio, user_dev])
    db.commit()

    # 2. Создаем установку с камерой и кадрами
    inst = Installation(
        name="Гидропоника 1",
        culture_name="Базилик Зеленый",
        light_gpio_pin=17,
        flash_gpio_pin=27,
        aruco_size_mm=40.0,
        is_archived=False,
    )
    db.add(inst)
    db.commit()

    cam = Camera(
        hardware_id="cam_hw_01",
        auto_name="HD WebCam 1",
        installation_id=inst.id,
        is_online=True,
    )
    db.add(cam)
    db.commit()

    # 1-й кадр: плановый, замер 12000 px, маркер 4000 px -> площадь 12000*(40/10)^2/4000 = 48.0 см²
    cap1 = Capture(
        installation_id=inst.id,
        camera_id=cam.id,
        captured_at=datetime(2026, 11, 20, 10, 0, 0, tzinfo=UTC),
        is_manual=False,
        aruco_area_px=4000.0,
        raw_image_path="media/1/raw_1.jpg",
        annotated_image_path="media/1/ann_1.jpg",
    )
    db.add(cap1)
    db.commit()
    db.add(PlantMeasurement(capture_id=cap1.id, plant_index=0, area_px=12000.0))

    # 2-й кадр: РУЧНОЙ (is_manual=True), не должен менять last_area_display!
    cap_manual = Capture(
        installation_id=inst.id,
        camera_id=cam.id,
        captured_at=datetime(2026, 11, 20, 10, 30, 0, tzinfo=UTC),
        is_manual=True,
        aruco_area_px=4000.0,
        raw_image_path="media/1/raw_m.jpg",
        annotated_image_path="media/1/ann_m.jpg",
    )
    db.add(cap_manual)
    db.commit()
    db.add(PlantMeasurement(capture_id=cap_manual.id, plant_index=0, area_px=99999.0))
    db.commit()
    inst_id = inst.id
    db.close()

    bio_headers = _make_headers(("telegram", "bio_tg"))
    dev_headers = _make_headers(("cli", "dev_cli"))

    # --- А. Запрос от роли Гость (без авторизации) ---
    r_guest = client.get(f"/api/installations/{inst_id}")
    assert r_guest.status_code == 200
    guest_data = r_guest.json()
    assert guest_data["name"] == "Гидропоника 1"
    assert guest_data["culture_name"] == "Базилик Зеленый"
    assert guest_data["last_area_display"] == "48.0 см²"
    # Скрытые для гостя поля:
    assert "light_gpio_pin" not in guest_data
    assert "flash_gpio_pin" not in guest_data
    assert "aruco_size_mm" not in guest_data
    assert "cameras" not in guest_data

    # --- Б. Запрос от роли Biologist ---
    r_bio = client.get(f"/api/installations/{inst_id}", headers=bio_headers)
    assert r_bio.status_code == 200
    bio_data = r_bio.json()
    assert bio_data["aruco_size_mm"] == 40.0
    assert "created_at" in bio_data
    assert len(bio_data["cameras"]) == 1
    assert bio_data["cameras"][0]["auto_name"] == "HD WebCam 1"
    # Скрытые для биолога аппаратные пины и hardware_id камеры:
    assert "light_gpio_pin" not in bio_data
    assert "flash_gpio_pin" not in bio_data
    assert "hardware_id" not in bio_data["cameras"][0]

    # --- В. Запрос от роли Developer ---
    r_dev = client.get(f"/api/installations/{inst_id}", headers=dev_headers)
    assert r_dev.status_code == 200
    dev_data = r_dev.json()
    assert dev_data["light_gpio_pin"] == 17
    assert dev_data["flash_gpio_pin"] == 27
    assert dev_data["cameras"][0]["hardware_id"] == "cam_hw_01"


# ==============================================================================
# 3. CRUD установок, права на изменение, архивация и удаление (Критерий 3)
# ==============================================================================
def test_qa_installation_crud_permissions_and_constraints(client_api_test_env):
    client, session_factory = client_api_test_env
    db = session_factory()

    user_bio = User(
        client_type="telegram",
        external_id="bio_tg_crud",
        login="Биолог",
        role="biologist",
    )
    user_dev = User(
        client_type="cli",
        external_id="dev_cli_crud",
        login="Разработчик",
        role="developer",
    )
    db.add_all([user_bio, user_dev])
    db.commit()
    db.close()

    bio_headers = _make_headers(("telegram", "bio_tg_crud"))
    dev_headers = _make_headers(("cli", "dev_cli_crud"))

    # 1. Создание установки: Biologist не имеет прав -> 403
    r_create_bio = client.post(
        "/api/installations",
        headers=bio_headers,
        json={
            "name": "Новая",
            "culture_name": "Томат",
            "light_gpio_pin": 12,
            "flash_gpio_pin": 13,
        },
    )
    assert r_create_bio.status_code == 403

    # Developer успешно создает установку -> 201
    r_create_dev = client.post(
        "/api/installations",
        headers=dev_headers,
        json={
            "name": "Новая Установка",
            "culture_name": "Томат Черри",
            "light_gpio_pin": 12,
            "flash_gpio_pin": 13,
            "aruco_size_mm": 50.0,
        },
    )
    assert r_create_dev.status_code == 201
    new_inst_id = r_create_dev.json()["id"]

    # Конфликт пина света (уже занят пином 12) -> 409
    r_dup_pin = client.post(
        "/api/installations",
        headers=dev_headers,
        json={
            "name": "Другая",
            "culture_name": "Огурец",
            "light_gpio_pin": 12,
            "flash_gpio_pin": 14,
        },
    )
    assert r_dup_pin.status_code == 409
    assert "Пин света" in r_dup_pin.json()["detail"]

    # 2. PATCH: Biologist может менять name и culture_name
    r_patch_bio_ok = client.patch(
        f"/api/installations/{new_inst_id}",
        headers=bio_headers,
        json={"name": "Новое Имя Биолога", "culture_name": "Шпинат"},
    )
    assert r_patch_bio_ok.status_code == 200
    assert r_patch_bio_ok.json()["name"] == "Новое Имя Биолога"

    # Biologist пытается изменить light_gpio_pin -> 403 Forbidden!
    r_patch_bio_forbidden = client.patch(
        f"/api/installations/{new_inst_id}",
        headers=bio_headers,
        json={"light_gpio_pin": 20},
    )
    assert r_patch_bio_forbidden.status_code == 403

    # 3. Архивация: Developer архивирует установку -> 200
    # Сначала привяжем камеру
    db = session_factory()
    cam = Camera(
        hardware_id="cam_bind_test", auto_name="Camera 1", installation_id=new_inst_id
    )
    db.add(cam)
    db.commit()
    cam_id = cam.id
    db.close()

    r_archive = client.post(
        f"/api/installations/{new_inst_id}/archive", headers=dev_headers
    )
    assert r_archive.status_code == 200
    assert r_archive.json()["is_archived"] is True

    # Проверяем, что камера отвязалась
    db = session_factory()
    assert db.get(Camera, cam_id).installation_id is None
    db.close()

    # 4. Удаление пустой установки без кадров -> 204 No Content
    r_del = client.delete(f"/api/installations/{new_inst_id}", headers=dev_headers)
    assert r_del.status_code == 204

    # 5. Удаление установки С КАДРАМИ -> 409
    # Создаем установку с кадром
    r_inst_with_cap = client.post(
        "/api/installations",
        headers=dev_headers,
        json={
            "name": "Установка со снимком",
            "culture_name": "Руккола",
            "light_gpio_pin": 22,
            "flash_gpio_pin": 23,
        },
    )
    inst_with_cap_id = r_inst_with_cap.json()["id"]

    db = session_factory()
    cap = Capture(
        installation_id=inst_with_cap_id,
        camera_id=None,
        captured_at=datetime.now(UTC),
        is_manual=False,
        raw_image_path="test.jpg",
        annotated_image_path="test.jpg",
    )
    db.add(cap)
    db.commit()
    db.close()

    r_del_conflict = client.delete(
        f"/api/installations/{inst_with_cap_id}", headers=dev_headers
    )
    assert r_del_conflict.status_code == 409
    assert (
        "У установки есть снимки, используйте архивацию"
        in r_del_conflict.json()["detail"]
    )


# ==============================================================================
# 4. Камеры (bind / unbind) (Критерий 4)
# ==============================================================================
def test_qa_cameras_bind_and_permissions(client_api_test_env):
    client, session_factory = client_api_test_env
    db = session_factory()

    user_dev = User(
        client_type="cli",
        external_id="dev_cameras",
        login="Dev",
        role="developer",
    )
    inst_active = Installation(
        name="Inst Active",
        culture_name="Салат",
        light_gpio_pin=15,
        flash_gpio_pin=16,
        is_archived=False,
    )
    inst_archived = Installation(
        name="Inst Arch",
        culture_name="Салат",
        light_gpio_pin=25,
        flash_gpio_pin=26,
        is_archived=True,
    )
    cam = Camera(
        hardware_id="cam_bind_qa",
        auto_name="QA Camera",
        installation_id=None,
    )
    db.add_all([user_dev, inst_active, inst_archived, cam])
    db.commit()
    cam_id = cam.id
    active_id = inst_active.id
    arch_id = inst_archived.id
    db.close()

    dev_headers = _make_headers(("cli", "dev_cameras"))

    # 1. Привязка камеры к активной установке -> 200
    r_bind = client.post(
        f"/api/cameras/{cam_id}/bind",
        headers=dev_headers,
        json={"installation_id": active_id},
    )
    assert r_bind.status_code == 200
    assert r_bind.json()["installation_id"] == active_id

    # 2. Попытка привязать к архивной установке -> 409 Conflict
    r_bind_arch = client.post(
        f"/api/cameras/{cam_id}/bind",
        headers=dev_headers,
        json={"installation_id": arch_id},
    )
    assert r_bind_arch.status_code == 409
    assert "архивн" in r_bind_arch.json()["detail"]

    # 3. Отвязка камеры (installation_id: null) -> 200
    r_unbind = client.post(
        f"/api/cameras/{cam_id}/bind",
        headers=dev_headers,
        json={"installation_id": None},
    )
    assert r_unbind.status_code == 200
    assert r_unbind.json()["installation_id"] is None


# ==============================================================================
# 5. Системные настройки и версия (Критерий 5)
# ==============================================================================
def test_qa_system_endpoints(client_api_test_env):
    client, session_factory = client_api_test_env
    db = session_factory()

    user_dev = User(
        client_type="cli",
        external_id="dev_sys",
        login="Dev System",
        role="developer",
    )
    db.add(user_dev)
    db.add(SystemSetting(key="capture_interval_hours", value="1"))
    db.add(SystemSetting(key="debug_mode", value="false"))
    db.commit()
    db.close()

    dev_headers = _make_headers(("cli", "dev_sys"))

    # 1. GET /api/version доступен без авторизации
    r_ver = client.get("/api/version")
    assert r_ver.status_code == 200
    assert "server_version" in r_ver.json()

    # 2. GET /api/system/status: Developer -> 200
    r_status = client.get("/api/system/status", headers=dev_headers)
    assert r_status.status_code == 200
    st_data = r_status.json()
    assert "rpi_connection" in st_data
    assert "settings" in st_data
    assert st_data["settings"]["capture_interval_hours"] == 1

    # 3. PATCH /api/system/settings
    r_patch_set = client.patch(
        "/api/system/settings",
        headers=dev_headers,
        json={"capture_interval_hours": 4, "debug_mode": True},
    )
    assert r_patch_set.status_code == 200
    assert r_patch_set.json()["capture_interval_hours"] == 4
    assert r_patch_set.json()["debug_mode"] is True

    # 4. Валидация настроек: дробный интервал или интервал < 1 -> 422
    r_bad_interval = client.patch(
        "/api/system/settings",
        headers=dev_headers,
        json={"capture_interval_hours": 0},
    )
    assert r_bad_interval.status_code == 422
