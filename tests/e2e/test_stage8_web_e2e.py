# tests/e2e/test_stage8_web_e2e.py
from pathlib import Path

import httpx

ROOT_DIR = Path(__file__).resolve().parent.parent.parent


def test_stage8_e2e_web_spa_flow(e2e_uvicorn_server):
    """Сквозной E2E тест Этапа 8:

    1. Раздача статики Web SPA (HTML, CSS, JS).
    2. Проверка изоляции: отсутствие X-Client-Key в исходниках clients/web/.
    3. Веб-авторизация (Гость -> needs_login -> Биолог с кукой -> переименование).
    4. Проверка прав (Биолог не может создать установку -> 403).
    5. Веб-вход Разработчика -> создание установки -> сессии -> кик Биолога.
    6. Логаут и проверка недействительности кикнутой сессии.
    """
    server_data = e2e_uvicorn_server
    base_url = server_data["base_url"]
    dev_pass = server_data["dev_pass"]
    client_key = server_data["client_key"]

    # 1. Проверка раздачи Web SPA
    with httpx.Client(base_url=base_url) as client:
        # Главная страница
        r_index = client.get("/")
        assert r_index.status_code == 200
        assert "GrowVision" in r_index.text
        assert "app.js" in r_index.text

        # Стили
        r_css = client.get("/style.css")
        assert r_css.status_code == 200
        assert "var(--bg-main)" in r_css.text

        # JS модули
        for mod in (
            "api.js",
            "app.js",
            "player.js",
            "units.js",
            "relays.js",
            "cameras.js",
            "sessions.js",
            "system.js",
            "terminal.js",
        ):
            r_mod = client.get(f"/js/{mod}")
            assert r_mod.status_code == 200, f"Модуль {mod} не найден"

    # 2. Проверка отсутствия X-Client-Key в веб-исходниках
    web_dir = ROOT_DIR / "clients" / "web"
    for f in web_dir.rglob("*"):
        if f.is_file() and f.suffix in (".js", ".html", ".css"):
            text = f.read_text(encoding="utf-8").lower()
            assert "x-client-key" not in text, f"Утечка X-Client-Key в {f.name}"

    # 3. Веб-авторизация через реальный HTTP сервер
    # Сначала сгенерируем инвайт через Developer API
    headers_dev_api = {
        "X-Client-Key": client_key,
        "X-Client-Type": "cli",
        "X-External-Id": "admin_cli",
    }

    # Логиним разработчика в CLI для создания сессии
    with httpx.Client(base_url=base_url) as admin_client:
        r_admin_login = admin_client.post(
            "/api/auth/login",
            headers={"X-Client-Key": client_key},
            json={
                "client_type": "cli",
                "external_id": "admin_cli",
                "password": dev_pass,
                "login": "Админ CLI",
            },
        )
        assert r_admin_login.status_code == 200

        # Генерируем инвайт-код
        r_inv = admin_client.post("/api/auth/invites", headers=headers_dev_api)
        assert r_inv.status_code == 200
        invite_code = r_inv.json()["code"]

    # Теперь открываем веб-клиент Биолога с сохранением cookies
    with httpx.Client(base_url=base_url) as bio_web_client:
        # Проверка начального статуса Гость
        r_me_guest = bio_web_client.get("/api/auth/web/me")
        assert r_me_guest.status_code == 200
        assert r_me_guest.json()["role"] == "guest"

        client_id_bio = "web_browser_bio_e2e"

        # Вход по инвайт-коду без логина -> needs_login
        r_bio_first = bio_web_client.post(
            "/api/auth/web/login",
            json={"password": invite_code, "client_id": client_id_bio},
        )
        assert r_bio_first.status_code == 200
        assert r_bio_first.json().get("status") == "needs_login"
        assert r_bio_first.json().get("role") == "biologist"

        # Завершение входа с именем
        r_bio_done = bio_web_client.post(
            "/api/auth/web/login",
            json={
                "password": invite_code,
                "client_id": client_id_bio,
                "login": "Ольга Васильева",
            },
        )
        assert r_bio_done.status_code == 200
        assert r_bio_done.json()["role"] == "biologist"
        assert r_bio_done.json()["login"] == "Ольга Васильева"
        assert "gv_session" in bio_web_client.cookies

        # Проверка текущего пользователя по куке
        r_me_bio = bio_web_client.get("/api/auth/web/me")
        assert r_me_bio.status_code == 200
        assert r_me_bio.json()["role"] == "biologist"
        assert r_me_bio.json()["login"] == "Ольга Васильева"

        # Биолог переименовывает себя через PATCH /api/auth/me
        r_rename_me = bio_web_client.patch(
            "/api/auth/me",
            json={"login": "Ольга Лабораторная"},
        )
        assert r_rename_me.status_code == 200
        assert r_rename_me.json()["login"] == "Ольга Лабораторная"

        # Биолог пытается создать установку -> 403 Forbidden
        r_bio_create = bio_web_client.post(
            "/api/installations",
            json={
                "name": "Теплица Биолога",
                "culture_name": "Мята",
                "light_gpio_pin": 12,
                "flash_gpio_pin": 13,
            },
        )
        assert r_bio_create.status_code == 403

        # 4. Веб-вход Разработчика в отдельном браузере
        with httpx.Client(base_url=base_url) as dev_web_client:
            r_dev_login = dev_web_client.post(
                "/api/auth/web/login",
                json={
                    "password": dev_pass,
                    "client_id": "web_browser_dev_e2e",
                    "login": "Веб Разработчик",
                },
            )
            assert r_dev_login.status_code == 200
            assert r_dev_login.json()["role"] == "developer"

            # Разработчик создает установку через веб
            r_inst_dev = dev_web_client.post(
                "/api/installations",
                json={
                    "name": "Установка Веб 1",
                    "culture_name": "Томат",
                    "light_gpio_pin": 21,
                    "flash_gpio_pin": 22,
                },
            )
            assert r_inst_dev.status_code == 201

            # Разработчик смотрит сессии
            r_sess = dev_web_client.get("/api/auth/sessions")
            assert r_sess.status_code == 200
            sessions = r_sess.json()
            bio_sess = next(
                (
                    s
                    for s in sessions
                    if s["client_type"] == "web" and s["external_id"] == client_id_bio
                ),
                None,
            )
            assert bio_sess is not None
            assert bio_sess["login"] == "Ольга Лабораторная"
            assert bio_sess["is_active"] is True

            # Разработчик кикает сессию биолога
            r_kick = dev_web_client.post(
                f"/api/auth/sessions/{bio_sess['id']}/terminate"
            )
            assert r_kick.status_code == 200

        # Запрос от биолога после кика возвращает 401
        r_bio_kicked = bio_web_client.patch(
            "/api/auth/me",
            json={"login": "Попытка после кика"},
        )
        assert r_bio_kicked.status_code == 401
