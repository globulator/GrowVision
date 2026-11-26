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


def test_telegram_contact_sync_and_lookup(test_env, client: TestClient):
    """Тест синхронизации контактов бота, lookup и отсутствия очереди кодов."""
    r_unauth = client.post(
        "/api/auth/telegram/contact",
        json={"telegram_id": 111222, "username": "ivan_bio"},
    )
    assert r_unauth.status_code == 401

    headers = {"X-Client-Key": CLIENT_KEY}
    r_sync = client.post(
        "/api/auth/telegram/contact",
        headers=headers,
        json={
            "telegram_id": 111222,
            "username": "@Ivan_Bio",
            "first_name": "Иван",
            "last_name": "Биолог",
        },
    )
    assert r_sync.status_code == 200
    assert r_sync.json()["ok"] is True

    session_factory, _ = test_env
    db: Session = session_factory()
    contact = (
        db.query(TelegramContact).filter(TelegramContact.telegram_id == 111222).first()
    )
    assert contact is not None
    assert contact.username == "ivan_bio"
    db.close()

    # Поиск по @username и по числовому ID
    r_lookup_un = client.get("/api/auth/web/telegram-lookup?telegram=@Ivan_Bio")
    assert r_lookup_un.status_code == 200
    lu = r_lookup_un.json()
    assert lu["found"] is True
    assert lu["telegram_id"] == 111222
    assert lu["username"] == "ivan_bio"
    assert lu["first_name"] == "Иван"
    assert lu["last_name"] == "Биолог"
    assert lu["recommended_name"] == "Иван Биолог"
    assert lu["telegram_url"] == "https://t.me/ivan_bio"

    r_lookup_id = client.get("/api/auth/web/telegram-lookup?telegram=111222")
    assert r_lookup_id.status_code == 200
    assert r_lookup_id.json()["found"] is True

    r_lookup_miss = client.get("/api/auth/web/telegram-lookup?telegram=@no_such_user")
    assert r_lookup_miss.status_code == 200
    assert r_lookup_miss.json()["found"] is False

    # Эндпоинты кодов верификации полностью удалены
    assert client.get("/api/auth/web/pending-codes", headers=headers).status_code == 404


def test_web_telegram_registration_and_login_full_flow(test_env, client: TestClient):
    """Полный цикл: регистрация в 1 шаг без кодов, профиль с telegram_url, логаут и вход."""
    session_factory, _ = test_env
    headers_bot = {"X-Client-Key": CLIENT_KEY}

    tg_id = 987654321
    r_sync = client.post(
        "/api/auth/telegram/contact",
        headers=headers_bot,
        json={
            "telegram_id": tg_id,
            "username": "anna_grower",
            "first_name": "Анна",
            "last_name": "Садовая",
        },
    )
    assert r_sync.status_code == 200

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

    # Lookup предлагает имя/фамилию из БД
    r_lu = client.get("/api/auth/web/telegram-lookup?telegram=@Anna_Grower")
    assert r_lu.status_code == 200
    assert r_lu.json()["found"] is True
    assert r_lu.json()["first_name"] == "Анна"
    assert r_lu.json()["last_name"] == "Садовая"

    # Неверный инвайт -> 400
    r_bad_inv = client.post(
        "/api/auth/web/register/complete",
        json={
            "telegram": "@Anna_Grower",
            "first_name": "Анна",
            "last_name": "Гровер",
            "access_code": "WRONG_INV",
        },
    )
    assert r_bad_inv.status_code == 400

    # Успешная регистрация в 1 шаг с отредактированной фамилией
    r_reg = client.post(
        "/api/auth/web/register/complete",
        json={
            "telegram": "@Anna_Grower",
            "first_name": "Анна",
            "last_name": "Гровер",
            "access_code": "BIOINV1",
        },
    )
    assert r_reg.status_code == 200
    reg_data = r_reg.json()
    assert reg_data["role"] == "biologist"
    assert reg_data["login"] == "Анна Гровер"
    assert "gv_session" in client.cookies

    db = session_factory()
    inv_check = db.query(InviteCode).filter(InviteCode.code == "BIOINV1").first()
    assert inv_check.used_at is not None
    user_db = (
        db.query(User)
        .filter(User.client_type == "web", User.external_id == str(tg_id))
        .first()
    )
    assert user_db is not None
    assert user_db.login == "Анна Гровер"
    assert user_db.role == "biologist"
    assert user_db.is_active is True
    db.close()

    # GET /api/auth/web/me и /api/auth/me возвращают telegram_url
    r_me = client.get("/api/auth/web/me")
    assert r_me.status_code == 200
    assert r_me.json()["authenticated"] is True
    assert r_me.json()["role"] == "biologist"
    assert r_me.json()["login"] == "Анна Гровер"
    assert r_me.json()["telegram_url"] == "https://t.me/anna_grower"

    r_me_api = client.get("/api/auth/me")
    assert r_me_api.status_code == 200
    assert r_me_api.json()["telegram_url"] == "https://t.me/anna_grower"

    # Выход
    r_out = client.post("/api/auth/web/logout")
    assert r_out.status_code == 200
    assert client.get("/api/auth/web/me").json()["authenticated"] is False

    # Вход по @username и инвайту
    db2 = session_factory()
    dev_u = db2.query(User).filter(User.role == "developer").first()
    inv_login = InviteCode(
        code="BIOINV2",
        role="biologist",
        created_by_user_id=dev_u.id,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
        is_revoked=False,
    )
    db2.add(inv_login)
    db2.commit()
    db2.close()

    r_login_un = client.post(
        "/api/auth/web/login",
        json={"telegram": "@anna_grower", "code": "BIOINV2"},
    )
    assert r_login_un.status_code == 200
    assert r_login_un.json()["role"] == "biologist"
    assert "gv_session" in client.cookies

    # Вход разработчика по мастер-паролю
    client.cookies.clear()
    r_login_dev = client.post(
        "/api/auth/web/login",
        json={"telegram": str(tg_id), "code": DEV_PASSWORD},
    )
    assert r_login_dev.status_code == 200
    assert r_login_dev.json()["role"] == "developer"


def test_web_registration_without_telegram_and_with_numeric_tg_url(
    test_env, client: TestClient
):
    """Регистрация без Telegram (web:<uuid>) и с числовым ID без username (tg://user?id=...)."""
    # 1. Без Telegram
    r_no_tg = client.post(
        "/api/auth/web/register",
        json={
            "telegram": "",
            "first_name": "Мария",
            "last_name": "Петрова",
            "access_code": DEV_PASSWORD,
        },
    )
    assert r_no_tg.status_code == 200
    assert r_no_tg.json()["login"] == "Мария Петрова"
    me_no_tg = client.get("/api/auth/me").json()
    assert me_no_tg["external_id"].startswith("web:")
    assert me_no_tg["telegram_url"] is None

    # 2. Числовой telegram_id, отсутствующий в TelegramContact -> found=false, tg://user?id=...
    r_lu = client.get("/api/auth/web/telegram-lookup?telegram=555777999")
    assert r_lu.status_code == 200
    assert r_lu.json()["found"] is False

    client.cookies.clear()
    r_num = client.post(
        "/api/auth/web/register/complete",
        json={
            "telegram": "555777999",
            "first_name": "Пётр",
            "last_name": "Сидоров",
            "access_code": DEV_PASSWORD,
        },
    )
    assert r_num.status_code == 200
    me_num = client.get("/api/auth/me").json()
    assert me_num["telegram_url"] == "tg://user?id=555777999"

    # Проверка telegram_url в списке сессий
    sessions = client.get("/api/auth/sessions").json()
    urls = {s["login"]: s["telegram_url"] for s in sessions}
    assert urls["Мария Петрова"] is None
    assert urls["Пётр Сидоров"] == "tg://user?id=555777999"


def test_telegram_duplicate_conflict_409(test_env, client: TestClient):
    """Защита 409 от регистрации дубликата Telegram и привязки занятого Telegram в PATCH /api/auth/me."""
    # Регистрируем первого пользователя с @bio_one
    r1 = client.post(
        "/api/auth/web/register/complete",
        json={
            "telegram": "@bio_one",
            "first_name": "Первый",
            "access_code": DEV_PASSWORD,
        },
    )
    assert r1.status_code == 200

    # Попытка зарегистрировать второго с тем же @bio_one -> 409
    client.cookies.clear()
    r_dup = client.post(
        "/api/auth/web/register/complete",
        json={
            "telegram": "@Bio_One",
            "first_name": "Второй",
            "access_code": DEV_PASSWORD,
        },
    )
    assert r_dup.status_code == 409

    # Регистрируем второго без Telegram -> 200
    r2 = client.post(
        "/api/auth/web/register/complete",
        json={
            "telegram": None,
            "first_name": "Второй",
            "access_code": DEV_PASSWORD,
        },
    )
    assert r2.status_code == 200

    # Попытка в PATCH /api/auth/me привязать занятый @bio_one -> 409
    r_patch_dup = client.patch(
        "/api/auth/me",
        json={"first_name": "Второй", "telegram": "@bio_one"},
    )
    assert r_patch_dup.status_code == 409

    # Привязка свободного @bio_two -> 200 и обновление telegram_url
    r_patch_ok = client.patch(
        "/api/auth/me",
        json={"first_name": "Второй", "last_name": "Биолог", "telegram": "@bio_two"},
    )
    assert r_patch_ok.status_code == 200
    assert r_patch_ok.json()["login"] == "Второй Биолог"
    assert r_patch_ok.json()["telegram_url"] == "https://t.me/bio_two"


def test_web_ui_no_verification_steps_and_has_lookup():
    """Проверка разметки и JS Web UI: нет кодов подтверждения, есть lookup и rel=noopener noreferrer."""
    from pathlib import Path

    web_dir = Path(__file__).resolve().parents[2] / "clients" / "web"
    html = (web_dir / "index.html").read_text(encoding="utf-8")
    api_js = (web_dir / "js" / "api.js").read_text(encoding="utf-8")
    auth_js = (web_dir / "js" / "auth.js").read_text(encoding="utf-8")
    profile_js = (web_dir / "js" / "profile.js").read_text(encoding="utf-8")

    for forbidden in (
        "reg-step-1",
        "reg-step-2",
        "reg-step-3",
        "reg-resend-btn",
        "telegram_verification_required",
        "registerStart",
        "registerVerify",
        "getCodeStatus",
    ):
        assert forbidden not in html
        assert forbidden not in api_js
        assert forbidden not in auth_js

    assert "reg-telegram" in html
    assert "reg-first-name" in html
    assert "reg-last-name" in html
    assert "reg-access-code" in html
    assert "telegram-lookup" in api_js
    assert "telegramLookup" in auth_js
    assert "noopener noreferrer" in profile_js


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
