# server/tests/test_stage8_web_auth.py
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from server.api.deps import get_db
from server.config import get_settings
from server.core.security import verify_password
from server.core.state import app_state
from server.db.database import Base
from server.db.models import (
    Camera,
    Capture,
    Installation,
    InviteCode,
    TelegramContact,
    User,
)
from server.main import app

DEV_PASSWORD = "developer-strong-password"
BIO_PASSWORD = "biologist-strong-password"
CLIENT_KEY = "test-client-secret-key"


@pytest.fixture
def test_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "test_stage8_rebuild.db"
    db_url = f"sqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    settings = get_settings()
    monkeypatch.setattr(settings, "client_api_key", CLIENT_KEY)
    monkeypatch.setattr(settings, "developer_password", DEV_PASSWORD)
    monkeypatch.setattr(settings, "biologist_password", BIO_PASSWORD)

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


def test_web_static_files_served(client: TestClient):
    """Проверка корректной раздачи статики clients/web/ без конфликта с API."""
    # Главная страница
    r_index = client.get("/")
    assert r_index.status_code == 200
    assert "GrowVision" in r_index.text
    assert "text/html" in r_index.headers.get("content-type", "")

    # Стили
    r_css = client.get("/style.css")
    assert r_css.status_code == 200
    assert "text/css" in r_css.headers.get("content-type", "")

    # JS модули
    for js_file in [
        "app.js",
        "api.js",
        "auth.js",
        "dialogs.js",
        "units.js",
        "gallery.js",
        "test_snap.js",
        "gif_encoder.js",
    ]:
        r_js = client.get(f"/js/{js_file}")
        assert r_js.status_code == 200
        assert "javascript" in r_js.headers.get("content-type", "")

    # Фон
    r_bg = client.get("/assets/background.svg")
    assert r_bg.status_code == 200
    assert "svg" in r_bg.headers.get("content-type", "")

    # API не перехватывается статикой
    r_api = client.get("/api/version")
    assert r_api.status_code == 200
    assert "server_version" in r_api.json()


def test_no_x_client_key_in_web_files():
    """Проверка отсутствия утечки секретного заголовка X-Client-Key в веб-файлах."""
    web_dir = Path(__file__).resolve().parent.parent.parent / "clients" / "web"
    assert web_dir.is_dir()

    for file_path in web_dir.rglob("*"):
        if file_path.is_file() and file_path.suffix in (".js", ".html", ".css"):
            content = file_path.read_text(encoding="utf-8").lower()
            assert "x-client-key" not in content, (
                f"Обнаружен заголовок X-Client-Key в файле {file_path.name}"
            )


def test_telegram_contact_sync_and_pending_codes_queue(test_env, client: TestClient):
    """Тест синхронизации контактов бота и очереди кодов подтверждения."""
    # 1. Синхронизация контакта без X-Client-Key -> 401
    r_unauth = client.post(
        "/api/auth/telegram/contact",
        json={"telegram_id": 111222, "username": "ivan_bio"},
    )
    assert r_unauth.status_code == 401

    # 2. Успешная синхронизация контакта под ключом бота
    headers = {"X-Client-Key": CLIENT_KEY}
    r_sync = client.post(
        "/api/auth/telegram/contact",
        headers=headers,
        json={
            "telegram_id": 111222,
            "username": "@Ivan_Bio",  # проверка нормализации
            "first_name": "Иван",
            "last_name": "Биолог",
        },
    )
    assert r_sync.status_code == 200
    assert r_sync.json()["ok"] is True

    # Проверяем в БД нормализацию
    session_factory, _ = test_env
    db: Session = session_factory()
    contact = (
        db.query(TelegramContact).filter(TelegramContact.telegram_id == 111222).first()
    )
    assert contact is not None
    assert contact.username == "ivan_bio"
    db.close()

    # 3. Очередь кодов пока пуста
    r_codes = client.get("/api/auth/web/pending-codes", headers=headers)
    assert r_codes.status_code == 200
    assert r_codes.json() == []


def test_web_telegram_registration_and_login_full_flow(test_env, client: TestClient):
    """Полный цикл: регистрация через Telegram (3 шага), вход, cookie, сессия и логаут."""
    session_factory, _ = test_env
    headers_bot = {"X-Client-Key": CLIENT_KEY}

    # 1. Бот передает контакт пользователя в Telegram
    tg_id = 987654321
    r_sync = client.post(
        "/api/auth/telegram/contact",
        headers=headers_bot,
        json={
            "telegram_id": tg_id,
            "username": "anna_grower",
            "first_name": "Анна",
        },
    )
    assert r_sync.status_code == 200

    # Создадим инвайт-код для биолога
    db: Session = session_factory()
    dev_user = User(
        client_type="cli",
        external_id="dev_1",
        login="Разработчик",
        role="developer",
        is_active=True,
    )
    db.add(dev_user)
    db.commit()

    now = datetime.now(UTC)
    inv = InviteCode(
        code="BIOINV1",
        role="biologist",
        created_by_user_id=dev_user.id,
        created_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    db.add(inv)
    db.commit()
    db.close()

    # 2. Неизвестный пользователь в Шаге 1 -> 400 с понятной подсказкой
    r_unknown = client.post(
        "/api/auth/web/register/start",
        json={"telegram": "@unknown_user_123"},
    )
    assert r_unknown.status_code == 400
    assert "нажмите /start" in r_unknown.json()["detail"]

    # 3. Шаг 1: старт регистрации по @username
    r_step1 = client.post(
        "/api/auth/web/register/start",
        json={"telegram": "@Anna_Grower"},
    )
    assert r_step1.status_code == 200
    data_step1 = r_step1.json()
    assert data_step1["status"] == "code_sent"
    assert data_step1["telegram_id"] == tg_id

    # 4. Бот забирает код из очереди
    r_pending = client.get("/api/auth/web/pending-codes", headers=headers_bot)
    assert r_pending.status_code == 200
    codes = r_pending.json()
    assert len(codes) == 1
    code_item = codes[0]
    assert code_item["telegram_id"] == tg_id
    code_id = code_item["id"]
    code_val = code_item["code"]
    assert len(code_val) == 6

    # Бот подтверждает доставку
    r_ack = client.post(
        f"/api/auth/web/pending-codes/{code_id}/ack", headers=headers_bot
    )
    assert r_ack.status_code == 200
    # В очереди больше нет неотправленных кодов
    assert client.get("/api/auth/web/pending-codes", headers=headers_bot).json() == []

    # 5. Шаг 2: проверка кода (неверный код -> 400, верный -> 200)
    r_bad_code = client.post(
        "/api/auth/web/register/verify",
        json={"telegram_id": tg_id, "code": "000000"},
    )
    assert r_bad_code.status_code == 400

    r_verify = client.post(
        "/api/auth/web/register/verify",
        json={"telegram_id": tg_id, "code": code_val},
    )
    assert r_verify.status_code == 200
    assert r_verify.json()["status"] == "verified"

    # 6. Шаг 3: завершение регистрации
    # Неверный инвайт -> 400
    r_bad_inv = client.post(
        "/api/auth/web/register/complete",
        json={
            "telegram_id": tg_id,
            "verification_code": code_val,
            "full_name": "Анна Гровер",
            "access_code": "WRONG_INV",
            "password": "secret_password",
        },
    )
    assert r_bad_inv.status_code == 400

    # Успешная регистрация с верным инвайтом
    r_reg = client.post(
        "/api/auth/web/register/complete",
        json={
            "telegram_id": tg_id,
            "verification_code": code_val,
            "full_name": "Анна Гровер",
            "access_code": "BIOINV1",
            "password": "secret_password",
        },
    )
    assert r_reg.status_code == 200
    reg_data = r_reg.json()
    assert reg_data["role"] == "biologist"
    assert reg_data["login"] == "Анна Гровер"
    assert "gv_session" in client.cookies

    # Проверяем, что инвайт сожжен
    db = session_factory()
    inv_check = db.query(InviteCode).filter(InviteCode.code == "BIOINV1").first()
    assert inv_check.used_at is not None
    # Проверяем пользователя в БД: external_id равен числовому telegram_id, password_hash сохранен
    user_db = (
        db.query(User)
        .filter(User.client_type == "web", User.external_id == str(tg_id))
        .first()
    )
    assert user_db is not None
    assert user_db.login == "Анна Гровер"
    assert user_db.role == "biologist"
    assert verify_password("secret_password", user_db.password_hash)
    db.close()

    # 7. GET /api/auth/web/me возвращает авторизованного биолога
    r_me = client.get("/api/auth/web/me")
    assert r_me.status_code == 200
    assert r_me.json()["authenticated"] is True
    assert r_me.json()["role"] == "biologist"
    assert r_me.json()["login"] == "Анна Гровер"

    # 8. Выход POST /api/auth/web/logout
    r_out = client.post("/api/auth/web/logout")
    assert r_out.status_code == 200
    r_me_after = client.get("/api/auth/web/me")
    assert r_me_after.json()["authenticated"] is False
    assert r_me_after.json()["role"] == "guest"

    # 9. Вход POST /api/auth/web/login по @username и паролю
    r_login_un = client.post(
        "/api/auth/web/login",
        json={"telegram": "@anna_grower", "password": "secret_password"},
    )
    assert r_login_un.status_code == 200
    assert r_login_un.json()["role"] == "biologist"
    assert "gv_session" in client.cookies

    # 10. Вход POST /api/auth/web/login по числовому ID и паролю
    client.cookies.clear()
    r_login_id = client.post(
        "/api/auth/web/login",
        json={"telegram": str(tg_id), "password": "secret_password"},
    )
    assert r_login_id.status_code == 200
    assert r_login_id.json()["role"] == "biologist"


def test_installations_fields_for_web(test_env, client: TestClient):
    """Проверка, что GET /api/installations отдает frames_count и last_frame_url."""
    session_factory, _ = test_env
    db: Session = session_factory()
    inst = Installation(
        name="Тестовая Установка",
        culture_name="Базилик",
        light_gpio_pin=17,
        flash_gpio_pin=27,
        is_archived=False,
    )
    db.add(inst)
    db.commit()

    cam = Camera(
        hardware_id="cam_hw_1",
        auto_name="Камера 1",
        installation_id=inst.id,
    )
    db.add(cam)
    db.commit()

    cap = Capture(
        installation_id=inst.id,
        camera_id=cam.id,
        captured_at=datetime.now(UTC),
        raw_image_path=f"media/{inst.id}/raw_1.jpg",
        annotated_image_path=f"media/{inst.id}/ann_1.jpg",
        is_manual=False,
    )
    db.add(cap)
    db.commit()
    inst_id = inst.id
    db.close()

    r = client.get("/api/installations")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    unit = data[0]
    assert unit["id"] == inst_id
    assert unit["frames_count"] == 1
    assert unit["last_frame_url"] == f"/media/{inst_id}/raw_1.jpg"
