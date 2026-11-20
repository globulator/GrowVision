from pathlib import Path

import pytest

from edge_agent.config import EdgeConfig
from edge_agent.hardware import HardwareManager
from edge_agent.vision import VisionPipeline


@pytest.fixture
def tmp_queue_dir(tmp_path: Path) -> Path:
    """Временный каталог для тестирования офлайн-очереди."""
    queue_dir = tmp_path / "offline_queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    return queue_dir


@pytest.fixture
def test_config(tmp_path: Path, tmp_queue_dir: Path) -> EdgeConfig:
    """Тестовая конфигурация Edge-агента."""
    models_dir = tmp_path / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    return EdgeConfig(
        server_url="http://testserver:8000",
        edge_api_key="test-secret-key-123",
        hardware_mode="mock",
        heartbeat_interval_sec=0.1,
        claim_poll_interval_sec=0.1,
        offline_queue_dir=tmp_queue_dir,
        models_dir=models_dir,
        model_name="test_model.onnx",
        capture_interval_hours=1,
        debug_mode=True,
    )


@pytest.fixture
def mock_hardware() -> HardwareManager:
    """Менеджер оборудования в mock-режиме."""
    hw = HardwareManager(mode="mock")
    yield hw
    hw.cleanup()


@pytest.fixture
def mock_vision() -> VisionPipeline:
    """Пайплайн компьютерного зрения в mock-режиме."""
    return VisionPipeline(model_path=None, mock_mode=True)
