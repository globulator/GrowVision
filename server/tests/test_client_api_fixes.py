from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from server.api.deps import get_db
from server.config import get_settings
from server.core.rate_limit import login_limiter
from server.db.database import Base
from server.db.models import Installation
from server.main import app

CLIENT_KEY = "test-client-secret-key"
EDGE_KEY = "test-edge-secret-key"
BIO_PASS = "bio-pass"
DEV_PASS = "dev-pass"


@pytest.fixture
def test_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Изолирует окружение: базу данных, настройки и лимитер."""
    db_file = tmp_path / "test_client_api_fixes.db"
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
    try:
        yield {"session_factory": session_factory}
    finally:
        app.dependency_overrides.clear()
        login_limiter.clear()


def test_version_auth_endpoints(test_env):
    """Критерий 1: GET /api/version и зависимость get_current_user_optional."""
    client = TestClient(app)

    # 1. Без заголовков -> 200 (роль Гость)
    r_no_headers = client.get("/api/version")
    assert r_no_headers.status_code == 200
    assert "server_version" in r_no_headers.json()

    # 2. С верным X-Client-Key -> 200
    r_valid_key = client.get("/api/version", headers={"X-Client-Key": CLIENT_KEY})
    assert r_valid_key.status_code == 200
    assert "server_version" in r_valid_key.json()

    # 3. С неверным X-Client-Key -> 401
    r_invalid_key = client.get(
        "/api/version", headers={"X-Client-Key": "wrong-secret-key"}
    )
    assert r_invalid_key.status_code == 401
    assert r_invalid_key.json() == {"detail": "Неверный X-Client-Key"}


def test_patch_installations_nulls_and_aruco(test_env):
    """Критерий 2: PATCH /api/installations/{id} с null и aruco_size_mm."""
    client = TestClient(app)
    session_factory = test_env["session_factory"]

    # Авторизуем developer
    r_login = client.post(
        "/api/auth/login",
        headers={"X-Client-Key": CLIENT_KEY},
        json={
            "client_type": "cli",
            "external_id": "dev-1",
            "login": "developer",
            "password": DEV_PASS,
        },
    )
    assert r_login.status_code == 200

    headers = {
        "X-Client-Key": CLIENT_KEY,
        "X-Client-Type": "cli",
        "X-External-Id": "dev-1",
    }

    # Создаем установку
    r_create = client.post(
        "/api/installations",
        headers=headers,
        json={
            "name": "Box 1",
            "culture_name": "Tomato",
            "light_gpio_pin": 12,
            "flash_gpio_pin": 13,
            "aruco_size_mm": 50.0,
        },
    )
    assert r_create.status_code == 201
    inst_id = r_create.json()["id"]

    # Передача явного null для обязательных полей -> 422
    for field_name in ("name", "culture_name", "light_gpio_pin", "flash_gpio_pin"):
        r_null = client.patch(
            f"/api/installations/{inst_id}",
            headers=headers,
            json={field_name: None},
        )
        assert r_null.status_code == 422, f"Поле {field_name} должно давать 422 на null"
        assert f"Поле {field_name} не может быть null" in r_null.json()["detail"]

    # aruco_size_mm: null допустим и обнуляет поле в БД
    r_aruco_null = client.patch(
        f"/api/installations/{inst_id}",
        headers=headers,
        json={"aruco_size_mm": None},
    )
    assert r_aruco_null.status_code == 200
    assert r_aruco_null.json()["aruco_size_mm"] is None

    with session_factory() as db:
        inst_db = db.query(Installation).filter(Installation.id == inst_id).first()
        assert inst_db.aruco_size_mm is None

    # aruco_size_mm: число > 0 допустимо
    r_aruco_val = client.patch(
        f"/api/installations/{inst_id}",
        headers=headers,
        json={"aruco_size_mm": 45.0},
    )
    assert r_aruco_val.status_code == 200
    assert r_aruco_val.json()["aruco_size_mm"] == 45.0


def test_patch_archived_installation_pins_conflict(test_env):
    """Критерий 3: PATCH пинов у архивной установки даёт 409 'Установка в архиве'."""
    client = TestClient(app)

    # Авторизуем developer
    client.post(
        "/api/auth/login",
        headers={"X-Client-Key": CLIENT_KEY},
        json={
            "client_type": "cli",
            "external_id": "dev-2",
            "login": "developer",
            "password": DEV_PASS,
        },
    )
    headers = {
        "X-Client-Key": CLIENT_KEY,
        "X-Client-Type": "cli",
        "X-External-Id": "dev-2",
    }

    # Создаем установку
    r_create = client.post(
        "/api/installations",
        headers=headers,
        json={
            "name": "Box 2",
            "culture_name": "Basil",
            "light_gpio_pin": 14,
            "flash_gpio_pin": 15,
        },
    )
    assert r_create.status_code == 201
    inst_id = r_create.json()["id"]

    # Архивируем
    r_arch = client.post(f"/api/installations/{inst_id}/archive", headers=headers)
    assert r_arch.status_code == 200
    assert r_arch.json()["is_archived"] is True

    # Попытка сменить light_gpio_pin -> 409
    r_light = client.patch(
        f"/api/installations/{inst_id}",
        headers=headers,
        json={"light_gpio_pin": 20},
    )
    assert r_light.status_code == 409
    assert r_light.json()["detail"] == "Установка в архиве"

    # Попытка сменить flash_gpio_pin -> 409
    r_flash = client.patch(
        f"/api/installations/{inst_id}",
        headers=headers,
        json={"flash_gpio_pin": 21},
    )
    assert r_flash.status_code == 409
    assert r_flash.json()["detail"] == "Установка в архиве"

    # Смена имени или культуры у архивной установки разрешена
    r_name = client.patch(
        f"/api/installations/{inst_id}",
        headers=headers,
        json={"name": "Archived Box 2 Renamed"},
    )
    assert r_name.status_code == 200
    assert r_name.json()["name"] == "Archived Box 2 Renamed"


def test_validation_empty_strings_and_pin_ranges(test_env):
    """Критерий 4: Валидация strip() строк, BCM GPIO диапазонов 0..53 и login/external_id."""
    client = TestClient(app)

    # 1. Валидация логина: пустые login и external_id (включая пробелы) -> 422
    for bad_val in ("", "   ", "\t  \n"):
        r_bad_login = client.post(
            "/api/auth/login",
            headers={"X-Client-Key": CLIENT_KEY},
            json={
                "client_type": "cli",
                "external_id": "cli-user",
                "login": bad_val,
                "password": DEV_PASS,
            },
        )
        assert r_bad_login.status_code == 422
        assert "login" in r_bad_login.json()["detail"]

        r_bad_ext = client.post(
            "/api/auth/login",
            headers={"X-Client-Key": CLIENT_KEY},
            json={
                "client_type": "cli",
                "external_id": bad_val,
                "login": "developer",
                "password": DEV_PASS,
            },
        )
        assert r_bad_ext.status_code == 422
        assert "external_id" in r_bad_ext.json()["detail"]

    # Авторизуем developer для проверок установок
    client.post(
        "/api/auth/login",
        headers={"X-Client-Key": CLIENT_KEY},
        json={
            "client_type": "cli",
            "external_id": "dev-valid",
            "login": "developer",
            "password": DEV_PASS,
        },
    )
    headers = {
        "X-Client-Key": CLIENT_KEY,
        "X-Client-Type": "cli",
        "X-External-Id": "dev-valid",
    }

    # 2. POST /api/installations: пустые строки name и culture_name -> 422
    for bad_name in ("", "   "):
        r_name = client.post(
            "/api/installations",
            headers=headers,
            json={
                "name": bad_name,
                "culture_name": "Salad",
                "light_gpio_pin": 2,
                "flash_gpio_pin": 3,
            },
        )
        assert r_name.status_code == 422
        assert "name" in r_name.json()["detail"]

    for bad_cult in ("", "   \t"):
        r_cult = client.post(
            "/api/installations",
            headers=headers,
            json={
                "name": "Box Valid Name",
                "culture_name": bad_cult,
                "light_gpio_pin": 2,
                "flash_gpio_pin": 3,
            },
        )
        assert r_cult.status_code == 422
        assert "culture_name" in r_cult.json()["detail"]

    # 3. POST /api/installations: light_gpio_pin и flash_gpio_pin диапазон 0..53 -> 422
    for bad_pin in (-1, 54, 100):
        r_pin_l = client.post(
            "/api/installations",
            headers=headers,
            json={
                "name": "Box Pin",
                "culture_name": "Salad",
                "light_gpio_pin": bad_pin,
                "flash_gpio_pin": 3,
            },
        )
        assert r_pin_l.status_code == 422

        r_pin_f = client.post(
            "/api/installations",
            headers=headers,
            json={
                "name": "Box Pin",
                "culture_name": "Salad",
                "light_gpio_pin": 2,
                "flash_gpio_pin": bad_pin,
            },
        )
        assert r_pin_f.status_code == 422

    # Проверка граничных пинов: 0 и 53 разрешены
    r_boundary = client.post(
        "/api/installations",
        headers=headers,
        json={
            "name": "Box Boundary Pins",
            "culture_name": "Pepper",
            "light_gpio_pin": 0,
            "flash_gpio_pin": 53,
        },
    )
    assert r_boundary.status_code == 201
    inst_boundary_id = r_boundary.json()["id"]

    # 4. PATCH /api/installations/{id}: пустые строки name и culture_name -> 422
    r_patch_empty_name = client.patch(
        f"/api/installations/{inst_boundary_id}",
        headers=headers,
        json={"name": "   "},
    )
    assert r_patch_empty_name.status_code == 422
    assert "name" in r_patch_empty_name.json()["detail"]

    r_patch_bad_pin = client.patch(
        f"/api/installations/{inst_boundary_id}",
        headers=headers,
        json={"light_gpio_pin": 54},
    )
    assert r_patch_bad_pin.status_code == 422


def test_request_validation_error_handler_format(test_env):
    """Критерий 5: Формат ответа RequestValidationError 422 (лаконичный detail без трейсбеков)."""
    client = TestClient(app)

    r = client.post(
        "/api/auth/login",
        headers={"X-Client-Key": CLIENT_KEY},
        json={
            "client_type": "cli",
            "external_id": "   ",
            "login": "developer",
            "password": DEV_PASS,
        },
    )
    assert r.status_code == 422
    data = r.json()
    assert list(data.keys()) == ["detail"]
    detail = data["detail"]
    assert isinstance(detail, str)
    assert "external_id: Поле external_id не может быть пустым" in detail
    assert "Traceback" not in detail
    assert "pydantic.dev" not in detail
    assert ".py" not in detail
    assert "line " not in detail
