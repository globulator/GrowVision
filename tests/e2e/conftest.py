import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.staticfiles import StaticFiles

# Добавляем корень проекта в sys.path
root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from server.api.deps import get_db
from server.config import get_settings
from server.db.database import Base
from server.main import app

CLIENT_API_KEY = "test-client-cli-key"
EDGE_API_KEY = "test-edge-cli-key"
DEV_PASS = "dev-pass-123"
BIO_PASS = "bio-pass-123"

SAMPLE_HEARTBEAT_PAYLOAD = {
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


def get_free_port() -> int:
    """Возвращает свободный порт на localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def e2e_uvicorn_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Поднимает изолированный FastAPI uvicorn сервер на свободном порту."""
    db_file = tmp_path / "e2e_uvicorn.db"
    db_url = f"sqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    # Изоляция настроек
    settings = get_settings()
    monkeypatch.setattr(settings, "client_api_key", CLIENT_API_KEY)
    monkeypatch.setattr(settings, "edge_api_key", EDGE_API_KEY)
    monkeypatch.setattr(settings, "developer_password", DEV_PASS)
    monkeypatch.setattr(settings, "biologist_password", BIO_PASS)
    monkeypatch.setattr(settings, "video_max_fps", 10)
    monkeypatch.setattr(settings, "video_min_duration_sec", 2)

    # Изоляция media
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("server.api.edge.get_media_dir", lambda: media_dir)
    monkeypatch.setattr("server.api.timelapse.get_media_dir", lambda: media_dir)

    orig_media_app = None
    media_route = None
    for route in app.routes:
        if getattr(route, "name", None) == "media":
            media_route = route
            orig_media_app = route.app
            route.app = StaticFiles(directory=media_dir)
            break

    port = get_free_port()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="error",
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    base_url = f"http://127.0.0.1:{port}"

    # Ожидание готовности сервера
    start_t = time.monotonic()
    ready = False
    while time.monotonic() - start_t < 5.0:
        try:
            with httpx.Client(base_url=base_url, timeout=0.5) as c:
                r = c.get("/api/version")
                if r.status_code == 200:
                    ready = True
                    break
        except (httpx.HTTPError, OSError):
            time.sleep(0.05)

    if not ready:
        server.should_exit = True
        raise RuntimeError(f"Uvicorn сервер не запустился на порту {port}")

    server_data = {
        "base_url": base_url,
        "port": port,
        "session_factory": session_factory,
        "media_dir": media_dir,
        "client_key": CLIENT_API_KEY,
        "edge_key": EDGE_API_KEY,
        "dev_pass": DEV_PASS,
        "bio_pass": BIO_PASS,
        "tmp_path": tmp_path,
    }

    try:
        yield server_data
    finally:
        server.should_exit = True
        server_thread.join(timeout=3.0)
        app.dependency_overrides.clear()
        if media_route and orig_media_app:
            media_route.app = orig_media_app
        engine.dispose()
