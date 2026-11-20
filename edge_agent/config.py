import os
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import BaseModel, Field

DEFAULT_ENV_FILE = Path(__file__).resolve().parent / ".env"
DEFAULT_OFFLINE_QUEUE_DIR = Path(__file__).resolve().parent / "offline_queue"
DEFAULT_MODELS_DIR = Path(__file__).resolve().parent / "models"


class EdgeConfig(BaseModel):
    """Конфигурация Edge-агента."""

    server_url: str = Field(default="http://127.0.0.1:8000")
    edge_api_key: str = Field(default="")
    hardware_mode: Literal["rpi", "mock"] = Field(default="mock")
    heartbeat_interval_sec: float = Field(default=30.0, gt=0)
    claim_poll_interval_sec: float = Field(default=2.0, gt=0)
    offline_queue_dir: Path = Field(default=DEFAULT_OFFLINE_QUEUE_DIR)
    models_dir: Path = Field(default=DEFAULT_MODELS_DIR)
    model_name: str = Field(default="lettuce_seg_v1.onnx")
    capture_interval_hours: int = Field(default=1, gt=0)
    debug_mode: bool = Field(default=False)

    def validate_api_key(self) -> None:
        """Проверяет наличие обязательного ключа API."""
        if not self.edge_api_key.strip():
            raise ValueError(
                "EDGE_API_KEY не задан. Укажите ключ в .env файле или переменной окружения."
            )


def load_config(env_path: Path | str | None = None) -> EdgeConfig:
    """Загружает конфигурацию из указанного .env файла и переменных окружения."""
    env_file = Path(env_path) if env_path else DEFAULT_ENV_FILE

    # Сначала считываем значения из файла, если он существует
    file_vars: dict[str, str] = {}
    if env_file.is_file():
        file_vars = {
            k: v
            for k, v in dotenv_values(dotenv_path=env_file).items()
            if v is not None
        }

    # Объединяем: file_vars перекрываются os.environ
    def get_val(key: str, default: str | None = None) -> str | None:
        if key in os.environ and os.environ[key] != "":
            return os.environ[key]
        if key in file_vars:
            return file_vars[key]
        return default

    server_url = (
        get_val("SERVER_URL", "http://127.0.0.1:8000") or "http://127.0.0.1:8000"
    )
    edge_api_key = get_val("EDGE_API_KEY", "") or ""

    hw_mode_raw = (get_val("HARDWARE_MODE", "mock") or "mock").lower()
    hardware_mode = "rpi" if hw_mode_raw == "rpi" else "mock"

    try:
        heartbeat_interval = float(get_val("HEARTBEAT_INTERVAL_SEC", "30.0") or 30.0)
    except ValueError:
        heartbeat_interval = 30.0

    try:
        claim_poll_interval = float(get_val("CLAIM_POLL_INTERVAL_SEC", "2.0") or 2.0)
    except ValueError:
        claim_poll_interval = 2.0

    queue_dir_raw = get_val("OFFLINE_QUEUE_DIR")
    queue_dir = Path(queue_dir_raw) if queue_dir_raw else DEFAULT_OFFLINE_QUEUE_DIR

    models_dir_raw = get_val("MODELS_DIR")
    models_dir = Path(models_dir_raw) if models_dir_raw else DEFAULT_MODELS_DIR

    model_name = get_val("MODEL_NAME", "lettuce_seg_v1.onnx") or "lettuce_seg_v1.onnx"

    try:
        capture_interval_hours = int(get_val("CAPTURE_INTERVAL_HOURS", "1") or 1)
    except ValueError:
        capture_interval_hours = 1

    debug_mode_raw = (get_val("DEBUG_MODE", "false") or "false").lower()
    debug_mode = debug_mode_raw in ("true", "1", "yes")

    return EdgeConfig(
        server_url=server_url.rstrip("/"),
        edge_api_key=edge_api_key,
        hardware_mode=hardware_mode,
        heartbeat_interval_sec=heartbeat_interval,
        claim_poll_interval_sec=claim_poll_interval,
        offline_queue_dir=queue_dir,
        models_dir=models_dir,
        model_name=model_name,
        capture_interval_hours=capture_interval_hours,
        debug_mode=debug_mode,
    )
