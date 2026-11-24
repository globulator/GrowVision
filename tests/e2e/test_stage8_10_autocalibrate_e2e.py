from datetime import UTC, datetime

import httpx

from server.db.models import Camera, SystemSetting, User


def test_autocalibrate_camera_e2e(e2e_uvicorn_server):
    """Сквозной E2E тест автокалибровки камеры разработчиком через API."""
    server_data = e2e_uvicorn_server
    base_url = server_data["base_url"]
    dev_pass = server_data["dev_pass"]
    session_factory = server_data["session_factory"]

    dev_tg_id = "11223344"
    now = datetime.now(UTC)

    with session_factory() as db:
        user = User(
            client_type="web",
            external_id=dev_tg_id,
            login="E2E Dev",
            role="developer",
            is_active=True,
            authorized_at=now,
            last_active_at=now,
        )
        db.add(user)
        # RPi онлайн
        db.add(
            SystemSetting(
                key="rpi_last_telemetry",
                value='{"hardware": {"board_model": "Raspberry Pi 5"}}',
                updated_at=now,
            )
        )
        # Камера
        cam = Camera(
            hardware_id="cam-e2e-hw-1",
            auto_name="E2E Camera 1",
            is_online=True,
        )
        db.add(cam)
        db.commit()
        cam_id = cam.id

    with httpx.Client(base_url=base_url) as client:
        # 1. Логин разработчика
        r_login = client.post(
            "/api/auth/web/login",
            json={"telegram": dev_tg_id, "password": dev_pass},
        )
        assert r_login.status_code == 200

        # Фоновый опрос и завершение команды от лица mock edge агента
        import threading
        import time

        from server.db.models import EdgeCommand

        def answer_command():
            time.sleep(0.15)
            with session_factory() as db:
                cmd = (
                    db.query(EdgeCommand)
                    .filter(EdgeCommand.command_type == "AUTOCALIBRATE_CAMERA")
                    .first()
                )
                if cmd:
                    cmd.status = "done"
                    cmd.result_message = "1920x1080 (MJPG), автофокус: поддерживается"
                    cmd.completed_at = datetime.now(UTC)
                    db.commit()

        t = threading.Thread(target=answer_command)
        t.start()

        # 2. Запрос автокалибровки
        res_calib = client.post(f"/api/cameras/{cam_id}/autocalibrate")
        t.join()

        assert res_calib.status_code == 200
        calib_data = res_calib.json()
        assert calib_data["status"] == "done"
        assert "1920x1080 (MJPG)" in calib_data["summary"]

        # 3. Запрос списка камер: профиль включен в ответ
        res_list = client.get("/api/cameras")
        assert res_list.status_code == 200
        cams = res_list.json()
        c = next(x for x in cams if x["id"] == cam_id)
        assert c["calibration_summary"] == "1920x1080 (MJPG), автофокус: поддерживается"
