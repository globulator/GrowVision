import os
import subprocess
from pathlib import Path

import httpx

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
EDGE_PYTHON = ROOT_DIR / "edge_agent" / ".venv" / "bin" / "python"


def test_simulator_seed_e2e(e2e_uvicorn_server):
    """Критерии 2, 5: Посев 6 кадров на 2 камеры через 'python -m edge_agent.simulator seed'.

    Проверяется:
    1. GET /api/timelapse/{id}/frames возвращает ровно 12 кадров (6 кадров * 2 камеры).
    2. area_display отображается в см² благодаря настоящей детекции ArUco (aruco_size_mm=40.0).
    3. Видео успешно собирается через GET /api/timelapse/{id}/video?camera_id={cam_id}.
    4. Повторный запуск seed безопасен (сервер принимает дубли со статусом 200).
    """
    srv = e2e_uvicorn_server
    base_url = srv["base_url"]
    client_key = srv["client_key"]
    edge_key = srv["edge_key"]
    client_headers = {
        "X-Client-Key": client_key,
        "X-Client-Type": "cli",
        "X-External-Id": "test-admin",
    }
    edge_headers = {"X-Edge-Key": edge_key}

    # 1. Авторизуемся под Developer в Client API
    with httpx.Client(base_url=base_url) as client:
        r_login = client.post(
            "/api/auth/login",
            headers=client_headers,
            json={
                "login": "Developer Admin",
                "password": srv["dev_pass"],
                "client_type": "cli",
                "external_id": "test-admin",
            },
        )
        assert r_login.status_code == 200

        # Создаем установку с заданным размером маркера ArUco
        r_inst = client.post(
            "/api/installations",
            headers=client_headers,
            json={
                "name": "Seed Test Box",
                "culture_name": "Базилик Тест",
                "light_gpio_pin": 17,
                "flash_gpio_pin": 27,
                "aruco_size_mm": 40.0,
            },
        )
        assert r_inst.status_code == 201, f"Не удалось создать установку: {r_inst.text}"
        inst_id = r_inst.json()["id"]

        # 2. Регистрируем 2 камеры в БД и шлем Heartbeat RPi
        with srv["session_factory"]() as db:
            from server.db.models import Camera

            c1 = Camera(
                hardware_id="cam-seed-01", auto_name="Camera Front", is_online=True
            )
            c2 = Camera(
                hardware_id="cam-seed-02", auto_name="Camera Top", is_online=True
            )
            db.add_all([c1, c2])
            db.commit()

        hb_payload = {
            "software": {
                "edge_version": "0.1.0",
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
                {"hardware_id": "cam-seed-01", "auto_name": "Camera Front"},
                {"hardware_id": "cam-seed-02", "auto_name": "Camera Top"},
            ],
        }
        r_hb = client.post("/api/edge/heartbeat", headers=edge_headers, json=hb_payload)
        assert r_hb.status_code == 200

        # Получаем ID камер из БД/API
        r_cams = client.get("/api/cameras", headers=client_headers)
        assert r_cams.status_code == 200
        cam_front = next(c for c in r_cams.json() if c["hardware_id"] == "cam-seed-01")
        cam_top = next(c for c in r_cams.json() if c["hardware_id"] == "cam-seed-02")

        # Привязываем обе камеры к установке
        client.post(
            f"/api/cameras/{cam_front['id']}/bind",
            headers=client_headers,
            json={"installation_id": inst_id},
        )
        client.post(
            f"/api/cameras/{cam_top['id']}/bind",
            headers=client_headers,
            json={"installation_id": inst_id},
        )

    # 3. Запускаем команду посева: 6 кадров на каждую камеру с шагом 1 час
    sim_env = {
        **os.environ,
        "PYTHONPATH": str(ROOT_DIR),
        "SERVER_URL": base_url,
        "EDGE_API_KEY": edge_key,
        "HARDWARE_MODE": "mock",
    }
    cmd = [
        str(EDGE_PYTHON),
        "-m",
        "edge_agent.simulator",
        "seed",
        "--installation",
        str(inst_id),
        "--frames",
        "6",
        "--step-hours",
        "1",
        "--start",
        "2026-11-20T00:00:00Z",
        "--cameras",
        "all",
    ]
    proc = subprocess.run(
        cmd,
        env=sim_env,
        cwd=str(ROOT_DIR),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"Ошибка запуска simulator seed: {proc.stderr}"
    assert "Посев успешно завершён: новых кадров: 12" in proc.stdout

    # 4. Проверяем результаты через Client API
    with httpx.Client(base_url=base_url) as client:
        # 12 кадров в галерее таймлапса
        r_frames = client.get(
            f"/api/timelapse/{inst_id}/frames", headers=client_headers
        )
        assert r_frames.status_code == 200
        frames_data = r_frames.json()["frames"]
        assert len(frames_data) == 12

        # Проверяем первый кадр: замеры, ArUco и расчет площади в см²
        first_frame = frames_data[0]
        assert "area_display" in first_frame
        # При aruco_size_mm=40.0 и обнаруженном ArUco маркере площадь должна быть в см²
        assert "см²" in first_frame["area_display"]

        # 5. Проверяем сборку видео таймлапса по camera_id
        r_vid = client.post(
            f"/api/timelapse/{inst_id}/video",
            headers=client_headers,
            json={"camera_id": cam_front["id"]},
            timeout=15.0,
        )
        assert r_vid.status_code == 200
        assert r_vid.headers.get("content-type") == "video/mp4"
        assert len(r_vid.content) > 1000

        # 6. Проверяем идемпотентность (повторный запуск посева возвращает 200 на дубли)
        proc_repeat = subprocess.run(
            cmd,
            env=sim_env,
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc_repeat.returncode == 0
        assert "существующих дублей: 12" in proc_repeat.stdout


def test_edge_agent_test_capture_interval_e2e(e2e_uvicorn_server, monkeypatch):
    """Критерии 1, 5: Работа Edge-агента с TEST_CAPTURE_INTERVAL_SEC=10 без реального ожидания."""
    srv = e2e_uvicorn_server
    base_url = srv["base_url"]
    client_key = srv["client_key"]
    edge_key = srv["edge_key"]
    client_headers = {
        "X-Client-Key": client_key,
        "X-Client-Type": "cli",
        "X-External-Id": "test-admin",
    }
    edge_headers = {"X-Edge-Key": edge_key}

    # 1. Авторизуемся и создаем установку и камеру
    with httpx.Client(base_url=base_url) as client:
        r_login = client.post(
            "/api/auth/login",
            headers=client_headers,
            json={
                "login": "Developer Admin",
                "password": srv["dev_pass"],
                "client_type": "cli",
                "external_id": "test-admin",
            },
        )
        assert r_login.status_code == 200

        with srv["session_factory"]() as db:
            from server.db.models import Camera

            c = Camera(
                hardware_id="cam-interval-01", auto_name="Cam Int", is_online=True
            )
            db.add(c)
            db.commit()

        r_inst = client.post(
            "/api/installations",
            headers=client_headers,
            json={
                "name": "Interval Box",
                "culture_name": "Мята",
                "light_gpio_pin": 12,
                "flash_gpio_pin": 13,
                "aruco_size_mm": 50.0,
            },
        )
        assert r_inst.status_code == 201
        inst_id = r_inst.json()["id"]

        r_hb = client.post(
            "/api/edge/heartbeat",
            headers=edge_headers,
            json={
                "software": {
                    "edge_version": "0.1.0",
                    "python_version": "3.12",
                    "model_name": "none",
                },
                "hardware": {
                    "board_model": "RPi 5",
                    "os_info": "Linux 12",
                    "uptime_sec": 100,
                    "cpu_temp_c": 40.0,
                    "cpu_usage_percent": 5.0,
                    "ram_used_mb": 100,
                    "ram_total_mb": 1000,
                    "disk_free_gb": 10.0,
                    "gpio_mode": "bcm",
                },
                "offline_queue_count": 0,
                "detected_cameras": [
                    {"hardware_id": "cam-interval-01", "auto_name": "Cam Int"}
                ],
            },
        )
        assert r_hb.status_code == 200

        r_cams = client.get("/api/cameras", headers=client_headers)
        cam = next(c for c in r_cams.json() if c["hardware_id"] == "cam-interval-01")
        client.post(
            f"/api/cameras/{cam['id']}/bind",
            headers=client_headers,
            json={"installation_id": inst_id},
        )

    # 2. Вызываем плановую съёмку через Edge-агент с TEST_CAPTURE_INTERVAL_SEC=10
    script = f"""
from edge_agent.config import EdgeConfig
from edge_agent.orchestrator import EdgeOrchestrator

cfg = EdgeConfig(
    server_url="{base_url}",
    edge_api_key="{edge_key}",
    hardware_mode="mock",
    test_capture_interval_sec=10,
)
orchestrator = EdgeOrchestrator(config=cfg)
orchestrator.perform_heartbeat()
assert len(orchestrator.active_installations) == 1
debug_log = orchestrator.trigger_scheduled_capture()
assert "[TEST_INTERVAL=10s]" in debug_log
print("ORCHESTRATOR_SUCCESS")
"""
    sim_env = {
        **os.environ,
        "PYTHONPATH": str(ROOT_DIR),
        "SERVER_URL": base_url,
        "EDGE_API_KEY": edge_key,
        "HARDWARE_MODE": "mock",
    }
    proc = subprocess.run(
        [str(EDGE_PYTHON), "-c", script],
        env=sim_env,
        cwd=str(ROOT_DIR),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"Ошибка в EdgeOrchestrator: {proc.stderr}"
    assert "ORCHESTRATOR_SUCCESS" in proc.stdout

    # 5. Проверяем, что плановый кадр доставлен на сервер
    with httpx.Client(base_url=base_url) as client:
        r_frames = client.get(
            f"/api/timelapse/{inst_id}/frames", headers=client_headers
        )
        assert r_frames.status_code == 200
        frames = r_frames.json()["frames"]
        assert len(frames) == 1
        assert frames[0]["capture_id"] > 0
        assert "area_display" in frames[0]
