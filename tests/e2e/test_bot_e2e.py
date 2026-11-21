import io
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import httpx

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
BOT_PYTHON = ROOT_DIR / "clients" / "telegram_bot" / ".venv" / "bin" / "python"
RUNNER_SCRIPT = ROOT_DIR / "tests" / "e2e" / "bot_e2e_runner.py"

TINY_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07"
    b"\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c"
    b"(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00\x02\x00\x02\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01"
    b"\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00"
    b"\x00?\x00\xbf\x00\xff\xd9"
)


def run_bot_scenario(
    scenario_name: str,
    server_url: str,
    client_key: str,
    dev_pass: str = "dev-pass-123",
    bio_pass: str = "bio-pass-123",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Запускает сценарий бота в изолированном виртуальном окружении бота."""
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT_DIR),
    }
    cmd = [
        str(BOT_PYTHON),
        str(RUNNER_SCRIPT),
        "--scenario",
        scenario_name,
        "--server-url",
        server_url,
        "--client-key",
        client_key,
        "--dev-pass",
        dev_pass,
        "--bio-pass",
        bio_pass,
        "--extra",
        json.dumps(extra or {}),
    ]

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ROOT_DIR),
        timeout=30,
        check=False,
    )

    if proc.returncode != 0:
        raise RuntimeError(
            f"Scenario {scenario_name} failed with code {proc.returncode}.\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )

    # Ищем последнюю строку с JSON
    for line in reversed(proc.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            res = json.loads(line)
            if not res.get("success"):
                raise RuntimeError(
                    f"Scenario error: {res.get('error')}\n{res.get('traceback')}"
                )
            return res.get("result", {})

    raise RuntimeError(
        f"Could not parse runner output:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )


def rpi_poll_and_handle(
    base_url: str,
    edge_key: str,
    handler,
    stop_event: threading.Event,
    timeout: float = 6.0,
):
    """Фоновый поллер команд от mock RPi."""
    start_t = time.monotonic()
    with httpx.Client(base_url=base_url) as http_client:
        while time.monotonic() - start_t < timeout and not stop_event.is_set():
            time.sleep(0.04)
            try:
                r_cl = http_client.post(
                    "/api/edge/commands/claim", headers={"X-Edge-Key": edge_key}
                )
                if r_cl.status_code == 200 and r_cl.json():
                    for cmd in r_cl.json():
                        handler(http_client, cmd)
            except Exception:  # noqa: BLE001, S110
                pass


# ==============================================================================
# 1. Архитектура и строгая изоляция
# ==============================================================================
def test_bot_isolation():
    """Критерий 1: Проверка запрещённых импортов из server, shared, edge_agent, clients.cli."""
    res = subprocess.run(
        [
            "grep",
            "-rn",
            "--exclude-dir=.venv",
            "--include=*.py",
            "from server\\|from shared\\|from edge_agent\\|from clients.cli\\|import server\\|import shared",
            "clients/telegram_bot",
        ],
        cwd=str(ROOT_DIR),
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode != 0, f"Found forbidden imports:\n{res.stdout}"
    assert res.stdout.strip() == ""


# ==============================================================================
# 2. Безопасность и работа с секретами
# ==============================================================================
def test_bot_config_and_security():
    """Критерий 2: Маскирование секретов в repr BotConfig и наличие .gitignore."""
    # Проверка .gitignore
    gitignore_path = ROOT_DIR / "clients" / "telegram_bot" / ".gitignore"
    assert gitignore_path.exists()
    content = gitignore_path.read_text(encoding="utf-8")
    assert ".env" in content
    assert ".venv" in content
    assert "__pycache__" in content

    # Проверка маскирования секретов через подпроцесс bot python
    code = """
from clients.telegram_bot.config import BotConfig
cfg = BotConfig(bot_token="123456:SECRET_BOT_TOKEN_999", server_url="http://localhost:8000", client_api_key="SUPER_SECRET_CLIENT_KEY")
repr_str = repr(cfg)
assert "SECRET_BOT_TOKEN_999" not in repr_str
assert "SUPER_SECRET_CLIENT_KEY" not in repr_str
print("OK")
"""
    res = subprocess.run(
        [str(BOT_PYTHON), "-c", code],
        cwd=str(ROOT_DIR),
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0
    assert "OK" in res.stdout


# ==============================================================================
# 3. Полный жизненный цикл авторизации (Сквозной e2e)
# ==============================================================================
def test_bot_e2e_auth_lifecycle(e2e_uvicorn_server):
    """Критерии 2, 3: Двухэтапный вход, удаление пароля из чата, переключение ролей и выход."""
    srv = e2e_uvicorn_server
    base_url = srv["base_url"]
    client_key = srv["client_key"]
    dev_pass = srv["dev_pass"]

    res = run_bot_scenario(
        scenario_name="auth_lifecycle",
        server_url=base_url,
        client_key=client_key,
        dev_pass=dev_pass,
    )

    assert res.get("status") == "ok"
    assert res.get("password_deleted") is True
    assert res.get("roles_switched_properly") is True


# ==============================================================================
# 4. Установки, камеры, сканирование и привязка
# ==============================================================================
def test_bot_e2e_installations_and_cameras(e2e_uvicorn_server):
    """Критерии 3, 4, 7: Сканирование камер RPi, привязка к установке, просмотр карточки и пагинатор."""
    srv = e2e_uvicorn_server
    base_url = srv["base_url"]
    client_key = srv["client_key"]
    edge_key = srv["edge_key"]
    dev_pass = srv["dev_pass"]

    # 1. Отправляем heartbeat от RPi
    hb_payload = {
        "software": {
            "edge_version": "0.1.0-bot-e2e",
            "python_version": "3.12.0",
            "model_name": "model_test.onnx",
        },
        "hardware": {
            "board_model": "Raspberry Pi 5 Model B",
            "os_info": "Debian GNU/Linux 12",
            "uptime_sec": 3600,
            "cpu_temp_c": 45.0,
            "cpu_usage_percent": 10.0,
            "ram_used_mb": 512,
            "ram_total_mb": 4096,
            "disk_free_gb": 20.0,
            "gpio_mode": "bcm",
        },
        "offline_queue_count": 0,
        "detected_cameras": [
            {"hardware_id": "cam-bot-01", "auto_name": "USB WebCam 1"}
        ],
    }
    with httpx.Client(base_url=base_url) as cl:
        r_hb = cl.post(
            "/api/edge/heartbeat",
            headers={"X-Edge-Key": edge_key},
            json=hb_payload,
        )
        assert r_hb.status_code == 200

    # 2. Создаем тестовую установку через API
    with httpx.Client(base_url=base_url) as cl:
        # Авторизуемся в API под Developer
        r_auth = cl.post(
            "/api/auth/login",
            headers={
                "X-Client-Key": client_key,
                "X-Client-Type": "telegram",
                "X-External-Id": "100",
            },
            json={
                "login": "Dev Setup",
                "password": dev_pass,
                "client_type": "telegram",
                "external_id": "100",
            },
        )
        assert r_auth.status_code == 200

        r_inst = cl.post(
            "/api/installations",
            headers={
                "X-Client-Key": client_key,
                "X-Client-Type": "telegram",
                "X-External-Id": "100",
            },
            json={
                "name": "Bot Test Box",
                "culture_name": "Томат E2E",
                "light_gpio_pin": 12,
                "flash_gpio_pin": 13,
                "aruco_size_mm": 50.0,
            },
        )
        assert r_inst.status_code == 201
        inst_id = r_inst.json()["id"]

    # 3. Запускаем фоновый обработчик команд RPi (для scan_cameras)
    stop_ev = threading.Event()

    def handle_scan(http_client, cmd):
        c_id = cmd["id"]
        if cmd.get("command_type") in ("scan_cameras", "SCAN_CAMERAS"):
            http_client.post(
                f"/api/edge/commands/{c_id}/result",
                headers={"X-Edge-Key": edge_key},
                json={
                    "status": "done",
                    "result_message": "Found 1 camera",
                    "scanned_cameras": [
                        {
                            "hardware_id": "cam-bot-01",
                            "auto_name": "USB WebCam 1",
                        }
                    ],
                },
            )

    th = threading.Thread(
        target=lambda: rpi_poll_and_handle(base_url, edge_key, handle_scan, stop_ev),
        daemon=True,
    )
    th.start()

    try:
        res = run_bot_scenario(
            scenario_name="installations_and_cameras",
            server_url=base_url,
            client_key=client_key,
            dev_pass=dev_pass,
            extra={
                "installation_id": inst_id,
                "camera_hardware_id": "cam-bot-01",
            },
        )
        assert res.get("status") == "ok"
        assert res.get("callbacks_validated") is True
    finally:
        stop_ev.set()
        th.join(timeout=3.0)


# ==============================================================================
# 5. Управление светом и ручная фотосъёмка snap (альбом из 2 фото)
# ==============================================================================
def test_bot_e2e_control_and_snap(e2e_uvicorn_server):
    """Критерии 5, 7: Управление светом и ручная фотосъёмка с отправкой альбома из двух фото."""
    srv = e2e_uvicorn_server
    base_url = srv["base_url"]
    client_key = srv["client_key"]
    edge_key = srv["edge_key"]
    dev_pass = srv["dev_pass"]

    # 1. Heartbeat от RPi
    with httpx.Client(base_url=base_url) as cl:
        cl.post(
            "/api/edge/heartbeat",
            headers={"X-Edge-Key": edge_key},
            json={
                "software": {
                    "edge_version": "0.1.0",
                    "python_version": "3.12.0",
                    "model_name": "model_test.onnx",
                },
                "hardware": {
                    "board_model": "RPi 5",
                    "os_info": "Linux",
                    "uptime_sec": 1000,
                    "cpu_temp_c": 40.0,
                    "cpu_usage_percent": 5.0,
                    "ram_used_mb": 400,
                    "ram_total_mb": 4096,
                    "disk_free_gb": 10.0,
                    "gpio_mode": "bcm",
                },
                "offline_queue_count": 0,
                "detected_cameras": [
                    {"hardware_id": "cam-snap-01", "auto_name": "Snap Camera"}
                ],
            },
        )

        # Авторизуемся под Developer для создания установки
        cl.post(
            "/api/auth/login",
            headers={
                "X-Client-Key": client_key,
                "X-Client-Type": "telegram",
                "X-External-Id": "200",
            },
            json={
                "login": "Dev Setup 2",
                "password": dev_pass,
                "client_type": "telegram",
                "external_id": "200",
            },
        )

        # Создаем установку
        r_inst = cl.post(
            "/api/installations",
            headers={
                "X-Client-Key": client_key,
                "X-Client-Type": "telegram",
                "X-External-Id": "200",
            },
            json={
                "name": "Snap Hydro Unit",
                "culture_name": "Огурцы",
                "light_gpio_pin": 14,
                "flash_gpio_pin": 15,
                "aruco_size_mm": 40.0,
            },
        )
        assert r_inst.status_code == 201
        inst_id = r_inst.json()["id"]

        # Привязываем камеру
        with srv["session_factory"]() as db:
            from server.db.models import Camera

            cam = Camera(
                hardware_id="cam-snap-01",
                auto_name="Snap Camera",
                is_online=True,
                installation_id=inst_id,
            )
            db.add(cam)
            db.commit()

    # 2. Фоновый обработчик команд для light и snap
    stop_ev = threading.Event()

    def handle_control(http_client, cmd):
        c_id = cmd["id"]
        c_type = cmd.get("command_type")
        if c_type in ("light_toggle", "LIGHT_MAIN", "LIGHT_FLASH"):
            http_client.post(
                f"/api/edge/commands/{c_id}/result",
                headers={"X-Edge-Key": edge_key},
                json={"status": "done", "result_message": "Relay ON OK"},
            )
        elif c_type in ("snap", "SNAP"):
            meta = {
                "installation_id": inst_id,
                "camera_hardware_id": "cam-snap-01",
                "captured_at": "2026-11-21T12:00:00Z",
                "is_manual": True,
                "aruco_area_px": 1600.0,
                "command_id": c_id,
                "measurements": [{"plant_index": 0, "area_px": 3500.0}],
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

    th = threading.Thread(
        target=lambda: rpi_poll_and_handle(base_url, edge_key, handle_control, stop_ev),
        daemon=True,
    )
    th.start()

    try:
        res = run_bot_scenario(
            scenario_name="control_and_snap",
            server_url=base_url,
            client_key=client_key,
            dev_pass=dev_pass,
            extra={
                "installation_id": inst_id,
                "camera_hardware_id": "cam-snap-01",
            },
        )
        assert res.get("status") == "ok"
        assert res.get("media_groups_sent") >= 1
        caption = res.get("caption", "")
        assert "Snap Hydro Unit" in caption
        assert "cam-snap-01" in caption
        assert "Площадь" in caption
    finally:
        stop_ev.set()
        th.join(timeout=3.0)


# ==============================================================================
# 6. Таймлапс (экспорт при 1 кадре) и галерея кадров
# ==============================================================================
def test_bot_e2e_timelapse_and_gallery(e2e_uvicorn_server):
    """Критерии 4, 6: Экспорт фото при 1 плановом кадре, защита от параллельного запуска и галерея."""
    srv = e2e_uvicorn_server
    base_url = srv["base_url"]
    client_key = srv["client_key"]
    edge_key = srv["edge_key"]
    dev_pass = srv["dev_pass"]

    # 1. Создаем установку с камерой
    with httpx.Client(base_url=base_url) as cl:
        cl.post(
            "/api/edge/heartbeat",
            headers={"X-Edge-Key": edge_key},
            json={
                "software": {
                    "edge_version": "0.1.0",
                    "python_version": "3.12.0",
                    "model_name": "model_test.onnx",
                },
                "hardware": {
                    "board_model": "RPi 5",
                    "os_info": "Linux",
                    "uptime_sec": 1000,
                    "cpu_temp_c": 40.0,
                    "cpu_usage_percent": 5.0,
                    "ram_used_mb": 400,
                    "ram_total_mb": 4096,
                    "disk_free_gb": 10.0,
                    "gpio_mode": "bcm",
                },
                "offline_queue_count": 0,
                "detected_cameras": [
                    {"hardware_id": "cam-tl-01", "auto_name": "TL Cam"}
                ],
            },
        )

        # Авторизуемся под Developer для создания установки
        cl.post(
            "/api/auth/login",
            headers={
                "X-Client-Key": client_key,
                "X-Client-Type": "telegram",
                "X-External-Id": "300",
            },
            json={
                "login": "Dev Setup 3",
                "password": dev_pass,
                "client_type": "telegram",
                "external_id": "300",
            },
        )

        r_inst = cl.post(
            "/api/installations",
            headers={
                "X-Client-Key": client_key,
                "X-Client-Type": "telegram",
                "X-External-Id": "300",
            },
            json={
                "name": "Timelapse Box",
                "culture_name": "Мята",
                "light_gpio_pin": 16,
                "flash_gpio_pin": 17,
                "aruco_size_mm": 40.0,
            },
        )
        assert r_inst.status_code == 201
        inst_id = r_inst.json()["id"]

        with srv["session_factory"]() as db:
            from server.db.models import Camera

            cam = Camera(
                hardware_id="cam-tl-01",
                auto_name="TL Cam",
                is_online=True,
                installation_id=inst_id,
            )
            db.add(cam)
            db.commit()

        # 2. Загружаем ровно ОДИН плановый снимок (is_manual=False)
        meta = {
            "installation_id": inst_id,
            "camera_hardware_id": "cam-tl-01",
            "captured_at": "2026-11-21T10:00:00Z",
            "is_manual": False,
            "aruco_area_px": 1600.0,
            "measurements": [{"plant_index": 0, "area_px": 2000.0}],
        }
        r_cap = cl.post(
            "/api/edge/captures",
            data={"metadata": json.dumps(meta)},
            files={
                "raw_image": (
                    "raw_frame1.jpg",
                    io.BytesIO(TINY_JPEG),
                    "image/jpeg",
                ),
                "annotated_image": (
                    "ann_frame1.jpg",
                    io.BytesIO(TINY_JPEG),
                    "image/jpeg",
                ),
            },
            headers={"X-Edge-Key": edge_key},
        )
        assert r_cap.status_code == 201

    res = run_bot_scenario(
        scenario_name="timelapse_and_gallery",
        server_url=base_url,
        client_key=client_key,
        dev_pass=dev_pass,
        extra={"installation_id": inst_id, "camera_hardware_id": "cam-tl-01"},
    )
    assert res.get("status") == "ok"
    assert res.get("one_frame_photo_sent") is True


# ==============================================================================
# 7. Статус системы и подробная телеметрия RPi 5
# ==============================================================================
def test_bot_e2e_system_status_and_telemetry(e2e_uvicorn_server):
    """Критерий 7: Отображение статуса системы и подробной телеметрии Raspberry Pi 5."""
    srv = e2e_uvicorn_server
    base_url = srv["base_url"]
    client_key = srv["client_key"]
    edge_key = srv["edge_key"]
    dev_pass = srv["dev_pass"]

    # Отправляем подробный heartbeat
    with httpx.Client(base_url=base_url) as cl:
        cl.post(
            "/api/edge/heartbeat",
            headers={"X-Edge-Key": edge_key},
            json={
                "software": {
                    "edge_version": "0.1.0-prod",
                    "python_version": "3.12.8",
                    "model_name": "model_onnx_v1.onnx",
                },
                "hardware": {
                    "board_model": "Raspberry Pi 5 Model B",
                    "os_info": "Debian GNU/Linux 12 (bookworm)",
                    "uptime_sec": 7200,
                    "cpu_temp_c": 49.5,
                    "cpu_usage_percent": 15.0,
                    "ram_used_mb": 1024,
                    "ram_total_mb": 4096,
                    "disk_free_gb": 28.5,
                    "gpio_mode": "bcm",
                },
                "offline_queue_count": 0,
                "detected_cameras": [],
            },
        )

    res = run_bot_scenario(
        scenario_name="system_status",
        server_url=base_url,
        client_key=client_key,
        dev_pass=dev_pass,
    )
    assert res.get("status") == "ok"
    sys_text = res.get("system_text", "")
    assert "Raspberry Pi 5" in sys_text
    assert "49.5" in sys_text


# ==============================================================================
# 8. Заголовки HTTP и обработка ошибок API
# ==============================================================================
def test_bot_e2e_headers_and_error_handling(e2e_uvicorn_server):
    """Критерии 1, 8: Проверка передачи заголовков X-Client-* и понятный маппинг ошибок (401)."""
    srv = e2e_uvicorn_server
    base_url = srv["base_url"]
    client_key = srv["client_key"]

    res = run_bot_scenario(
        scenario_name="headers_and_error_handling",
        server_url=base_url,
        client_key=client_key,
    )
    assert res.get("status") == "ok"
    assert res.get("headers_valid") is True
    assert res.get("auth_error_handled") is True
