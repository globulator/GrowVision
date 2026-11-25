import os
import signal
import subprocess
import time
from pathlib import Path

import httpx

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
EDGE_PYTHON = ROOT_DIR / "edge_agent" / ".venv" / "bin" / "python"


def launch_edge_agent(
    server_url: str,
    edge_key: str,
    tmp_path: Path,
    claim_poll_sec: float = 0.1,
    heartbeat_sec: float = 0.5,
) -> tuple[subprocess.Popen, Path]:
    """Запускает реальный процесс Edge-агента в mock-режиме с изолированным окружением."""
    queue_dir = tmp_path / "edge_offline_queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT_DIR),
        "SERVER_URL": server_url,
        "EDGE_API_KEY": edge_key,
        "HARDWARE_MODE": "mock",
        "MOCK_CAMERAS": "true",
        "OFFLINE_QUEUE_DIR": str(queue_dir),
        "CLAIM_POLL_INTERVAL_SEC": str(claim_poll_sec),
        "HEARTBEAT_INTERVAL_SEC": str(heartbeat_sec),
        "CAPTURE_INTERVAL_HOURS": "24",
        "DEBUG_MODE": "true",
    }
    proc = subprocess.Popen(
        [str(EDGE_PYTHON), "-m", "edge_agent.main"],
        env=env,
        cwd=str(ROOT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return proc, queue_dir


def stop_edge_agent(proc: subprocess.Popen) -> None:
    """Корректно останавливает Edge-агент сигналом SIGINT."""
    if proc.poll() is None:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2.0)


def wait_for_rpi_online(
    client: httpx.Client, headers: dict[str, str], timeout: float = 6.0
) -> dict:
    """Ожидает перехода RPi в статус online по ответу GET /api/system/status."""
    start_t = time.monotonic()
    last_resp = {}
    while time.monotonic() - start_t < timeout:
        time.sleep(0.1)
        r = client.get("/api/system/status", headers=headers)
        if r.status_code == 200:
            last_resp = r.json()
            if last_resp.get("rpi_connection") == "online":
                return last_resp
    raise TimeoutError(f"RPi не перешёл в онлайн за {timeout} с: {last_resp}")


# ==============================================================================
# 1. Сценарий 1: Heartbeat и телеметрия
# ==============================================================================
def test_edge_heartbeat_and_telemetry(e2e_uvicorn_server):
    """Сценарий 1: Агент отправляет Heartbeat, RPi переходит в online, телеметрия корректна."""
    srv = e2e_uvicorn_server
    base_url = srv["base_url"]
    client_key = srv["client_key"]
    edge_key = srv["edge_key"]

    dev_headers = {
        "X-Client-Key": client_key,
        "X-Client-Type": "cli",
        "X-External-Id": "e2e_dev_1",
    }

    proc, _ = launch_edge_agent(base_url, edge_key, srv["tmp_path"])
    try:
        with httpx.Client(base_url=base_url) as client:
            # Авторизуемся под Developer для доступа к /api/system/status
            r_login = client.post(
                "/api/auth/login",
                headers=dev_headers,
                json={
                    "client_type": "cli",
                    "external_id": "e2e_dev_1",
                    "login": "developer",
                    "password": srv["dev_pass"],
                },
            )
            assert r_login.status_code == 200

            # Ожидаем первого Heartbeat
            status_data = wait_for_rpi_online(client, dev_headers, timeout=6.0)
            assert status_data.get("rpi_connection") == "online"

            # Проверяем структуру телеметрии
            telemetry = status_data.get("rpi_telemetry")
            assert telemetry is not None
            sw = telemetry.get("software", {})
            hw = telemetry.get("hardware", {})

            assert sw.get("edge_version") is not None
            assert sw.get("python_version") is not None
            assert sw.get("model_name") is not None

            assert hw.get("gpio_mode") == "mock"
            assert hw.get("board_model") is not None
            assert hw.get("os_info") is not None
            assert hw.get("uptime_sec") is not None
            assert hw.get("ram_used_mb") is not None
            assert hw.get("ram_total_mb") is not None
            assert hw.get("disk_free_gb") is not None
            assert telemetry.get("offline_queue_count") == 0

    finally:
        stop_edge_agent(proc)


# ==============================================================================
# 2. Сценарий 2: Команды управления, сканирование камер и реле
# ==============================================================================
def test_edge_commands_lifecycle_and_scan(e2e_uvicorn_server):
    """Сценарий 2: SCAN_CAMERAS регистрирует камеры, LIGHT_MAIN и RELAY_TEST выполняются."""
    srv = e2e_uvicorn_server
    base_url = srv["base_url"]
    client_key = srv["client_key"]
    edge_key = srv["edge_key"]

    dev_headers = {
        "X-Client-Key": client_key,
        "X-Client-Type": "cli",
        "X-External-Id": "e2e_dev_2",
    }

    proc, _ = launch_edge_agent(base_url, edge_key, srv["tmp_path"])
    try:
        # Авторизуемся под Developer для отправки команд
        with httpx.Client(base_url=base_url) as client:
            r_login = client.post(
                "/api/auth/login",
                headers=dev_headers,
                json={
                    "client_type": "cli",
                    "external_id": "e2e_dev_2",
                    "login": "developer",
                    "password": srv["dev_pass"],
                },
            )
            assert r_login.status_code == 200

            # Ожидаем онлайна RPi перед отправкой команд
            wait_for_rpi_online(client, dev_headers, timeout=6.0)

            # 1. Сканирование камер: POST /api/cameras/scan
            # Сервер ставит команду, агент в mock-режиме возвращает 2 камеры
            r_scan = client.post(
                "/api/cameras/scan",
                headers=dev_headers,
                timeout=10.0,
            )
            assert r_scan.status_code == 200
            scan_json = r_scan.json()
            assert scan_json.get("status") == "done"

            # Проверяем регистрацию камер
            r_cams = client.get("/api/cameras", headers=dev_headers)
            assert r_cams.status_code == 200
            cams = r_cams.json()
            assert len(cams) >= 2
            hw_ids = [c["hardware_id"] for c in cams]
            assert "mock-usb-cam-1" in hw_ids
            assert "mock-csi-cam-2" in hw_ids

            # 2. Создаем установку
            r_add = client.post(
                "/api/installations",
                headers=dev_headers,
                json={
                    "name": "E2E Edge Unit",
                    "culture_name": "Базилик",
                    "light_gpio_pin": 17,
                    "flash_gpio_pin": 27,
                    "aruco_size_mm": 40.0,
                },
            )
            assert r_add.status_code == 201
            inst_id = r_add.json()["id"]

            # 3. Привязываем камеру
            cam_1 = next(c for c in cams if c["hardware_id"] == "mock-usb-cam-1")
            r_bind = client.post(
                f"/api/cameras/{cam_1['id']}/bind",
                headers=dev_headers,
                json={"installation_id": inst_id},
            )
            assert r_bind.status_code == 200

            # 4. Управление основным светом: LIGHT_MAIN
            r_light = client.post(
                "/api/control/light/main",
                headers=dev_headers,
                json={"target": inst_id, "state": True},
                timeout=10.0,
            )
            assert r_light.status_code == 200
            assert r_light.json().get("status") == "done"
            assert "успешно включен" in r_light.json().get("result_message", "")

            # 5. Управление вспышкой: LIGHT_FLASH
            r_flash = client.post(
                "/api/control/light/flash",
                headers=dev_headers,
                json={"target": inst_id, "state": False},
                timeout=10.0,
            )
            assert r_flash.status_code == 200
            assert r_flash.json().get("status") == "done"

            # 6. Тест реле: RELAY_TEST (кратковременный)
            r_relay = client.post(
                "/api/control/relay-test",
                headers=dev_headers,
                json={"target": inst_id},
                timeout=10.0,
            )
            assert r_relay.status_code == 200
            assert r_relay.json().get("status") == "done"

    finally:
        stop_edge_agent(proc)


# ==============================================================================
# 3. Сценарий 3: Ручной снимок SNAP с ArUco и сегментацией
# ==============================================================================
def test_edge_snap_command_with_aruco_and_segmentation(e2e_uvicorn_server):
    """Сценарий 3: SNAP захватывает кадр, находит ArUco, отправляет кадр и возвращает result."""
    srv = e2e_uvicorn_server
    base_url = srv["base_url"]
    client_key = srv["client_key"]
    edge_key = srv["edge_key"]

    dev_headers = {
        "X-Client-Key": client_key,
        "X-Client-Type": "cli",
        "X-External-Id": "e2e_dev_3",
    }

    proc, _ = launch_edge_agent(base_url, edge_key, srv["tmp_path"])
    try:
        with httpx.Client(base_url=base_url) as client:
            r_login = client.post(
                "/api/auth/login",
                headers=dev_headers,
                json={
                    "client_type": "cli",
                    "external_id": "e2e_dev_3",
                    "login": "developer",
                    "password": srv["dev_pass"],
                },
            )
            assert r_login.status_code == 200

            # Ожидаем онлайна RPi перед отправкой команд
            wait_for_rpi_online(client, dev_headers, timeout=6.0)

            # Сканируем камеры и создаем установку
            r_scan = client.post(
                "/api/cameras/scan",
                headers=dev_headers,
                timeout=10.0,
            )
            assert r_scan.status_code == 200

            r_add = client.post(
                "/api/installations",
                headers=dev_headers,
                json={
                    "name": "Snap Test Unit",
                    "culture_name": "Томат",
                    "light_gpio_pin": 17,
                    "flash_gpio_pin": 27,
                    "aruco_size_mm": 40.0,
                },
            )
            assert r_add.status_code == 201
            inst_id = r_add.json()["id"]

            r_cams = client.get("/api/cameras", headers=dev_headers)
            cam = r_cams.json()[0]
            client.post(
                f"/api/cameras/{cam['id']}/bind",
                headers=dev_headers,
                json={"installation_id": inst_id},
            )

            # Вызываем SNAP: POST /api/control/snap
            r_snap = client.post(
                "/api/control/snap",
                headers=dev_headers,
                json={"target": inst_id},
                timeout=15.0,
            )
            assert r_snap.status_code == 200
            snap_json = r_snap.json()
            assert snap_json.get("status") == "done"

            captures = snap_json.get("captures", [])
            assert len(captures) == 1
            cap = captures[0]

            assert cap["installation_id"] == inst_id
            assert cap["camera_id"] == cam["id"]
            assert cap.get("ephemeral") is True
            assert cap.get("raw_url", "").startswith("/api/control/test-snapshots/")
            assert cap.get("annotated_url", "").startswith(
                "/api/control/test-snapshots/"
            )
            assert cap.get("area_display") is not None

            # Проверяем скачивание медиафайлов из эфемерного кэша по HTTP (включая download=1)
            r_raw = client.get(f"{cap['raw_url']}?download=1", headers=dev_headers)
            assert r_raw.status_code == 200
            assert "attachment;" in r_raw.headers.get("content-disposition", "")
            assert len(r_raw.content) > 50

            r_ann = client.get(cap["annotated_url"], headers=dev_headers)
            assert r_ann.status_code == 200
            assert len(r_ann.content) > 50

            # Проверяем, что тестовый снимок НЕ попал в БД и media/
            r_frames = client.get(f"/api/timelapse/{inst_id}/frames")
            assert r_frames.status_code == 200
            assert r_frames.json()["frames"] == []
            assert list(srv["media_dir"].rglob("*.jpg")) == []

    finally:
        stop_edge_agent(proc)


# ==============================================================================
# 4. Сценарий 4: Офлайн-очередь, буферизация при сбое сети и выгрузка
# ==============================================================================
def test_edge_offline_queue_buffering_and_flush(e2e_uvicorn_server):
    """Сценарий 4: При недоступности сервера кадр пишется в очередь, при связи выгружается."""
    srv = e2e_uvicorn_server
    base_url = srv["base_url"]
    edge_key = srv["edge_key"]
    client_key = srv["client_key"]
    tmp_path = srv["tmp_path"]
    queue_dir = tmp_path / "offline_queue_test"

    # Шаг 0: Регистрируем установку и камеру на сервере
    with httpx.Client(base_url=base_url) as client:
        # Heartbeat с камерой
        hb_payload = {
            "software": {
                "edge_version": "0.1.0",
                "python_version": "3.12.0",
                "model_name": "model_test.onnx",
            },
            "hardware": {
                "board_model": "Mock Board",
                "os_info": "Linux",
                "uptime_sec": 100,
                "cpu_temp_c": 40.0,
                "cpu_usage_percent": 10.0,
                "ram_used_mb": 500,
                "ram_total_mb": 2048,
                "disk_free_gb": 10.0,
                "gpio_mode": "mock",
            },
            "offline_queue_count": 0,
            "detected_cameras": [
                {"hardware_id": "queue-cam-1", "auto_name": "Queue Cam 1"}
            ],
        }
        client.post(
            "/api/edge/heartbeat",
            headers={"X-Edge-Key": edge_key},
            json=hb_payload,
        )

        client.post(
            "/api/auth/login",
            headers={"X-Client-Key": client_key},
            json={"role": "developer", "password": srv["dev_pass"]},
        )

        # Сканируем камеры mock RPi для регистрации в БД
        # Прямая регистрация камеры через edge
        db = srv["session_factory"]()
        from server.db.models import Camera, Installation

        cam = Camera(
            hardware_id="queue-cam-1",
            auto_name="Queue Cam 1",
            is_online=True,
        )
        db.add(cam)
        inst = Installation(
            name="Queue Unit",
            culture_name="Салат",
            light_gpio_pin=17,
            flash_gpio_pin=27,
            is_archived=False,
        )
        db.add(inst)
        db.commit()
        inst_id = inst.id
        db.close()

    # Шаг 1: Скрипт в виртуальном окружении Edge сохраняет кадр при недоступном сервере
    script_save = f"""
from pathlib import Path
from edge_agent.config import EdgeConfig
from edge_agent.offline_queue import save_to_queue, get_queue_count
from shared.schemas import CaptureMeta

q_dir = Path(r'{queue_dir}')
meta = CaptureMeta(
    installation_id={inst_id},
    camera_hardware_id='queue-cam-1',
    captured_at='2026-11-21T00:00:00Z',
    is_manual=False,
    aruco_area_px=1000.0,
    measurements=[],
)
dummy_jpeg = b'\\xff\\xd8\\xff\\xe0\\x00\\x10JFIF\\x00\\x01\\x01\\x01\\x00H\\x00H\\x00\\x00\\xff\\xd9'
save_to_queue(q_dir, meta, dummy_jpeg, dummy_jpeg)
assert get_queue_count(q_dir) == 1
print('SAVED_OK')
"""
    res_save = subprocess.run(
        [str(EDGE_PYTHON), "-c", script_save],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "SAVED_OK" in res_save.stdout

    # Шаг 2: Выгрузка при восстановлении связи (сервер доступен) -> кадр отправляется, очередь очищается
    script_flush = f"""
from pathlib import Path
from edge_agent.client import EdgeAPIClient
from edge_agent.offline_queue import flush_queue, get_queue_count

q_dir = Path(r'{queue_dir}')
client = EdgeAPIClient(base_url=r'{base_url}', api_key=r'{edge_key}')
sent, skipped, failed = flush_queue(q_dir, client.upload_capture)
print(f'RESULT sent={{sent}} skipped={{skipped}} failed={{failed}} count={{get_queue_count(q_dir)}}')
"""
    res_flush = subprocess.run(
        [str(EDGE_PYTHON), "-c", script_flush],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "sent=1 skipped=0 failed=0 count=0" in res_flush.stdout

    # Проверяем, что на сервере появился кадр
    db = srv["session_factory"]()
    from server.db.models import Capture

    saved_cap = db.query(Capture).filter(Capture.installation_id == inst_id).first()
    assert saved_cap is not None
    assert saved_cap.camera.hardware_id == "queue-cam-1"
    db.close()

    # Шаг 3: Битый пакет (несуществующая установка -> 404) -> перемещается в failed/
    script_failed = f"""
from pathlib import Path
from edge_agent.client import EdgeAPIClient
from edge_agent.offline_queue import save_to_queue, flush_queue, get_queue_count
from shared.schemas import CaptureMeta

q_dir = Path(r'{queue_dir}')
client = EdgeAPIClient(base_url=r'{base_url}', api_key=r'{edge_key}')
meta_bad = CaptureMeta(
    installation_id=999999,
    camera_hardware_id='queue-cam-1',
    captured_at='2026-11-21T01:00:00Z',
    is_manual=False,
    aruco_area_px=1000.0,
    measurements=[],
)
dummy_jpeg = b'\\xff\\xd8\\xff\\xe0\\x00\\x10JFIF\\x00\\x01\\x01\\x01\\x00H\\x00H\\x00\\x00\\xff\\xd9'
save_to_queue(q_dir, meta_bad, dummy_jpeg, dummy_jpeg)
assert get_queue_count(q_dir) == 1

sent, skipped, failed = flush_queue(q_dir, client.upload_capture)
print(f'BAD_RESULT sent={{sent}} skipped={{skipped}} failed={{failed}} count={{get_queue_count(q_dir)}}')
failed_dir = q_dir / 'failed'
assert failed_dir.is_dir()
assert len(list(failed_dir.iterdir())) >= 1
print('FAILED_ISOLATED_OK')
"""
    res_failed = subprocess.run(
        [str(EDGE_PYTHON), "-c", script_failed],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "sent=0 skipped=0 failed=1 count=0" in res_failed.stdout
    assert "FAILED_ISOLATED_OK" in res_failed.stdout


# ==============================================================================
# 5. Сценарий 5: Аппаратный мьютекс и приоритет плановой автосъемки
# ==============================================================================
def test_edge_hardware_mutex_and_priority():
    """Сценарий 5: При занятом оборудовании ручная команда немедленно отвечает ошибкой."""
    script_mutex = """
from edge_agent.config import EdgeConfig
from edge_agent.hardware import HardwareManager
from edge_agent.orchestrator import EdgeOrchestrator
from shared.schemas import CommandItem, CommandResult

class DummyClient:
    def __init__(self):
        self.results = {}
    def send_command_result(self, cmd_id, res):
        self.results[cmd_id] = res

cfg = EdgeConfig(edge_api_key="test-key", hardware_mode="mock", mock_cameras=True)
client = DummyClient()
orch = EdgeOrchestrator(config=cfg, client=client)

# Симулируем работу плановой автосъемки (захваченный аппаратный мьютекс)
orch.is_scheduled_capture_running.set()
orch.hardware.acquire_lock(blocking=True)
orig_acq = orch.hardware.acquire_lock
orch.hardware.acquire_lock = lambda blocking=True, timeout=10.0: orig_acq(blocking=True, timeout=0.05)

cmd = CommandItem(id=10, command_type="SNAP", is_global=False)
orch.handle_command(cmd)

assert 10 in client.results
res = client.results[10]
assert res.status == "error"
assert "Камера недоступна, попробуйте позже" in res.result_message
print("MUTEX_PRIORITY_OK")
"""
    res_mutex = subprocess.run(
        [str(EDGE_PYTHON), "-c", script_mutex],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "MUTEX_PRIORITY_OK" in res_mutex.stdout
