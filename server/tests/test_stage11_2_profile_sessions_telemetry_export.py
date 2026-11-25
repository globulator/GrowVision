import csv
import io
import json
import re
import shutil
import subprocess
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
from server.db.models import (
    Camera,
    Capture,
    Installation,
    PlantMeasurement,
    SystemSetting,
    TelegramContact,
    User,
)
from server.main import app

CLIENT_KEY = "test-client-secret-key"
EDGE_KEY = "test-edge-secret-key"
ROOT_DIR = Path(__file__).resolve().parent.parent.parent


@pytest.fixture
def test_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "test_stage11_2.db"
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


def _create_web_user(
    session_factory,
    role: str,
    ext_id: str,
    login: str,
    tg_username: str | None = None,
) -> tuple[int, dict[str, str]]:
    with session_factory() as db:
        now = datetime.now(UTC)
        if tg_username and (
            ext_id.isdigit() or (ext_id.startswith("-") and ext_id[1:].isdigit())
        ):
            db.add(
                TelegramContact(
                    telegram_id=int(ext_id),
                    username=tg_username.lstrip("@").lower(),
                    first_name=login,
                )
            )
        user = User(
            client_type="web",
            external_id=ext_id,
            login=login,
            role=role,
            is_active=True,
            authorized_at=now,
            last_active_at=now,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user.id, {"gv_session": create_session_cookie(user)}


def test_web_profile_me_and_rename(client: TestClient, test_env):
    """GET /api/auth/web/me, GET/PATCH /api/auth/me по cookie gv_session и валидация имени."""
    session_factory, _ = test_env

    # 1. Гость
    r_guest_web = client.get("/api/auth/web/me")
    assert r_guest_web.status_code == 200
    assert r_guest_web.json()["authenticated"] is False
    assert r_guest_web.json()["role"] == "guest"

    assert client.get("/api/auth/me").status_code == 401
    assert client.patch("/api/auth/me", json={"login": "Новое"}).status_code == 401

    # 2. Авторизованный веб-биолог с привязанным TelegramContact
    bio_id, bio_cookies = _create_web_user(
        session_factory,
        role="biologist",
        ext_id="777001",
        login="Алиса Рост",
        tg_username="alice_grow",
    )

    r_web_me = client.get("/api/auth/web/me", cookies=bio_cookies)
    assert r_web_me.status_code == 200
    web_data = r_web_me.json()
    assert web_data["authenticated"] is True
    assert web_data["id"] == bio_id
    assert web_data["login"] == "Алиса Рост"
    assert web_data["role"] == "biologist"
    assert web_data["client_type"] == "web"
    assert web_data["external_id"] == "777001"
    assert web_data["telegram_username"] == "alice_grow"
    assert web_data["authorized_at"].endswith("Z")
    assert web_data["last_active_at"].endswith("Z")

    r_me = client.get("/api/auth/me", cookies=bio_cookies)
    assert r_me.status_code == 200
    me_data = r_me.json()
    assert me_data["id"] == bio_id
    assert me_data["login"] == "Алиса Рост"
    assert me_data["role"] == "biologist"
    assert me_data["client_type"] == "web"
    assert me_data["external_id"] == "777001"
    assert me_data["telegram_username"] == "alice_grow"
    assert me_data["authorized_at"].endswith("Z")
    assert me_data["last_active_at"].endswith("Z")

    # 3. Некорректное пустое имя -> 422
    r_bad = client.patch("/api/auth/me", json={"login": "   "}, cookies=bio_cookies)
    assert r_bad.status_code == 422

    # 4. Успешное переименование через PATCH /api/auth/me
    r_patch = client.patch(
        "/api/auth/me", json={"login": "Алиса Фито"}, cookies=bio_cookies
    )
    assert r_patch.status_code == 200
    patched = r_patch.json()
    assert patched["id"] == bio_id
    assert patched["login"] == "Алиса Фито"
    assert patched["telegram_username"] == "alice_grow"

    # Повторный GET /api/auth/web/me отражает новое имя
    r_web_after = client.get("/api/auth/web/me", cookies=bio_cookies)
    assert r_web_after.status_code == 200
    assert r_web_after.json()["login"] == "Алиса Фито"


def test_sessions_list_rbac_telegram_username_is_current_and_self_terminate(
    client: TestClient, test_env
):
    """GET /api/auth/sessions и POST /api/auth/sessions/{id}/terminate: RBAC, ссылки TG, is_current и самозавершение."""
    session_factory, _ = test_env

    dev_id, dev_cookies = _create_web_user(
        session_factory,
        role="developer",
        ext_id="888001",
        login="Дев Веб",
        tg_username="dev_web",
    )
    bio_id, bio_cookies = _create_web_user(
        session_factory,
        role="biologist",
        ext_id="888002",
        login="Био Веб",
        tg_username="bio_web",
    )

    with session_factory() as db:
        now = datetime.now(UTC)
        db.add(
            TelegramContact(
                telegram_id=888003,
                username="bio_bot_user",
                first_name="Бот",
            )
        )
        tg_user = User(
            client_type="telegram",
            external_id="888003",
            login="Био Бот",
            role="biologist",
            is_active=True,
            authorized_at=now,
            last_active_at=now,
        )
        cli_user = User(
            client_type="cli",
            external_id="cli-host-1",
            login="CLI Админ",
            role="developer",
            is_active=False,
            authorized_at=now,
            last_active_at=now,
            logged_out_at=now,
        )
        db.add_all([tg_user, cli_user])
        db.commit()
        db.refresh(tg_user)
        db.refresh(cli_user)
        tg_user_id = tg_user.id
        cli_user_id = cli_user.id

    # 1. Гость -> 401
    assert client.get("/api/auth/sessions").status_code == 401
    assert client.post(f"/api/auth/sessions/{bio_id}/terminate").status_code == 401

    # 2. Биолог -> 403
    assert client.get("/api/auth/sessions", cookies=bio_cookies).status_code == 403
    assert (
        client.post(
            f"/api/auth/sessions/{dev_id}/terminate", cookies=bio_cookies
        ).status_code
        == 403
    )

    # 3. Разработчик -> 200, проверяем поля всех сессий
    r_list = client.get("/api/auth/sessions", cookies=dev_cookies)
    assert r_list.status_code == 200
    by_id = {s["id"]: s for s in r_list.json()}

    assert by_id[dev_id]["is_current"] is True
    assert by_id[dev_id]["telegram_username"] == "dev_web"
    assert by_id[dev_id]["client_type"] == "web"
    assert by_id[dev_id]["is_active"] is True

    assert by_id[bio_id]["is_current"] is False
    assert by_id[bio_id]["telegram_username"] == "bio_web"
    assert by_id[bio_id]["is_active"] is True

    assert by_id[tg_user_id]["is_current"] is False
    assert by_id[tg_user_id]["telegram_username"] == "bio_bot_user"
    assert by_id[tg_user_id]["client_type"] == "telegram"

    assert by_id[cli_user_id]["is_current"] is False
    assert by_id[cli_user_id]["telegram_username"] is None
    assert by_id[cli_user_id]["client_type"] == "cli"
    assert by_id[cli_user_id]["is_active"] is False

    # 4. Завершаем чужой сеанс (bio_id) -> кука биолога перестаёт работать
    r_term_bio = client.post(
        f"/api/auth/sessions/{bio_id}/terminate", cookies=dev_cookies
    )
    assert r_term_bio.status_code == 200
    assert r_term_bio.json() == {"ok": True}
    assert client.get("/api/auth/me", cookies=bio_cookies).status_code == 401

    # 5. Завершаем собственный текущий сеанс (dev_id) -> 200, затем 401 и guest
    r_term_self = client.post(
        f"/api/auth/sessions/{dev_id}/terminate", cookies=dev_cookies
    )
    assert r_term_self.status_code == 200
    assert r_term_self.json() == {"ok": True}
    assert client.get("/api/auth/me", cookies=dev_cookies).status_code == 401
    r_web_after = client.get("/api/auth/web/me", cookies=dev_cookies)
    assert r_web_after.status_code == 200
    assert r_web_after.json()["authenticated"] is False
    assert r_web_after.json()["role"] == "guest"


def test_system_status_completeness_with_cameras_settings_and_telemetry(
    client: TestClient, test_env
):
    """GET /api/system/status возвращает полную телеметрию (вкл. uptime_sec), настройки и сводку по камерам."""
    session_factory, _ = test_env
    _, dev_cookies = _create_web_user(session_factory, "developer", "999001", "Dev Sys")
    _, bio_cookies = _create_web_user(session_factory, "biologist", "999002", "Bio Sys")

    with session_factory() as db:
        inst = Installation(
            name="Бокс Статус",
            culture_name="Базилик",
            light_gpio_pin=4,
            flash_gpio_pin=17,
        )
        db.add(inst)
        db.flush()

        db.add_all(
            [
                Camera(
                    hardware_id="cam-s-1",
                    auto_name="Cam 1",
                    installation_id=inst.id,
                    is_online=True,
                ),
                Camera(
                    hardware_id="cam-s-2",
                    auto_name="Cam 2",
                    installation_id=inst.id,
                    is_online=False,
                ),
                Camera(
                    hardware_id="cam-s-3",
                    auto_name="Cam 3",
                    installation_id=None,
                    is_online=True,
                ),
            ]
        )

        telem_payload = {
            "hardware": {
                "board_model": "Raspberry Pi 4 Model B",
                "cpu_temp_c": 51.2,
                "cpu_usage_percent": 18.5,
                "ram_used_mb": 740,
                "ram_total_mb": 4096,
                "disk_free_gb": 22.4,
                "uptime_sec": 94500,
                "gpio_mode": "rpi",
            },
            "software": {
                "edge_version": "0.10.2",
                "model_name": "leaf_seg_v2.onnx",
            },
            "offline_queue_count": 3,
        }
        now = datetime.now(UTC)
        db.add_all(
            [
                SystemSetting(
                    key="rpi_last_telemetry",
                    value=json.dumps(telem_payload),
                    updated_at=now,
                ),
                SystemSetting(key="capture_interval_hours", value="2", updated_at=now),
                SystemSetting(key="debug_mode", value="true", updated_at=now),
                SystemSetting(key="capture_delay_sec", value="2.5", updated_at=now),
            ]
        )
        db.commit()

    # Права доступа
    assert client.get("/api/system/status").status_code == 401
    assert client.get("/api/system/status", cookies=bio_cookies).status_code == 403

    r = client.get("/api/system/status", cookies=dev_cookies)
    assert r.status_code == 200
    data = r.json()

    assert data["rpi_connection"] == "online"
    assert data["rpi_last_heartbeat_at"] is not None
    assert data["is_real_rpi"] is True
    assert data["cameras_summary"] == {
        "total": 3,
        "online": 2,
        "offline": 1,
        "bound": 2,
        "unbound": 1,
    }
    assert data["settings"] == {
        "capture_interval_hours": 2,
        "debug_mode": True,
        "capture_delay_sec": 2.5,
    }
    assert data["rpi_telemetry"]["hardware"]["uptime_sec"] == 94500
    assert data["rpi_telemetry"]["hardware"]["cpu_temp_c"] == 51.2
    assert data["rpi_telemetry"]["hardware"]["cpu_usage_percent"] == 18.5
    assert data["rpi_telemetry"]["hardware"]["ram_used_mb"] == 740
    assert data["rpi_telemetry"]["hardware"]["ram_total_mb"] == 4096
    assert data["rpi_telemetry"]["hardware"]["disk_free_gb"] == 22.4
    assert data["rpi_telemetry"]["software"]["edge_version"] == "0.10.2"
    assert data["rpi_telemetry"]["software"]["model_name"] == "leaf_seg_v2.onnx"
    assert data["rpi_telemetry"]["offline_queue_count"] == 3


def test_timelapse_frames_dimensions_and_csv_export(client: TestClient, test_env):
    """GET /api/timelapse/{id}/frames отдает width_px/height_px/width_mm/height_mm и buildGrowthChartCsv генерирует валидный CSV с BOM."""
    session_factory, _ = test_env
    with session_factory() as db:
        inst = Installation(
            name="Бокс Экспорт",
            culture_name="Салат",
            light_gpio_pin=4,
            flash_gpio_pin=17,
            aruco_size_mm=50.0,
        )
        db.add(inst)
        db.flush()

        # Кадр 1: с ArUco и 2 кустами
        cap1 = Capture(
            installation_id=inst.id,
            camera_id=None,
            captured_at=datetime(2026, 11, 25, 8, 0, tzinfo=UTC),
            is_manual=False,
            aruco_area_px=1000.0,
            raw_image_path=f"media/{inst.id}/raw_1.jpg",
            annotated_image_path=f"media/{inst.id}/ann_1.jpg",
        )
        db.add(cap1)
        db.flush()
        db.add_all(
            [
                PlantMeasurement(
                    capture_id=cap1.id,
                    plant_index=0,
                    area_px=2000.0,
                    width_px=100.0,
                    height_px=80.0,
                    width_mm=50.0,
                    height_mm=40.0,
                    area_cm2=20.0,
                ),
                PlantMeasurement(
                    capture_id=cap1.id,
                    plant_index=1,
                    area_px=1000.0,
                    width_px=61.0,
                    height_px=41.0,
                    width_mm=30.5,
                    height_mm=20.5,
                    area_cm2=10.0,
                ),
            ]
        )

        # Кадр 2: без ArUco (ширина/высота в px есть, в мм -> None)
        cap2 = Capture(
            installation_id=inst.id,
            camera_id=None,
            captured_at=datetime(2026, 11, 25, 9, 0, tzinfo=UTC),
            is_manual=False,
            aruco_area_px=None,
            raw_image_path=f"media/{inst.id}/raw_2.jpg",
            annotated_image_path=f"media/{inst.id}/ann_2.jpg",
        )
        db.add(cap2)
        db.flush()
        db.add(
            PlantMeasurement(
                capture_id=cap2.id,
                plant_index=0,
                area_px=3500.0,
                width_px=120.0,
                height_px=95.0,
                width_mm=60.0,
                height_mm=47.5,
            )
        )
        db.commit()
        inst_id = inst.id

    res = client.get(f"/api/timelapse/{inst_id}/frames")
    assert res.status_code == 200
    frames = res.json()["frames"]
    assert len(frames) == 2

    f1, f2 = frames[0], frames[1]
    assert f1["width_px"] == 80.5
    assert f1["height_px"] == 60.5
    assert f1["width_mm"] == 40.2
    assert f1["height_mm"] == 30.2
    assert f1["aruco_calibrated"] is True

    assert f2["width_px"] == 120.0
    assert f2["height_px"] == 95.0
    assert f2["width_mm"] is None
    assert f2["height_mm"] is None
    assert f2["aruco_calibrated"] is False

    # Проверяем реализацию buildGrowthChartCsv в gallery.js
    web_dir = ROOT_DIR / "clients" / "web"
    gallery_js = (web_dir / "js" / "gallery.js").read_text(encoding="utf-8")
    assert "export function buildGrowthChartCsv" in gallery_js
    assert "\\uFEFF" in gallery_js
    expected_headers = [
        "Дата и время",
        "Количество кустов",
        "Общая площадь (см²)",
        "Средняя площадь (см²)",
        "Общая площадь (px)",
        "Средняя площадь (px)",
        "Ширина (мм)",
        "Высота (мм)",
        "Ширина (px)",
        "Высота (px)",
        "Калибровка ArUco",
    ]
    for col in expected_headers:
        assert col in gallery_js

    # Если доступен Node, запускаем реальную JS-функцию buildGrowthChartCsv и парсим CSV в Python
    if shutil.which("node"):
        script = f"""
        import {{ buildGrowthChartCsv }} from {json.dumps(str(web_dir / "js" / "gallery.js"))};
        const frames = {json.dumps(frames, ensure_ascii=False)};
        process.stdout.write(buildGrowthChartCsv(frames, 'Бокс Экспорт'));
        """
        node_res = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert node_res.returncode == 0, node_res.stderr
        csv_text = node_res.stdout
        assert csv_text.startswith("\ufeff")
        rows = list(csv.reader(io.StringIO(csv_text.lstrip("\ufeff"))))
        assert rows[0] == expected_headers
        assert len(rows) == 3
        assert rows[1][1] == "2"
        assert rows[1][2] == "30"
        assert rows[1][6] == "40.2"
        assert rows[1][10] == "Да"
        assert rows[2][2] == ""
        assert rows[2][6] == ""
        assert rows[2][8] == "120"
        assert rows[2][10] == "Нет"


def test_web_ui_stage11_2_markup_and_modules():
    """Проверяет разметку шапки, модалок профиля и сессий, панели Raspberry и экспорта CSV."""
    web_dir = ROOT_DIR / "clients" / "web"
    html = (web_dir / "index.html").read_text(encoding="utf-8")
    app_js = (web_dir / "js" / "app.js").read_text(encoding="utf-8")
    profile_js = (web_dir / "js" / "profile.js").read_text(encoding="utf-8")
    rpi_js = (web_dir / "js" / "rpi_setup.js").read_text(encoding="utf-8")

    # 1. Имя пользователя в шапке — <button id="header-user-name">
    assert re.search(r'<button[^>]*\bid="header-user-name"', html) is not None
    assert not re.search(r'<span[^>]*\bid="header-user-name"', html)

    # 2. Модалка учётной записи (#modal-profile) и кнопка «Активные сессии»
    assert 'id="modal-profile"' in html
    assert 'id="modal-profile-close"' in html
    assert 'id="profile-login-input"' in html
    assert 'id="profile-save-btn"' in html
    assert 'id="profile-btn-sessions"' in html

    # 3. Модалка активных сессий (#modal-sessions)
    assert 'id="modal-sessions"' in html
    assert 'id="modal-sessions-close"' in html
    assert 'id="sessions-list"' in html

    # 4. Дополненная панель Raspberry
    assert 'id="rpi-telem-model"' in html
    assert 'id="rpi-telem-uptime"' in html
    assert 'id="rpi-telem-cameras"' in html
    assert 'id="rpi-telem-capture-settings"' in html
    assert "export function formatUptime" in rpi_js

    # 5. Кнопка экспорта CSV в графике роста
    assert 'id="gallery-chart-export-csv"' in html

    # 6. Интеграция profile.js в app.js и обработка self-terminate + ссылки t.me
    assert "initProfile" in app_js
    assert "setProfileUpdatedHandler" in app_js
    assert "setSelfSessionTerminatedHandler" in app_js
    assert "https://t.me/" in profile_js
    assert "rel = 'noopener'" in profile_js or 'rel="noopener"' in profile_js
    assert "updateProfile" in profile_js
    assert "terminateSession" in profile_js

    # Если доступен Node, проверяем formatUptime
    if shutil.which("node"):
        script = f"""
        import {{ formatUptime }} from {json.dumps(str(web_dir / "js" / "rpi_setup.js"))};
        if (formatUptime(94500) !== '1д 02ч 15м') throw new Error('bad 94500: ' + formatUptime(94500));
        if (formatUptime(3660) !== '1ч 01м') throw new Error('bad 3660: ' + formatUptime(3660));
        if (formatUptime(45) !== '45с') throw new Error('bad 45: ' + formatUptime(45));
        if (formatUptime(null) !== '—') throw new Error('bad null');
        """
        res = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0, res.stderr
