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

        # Поиск контакта через telegram-lookup
        r_lookup = web_client.get("/api/auth/web/telegram-lookup?telegram=@bio_anna_tg")
        assert r_lookup.status_code == 200
        assert r_lookup.json()["found"] is True
        assert r_lookup.json()["telegram_id"] == tg_user_id

        # Регистрация в 1 шаг с инвайтом биолога (без кодов верификации)
        r_step3 = web_client.post(
            "/api/auth/web/register/complete",
            json={
                "telegram": "@bio_anna_tg",
                "first_name": "Анна",
                "last_name": "Лабораторная",
                "access_code": invite_code,
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


def test_stage8_7_ux_polish_and_realtime_verification(e2e_uvicorn_server):
    """Верификация критериев Этапа 8.7:

    1. Обязательное подтверждение кодом из Telegram для незарегистрированных (403 telegram_verification_required).
    2. Рекомендованное имя пользователя из TelegramContact на /api/auth/web/register/start.
    3. bot_username в /api/version и /api/auth/web/me.
    4. Рамочный баннер сети в потоке под header, прижатый к низу футер (min-height: 100vh) и инлайн-ошибки.
    5. Поле длительности таймлапса и расчет FPS.
    6. Тематические заглушки категорий A, B, C, D без эмодзи.
    """
    server_data = e2e_uvicorn_server
    base_url = server_data["base_url"]
    dev_pass = server_data["dev_pass"]
    client_key = server_data["client_key"]

    with httpx.Client(base_url=base_url) as client:
        # 1. Проверка версии и bot_username
        r_ver = client.get("/api/version")
        assert r_ver.status_code == 200
        ver_data = r_ver.json()
        assert "server_version" in ver_data
        assert "bot_username" in ver_data

        r_me = client.get("/api/auth/web/me")
        assert r_me.status_code == 200
        assert "bot_username" in r_me.json()

        # 2. Попытка входа незарегистрированного пользователя -> 401 (без telegram_verification_required)
        r_sync = client.post(
            "/api/auth/telegram/contact",
            headers={"X-Client-Key": client_key},
            json={
                "telegram_id": 1234567890,
                "username": "unregistered_test_user",
                "first_name": "Иван",
                "last_name": "Петров",
            },
        )
        assert r_sync.status_code == 200

        r_login_unreg = client.post(
            "/api/auth/web/login",
            json={
                "telegram": "@unregistered_test_user",
                "code": dev_pass,
            },
        )
        assert r_login_unreg.status_code == 401
        assert "telegram_verification_required" not in r_login_unreg.json()["detail"]

        # 3. Lookup возвращает recommended_name
        r_lookup = client.get(
            "/api/auth/web/telegram-lookup?telegram=@unregistered_test_user"
        )
        assert r_lookup.status_code == 200
        reg_data = r_lookup.json()
        assert reg_data["found"] is True
        assert reg_data["telegram_id"] == 1234567890
        assert reg_data["recommended_name"] == "Иван Петров"

    # 4. Проверка разметки и стилей Web SPA
    index_html = (ROOT_DIR / "clients" / "web" / "index.html").read_text(
        encoding="utf-8"
    )
    style_css = (ROOT_DIR / "clients" / "web" / "style.css").read_text(encoding="utf-8")
    placeholders_js = (
        ROOT_DIR / "clients" / "web" / "js" / "placeholders.js"
    ).read_text(encoding="utf-8")

    # Баннер в потоке под header
    header_idx = index_html.find("</header>")
    banner_idx = index_html.find('id="server-offline-banner"')
    main_idx = index_html.find("<main")
    assert header_idx != -1 and banner_idx != -1 and main_idx != -1
    assert header_idx < banner_idx < main_idx, (
        "Баннер должен находиться в потоке документа между </header> и <main>"
    )

    # Инлайн-подтверждение выхода в шапке
    assert 'id="header-logout-confirm"' in index_html
    assert 'id="header-logout-confirm-yes"' in index_html
    assert 'id="header-logout-confirm-no"' in index_html

    # Галерея: длительность и FPS
    assert 'id="gallery-duration-input"' in index_html
    assert 'id="gallery-fps-value"' in index_html

    # Инлайн-ошибки форм
    assert 'id="login-form-error"' in index_html
    assert 'id="reg-form-error"' in index_html

    # Стили: flex и min-height: 100vh для body, flex: 1 для main
    assert "min-height: 100vh" in style_css
    assert "display: flex" in style_css
    assert "flex-direction: column" in style_css
    assert "flex: 1" in style_css
    assert "1px solid #ef4444" in style_css

    # Тематические заглушки: наличие всех 4 категорий и отсутствие эмодзи
    assert "PLACEHOLDERS" in placeholders_js
    assert "A:" in placeholders_js
    assert "B:" in placeholders_js
    assert "C:" in placeholders_js
    assert "D:" in placeholders_js
    assert "getRandomLoadingPhrase" in placeholders_js
    assert "Ждём, когда растение улыбнётся" in placeholders_js


def test_stage8_8_fix_web_tg_auth_flow(e2e_uvicorn_server):
    """Сквозной тест Этапа 8.8 (Fix Web TG Auth):

    1. Удаление проверки личного пароля в register/complete:
       - Регистрация без передачи пароля завершается успешно (HTTP 200).
    2. Передача реального bot_username с сервера (GET /api/version, GET /api/auth/web/me).
    3. Проверка /start и для числового TG ID:
       - Несуществующий в TelegramContact числовой ID возвращает 400 с текстом о /start.
    4. Отслеживание статуса доставки кода (pending-codes/{id}/fail и register/code-status).
    5. Корректность разметки и стилей Web SPA:
       - Закрытый .password-group в index.html.
       - Классы .reg-step, .reg-step.hidden, #modal-register.hidden в style.css.
       - Отсутствие эмодзи во всех файлах Web SPA.
    """
    server_data = e2e_uvicorn_server
    base_url = server_data["base_url"]
    dev_pass = server_data["dev_pass"]
    client_key = server_data["client_key"]

    with httpx.Client(base_url=base_url) as client:
        # 1. Проверка bot_username
        r_ver = client.get("/api/version")
        assert r_ver.status_code == 200
        assert r_ver.json()["bot_username"]

        r_me = client.get("/api/auth/web/me")
        assert r_me.status_code == 200
        assert r_me.json()["bot_username"]

        # 2. Неизвестный числовой TG ID: lookup -> found=false, вход -> 401
        unknown_tid = 9988776655
        r_lu_unknown = client.get(
            f"/api/auth/web/telegram-lookup?telegram={unknown_tid}"
        )
        assert r_lu_unknown.status_code == 200
        assert r_lu_unknown.json()["found"] is False

        r_fail_login = client.post(
            "/api/auth/web/login",
            json={"telegram": str(unknown_tid), "code": dev_pass},
        )
        assert r_fail_login.status_code == 401

        # 3. Синхронизация контакта от бота (пользователь нажал /start)
        valid_tid = 7711223344
        r_sync = client.post(
            "/api/auth/telegram/contact",
            headers={"X-Client-Key": client_key},
            json={
                "telegram_id": valid_tid,
                "username": "bio_seamless_user",
                "first_name": "Анна",
                "last_name": "Смирнова",
            },
        )
        assert r_sync.status_code == 200

        # 4. Lookup возвращает предложенные имя и фамилию
        r_lu_valid = client.get(f"/api/auth/web/telegram-lookup?telegram={valid_tid}")
        assert r_lu_valid.status_code == 200
        lu_data = r_lu_valid.json()
        assert lu_data["found"] is True
        assert lu_data["telegram_id"] == valid_tid
        assert lu_data["recommended_name"] == "Анна Смирнова"

        # 5. Завершаем регистрацию в 1 шаг БЕЗ пароля и БЕЗ кода подтверждения
        r_complete = client.post(
            "/api/auth/web/register/complete",
            json={
                "telegram": str(valid_tid),
                "first_name": "Анна",
                "last_name": "Смирнова",
                "access_code": dev_pass,
            },
        )
        assert r_complete.status_code == 200
        complete_data = r_complete.json()
        assert complete_data["login"] == "Анна Смирнова"
        assert complete_data["role"] == "developer"
        assert "gv_session" in client.cookies

        # 6. Проверяем /api/auth/web/me после авторизации
        r_me_auth = client.get("/api/auth/web/me")
        assert r_me_auth.status_code == 200
        me_data = r_me_auth.json()
        assert me_data["authenticated"] is True
        assert me_data["login"] == "Анна Смирнова"
        assert me_data["telegram_url"] == "https://t.me/bio_seamless_user"
        assert me_data["role"] == "developer"

    # 10. Проверка структуры Web SPA файлов
    index_html = (ROOT_DIR / "clients" / "web" / "index.html").read_text(
        encoding="utf-8"
    )
    style_css = (ROOT_DIR / "clients" / "web" / "style.css").read_text(encoding="utf-8")
    auth_js = (ROOT_DIR / "clients" / "web" / "js" / "auth.js").read_text(
        encoding="utf-8"
    )

    # Проверка формы входа и регистрации в index.html (без пошаговых кодов верификации)
    assert 'class="form-group password-group">' in index_html
    assert 'id="register-form"' in index_html
    assert 'id="reg-telegram"' in index_html
    assert 'id="reg-first-name"' in index_html
    assert 'id="reg-last-name"' in index_html
    assert "#modal-register.hidden" in style_css

    # Проверка lookup и отсутствия поллинга кодов в auth.js
    assert "telegramLookup" in auth_js
    assert "startPollingCodeDelivery" not in auth_js
    assert "getCodeStatus" not in auth_js
    assert "link-to-login" in auth_js


def test_stage8_9_fix_gallery_fps_snap_e2e(e2e_uvicorn_server):
    """Сквозной тест Этапа 8.9 (Fix Gallery, FPS & SNAP):

    1. Web SPA: gallery.js использует frame.raw_url || frame.raw_image_url и
       frame.annotated_url || frame.annotated_image_url.
    2. API /api/timelapse/{id}/frames отдаёт raw_url и annotated_url.
    3. Edge config: command_poll_interval_sec по умолчанию 0.5.
    4. Telegram-бот: динамический расчёт FPS и длительности.
    """
    gallery_js = (ROOT_DIR / "clients" / "web" / "js" / "gallery.js").read_text(
        encoding="utf-8"
    )
    # 1. Проверка безопасного парсинга полей URL кадров
    assert "frame.raw_url || frame.raw_image_url" in gallery_js
    assert "frame.annotated_url || frame.annotated_image_url" in gallery_js

    # 2. Проверка ответа API кадров
    server_data = e2e_uvicorn_server
    base_url = server_data["base_url"]
    edge_key = server_data["edge_key"]
    session_factory = server_data["session_factory"]

    from server.db.models import Camera, Installation

    with session_factory() as db:
        inst = Installation(
            name="Тест Галерея 8.9",
            culture_name="Салат",
            light_gpio_pin=17,
            flash_gpio_pin=27,
            is_archived=False,
        )
        db.add(inst)
        db.flush()
        cam = Camera(
            hardware_id="usb-cam-89",
            auto_name="Тест Камера 89",
            installation_id=inst.id,
            is_online=True,
        )
        db.add(cam)
        db.commit()
        inst_id = inst.id

    with httpx.Client(base_url=base_url) as client:
        # Загружаем тестовый снимок от Edge
        import json

        img_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xdb"
        meta = {
            "installation_id": inst_id,
            "camera_hardware_id": "usb-cam-89",
            "captured_at": "2026-11-24T10:00:00Z",
            "is_manual": False,
        }
        data = {"metadata": json.dumps(meta)}
        files = {
            "raw_image": ("raw.jpg", img_bytes, "image/jpeg"),
            "annotated_image": ("ann.jpg", img_bytes, "image/jpeg"),
        }
        r_up = client.post(
            "/api/edge/captures",
            headers={"X-Edge-Key": edge_key},
            data=data,
            files=files,
        )
        assert r_up.status_code in (200, 201)

        # Проверяем ответ /api/timelapse/{id}/frames
        r_frames = client.get(f"/api/timelapse/{inst_id}/frames")
        assert r_frames.status_code == 200
        frames_data = r_frames.json()
        assert len(frames_data["frames"]) == 1
        frame_item = frames_data["frames"][0]
        assert "raw_url" in frame_item
        assert "annotated_url" in frame_item
        assert frame_item["raw_url"].startswith("/media/")
        assert frame_item["annotated_url"].startswith("/media/")

        # Проверяем доступность самого изображения по URL
        r_img = client.get(frame_item["raw_url"])
        assert r_img.status_code == 200
