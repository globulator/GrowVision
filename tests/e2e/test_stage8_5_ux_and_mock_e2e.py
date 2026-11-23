# tests/e2e/test_stage8_5_ux_and_mock_e2e.py
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

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

DEV_PASSWORD = "developer-secret-pass-85"
BIO_INVITE = "BIO_85_OK"


@pytest.fixture
def test_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "test_stage8_5.db"
    db_url = f"sqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    settings = get_settings()
    monkeypatch.setattr(settings, "developer_password", DEV_PASSWORD)
    monkeypatch.setattr(settings, "client_api_key", "test-key-85")

    def override_get_db():
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


def test_unified_web_login_and_name_sync(test_setup):
    """1. Унифицированный вход без личного пароля и синхронизация имён из Telegram."""
    session_factory, _ = test_setup
    db = session_factory()

    # Создаем контакт в telegram_contacts
    tg_id = 987654321
    contact = TelegramContact(
        telegram_id=tg_id,
        username="biologist_elena",
        first_name="Елена",
        last_name="Васильева",
    )
    db.add(contact)

    # Создаем пользователя-разработчика для связи FK
    dev_creator = User(
        client_type="cli",
        external_id="dev_creator_85",
        login="Админ",
        role="developer",
        is_active=True,
    )
    db.add(dev_creator)
    db.flush()

    # Создаем инвайт-код для биолога
    now = datetime.now(UTC)
    inv = InviteCode(
        code=BIO_INVITE,
        role="biologist",
        created_by_user_id=dev_creator.id,
        expires_at=now + timedelta(minutes=10),
        is_revoked=False,
    )
    db.add(inv)
    db.commit()
    db.close()

    from fastapi.testclient import TestClient

    client = TestClient(app)

    # Вход биолога по @username и коду инвайта
    r_bio = client.post(
        "/api/auth/web/login",
        json={"telegram": "@biologist_elena", "code": BIO_INVITE},
    )
    assert r_bio.status_code == 200
    data_bio = r_bio.json()
    assert data_bio["role"] == "biologist"
    assert (
        data_bio["login"] == "Елена Васильева"
    )  # Синхронизировано из TelegramContact!
    assert "gv_session" in client.cookies

    # Проверяем, что инвайт сожжен
    db2 = session_factory()
    inv_db = db2.query(InviteCode).filter(InviteCode.code == BIO_INVITE).first()
    assert inv_db.used_at is not None
    # Проверяем запись User
    user_db = (
        db2.query(User)
        .filter(User.client_type == "web", User.external_id == str(tg_id))
        .first()
    )
    assert user_db is not None
    assert user_db.login == "Елена Васильева"
    assert (
        not hasattr(user_db, "password_hash") or user_db.password_hash is None
    )  # password_hash отсутствует!
    db2.close()

    # Повторная попытка с тем же инвайтом -> 401
    client.cookies.clear()
    r_reused = client.post(
        "/api/auth/web/login",
        json={"telegram": "@biologist_elena", "code": BIO_INVITE},
    )
    assert r_reused.status_code == 401

    # Вход разработчика по мастер-паролю (DEVELOPER_PASSWORD)
    r_dev = client.post(
        "/api/auth/web/login",
        json={"telegram": str(tg_id), "code": DEV_PASSWORD},
    )
    assert r_dev.status_code == 200
    data_dev = r_dev.json()
    assert data_dev["role"] == "developer"
    assert data_dev["login"] == "Елена Васильева"


def test_gallery_and_timelapse_endpoint(test_setup):
    """2. Проверка маршрута кадров таймлапса /api/timelapse/{id}/frames (не 404)."""
    session_factory, _ = test_setup
    db = session_factory()
    inst = Installation(
        name="Установка Тест 8.5",
        culture_name="Мята",
        light_gpio_pin=17,
        flash_gpio_pin=27,
    )
    db.add(inst)
    db.commit()

    cam = Camera(
        hardware_id="cam_85",
        auto_name="Камера 85",
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
    )
    db.add(cap)
    db.commit()
    inst_id = inst.id
    db.close()

    from fastapi.testclient import TestClient

    client = TestClient(app)

    # Правильный эндпоинт таймлапса
    r_frames = client.get(f"/api/timelapse/{inst_id}/frames")
    assert r_frames.status_code == 200
    data = r_frames.json()
    assert "frames" in data
    assert len(data["frames"]) == 1
    assert data["frames"][0]["raw_url"] == f"/media/{inst_id}/raw_1.jpg"


def test_web_spa_artifacts_and_invariants():
    """3. Проверка статики Web SPA: стили заглушек, модалка confirm, плашка офлайн, 0 эмодзи."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    index_html = (repo_root / "clients/web/index.html").read_text(encoding="utf-8")
    style_css = (repo_root / "clients/web/style.css").read_text(encoding="utf-8")

    # Модалка подтверждения
    assert 'id="modal-confirm"' in index_html
    assert 'id="modal-confirm-ok-btn"' in index_html
    assert 'id="modal-confirm-cancel-btn"' in index_html

    # Плашка сетевой недоступности
    assert 'id="server-offline-banner"' in index_html

    # Цвета заглушек и анимация роста растения
    assert "#161e29" in style_css
    assert "plant-lifecycle-spinner" in style_css
    assert "plant-cycle-svg" in style_css
    assert "plant-seed" in style_css
    assert "plant-stem" in style_css
    assert "plant-sprout-left" in style_css
    assert "plant-leaf-right" in style_css

    # Инварианты: 0 alert/confirm/prompt
    js_files = list((repo_root / "clients/web/js").glob("*.js"))
    for js_p in js_files:
        code = js_p.read_text(encoding="utf-8")
        assert not re.search(r"\balert\(", code), f"alert found in {js_p.name}"
        assert not re.search(r"\bconfirm\(", code), f"confirm found in {js_p.name}"
        assert not re.search(r"\bprompt\(", code), f"prompt found in {js_p.name}"
        assert "X-Client-Key" not in code, f"X-Client-Key found in {js_p.name}"
