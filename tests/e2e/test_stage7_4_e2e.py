import subprocess
import threading
import time
from pathlib import Path

import httpx

from server.core.state import app_state
from server.db.models import EdgeCommand, User

CLI_PYTHON = (
    Path(__file__).resolve().parent.parent.parent
    / "clients"
    / "cli"
    / ".venv"
    / "bin"
    / "python"
)
ROOT_DIR = Path(__file__).resolve().parent.parent.parent


def run_cli_cmd(
    *args: str,
    server_url: str,
    client_key: str,
    session_file: Path,
    input_text: str | None = None,
) -> subprocess.CompletedProcess:
    """Запускает CLI через подпроцесс с изолированной сессией и настройками."""
    import os

    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT_DIR),
        "SERVER_URL": server_url,
        "CLIENT_API_KEY": client_key,
        "GROWVISION_SESSION_FILE": str(session_file),
    }
    return subprocess.run(
        [
            str(CLI_PYTHON),
            "-m",
            "clients.cli.main",
            *args,
        ],
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
        env=env,
        cwd=str(ROOT_DIR),
        timeout=15,
        start_new_session=True,
    )


def test_stage7_4_e2e_shared_pins_and_rename(e2e_uvicorn_server, tmp_path: Path):
    """Сквозной E2E тест этапа 7.4:

    1. Создание 2 установок с общим пином через CLI и проверка предупреждения.
    2. Программная синхронизация состояния реле по пину.
    3. Переименование пользователей через CLI (Developer) и через API (Biologist).
    """
    server_url = e2e_uvicorn_server["base_url"]
    session_factory = e2e_uvicorn_server["session_factory"]
    client_key = e2e_uvicorn_server["client_key"]
    edge_key = e2e_uvicorn_server["edge_key"]
    dev_pass = e2e_uvicorn_server["dev_pass"]
    cli_session = tmp_path / "cli_session.json"
    app_state.clear()

    # Посылаем heartbeat от edge, чтобы сервер знал, что RPi онлайн
    heartbeat_payload = {
        "software": {
            "edge_version": "0.1.0",
            "python_version": "3.12.0",
            "model_name": "model_test.onnx",
        },
        "hardware": {
            "board_model": "Raspberry Pi 5 Model B",
            "os_info": "Debian GNU/Linux 12 (bookworm)",
            "uptime_sec": 1200,
            "cpu_temp_c": 47.0,
            "cpu_usage_percent": 12.0,
            "ram_used_mb": 512,
            "ram_total_mb": 4096,
            "disk_free_gb": 16.0,
            "gpio_mode": "bcm",
        },
        "offline_queue_count": 0,
        "detected_cameras": [
            {"hardware_id": "cam-rpi-01", "auto_name": "RPi CSI Camera 1"}
        ],
    }

    with httpx.Client(base_url=server_url) as http_client:
        r_hb = http_client.post(
            "/api/edge/heartbeat",
            headers={"X-Edge-Key": edge_key},
            json=heartbeat_payload,
        )
        assert r_hb.status_code == 200

    # 1. Авторизация разработчика в CLI
    r_login = run_cli_cmd(
        "auth",
        "login",
        server_url=server_url,
        client_key=client_key,
        session_file=cli_session,
        input_text=f"developer\n{dev_pass}\n",
    )
    assert r_login.returncode == 0
    assert "Авторизация успешна" in r_login.stdout

    # 2. Создаем Установку 1 (Альфа) с пинами 4 и 5
    r_add1 = run_cli_cmd(
        "unit",
        "add",
        "--name",
        "Альфа",
        "--culture",
        "Томат",
        "--light-pin",
        "4",
        "--flash-pin",
        "5",
        server_url=server_url,
        client_key=client_key,
        session_file=cli_session,
    )
    assert r_add1.returncode == 0
    assert "Установка успешно добавлена" in r_add1.stdout

    # 3. Создаем Установку 2 (Бета) с тем же пином света 4
    r_add2 = run_cli_cmd(
        "unit",
        "add",
        "--name",
        "Бета",
        "--culture",
        "Огурец",
        "--light-pin",
        "4",
        "--flash-pin",
        "6",
        server_url=server_url,
        client_key=client_key,
        session_file=cli_session,
    )
    assert r_add2.returncode == 0
    assert "Установка успешно добавлена" in r_add2.stdout
    assert "⚠️ Пин 4 уже занят («Альфа»). Реле будут общими." in r_add2.stdout

    # Фоновый поток для авто-завершения команд edge
    stop_event = threading.Event()

    def auto_complete_commands():
        while not stop_event.is_set():
            time.sleep(0.05)
            db = session_factory()
            cmds = db.query(EdgeCommand).filter(EdgeCommand.status == "pending").all()
            for cmd in cmds:
                cmd.status = "done"
                cmd.result_message = "OK"
            db.commit()
            db.close()

    bg_worker = threading.Thread(target=auto_complete_commands, daemon=True)
    bg_worker.start()

    try:
        # 4. Включаем свет на Установке "Альфа" через CLI
        r_light = run_cli_cmd(
            "light",
            "main",
            "Альфа",
            "on",
            server_url=server_url,
            client_key=client_key,
            session_file=cli_session,
        )
        assert r_light.returncode == 0

        # 5. Проверяем карточку Установки "Бета" — свет на пине 4 должен быть ON!
        r_show = run_cli_cmd(
            "unit",
            "show",
            "Бета",
            server_url=server_url,
            client_key=client_key,
            session_file=cli_session,
        )
        assert r_show.returncode == 0
        assert "GPIO 4 ● ON" in r_show.stdout

        # 6. Переименование пользователей:
        # Создаем сессию биолога в БД
        db = session_factory()
        bio_user = User(
            client_type="telegram",
            external_id="1234567",
            login="Анна Первоначальная",
            role="biologist",
            is_active=True,
        )
        db.add(bio_user)
        db.commit()
        bio_user_id = bio_user.id
        db.close()

        # Разработчик переименовывает биолога через CLI: auth rename
        r_rename = run_cli_cmd(
            "auth",
            "rename",
            str(bio_user_id),
            "Анна Модифицированная",
            server_url=server_url,
            client_key=client_key,
            session_file=cli_session,
        )
        assert r_rename.returncode == 0
        assert (
            f"Пользователь сессии #{bio_user_id} успешно переименован в «Анна Модифицированная»"
            in r_rename.stdout
        )

        # Биолог сам переименовывает себя через PATCH /api/auth/me
        with httpx.Client(base_url=server_url) as http_client:
            bio_hdrs = {
                "X-Client-Key": client_key,
                "X-Client-Type": "telegram",
                "X-External-Id": "1234567",
            }
            r_me = http_client.patch(
                "/api/auth/me",
                headers=bio_hdrs,
                json={"login": "Анна Финальная"},
            )
            assert r_me.status_code == 200
            assert r_me.json()["login"] == "Анна Финальная"

            # Проверяем через GET /api/auth/me
            r_get_me = http_client.get("/api/auth/me", headers=bio_hdrs)
            assert r_get_me.json()["login"] == "Анна Финальная"

    finally:
        stop_event.set()
        bg_worker.join(timeout=1.0)
