import io
import json
import os
import subprocess
import threading
import time
from pathlib import Path

import httpx

from server.db.models import Installation

CLI_PYTHON = (
    Path(__file__).resolve().parent.parent.parent
    / "clients"
    / "cli"
    / ".venv"
    / "bin"
    / "python"
)
ROOT_DIR = Path(__file__).resolve().parent.parent.parent

TINY_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07"
    b"\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c"
    b"(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00\x02\x00\x02\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01"
    b"\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00"
    b"\x00?\x00\xbf\x00\xff\xd9"
)


def run_cli_cmd(
    *args: str,
    server_url: str,
    client_key: str,
    session_file: Path,
    input_text: str | None = None,
) -> subprocess.CompletedProcess:
    """Запускает CLI через подпроцесс с изолированной сессией и настройками."""
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
        env=env,
        cwd=str(ROOT_DIR),
        timeout=15,
        start_new_session=True,
        check=False,
    )


# ==============================================================================
# 1. Проверка справки и информации о разделах
# ==============================================================================
def test_cli_help_and_info_commands(e2e_uvicorn_server):
    """Критерий 1: help и <раздел> info для всех 6 разделов возвращают 0 и содержат примеры."""
    srv = e2e_uvicorn_server
    session_file = srv["tmp_path"] / "cli_session_help.json"

    # 1. Общая справка
    res_help = run_cli_cmd(
        "help",
        server_url=srv["base_url"],
        client_key=srv["client_key"],
        session_file=session_file,
    )
    assert res_help.returncode == 0
    assert "GrowVision" in res_help.stdout
    assert "help" in res_help.stdout
    assert "auth" in res_help.stdout
    assert "unit" in res_help.stdout
    assert "snap" in res_help.stdout

    # 2. Все 6 разделов info
    sections = ["auth", "unit", "camera", "light", "snap", "system"]
    for sec in sections:
        res_info = run_cli_cmd(
            sec,
            "info",
            server_url=srv["base_url"],
            client_key=srv["client_key"],
            session_file=session_file,
        )
        assert res_info.returncode == 0, (
            f"{sec} info завершился с кодом {res_info.returncode}: {res_info.stderr}"
        )
        assert f"Раздел {sec}" in res_info.stdout
        assert "Примеры:" in res_info.stdout


# ==============================================================================
# 2. Проверка изоляции импортов
# ==============================================================================
def test_cli_import_isolation():
    """Критерий 2: CLI не импортирует модули server/, shared/, edge_agent/."""
    cli_dir = ROOT_DIR / "clients" / "cli"
    cmd = (
        'grep -rn --exclude-dir="__pycache__" '
        '"from server\\|from shared\\|from edge_agent\\|import server\\|import shared" '
        f'"{cli_dir}"'
    )
    res = subprocess.run(
        cmd,
        shell=True,
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode != 0 or not res.stdout.strip(), (
        f"Обнаружены запрещённые импорты в clients/cli:\n{res.stdout}"
    )


# ==============================================================================
# 3. Сквозной сценарий e2e (полный жизненный цикл)
# ==============================================================================
def test_cli_e2e_full_lifecycle(e2e_uvicorn_server):
    """Критерий 3: Сквозной e2e сценарий: heartbeat, login, unit, camera, light, snap, video."""
    srv = e2e_uvicorn_server
    base_url = srv["base_url"]
    client_key = srv["client_key"]
    edge_key = srv["edge_key"]
    session_file = srv["tmp_path"] / "cli_session_lifecycle.json"

    # 1. Отправляем Heartbeat от RPi, чтобы сервер был онлайн
    hb_payload = {
        "software": {
            "edge_version": "0.1.0",
            "python_version": "3.12.0",
            "model_name": "model_test.onnx",
        },
        "hardware": {
            "board_model": "Raspberry Pi 5 Model B",
            "os_info": "Debian GNU/Linux 12 (bookworm)",
            "uptime_sec": 3600,
            "cpu_temp_c": 45.0,
            "cpu_usage_percent": 15.0,
            "ram_used_mb": 600,
            "ram_total_mb": 4096,
            "disk_free_gb": 18.0,
            "gpio_mode": "bcm",
        },
        "offline_queue_count": 0,
        "detected_cameras": [
            {"hardware_id": "cam-e2e-01", "auto_name": "CSI Camera 1"}
        ],
    }
    with httpx.Client(base_url=base_url) as http_client:
        r_hb = http_client.post(
            "/api/edge/heartbeat",
            headers={"X-Edge-Key": edge_key},
            json=hb_payload,
        )
        assert r_hb.status_code == 200

    # 2. Проверяем version
    res_ver = run_cli_cmd(
        "version",
        server_url=base_url,
        client_key=client_key,
        session_file=session_file,
    )
    assert res_ver.returncode == 0
    assert "GrowVision" in res_ver.stdout
    assert "server" in res_ver.stdout.lower()

    # 3. До входа роль Гость: unit list
    res_unit_guest = run_cli_cmd(
        "unit",
        "list",
        server_url=base_url,
        client_key=client_key,
        session_file=session_file,
    )
    assert res_unit_guest.returncode == 0
    assert "Установок пока нет" in res_unit_guest.stdout

    # 4. Вход под Developer
    res_login = run_cli_cmd(
        "auth",
        "login",
        server_url=base_url,
        client_key=client_key,
        session_file=session_file,
        input_text=f"developer\n{srv['dev_pass']}\n",
    )
    assert res_login.returncode == 0
    assert "Авторизация успешна" in res_login.stdout
    assert "developer" in res_login.stdout

    # Проверка auth status
    res_status = run_cli_cmd(
        "auth",
        "status",
        server_url=base_url,
        client_key=client_key,
        session_file=session_file,
    )
    assert res_status.returncode == 0
    assert "developer" in res_status.stdout

    # 5. Добавление установки: unit add
    res_add = run_cli_cmd(
        "unit",
        "add",
        "--name",
        "E2E Hydro Box",
        "--culture",
        "Томат",
        "--light-pin",
        "10",
        "--flash-pin",
        "11",
        "--aruco-mm",
        "40.0",
        server_url=base_url,
        client_key=client_key,
        session_file=session_file,
    )
    assert res_add.returncode == 0
    assert "Установка успешно добавлена" in res_add.stdout
    assert "E2E Hydro Box" in res_add.stdout

    # 6. unit list и unit show
    res_list = run_cli_cmd(
        "unit",
        "list",
        server_url=base_url,
        client_key=client_key,
        session_file=session_file,
    )
    assert res_list.returncode == 0
    assert "E2E Hydro Box" in res_list.stdout
    assert "10" in res_list.stdout
    assert "11" in res_list.stdout

    res_show = run_cli_cmd(
        "unit",
        "show",
        "1",
        server_url=base_url,
        client_key=client_key,
        session_file=session_file,
    )
    assert res_show.returncode == 0
    assert "E2E Hydro Box" in res_show.stdout

    # Вспомогательная функция для эмуляции ожидания команд агентом RPi
    def rpi_poll_and_handle(handler, timeout=6.0):
        start_t = time.monotonic()
        with httpx.Client(base_url=base_url) as http_client:
            while time.monotonic() - start_t < timeout:
                time.sleep(0.05)
                r_cl = http_client.post(
                    "/api/edge/commands/claim", headers={"X-Edge-Key": edge_key}
                )
                if r_cl.status_code == 200 and r_cl.json():
                    for cmd in r_cl.json():
                        handler(http_client, cmd)
                    return

    # 7. camera scan и camera list
    def handle_scan(http_client, cmd):
        c_id = cmd["id"]
        http_client.post(
            f"/api/edge/commands/{c_id}/result",
            headers={"X-Edge-Key": edge_key},
            json={
                "status": "done",
                "result_message": "Found 1 cam",
                "scanned_cameras": [
                    {"hardware_id": "cam-e2e-01", "auto_name": "CSI Camera 1"}
                ],
            },
        )

    th_scan = threading.Thread(
        target=lambda: rpi_poll_and_handle(handle_scan), daemon=True
    )
    th_scan.start()

    res_scan = run_cli_cmd(
        "camera",
        "scan",
        server_url=base_url,
        client_key=client_key,
        session_file=session_file,
    )
    th_scan.join(timeout=5.0)
    assert res_scan.returncode == 0
    assert "cam-e2e-01" in res_scan.stdout

    res_cams = run_cli_cmd(
        "camera",
        "list",
        server_url=base_url,
        client_key=client_key,
        session_file=session_file,
    )
    assert res_cams.returncode == 0
    assert "cam-e2e-01" in res_cams.stdout

    res_bind = run_cli_cmd(
        "camera",
        "bind",
        "1",
        "1",
        server_url=base_url,
        client_key=client_key,
        session_file=session_file,
    )
    assert res_bind.returncode == 0
    assert "привязана к установке" in res_bind.stdout

    # 8. Управление светом light main 1 on с эмуляцией ответа RPi
    def handle_light(http_client, cmd):
        c_id = cmd["id"]
        http_client.post(
            f"/api/edge/commands/{c_id}/result",
            headers={"X-Edge-Key": edge_key},
            json={"status": "done", "result_message": "Relay ON OK"},
        )

    th_light = threading.Thread(
        target=lambda: rpi_poll_and_handle(handle_light), daemon=True
    )
    th_light.start()

    res_light = run_cli_cmd(
        "light",
        "main",
        "1",
        "on",
        server_url=base_url,
        client_key=client_key,
        session_file=session_file,
    )
    th_light.join(timeout=5.0)
    assert res_light.returncode == 0
    assert "основного света" in res_light.stdout.lower()
    assert "relay on ok" in res_light.stdout.lower()

    # 9. snap take 1 с эмуляцией выполнения снимка mock RPi
    def handle_snap(http_client, cmd):
        c_id = cmd["id"]
        meta = {
            "installation_id": 1,
            "camera_hardware_id": "cam-e2e-01",
            "captured_at": "2026-11-20T17:00:00Z",
            "is_manual": True,
            "aruco_area_px": 1600.0,
            "command_id": c_id,
            "measurements": [{"plant_index": 0, "area_px": 3200.0}],
        }
        http_client.post(
            "/api/edge/captures",
            data={"metadata": json.dumps(meta)},
            files={
                "raw_image": (
                    "raw_snap.jpg",
                    io.BytesIO(TINY_JPEG),
                    "image/jpeg",
                ),
                "annotated_image": (
                    "ann_snap.jpg",
                    io.BytesIO(TINY_JPEG),
                    "image/jpeg",
                ),
            },
            headers={"X-Edge-Key": edge_key},
        )
        http_client.post(
            f"/api/edge/commands/{c_id}/result",
            headers={"X-Edge-Key": edge_key},
            json={"status": "done", "result_message": "Snap completed"},
        )

    th_snap = threading.Thread(
        target=lambda: rpi_poll_and_handle(handle_snap), daemon=True
    )
    th_snap.start()

    download_dir = srv["tmp_path"] / "downloads_cli"
    res_snap = run_cli_cmd(
        "snap",
        "take",
        "1",
        "--download",
        str(download_dir),
        server_url=base_url,
        client_key=client_key,
        session_file=session_file,
    )
    th_snap.join(timeout=5.0)
    assert res_snap.returncode == 0
    assert "Полученные снимки" in res_snap.stdout
    assert "32.0 см²" in res_snap.stdout
    # Проверяем, что файлы скачались
    downloaded = list(download_dir.iterdir())
    assert len(downloaded) >= 2

    # 10. Загружаем 2 ПЛАНОВЫХ кадра через /api/edge/captures для сборки видео
    with httpx.Client(base_url=base_url) as http_client:
        for idx, cap_time in enumerate(
            ["2026-11-20T18:00:00Z", "2026-11-20T19:00:00Z"], start=1
        ):
            meta_plan = {
                "installation_id": 1,
                "camera_hardware_id": "cam-e2e-01",
                "captured_at": cap_time,
                "is_manual": False,
                "aruco_area_px": 1600.0,
                "measurements": [{"plant_index": 0, "area_px": 3200.0 + idx * 200}],
            }
            r_up = http_client.post(
                "/api/edge/captures",
                data={"metadata": json.dumps(meta_plan)},
                files={
                    "raw_image": (
                        f"plan_raw_{idx}.jpg",
                        io.BytesIO(TINY_JPEG),
                        "image/jpeg",
                    ),
                    "annotated_image": (
                        f"plan_ann_{idx}.jpg",
                        io.BytesIO(TINY_JPEG),
                        "image/jpeg",
                    ),
                },
                headers={"X-Edge-Key": edge_key},
            )
            assert r_up.status_code == 201

    video_out = srv["tmp_path"] / "timelapse_test.mp4"
    res_video = run_cli_cmd(
        "snap",
        "video",
        "1",
        "--out",
        str(video_out),
        server_url=base_url,
        client_key=client_key,
        session_file=session_file,
    )
    assert res_video.returncode == 0
    assert "Видео таймлапса сохранено" in res_video.stdout
    assert video_out.is_file()
    assert video_out.stat().st_size > 100

    # 11. Проверка обработки 1 кадра в snap video -> сохранение как .jpg
    session_factory = srv["session_factory"]
    with session_factory() as db:
        inst2 = Installation(
            name="Single Frame Box",
            culture_name="Мята",
            light_gpio_pin=30,
            flash_gpio_pin=31,
            is_archived=False,
        )
        db.add(inst2)
        db.commit()
        inst2_id = inst2.id

    with httpx.Client(base_url=base_url) as http_client:
        meta_single = {
            "installation_id": inst2_id,
            "camera_hardware_id": "cam-e2e-01",
            "captured_at": "2026-11-20T20:00:00Z",
            "is_manual": False,
        }
        r_single = http_client.post(
            "/api/edge/captures",
            data={"metadata": json.dumps(meta_single)},
            files={
                "raw_image": ("single.jpg", io.BytesIO(TINY_JPEG), "image/jpeg"),
                "annotated_image": (
                    "single.jpg",
                    io.BytesIO(TINY_JPEG),
                    "image/jpeg",
                ),
            },
            headers={"X-Edge-Key": edge_key},
        )
        assert r_single.status_code == 201

    single_out = srv["tmp_path"] / "single_frame.jpg"
    res_single = run_cli_cmd(
        "snap",
        "video",
        str(inst2_id),
        "--out",
        str(single_out),
        server_url=base_url,
        client_key=client_key,
        session_file=session_file,
    )
    assert res_single.returncode == 0
    assert "вместо видео сохранено фото" in res_single.stdout
    assert single_out.is_file()
    assert single_out.read_bytes() == TINY_JPEG

    # 12. auth logout
    res_logout = run_cli_cmd(
        "auth",
        "logout",
        server_url=base_url,
        client_key=client_key,
        session_file=session_file,
    )
    assert res_logout.returncode == 0
    assert "Сессия завершена" in res_logout.stdout


# ==============================================================================
# 4. Негативные сценарии (ошибки, коды выхода 1, без трейсбеков)
# ==============================================================================
def test_cli_e2e_negative_scenarios(e2e_uvicorn_server):
    """Критерий 4: Неверный пароль, 403, недоступный сервер, неверный target без трейсбеков (код 1)."""
    srv = e2e_uvicorn_server
    base_url = srv["base_url"]
    client_key = srv["client_key"]
    session_file = srv["tmp_path"] / "cli_session_neg.json"

    # 1. Неверный пароль при auth login -> код 1, без трейсбека
    res_bad_pass = run_cli_cmd(
        "auth",
        "login",
        server_url=base_url,
        client_key=client_key,
        session_file=session_file,
        input_text="developer\nwrong_password_here\n",
    )
    assert res_bad_pass.returncode == 1
    assert "Ошибка" in res_bad_pass.stderr
    assert "Неверный пароль" in res_bad_pass.stderr
    assert "Traceback" not in res_bad_pass.stderr

    # 2. Недостаточные права (403): вход под Biologist и попытка camera scan
    res_login_bio = run_cli_cmd(
        "auth",
        "login",
        server_url=base_url,
        client_key=client_key,
        session_file=session_file,
        input_text=f"biologist\n{srv['bio_pass']}\n",
    )
    assert res_login_bio.returncode == 0

    res_forbidden = run_cli_cmd(
        "camera",
        "scan",
        server_url=base_url,
        client_key=client_key,
        session_file=session_file,
    )
    assert res_forbidden.returncode == 1
    assert "Ошибка" in res_forbidden.stderr
    assert "Недостаточно прав" in res_forbidden.stderr
    assert "Traceback" not in res_forbidden.stderr

    # 3. Сервер недоступен -> сообщение по CLI_SPEC.md разд. 4, код 1
    dead_port = srv["port"] + 50
    res_unavail = run_cli_cmd(
        "unit",
        "list",
        server_url=f"http://127.0.0.1:{dead_port}",
        client_key=client_key,
        session_file=session_file,
    )
    assert res_unavail.returncode == 1
    assert "Не удалось подключиться к серверу" in res_unavail.stderr
    assert "Traceback" not in res_unavail.stderr

    # 4. Неверный target установки -> код 1
    res_not_found = run_cli_cmd(
        "unit",
        "show",
        "99999",
        server_url=base_url,
        client_key=client_key,
        session_file=session_file,
    )
    assert res_not_found.returncode == 1
    assert "Установка не найдена" in res_not_found.stderr
    assert "Traceback" not in res_not_found.stderr
