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
from server.db.models import InviteCode, User
from server.main import app

DEV_PASSWORD = "developer-strong-password"
BIO_PASSWORD = "biologist-strong-password"
CLIENT_KEY = "test-client-secret-key"


@pytest.fixture
def test_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "test_stage8.db"
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
    r_js = client.get("/js/app.js")
    assert r_js.status_code == 200
    assert "javascript" in r_js.headers.get("content-type", "")

    # API по-прежнему отвечает JSON, не перехватывается статикой
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


def test_web_auth_flow_biologist_and_developer(test_env, client: TestClient):
    """Полный цикл веб-авторизации: needs_login, сессия с кукой, доступ к API, повторный вход, кик и логаут."""
    session_factory, _ = test_env

    # 1. Гость: без куки GET /api/auth/web/me возвращает роль guest
    r_guest = client.get("/api/auth/web/me")
    assert r_guest.status_code == 200
    assert r_guest.json()["role"] == "guest"
    assert r_guest.json()["login"] is None

    # Создадим создателя инвайта (Developer) в БД
    db: Session = session_factory()
    now = datetime.now(UTC)
    creator = User(
        client_type="cli",
        external_id="dev_admin",
        login="Админ",
        role="developer",
        is_active=True,
    )
    db.add(creator)
    db.commit()
    db.refresh(creator)

    inv = InviteCode(
        code="WEBTEST1",
        role="biologist",
        created_by_user_id=creator.id,
        created_at=now,
        expires_at=now + timedelta(minutes=10),
        is_revoked=False,
    )
    db.add(inv)
    db.commit()
    creator_id = creator.id
    db.close()

    client_id = "web_browser_client_1"

    # 2. Неверный пароль -> 401
    r_bad = client.post(
        "/api/auth/web/login",
        json={"password": "WRONG_PASSWORD", "client_id": client_id},
    )
    assert r_bad.status_code == 401

    # 3. Первый вход биолога по коду БЕЗ логина -> needs_login (код НЕ сжигается)
    r_needs = client.post(
        "/api/auth/web/login",
        json={"password": "WEBTEST1", "client_id": client_id},
    )
    assert r_needs.status_code == 200
    data_needs = r_needs.json()
    assert data_needs.get("status") == "needs_login"
    assert data_needs.get("role") == "biologist"
    assert data_needs.get("client_id") == client_id

    # Проверяем, что код не использован
    db = session_factory()
    inv_check = db.query(InviteCode).filter(InviteCode.code == "WEBTEST1").first()
    assert inv_check.used_at is None
    db.close()

    # 4. Завершение первого входа с логином -> 200 OK, код сожжен, кука gv_session установлена
    r_login = client.post(
        "/api/auth/web/login",
        json={
            "password": "WEBTEST1",
            "client_id": client_id,
            "login": "Мария Склодовская",
        },
    )
    assert r_login.status_code == 200
    assert r_login.json()["role"] == "biologist"
    assert r_login.json()["login"] == "Мария Склодовская"
    assert "gv_session" in client.cookies

    # Проверяем, что код помечен как использованный
    db = session_factory()
    inv_check = db.query(InviteCode).filter(InviteCode.code == "WEBTEST1").first()
    assert inv_check.used_at is not None
    user_bio = (
        db.query(User)
        .filter(User.client_type == "web", User.external_id == client_id)
        .first()
    )
    assert user_bio is not None
    assert user_bio.login == "Мария Склодовская"
    bio_user_id = user_bio.id
    db.close()

    # 5. GET /api/auth/web/me с кукой -> роль biologist
    r_me = client.get("/api/auth/web/me")
    assert r_me.status_code == 200
    assert r_me.json()["role"] == "biologist"
    assert r_me.json()["login"] == "Мария Склодовская"

    # 6. Изменение имени через PATCH /api/auth/me (доступно с кукой без заголовков X-Client-Key!)
    r_rename = client.patch(
        "/api/auth/me",
        json={"login": "Мария Кюри"},
    )
    assert r_rename.status_code == 200
    assert r_rename.json()["login"] == "Мария Кюри"

    # 7. Проверка прав: биолог пытается создать установку -> 403 Forbidden
    r_create_inst_bio = client.post(
        "/api/installations",
        json={
            "name": "Тест Установка",
            "culture_name": "Базилик",
            "light_gpio_pin": 10,
            "flash_gpio_pin": 11,
        },
    )
    assert r_create_inst_bio.status_code == 403

    # 8. Логаут биолога через POST /api/auth/web/logout
    r_logout = client.post("/api/auth/web/logout")
    assert r_logout.status_code == 200
    assert r_logout.json()["ok"] is True

    # Теперь кука очищена / сессия не активна
    r_me_after = client.get("/api/auth/web/me")
    assert r_me_after.status_code == 200
    assert r_me_after.json()["role"] == "guest"

    # 9. Повторный вход тем же браузером (client_id): создаем новый инвайт-код
    db = session_factory()
    inv2 = InviteCode(
        code="WEBTEST2",
        role="biologist",
        created_by_user_id=creator_id,
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
        is_revoked=False,
    )
    db.add(inv2)
    db.commit()
    db.close()

    # Вход БЕЗ передачи логина: имя «Мария Кюри» подтягивается автоматически!
    r_relogin = client.post(
        "/api/auth/web/login",
        json={"password": "WEBTEST2", "client_id": client_id},
    )
    assert r_relogin.status_code == 200
    assert r_relogin.json().get("status") is None
    assert r_relogin.json()["login"] == "Мария Кюри"
    assert r_relogin.json()["role"] == "biologist"

    # 10. Вход разработчика из другого браузера
    client_dev = TestClient(app)
    r_dev_login = client_dev.post(
        "/api/auth/web/login",
        json={
            "password": DEV_PASSWORD,
            "client_id": "web_dev_browser",
            "login": "Главный Разработчик",
        },
    )
    assert r_dev_login.status_code == 200
    assert r_dev_login.json()["role"] == "developer"

    # Разработчик может создавать установки
    r_create_inst_dev = client_dev.post(
        "/api/installations",
        json={
            "name": "Тест Установка Веб",
            "culture_name": "Томат",
            "light_gpio_pin": 14,
            "flash_gpio_pin": 15,
        },
    )
    assert r_create_inst_dev.status_code == 201

    # Разработчик видит веб-сессии в GET /api/auth/sessions
    r_sessions = client_dev.get("/api/auth/sessions")
    assert r_sessions.status_code == 200
    sess_list = r_sessions.json()
    assert any(
        s["client_type"] == "web" and s["login"] == "Мария Кюри" for s in sess_list
    )

    # 11. Разработчик принудительно завершает (кикает) сессию биолога
    r_kick = client_dev.post(f"/api/auth/sessions/{bio_user_id}/terminate")
    assert r_kick.status_code == 200

    # Запрос от биолога с прежней кукой теперь возвращает 401
    r_bio_after_kick = client.patch(
        "/api/auth/me",
        json={"login": "Попытка Изменения"},
    )
    assert r_bio_after_kick.status_code == 401
