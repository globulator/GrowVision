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
from server.db.models import InviteCode, User
from server.main import app

CLIENT_KEY = "test-client-secret-key"
DEV_PASS = "dev-secret-password-123"


@pytest.fixture
def test_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Изолирует окружение: временную БД, секреты, ключи и лимиты."""
    db_file = tmp_path / "test_auth_invites.db"
    db_url = f"sqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    settings = get_settings()
    monkeypatch.setattr(settings, "client_api_key", CLIENT_KEY)
    monkeypatch.setattr(settings, "developer_password", DEV_PASS)
    monkeypatch.setattr(settings, "biologist_password", "bio-pass-legacy")

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
    engine.dispose()


def auth_headers(role: str = "developer", external_id: str = "dev-1") -> dict[str, str]:
    return {
        "X-Client-Key": CLIENT_KEY,
        "X-Client-Type": "cli" if role == "developer" else "telegram",
        "X-External-Id": external_id,
    }


def test_invite_codes_crud_and_permissions(test_env):
    """Создание, получение списка и отзыв одноразовых инвайт-кодов."""
    client = TestClient(app)
    _ = test_env

    # 1. Авторизуем developer
    r_dev = client.post(
        "/api/auth/login",
        headers={"X-Client-Key": CLIENT_KEY},
        json={
            "client_type": "cli",
            "external_id": "dev-admin",
            "login": "Главный Разработчик",
            "password": DEV_PASS,
        },
    )
    assert r_dev.status_code == 200
    dev_h = auth_headers("developer", "dev-admin")

    # 2. Создание инвайта
    r_create = client.post("/api/auth/invites", headers=dev_h)
    assert r_create.status_code == 200
    invite_data = r_create.json()
    assert len(invite_data["code"]) == 6
    assert invite_data["role"] == "biologist"
    invite_id = invite_data["id"]
    code = invite_data["code"]

    # 3. Список инвайтов
    r_list = client.post("/api/auth/invites", headers=dev_h)
    assert r_list.status_code == 200

    r_get = client.get("/api/auth/invites", headers=dev_h)
    assert r_get.status_code == 200
    invites = r_get.json()
    assert len(invites) == 2
    codes = [i["code"] for i in invites]
    assert code in codes

    # 4. Отзыв инвайта
    r_revoke = client.delete(f"/api/auth/invites/{invite_id}", headers=dev_h)
    assert r_revoke.status_code == 200
    assert r_revoke.json() == {"ok": True}

    # Проверяем, что отозванный код не выводится в активных
    r_get_after = client.get("/api/auth/invites", headers=dev_h)
    active_codes = [i["code"] for i in r_get_after.json()]
    assert code not in active_codes


def test_invite_login_lifecycle_and_needs_login(test_env):
    """Сценарий входа по одноразовому коду: needs_login, активация, сгорание, подтягивание имени."""
    client = TestClient(app)
    session_factory, _ = test_env

    # 1. Создаем developer и инвайт-код
    client.post(
        "/api/auth/login",
        headers={"X-Client-Key": CLIENT_KEY},
        json={
            "client_type": "cli",
            "external_id": "dev-admin",
            "login": "Админ",
            "password": DEV_PASS,
        },
    )
    dev_h = auth_headers("developer", "dev-admin")
    r_inv = client.post("/api/auth/invites", headers=dev_h)
    code = r_inv.json()["code"]

    # 2. Первый вход нового пользователя без указания login -> needs_login (код не сжигается)
    r_first_try = client.post(
        "/api/auth/login",
        headers={"X-Client-Key": CLIENT_KEY},
        json={
            "client_type": "telegram",
            "external_id": "tg-bio-1",
            "password": code,
            # login не передан
        },
    )
    assert r_first_try.status_code == 200
    assert r_first_try.json() == {"status": "needs_login", "role": "biologist"}

    # Проверяем в БД, что код всё ещё активен и не использован
    with session_factory() as db:
        inv_db = db.query(InviteCode).filter(InviteCode.code == code).first()
        assert inv_db.used_at is None

    # 3. Вход с указанием имени и фамилии
    r_login_ok = client.post(
        "/api/auth/login",
        headers={"X-Client-Key": CLIENT_KEY},
        json={
            "client_type": "telegram",
            "external_id": "tg-bio-1",
            "login": "Андрей Бармин",
            "password": code,
        },
    )
    assert r_login_ok.status_code == 200
    assert r_login_ok.json() == {"role": "biologist", "login": "Андрей Бармин"}

    # Проверяем в БД, что код теперь использован
    with session_factory() as db:
        inv_db = db.query(InviteCode).filter(InviteCode.code == code).first()
        assert inv_db.used_at is not None
        user_db = db.query(User).filter(User.external_id == "tg-bio-1").first()
        assert user_db.login == "Андрей Бармин"
        assert user_db.is_active is True
        assert inv_db.used_by_user_id == user_db.id

    # 4. Попытка использовать тот же код другим пользователем -> 401
    r_used_fail = client.post(
        "/api/auth/login",
        headers={"X-Client-Key": CLIENT_KEY},
        json={
            "client_type": "telegram",
            "external_id": "tg-bio-2",
            "login": "Второй",
            "password": code,
        },
    )
    assert r_used_fail.status_code == 401

    # 5. Выход пользователя (logout)
    bio_h = {
        "X-Client-Key": CLIENT_KEY,
        "X-Client-Type": "telegram",
        "X-External-Id": "tg-bio-1",
    }
    r_logout = client.post("/api/auth/logout", headers=bio_h)
    assert r_logout.status_code == 200

    # Проверяем в БД: запись пользователя НЕ удалена, но is_active = False
    with session_factory() as db:
        user_db = db.query(User).filter(User.external_id == "tg-bio-1").first()
        assert user_db is not None
        assert user_db.is_active is False
        assert user_db.logged_out_at is not None

    # 6. Запрос к /api/auth/me после logout -> 401
    r_me_after = client.get("/api/auth/me", headers=bio_h)
    assert r_me_after.status_code == 401

    # 7. Повторный вход того же пользователя по новому инвайту БЕЗ передачи login
    r_inv2 = client.post("/api/auth/invites", headers=dev_h)
    code2 = r_inv2.json()["code"]

    r_login2 = client.post(
        "/api/auth/login",
        headers={"X-Client-Key": CLIENT_KEY},
        json={
            "client_type": "telegram",
            "external_id": "tg-bio-1",
            "password": code2,
            # login не передан!
        },
    )
    assert r_login2.status_code == 200
    # Имя подтянулось автоматически из базы!
    assert r_login2.json() == {"role": "biologist", "login": "Андрей Бармин"}

    # Сессия снова активна
    r_me_again = client.get("/api/auth/me", headers=bio_h)
    assert r_me_again.status_code == 200
    assert r_me_again.json()["login"] == "Андрей Бармин"


def test_sessions_list_and_termination(test_env):
    """Список всех сессий и принудительное завершение (кик) сессии."""
    client = TestClient(app)
    _ = test_env

    # 1. Логиним developer
    client.post(
        "/api/auth/login",
        headers={"X-Client-Key": CLIENT_KEY},
        json={
            "client_type": "cli",
            "external_id": "dev-root",
            "login": "Root",
            "password": DEV_PASS,
        },
    )
    dev_h = auth_headers("developer", "dev-root")

    # 2. Создаем инвайт и логиним биолога
    r_inv = client.post("/api/auth/invites", headers=dev_h)
    code = r_inv.json()["code"]
    client.post(
        "/api/auth/login",
        headers={"X-Client-Key": CLIENT_KEY},
        json={
            "client_type": "telegram",
            "external_id": "tg-user-42",
            "login": "Иван Биолог",
            "password": code,
        },
    )

    # 3. Список сессий
    r_sessions = client.get("/api/auth/sessions", headers=dev_h)
    assert r_sessions.status_code == 200
    sessions = r_sessions.json()
    assert len(sessions) >= 2
    bio_session = next(s for s in sessions if s["external_id"] == "tg-user-42")
    assert bio_session["login"] == "Иван Биолог"
    assert bio_session["is_active"] is True
    bio_id = bio_session["id"]

    # 4. Принудительно завершаем сессию биолога (кик)
    r_kick = client.post(f"/api/auth/sessions/{bio_id}/terminate", headers=dev_h)
    assert r_kick.status_code == 200
    assert r_kick.json() == {"ok": True}

    # 5. Проверяем, что биолог теперь не авторизован (401)
    bio_h = {
        "X-Client-Key": CLIENT_KEY,
        "X-Client-Type": "telegram",
        "X-External-Id": "tg-user-42",
    }
    r_me = client.get("/api/auth/me", headers=bio_h)
    assert r_me.status_code == 401

    # В списке сессий статус стал is_active = False
    r_sessions_after = client.get("/api/auth/sessions", headers=dev_h)
    bio_session_after = next(s for s in r_sessions_after.json() if s["id"] == bio_id)
    assert bio_session_after["is_active"] is False
    assert bio_session_after["logged_out_at"] is not None
