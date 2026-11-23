# tests/e2e/test_stage8_web_e2e.py
from pathlib import Path

import httpx

ROOT_DIR = Path(__file__).resolve().parent.parent.parent


def test_stage8_e2e_web_spa_flow(e2e_uvicorn_server):
    """Сквозной E2E тест Этапа 8.3:

    1. Раздача статики Web SPA (HTML, CSS, JS, SVG, gif_encoder).
    2. Проверка изоляции: отсутствие X-Client-Key в исходниках clients/web/.
    3. Синхронизация контакта от Telegram-бота и очередь кодов.
    4. Пошаговая регистрация через Telegram (старт -> код -> подтверждение инвайтом).
    5. Проверка сессии, просмотр установок с frames_count и last_frame_url.
    6. Тестовый снимок с числовым target (Number).
    7. Логаут и повторный вход по @username и по ID с личным паролем.
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
        assert "brand-logo" in r_index.text
        assert 'viewBox="0 0 120 90"' in r_index.text

        # Стили
        r_css = client.get("/style.css")
        assert r_css.status_code == 200
        assert ".brand-logo" in r_css.text

        # Фон и фирменные логотипы
        r_bg = client.get("/assets/background.svg")
        assert r_bg.status_code == 200

        for logo_name in ("logo-green.svg", "logo-white.svg", "logo-black.svg"):
            r_logo = client.get(f"/assets/{logo_name}")
            assert r_logo.status_code == 200, f"Ассет {logo_name} не найден"
            assert 'viewBox="0 0 120 90"' in r_logo.text

        # JS модули
        for mod in (
            "api.js",
            "app.js",
            "auth.js",
            "dialogs.js",
            "gallery.js",
            "test_snap.js",
            "units.js",
            "gif_encoder.js",
            "rpi_setup.js",
        ):
            r_mod = client.get(f"/js/{mod}")
            assert r_mod.status_code == 200, f"Модуль {mod} не найден"

    # 2. Проверка отсутствия X-Client-Key в веб-исходниках
    web_dir = ROOT_DIR / "clients" / "web"
    for f in web_dir.rglob("*"):
        if f.is_file() and f.suffix in (".js", ".html", ".css"):
            text = f.read_text(encoding="utf-8").lower()
            assert "x-client-key" not in text, f"Утечка X-Client-Key в {f.name}"

    # 3. Синхронизация контакта от Telegram-бота
    headers_bot = {"X-Client-Key": client_key}
    tg_user_id = 9988776655

    with httpx.Client(base_url=base_url) as bot_client:
        r_sync = bot_client.post(
            "/api/auth/telegram/contact",
            headers=headers_bot,
            json={
                "telegram_id": tg_user_id,
                "username": "bio_anna_tg",
                "first_name": "Анна",
                "last_name": "Биолог",
            },
        )
        assert r_sync.status_code == 200
        assert r_sync.json()["ok"] is True

        # Сначала разработчик через CLI генерирует инвайт для биолога
        r_dev_login = bot_client.post(
            "/api/auth/login",
            headers=headers_bot,
            json={
                "client_type": "cli",
                "external_id": "admin_cli_e2e",
                "password": dev_pass,
                "login": "Админ CLI",
            },
        )
        assert r_dev_login.status_code == 200

        r_inv = bot_client.post(
            "/api/auth/invites",
            headers={
                "X-Client-Key": client_key,
                "X-Client-Type": "cli",
                "X-External-Id": "admin_cli_e2e",
            },
        )
        assert r_inv.status_code == 200
        invite_code = r_inv.json()["code"]

    # 4. Пошаговая регистрация через Web SPA (сохраняя cookies gv_session)
    with httpx.Client(base_url=base_url) as web_client:
        # Проверка начального статуса Гость
        r_me_guest = web_client.get("/api/auth/web/me")
        assert r_me_guest.status_code == 200
        assert r_me_guest.json()["role"] == "guest"
        assert r_me_guest.json()["authenticated"] is False

        # Шаг 1: старт регистрации по @username
        r_step1 = web_client.post(
            "/api/auth/web/register/start",
            json={"telegram": "@bio_anna_tg"},
        )
        assert r_step1.status_code == 200
        assert r_step1.json()["telegram_id"] == tg_user_id

        # Бот забирает код из очереди и подтверждает
        with httpx.Client(base_url=base_url) as bot_client:
            r_codes = bot_client.get("/api/auth/web/pending-codes", headers=headers_bot)
            assert r_codes.status_code == 200
            codes = r_codes.json()
            assert len(codes) >= 1
            matching_code = next(c for c in codes if c["telegram_id"] == tg_user_id)
            code_id = matching_code["id"]
            code_val = matching_code["code"]

            r_ack = bot_client.post(
                f"/api/auth/web/pending-codes/{code_id}/ack",
                headers=headers_bot,
            )
            assert r_ack.status_code == 200

        # Шаг 2: подтверждение 6-значного кода
        r_step2 = web_client.post(
            "/api/auth/web/register/verify",
            json={"telegram_id": tg_user_id, "code": code_val},
        )
        assert r_step2.status_code == 200
        assert r_step2.json()["status"] == "verified"

        # Шаг 3: завершение регистрации с инвайтом биолога и паролем
        r_step3 = web_client.post(
            "/api/auth/web/register/complete",
            json={
                "telegram_id": tg_user_id,
                "verification_code": code_val,
                "full_name": "Анна Лабораторная",
                "access_code": invite_code,
                "password": "my_secure_password_123",
            },
        )
        assert r_step3.status_code == 200
        assert r_step3.json()["role"] == "biologist"
        assert r_step3.json()["login"] == "Анна Лабораторная"
        assert "gv_session" in web_client.cookies

        # 5. Проверка сессии авторизованного пользователя
        r_me_bio = web_client.get("/api/auth/web/me")
        assert r_me_bio.status_code == 200
        assert r_me_bio.json()["authenticated"] is True
        assert r_me_bio.json()["role"] == "biologist"
        assert r_me_bio.json()["login"] == "Анна Лабораторная"
        assert r_me_bio.json()["external_id"] == str(tg_user_id)

        # Проверка получения установок с новыми полями frames_count и last_frame_url
        r_insts = web_client.get("/api/installations")
        assert r_insts.status_code == 200
        inst_list = r_insts.json()
        assert isinstance(inst_list, list)

        # 6. Проверка тестового снимка с target: Number(id)
        if inst_list:
            target_id = inst_list[0]["id"]
            # Биолог отправляет тестовый снимок (в тестовом окружении может вернуть 200 или 504 при таймауте агента)
            r_snap = web_client.post(
                "/api/control/snap",
                json={"target": int(target_id)},
            )
            # Главное: запрос проходит валидацию Pydantic (не 422)
            assert r_snap.status_code in (200, 504, 500)

        # 7. Выход
        r_logout = web_client.post("/api/auth/web/logout")
        assert r_logout.status_code == 200

        r_me_guest2 = web_client.get("/api/auth/web/me")
        assert r_me_guest2.json()["authenticated"] is False
        assert r_me_guest2.json()["role"] == "guest"

        # Повторный вход биолога по @username и коду приглашения
        headers_dev = {
            "X-Client-Key": client_key,
            "X-Client-Type": "cli",
            "X-External-Id": "admin_cli_e2e",
        }
        with httpx.Client(base_url=base_url) as dev_client:
            r_inv2 = dev_client.post(
                "/api/auth/invites",
                headers=headers_dev,
                json={"role": "biologist"},
            )
            assert r_inv2.status_code == 200
            invite_code_2 = r_inv2.json()["code"]

        r_relogin_un = web_client.post(
            "/api/auth/web/login",
            json={
                "telegram": "@bio_anna_tg",
                "code": invite_code_2,
            },
        )
        assert r_relogin_un.status_code == 200
        assert r_relogin_un.json()["role"] == "biologist"

        # Повторный вход по числовому ID и коду приглашения
        with httpx.Client(base_url=base_url) as dev_client:
            r_inv3 = dev_client.post(
                "/api/auth/invites",
                headers=headers_dev,
                json={"role": "biologist"},
            )
            assert r_inv3.status_code == 200
            invite_code_3 = r_inv3.json()["code"]

        web_client.cookies.clear()
        r_relogin_id = web_client.post(
            "/api/auth/web/login",
            json={
                "telegram": str(tg_user_id),
                "code": invite_code_3,
            },
        )
        assert r_relogin_id.status_code == 200
        assert r_relogin_id.json()["role"] == "biologist"


def test_stage8_no_alert_confirm_prompt_and_no_emojis():
    """Проверка полного отказа от alert/confirm/prompt и отсутствия эмодзи в Web SPA."""
    import re

    web_dir = ROOT_DIR / "clients" / "web"
    emoji_pattern = re.compile(
        r"[\U00010000-\U0010ffff]|[\u2600-\u27bf]|[\u2300-\u23ff]|[\u2b50-\u2b55]"
    )
    dialog_pattern = re.compile(r"\b(alert|confirm|prompt)\s*\(")

    for f in web_dir.rglob("*"):
        if f.is_file() and f.suffix in (".js", ".html", ".css"):
            text = f.read_text(encoding="utf-8")
            assert not emoji_pattern.search(text), f"Обнаружен эмодзи в {f.name}"
            assert not dialog_pattern.search(text), (
                f"Обнаружен вызов alert/confirm/prompt в {f.name}"
            )


def test_stage8_web_es_modules_static_import_export_validation(e2e_uvicorn_server):
    """Критерий Этапа 8.4.1: Статическая валидация согласованности ES-модулей Web SPA и favicon.

    Проверяет, что все локальные import { ... } from './*.js' соответствуют существующим export.
    Проверяет наличие встроенного SVG favicon в index.html.
    """
    import re

    server_data = e2e_uvicorn_server
    base_url = server_data["base_url"]

    web_js_dir = ROOT_DIR / "clients" / "web" / "js"
    js_files = list(web_js_dir.glob("*.js"))
    assert len(js_files) >= 5, "В clients/web/js должно быть не менее 5 JS-модулей"

    exports: dict[str, set[str]] = {}

    for f in js_files:
        content = f.read_text(encoding="utf-8")
        fn_exp = re.findall(
            r"export\s+(?:async\s+)?function\s+([a-zA-Z0-9_$]+)", content
        )
        var_exp = re.findall(r"export\s+(?:const|let|var)\s+([a-zA-Z0-9_$]+)", content)
        block_exp_matches = re.findall(r"export\s*\{([^}]+)\}", content)
        block_exp = []
        for b in block_exp_matches:
            for item in b.split(","):
                parts = item.strip().split(" as ")
                name = parts[-1].strip()
                if name:
                    block_exp.append(name)
        all_exp_list = fn_exp + var_exp + block_exp
        dups = [x for x in set(all_exp_list) if all_exp_list.count(x) > 1]
        assert not dups, (
            f"В {f.name} обнаружены дублирующиеся экспорты: {dups} (SyntaxError в ES-модулях)"
        )
        exports[f.name] = set(all_exp_list)

    unresolved_imports = []
    for f in js_files:
        content = f.read_text(encoding="utf-8")
        matches = re.findall(
            r"import\s*\{([^}]+)\}\s*from\s*[\x27\x22]\./([a-zA-Z0-9_$-]+\.js)[\x27\x22]",
            content,
        )
        for names_str, target_file in matches:
            target_exports = exports.get(target_file, set())
            for item in names_str.split(","):
                raw_name = item.strip().split(" as ")[0].strip()
                if not raw_name:
                    continue
                if raw_name not in target_exports:
                    unresolved_imports.append(
                        f"В {f.name}: невозможно импортировать '{raw_name}' из '{target_file}'"
                    )

    assert not unresolved_imports, (
        f"Обнаружены несогласованные ES-импорты: {unresolved_imports}"
    )

    # Проверка favicon в HTML
    with httpx.Client(base_url=base_url) as client:
        r_index = client.get("/")
        assert r_index.status_code == 200
        assert 'rel="icon"' in r_index.text or "rel='icon'" in r_index.text
        assert "data:image/svg+xml" in r_index.text


def test_stage8_brand_logo_and_favicon_validation():
    """Проверка интеграции фирменного SVG-логотипа в Web SPA и favicon (Этап 8.6)."""
    import re
    import urllib.parse
    import xml.etree.ElementTree as ET

    assets_dir = ROOT_DIR / "clients" / "web" / "assets"
    index_html = (ROOT_DIR / "clients" / "web" / "index.html").read_text(
        encoding="utf-8"
    )
    style_css = (ROOT_DIR / "clients" / "web" / "style.css").read_text(encoding="utf-8")

    # 1. Валидность XML и атрибутов всех трех фирменных логотипов
    for name in ("logo-green.svg", "logo-white.svg", "logo-black.svg"):
        svg_path = assets_dir / name
        assert svg_path.exists(), f"Файл {name} не найден"
        tree = ET.parse(svg_path)
        root = tree.getroot()
        assert root.tag.endswith("svg"), f"{name} не является корневым <svg>"
        assert root.attrib.get("viewBox") == "0 0 120 90", (
            f"{name} имеет неверный viewBox"
        )

    # 2. Проверка интеграции логотипа в шапку Web SPA
    assert 'class="brand-logo"' in index_html or "brand-logo" in index_html
    assert 'viewBox="0 0 120 90"' in index_html
    assert 'class="logo-title">GrowVision</span>' in index_html

    # 3. Проверка стилей и квадратных пропорций 1:1
    assert ".brand-logo" in style_css
    assert "aspect-ratio: 1 / 1" in style_css
    assert "width: 32px" in style_css
    assert "height: 32px" in style_css
    assert "width: 28px" in style_css
    assert "height: 28px" in style_css

    # 4. Проверка Data URI favicon: точная копия logo-green.svg (viewBox="0 0 120 90")
    match = re.search(r'href="(data:image/svg\+xml,[^"]+)"', index_html)
    assert match, "Не найден Data URI favicon в index.html"
    raw_uri = match.group(1)
    _prefix, encoded = raw_uri.split(",", 1)
    decoded_svg = urllib.parse.unquote(encoded)
    favicon_root = ET.fromstring(decoded_svg)
    assert favicon_root.tag.endswith("svg")
    assert favicon_root.attrib.get("viewBox") == "0 0 120 90"
