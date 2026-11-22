import json
import subprocess
from pathlib import Path
from typing import Any

import httpx

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
BOT_PYTHON = ROOT_DIR / "clients" / "telegram_bot" / ".venv" / "bin" / "python"


def run_fake_bot_scenario(
    scenario: str,
    server_url: str,
    client_key: str,
    dev_pass: str = "dev-pass-123",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Запускает изолированный E2E сценарий бота с фейковым Telegram Bot API."""
    cmd = [
        str(BOT_PYTHON),
        "-m",
        "tests.e2e.fake_telegram",
        "--scenario",
        scenario,
        "--server-url",
        server_url,
        "--client-key",
        client_key,
        "--dev-pass",
        dev_pass,
        "--extra",
        json.dumps(extra or {}),
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT_DIR),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"Сценарий {scenario} завершился с ошибкой (код {proc.returncode}):\n"
            f"STDOUT:\n{proc.stdout}\n"
            f"STDERR:\n{proc.stderr}"
        )
    return json.loads(proc.stdout.strip())


def test_bot_e2e_profile_dates_valid(e2e_uvicorn_server):
    """E2E: Вход в систему и просмотр профиля с реальными датами без ложных прочерков."""
    srv = e2e_uvicorn_server
    res = run_fake_bot_scenario(
        scenario="profile_and_dates",
        server_url=srv["base_url"],
        client_key=srv["client_key"],
        dev_pass=srv["dev_pass"],
    )
    assert res.get("ok") is True
    p_text = res.get("profile_text", "")
    assert "Логин:</b>" in p_text
    assert "Роль:</b> Разработчик" in p_text
    assert "Время входа:</b>" in p_text
    assert "Последняя активность:</b>" in p_text
    assert "—" not in p_text


def test_bot_e2e_installation_create_name_validation_and_aruco_default(
    e2e_uvicorn_server,
):
    """E2E: Создание установки с проверкой занятого имени и ArUco 50 мм по умолчанию."""
    srv = e2e_uvicorn_server

    # Авторизуемся разработчиком и создаем исходную установку на сервере
    hdrs = {
        "X-Client-Key": srv["client_key"],
        "X-Client-Type": "cli",
        "X-External-Id": "e2e-setup-admin",
    }
    with httpx.Client(base_url=srv["base_url"], headers=hdrs) as client:
        r_login = client.post(
            "/api/auth/login",
            json={
                "login": "admin",
                "client_type": "cli",
                "external_id": "e2e-setup-admin",
                "password": srv["dev_pass"],
            },
        )
        assert r_login.status_code == 200

        r_create = client.post(
            "/api/installations",
            json={
                "name": "Существующая Установка",
                "culture_name": "Шпинат",
                "light_gpio_pin": 10,
                "flash_gpio_pin": 11,
                "aruco_size_mm": 40.0,
            },
        )
        assert r_create.status_code == 201

    # Запускаем сценарий бота, проверяющий дубль имени и ArUco 50 мм по умолчанию
    res = run_fake_bot_scenario(
        scenario="installation_create_name_validation_and_aruco_default",
        server_url=srv["base_url"],
        client_key=srv["client_key"],
        dev_pass=srv["dev_pass"],
        extra={"existing_name": "Существующая Установка"},
    )
    assert res.get("ok") is True
    card_text = res.get("card_text", "")
    assert "Уникальный Базилик E2E" in card_text
    assert "Базилик Изумрудный" in card_text
    assert "50.0 мм" in card_text
    assert "Пин света (GPIO):</b> 17" in card_text
    assert "Пин вспышки (GPIO):</b> 27" in card_text


def test_bot_e2e_callback_user_id_system_and_installations(e2e_uvicorn_server):
    """E2E: Регрессионная проверка user_id в callback_query (где callback.message.from_user — бот)."""
    srv = e2e_uvicorn_server

    # Создаем установку на сервере для теста
    hdrs = {
        "X-Client-Key": srv["client_key"],
        "X-Client-Type": "cli",
        "X-External-Id": "e2e-setup-admin-2",
    }
    inst_id = 1
    with httpx.Client(base_url=srv["base_url"], headers=hdrs) as client:
        r_log = client.post(
            "/api/auth/login",
            json={
                "login": "admin",
                "client_type": "cli",
                "external_id": "e2e-setup-admin-2",
                "password": srv["dev_pass"],
            },
        )
        assert r_log.status_code == 200
        r_create = client.post(
            "/api/installations",
            json={
                "name": "Стеллаж Регресс Коллбэк",
                "culture_name": "Салат Романо",
                "light_gpio_pin": 12,
                "flash_gpio_pin": 13,
                "aruco_size_mm": 50.0,
            },
        )
        if r_create.status_code == 201:
            inst_id = r_create.json()["id"]

    res = run_fake_bot_scenario(
        scenario="callback_user_id_system_and_installations",
        server_url=srv["base_url"],
        client_key=srv["client_key"],
        dev_pass=srv["dev_pass"],
        extra={"installation_id": inst_id},
    )
    assert res.get("ok") is True


def test_bot_e2e_unknown_message_fallback(e2e_uvicorn_server):
    """E2E: Ответ бота на неизвестное сообщение вне FSM."""
    srv = e2e_uvicorn_server
    res = run_fake_bot_scenario(
        scenario="unknown_message_fallback",
        server_url=srv["base_url"],
        client_key=srv["client_key"],
    )
    assert res.get("ok") is True
    assert res.get("answer") == "Не понял. Список команд: /help"


def test_bot_e2e_bug_note_preserves_fsm(e2e_uvicorn_server):
    """E2E: Баг-заметка через префикс * сохраняет активное FSM-состояние мастера."""
    srv = e2e_uvicorn_server
    res = run_fake_bot_scenario(
        scenario="bug_note_preserves_fsm",
        server_url=srv["base_url"],
        client_key=srv["client_key"],
        dev_pass=srv["dev_pass"],
    )
    assert res.get("ok") is True
    assert "BUG-LOG-" in res.get("bug_tag", "")
    assert "Шаг 2 из 5" in res.get("step2", "")
