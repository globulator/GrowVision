import os
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import BaseModel, Field

DEFAULT_ENV_FILE = Path(__file__).resolve().parent / ".env"
DEFAULT_OFFLINE_QUEUE_DIR = Path(__file__).resolve().parent / "offline_queue"
DEFAULT_MODELS_DIR = Path(__file__).resolve().parent / "models"
DEFAULT_MODEL_PATH = DEFAULT_MODELS_DIR / "lettuce_seg_v1.onnx"
DEFAULT_V4L_BY_ID_DIR = Path("/dev/v4l/by-id")
DEFAULT_SYS_DIR = Path("/sys")


class EdgeConfig(BaseModel):
    """Конфигурация Edge-агента."""

    server_url: str = Field(default="http://127.0.0.1:8000")
    edge_api_key: str = Field(default="")
    hardware_mode: Literal["rpi", "mock"] = Field(default="mock")
    heartbeat_interval_sec: float = Field(default=30.0, gt=0)
    command_poll_interval_sec: float = Field(default=2.0, gt=0)
    offline_queue_dir: Path = Field(default=DEFAULT_OFFLINE_QUEUE_DIR)
    model_path: Path = Field(default=DEFAULT_MODEL_PATH)
    capture_interval_hours: int = Field(default=1, gt=0)
    debug_mode: bool = Field(default=False)

    # Параметры железа и таймингов
    relay_switch_delay_ms: int = Field(default=200, ge=0)
    camera_warmup_frames: int = Field(default=5, ge=0)
    relay_active_high: bool = Field(default=True)
    upload_timeout_sec: float = Field(default=60.0, gt=0)

    # Пути к системным устройствам для сканирования и тестов
    v4l_by_id_dir: Path = Field(default=DEFAULT_V4L_BY_ID_DIR)
    sys_dir: Path = Field(default=DEFAULT_SYS_DIR)

    @property
    def claim_poll_interval_sec(self) -> float:
        """Алиас для обратной совместимости."""
        return self.command_poll_interval_sec

    @property
    def model_name(self) -> str:
        """Имя файла модели."""
        return self.model_path.name

    def validate_api_key(self) -> None:
        """Проверяет наличие обязательного ключа API."""
        if not self.edge_api_key.strip():
            raise ValueError(
                "EDGE_API_KEY не задан. Укажите ключ в .env файле или переменной окружения."
            )


def load_config(env_path: Path | str | None = None) -> EdgeConfig:
    """Загружает конфигурацию из указанного .env файла и переменных окружения."""
    env_file = Path(env_path) if env_path else DEFAULT_ENV_FILE

    file_vars: dict[str, str] = {}
    if env_file.is_file():
        file_vars = {
            k: v
            for k, v in dotenv_values(dotenv_path=env_file).items()
            if v is not None
        }

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

    hw_mode_raw = (get_val("HARDWARE_MODE", "mock") or "mock").strip().lower()
    if hw_mode_raw not in ("rpi", "mock"):
        raise ValueError(
            f"Недопустимое значение HARDWARE_MODE='{hw_mode_raw}'. Допустимы только 'rpi' или 'mock'."
        )
    hardware_mode: Literal["rpi", "mock"] = "rpi" if hw_mode_raw == "rpi" else "mock"

    try:
        heartbeat_interval = float(get_val("HEARTBEAT_INTERVAL_SEC", "30.0") or 30.0)
    except ValueError:
        heartbeat_interval = 30.0

    poll_raw = get_val("COMMAND_POLL_INTERVAL_SEC") or get_val(
        "CLAIM_POLL_INTERVAL_SEC", "2.0"
    )
    try:
        command_poll_interval = float(poll_raw or 2.0)
    except ValueError:
        command_poll_interval = 2.0

    queue_dir_raw = get_val("OFFLINE_QUEUE_DIR")
    queue_dir = Path(queue_dir_raw) if queue_dir_raw else DEFAULT_OFFLINE_QUEUE_DIR

    model_path_raw = get_val("MODEL_PATH")
    if model_path_raw:
        model_path = Path(model_path_raw)
    else:
        models_dir_raw = get_val("MODELS_DIR")
        models_dir = Path(models_dir_raw) if models_dir_raw else DEFAULT_MODELS_DIR
        model_name = (
            get_val("MODEL_NAME", "lettuce_seg_v1.onnx") or "lettuce_seg_v1.onnx"
        )
        model_path = models_dir / model_name

    try:
        capture_interval_hours = int(get_val("CAPTURE_INTERVAL_HOURS", "1") or 1)
    except ValueError:
        capture_interval_hours = 1

    debug_mode_raw = (get_val("DEBUG_MODE", "false") or "false").lower()
    debug_mode = debug_mode_raw in ("true", "1", "yes")

    try:
        relay_switch_delay_ms = int(get_val("RELAY_SWITCH_DELAY_MS", "200") or 200)
    except ValueError:
        relay_switch_delay_ms = 200

    try:
        camera_warmup_frames = int(get_val("CAMERA_WARMUP_FRAMES", "5") or 5)
    except ValueError:
        camera_warmup_frames = 5

    relay_active_high_raw = (get_val("RELAY_ACTIVE_HIGH", "true") or "true").lower()
    relay_active_high = relay_active_high_raw in ("true", "1", "yes")

    try:
        upload_timeout_sec = float(get_val("UPLOAD_TIMEOUT_SEC", "60.0") or 60.0)
    except ValueError:
        upload_timeout_sec = 60.0

    v4l_raw = get_val("V4L_BY_ID_DIR")
    v4l_by_id_dir = Path(v4l_raw) if v4l_raw else DEFAULT_V4L_BY_ID_DIR

    sys_raw = get_val("SYS_DIR")
    sys_dir = Path(sys_raw) if sys_raw else DEFAULT_SYS_DIR

    return EdgeConfig(
        server_url=server_url.rstrip("/"),
        edge_api_key=edge_api_key,
        hardware_mode=hardware_mode,
        heartbeat_interval_sec=heartbeat_interval,
        command_poll_interval_sec=command_poll_interval,
        offline_queue_dir=queue_dir,
        model_path=model_path,
        capture_interval_hours=capture_interval_hours,
        debug_mode=debug_mode,
        relay_switch_delay_ms=relay_switch_delay_ms,
        camera_warmup_frames=camera_warmup_frames,
        relay_active_high=relay_active_high,
        upload_timeout_sec=upload_timeout_sec,
        v4l_by_id_dir=v4l_by_id_dir,
        sys_dir=sys_dir,
    )
