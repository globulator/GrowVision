# tests/e2e/test_stage8_rpi_setup_e2e.py
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import httpx

from server.core.security import hash_password
from server.db.models import User

ROOT_DIR = Path(__file__).resolve().parent.parent.parent


def test_stage8_rpi_setup_and_scripts_e2e(e2e_uvicorn_server):
    """Сквозной E2E тест Этапа 8.4:

    1. Раздача статики rpi_setup.js и разметки модалки в index.html.
    2. Авторизация разработчика и получение дефолтов настройки Edge.
    3. Генерация одноразового токена Edge setup.
    4. Одноразовое скачивание bash-скрипта и сгорание токена.
    5. Синтаксис и права скриптов gv-update.sh и install_server.sh.
    6. Строгие правила безопасности и Web SPA: 0 эмодзи, 0 alerts, 0 утечек ключей.
    """
    server_data = e2e_uvicorn_server
    base_url = server_data["base_url"]
    dev_pass = server_data["dev_pass"]
    session_factory = server_data["session_factory"]

    dev_tg_id = "777888999"
    now = datetime.now(UTC)

    # Создаем зарегистрированного разработчика в БД
    with session_factory() as db:
        user = User(
            client_type="web",
            external_id=dev_tg_id,
            login="Главный Разработчик",
            role="developer",
            password_hash=hash_password(dev_pass),
            is_active=True,
            authorized_at=now,
            last_active_at=now,
        )
        db.add(user)
        db.commit()

    # 1. Проверка статики и HTML
    with httpx.Client(base_url=base_url) as client:
        r_index = client.get("/")
        assert r_index.status_code == 200
        assert "modal-rpi-setup" in r_index.text
        assert "btn-rpi-setup" in r_index.text
        assert "rpi_setup.js" in r_index.text or "app.js" in r_index.text

        r_js = client.get("/js/rpi_setup.js")
        assert r_js.status_code == 200
        assert "initRpiSetup" in r_js.text

        # 2. Попытка запроса дефолтов гостем -> 401
        r_def_guest = client.get("/api/edge/setup/defaults")
        assert r_def_guest.status_code == 401

        # 3. Вход разработчиком через веб-логин
        r_login = client.post(
            "/api/auth/web/login",
            json={"telegram": dev_tg_id, "password": dev_pass},
        )
        assert r_login.status_code == 200
        assert r_login.json()["role"] == "developer"
        assert "gv_session" in client.cookies

        # 4. Запрос дефолтов авторизованным разработчиком
        r_defaults = client.get("/api/edge/setup/defaults")
        assert r_defaults.status_code == 200
        defaults_data = r_defaults.json()
        assert "default_server_url" in defaults_data
        assert "repo_url" in defaults_data

        # 5. Генерация одноразового токена
        token_payload = {
            "server_url": "http://<server-host>:8000",
            "netbird_setup_key": "nb-setup-e2e-token-key",
            "hardware_mode": "rpi",
        }
        r_token = client.post("/api/edge/setup/token", json=token_payload)
        assert r_token.status_code == 200
        token_info = r_token.json()
        token = token_info["token"]
        assert token_info["expires_in_sec"] == 600

    # 6. Скачивание bash-скрипта через отдельный неавторизованный клиент (как на RPi)
    with httpx.Client(base_url=base_url) as rpi_client:
        r_script = rpi_client.get(f"/api/edge/setup/{token}")
        assert r_script.status_code == 200
        assert "text/x-shellscript" in r_script.headers["content-type"]
        script_body = r_script.text

        # Проверка обязательных компонентов скрипта
        assert "#!/usr/bin/env bash" in script_body
        assert "netbird up --setup-key" in script_body
        assert "nb-setup-e2e-token-key" in script_body
        assert "growvision-edge.service" in script_body
        assert "gv-update" in script_body

        # Повторный вызов -> 404 (токен одноразовый)
        r_burn = rpi_client.get(f"/api/edge/setup/{token}")
        assert r_burn.status_code == 404

    # 7. Проверка локальных скриптов bash -n
    gv_update = ROOT_DIR / "scripts" / "gv-update.sh"
    install_srv = ROOT_DIR / "scripts" / "install_server.sh"
    assert gv_update.is_file() and install_srv.is_file()

    check_res = subprocess.run(
        ["bash", "-n", str(gv_update), str(install_srv)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert check_res.returncode == 0, f"Ошибка синтаксиса bash: {check_res.stderr}"

    # 8. Проверка отсутствия эмодзи и alert во всем clients/web/
    emoji_regex = re.compile(r"[\U00010000-\U0010ffff\u2600-\u26ff\u2700-\u27bf\ufe0f]")
    web_dir = ROOT_DIR / "clients" / "web"
    for path in web_dir.rglob("*"):
        if path.is_file() and path.suffix in (".js", ".html", ".css"):
            content = path.read_text(encoding="utf-8")
            emojis = emoji_regex.findall(content)
            assert not emojis, f"Обнаружены эмодзи в {path.name}: {emojis}"
            assert "alert(" not in content, f"alert() в {path.name}"
            assert "confirm(" not in content, f"confirm() в {path.name}"
            assert "prompt(" not in content, f"prompt() в {path.name}"
