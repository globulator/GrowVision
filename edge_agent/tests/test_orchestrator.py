import threading
import time
from unittest.mock import MagicMock

import pytest

from edge_agent.config import EdgeConfig
from edge_agent.hardware import HardwareManager
from edge_agent.offline_queue import get_queue_count
from edge_agent.orchestrator import EdgeOrchestrator
from edge_agent.vision import VisionPipeline
from shared.schemas import (
    CommandInstallation,
    CommandItem,
    CommandResult,
    HeartbeatActiveInstallation,
    HeartbeatResponse,
)


class DummyEdgeClient:
    """Эмулятор Edge API клиента для юнит-тестов оркестратора."""

    def __init__(self, heartbeat_response: HeartbeatResponse | None = None):
        self.heartbeat_calls = []
        self.claim_calls = []
        self.results = {}
        self.uploaded_captures = []
        self.fail_network_upload = False
        self.heartbeat_response = heartbeat_response

    def send_heartbeat(self, telemetry) -> HeartbeatResponse | None:
        self.heartbeat_calls.append(telemetry)
        return self.heartbeat_response

    def claim_command(self):
        self.claim_calls.append(True)
        return []

    def send_command_result(self, command_id: int, result: CommandResult, **_kwargs):
        self.results[command_id] = result
        return True

    def upload_capture(self, metadata, raw_bytes, annotated_bytes):
        if self.fail_network_upload:
            return 0, {"error": "Connection error"}
        self.uploaded_captures.append((metadata, raw_bytes, annotated_bytes))
        return 201, {"capture_id": 99}


@pytest.fixture
def dummy_client() -> DummyEdgeClient:
    return DummyEdgeClient()


@pytest.fixture
def orchestrator(
    test_config: EdgeConfig,
    dummy_client: DummyEdgeClient,
    mock_hardware: HardwareManager,
    mock_vision: VisionPipeline,
) -> EdgeOrchestrator:
    return EdgeOrchestrator(
        config=test_config,
        client=dummy_client,
        hardware=mock_hardware,
        vision=mock_vision,
    )


def test_init_installations_light_turns_main_light_on(
    orchestrator: EdgeOrchestrator,
    mock_hardware: HardwareManager,
):
    """При старте основной свет активных установок переводится в состояние ВКЛ."""
    orchestrator.active_installations = [
        HeartbeatActiveInstallation(
            id=1,
            light_gpio_pin=17,
            flash_gpio_pin=27,
            camera_hardware_ids=["cam-1"],
        )
    ]
    orchestrator.init_installations_light()
    assert mock_hardware.get_pin_state(17) is True
    assert mock_hardware.get_pin_state(27) is False
    assert (1, 17, 27) in orchestrator._initialized_installations


def test_subsequent_heartbeats_do_not_retrigger_relay(
    orchestrator: EdgeOrchestrator,
    dummy_client: DummyEdgeClient,
    mock_hardware: HardwareManager,
):
    """Два heartbeat подряд не переключают реле уже инициализированной установки."""
    dummy_client.heartbeat_response = HeartbeatResponse(
        capture_interval_hours=1,
        debug_mode=False,
        active_installations=[
            HeartbeatActiveInstallation(
                id=1,
                light_gpio_pin=17,
                flash_gpio_pin=27,
                camera_hardware_ids=["cam-1"],
            )
        ],
    )

    # Первый heartbeat инициализирует свет
    orchestrator.perform_heartbeat()
    assert mock_hardware.get_pin_state(17) is True
    assert (1, 17, 27) in orchestrator._initialized_installations

    # Мокаем вызовы переключения реле
    mock_hardware.set_light = MagicMock(wraps=mock_hardware.set_light)
    mock_hardware.set_flash = MagicMock(wraps=mock_hardware.set_flash)

    # Второй heartbeat для тех же установок не должен трогать реле
    orchestrator.perform_heartbeat()
    mock_hardware.set_light.assert_not_called()
    mock_hardware.set_flash.assert_not_called()


def test_light_main_off_persists_after_subsequent_heartbeat(
    orchestrator: EdgeOrchestrator,
    dummy_client: DummyEdgeClient,
    mock_hardware: HardwareManager,
):
    """Ручная команда LIGHT_MAIN off сохраняется после последующего heartbeat."""
    inst = HeartbeatActiveInstallation(
        id=1,
        light_gpio_pin=17,
        flash_gpio_pin=27,
        camera_hardware_ids=["cam-1"],
    )
    dummy_client.heartbeat_response = HeartbeatResponse(
        capture_interval_hours=1,
        debug_mode=False,
        active_installations=[inst],
    )

    # Инициализация первым heartbeat
    orchestrator.perform_heartbeat()
    assert mock_hardware.get_pin_state(17) is True

    # Ручная команда выключения света
    cmd = CommandItem(
        id=10,
        command_type="LIGHT_MAIN",
        is_global=False,
        installation=CommandInstallation(
            id=1,
            light_gpio_pin=17,
            flash_gpio_pin=27,
            camera_hardware_ids=["cam-1"],
        ),
        payload={"state": False},
    )
    orchestrator.handle_command(cmd)
    assert mock_hardware.get_pin_state(17) is False

    # Последующий heartbeat не должен сбрасывать ручное состояние
    orchestrator.perform_heartbeat()
    assert mock_hardware.get_pin_state(17) is False


def test_heartbeat_defers_light_init_when_mutex_locked(
    orchestrator: EdgeOrchestrator,
    dummy_client: DummyEdgeClient,
    mock_hardware: HardwareManager,
):
    """Heartbeat при занятом аппаратном мьютексе не трогает пины и откладывает инициализацию."""
    inst = HeartbeatActiveInstallation(
        id=2,
        light_gpio_pin=22,
        flash_gpio_pin=23,
        camera_hardware_ids=["cam-2"],
    )
    dummy_client.heartbeat_response = HeartbeatResponse(
        capture_interval_hours=1,
        debug_mode=False,
        active_installations=[inst],
    )

    # Блокируем мьютекс аппаратуры (имитация идущей съемки)
    assert mock_hardware.acquire_lock(blocking=False) is True

    # Выполняем heartbeat
    orchestrator.perform_heartbeat()

    # Пины не должны быть изменены, установка не должна числиться инициализированной
    assert mock_hardware.get_pin_state(22) is False
    assert (2, 22, 23) not in orchestrator._initialized_installations

    # Освобождаем мьютекс
    mock_hardware.release_lock()

    # Следующий heartbeat должен успешно инициализировать пины
    orchestrator.perform_heartbeat()
    assert mock_hardware.get_pin_state(22) is True
    assert (2, 22, 23) in orchestrator._initialized_installations


def test_handle_command_light_main(
    orchestrator: EdgeOrchestrator,
    dummy_client: DummyEdgeClient,
    mock_hardware: HardwareManager,
):
    """Проверка выполнения команды LIGHT_MAIN."""
    cmd = CommandItem(
        id=1,
        command_type="LIGHT_MAIN",
        is_global=False,
        installation=CommandInstallation(
            id=10,
            light_gpio_pin=17,
            flash_gpio_pin=27,
            camera_hardware_ids=["cam-1"],
        ),
        payload={"state": True},
    )

    orchestrator.handle_command(cmd)

    assert mock_hardware.get_pin_state(17) is True
    assert 1 in dummy_client.results
    res = dummy_client.results[1]
    assert res.status == "done"
    assert "успешно включен" in res.result_message


def test_handle_command_relay_test_zero_duration(
    orchestrator: EdgeOrchestrator,
    dummy_client: DummyEdgeClient,
    mock_hardware: HardwareManager,
):
    """Проверка выполнения RELAY_TEST с нулевой задержкой и возвратом исходного состояния."""
    # Исходное состояние: пин 17 включен (свет), пин 27 выключен (вспышка)
    mock_hardware.set_pin(17, True)
    mock_hardware.set_pin(27, False)

    cmd = CommandItem(
        id=3,
        command_type="RELAY_TEST",
        is_global=False,
        installation=CommandInstallation(
            id=10,
            light_gpio_pin=17,
            flash_gpio_pin=27,
            camera_hardware_ids=["cam-1"],
        ),
    )

    orchestrator._handle_relay_test(cmd, duration=0.0)

    assert 3 in dummy_client.results
    res = dummy_client.results[3]
    assert res.status == "done"
    assert "успешно завершен" in res.result_message

    # Исходное состояние должно сохраниться
    assert mock_hardware.get_pin_state(17) is True
    assert mock_hardware.get_pin_state(27) is False


def test_handle_command_snap_multiple_cameras_debug_log(
    orchestrator: EdgeOrchestrator,
    dummy_client: DummyEdgeClient,
):
    """Проверка SNAP: снимает все камеры установки, формирует строку [GPIO] Inst#N... в debug_log_line."""
    cmd = CommandItem(
        id=5,
        command_type="SNAP",
        is_global=False,
        installation=CommandInstallation(
            id=10,
            light_gpio_pin=17,
            flash_gpio_pin=27,
            camera_hardware_ids=["mock-usb-cam-1", "mock-csi-cam-2"],
        ),
    )

    orchestrator.handle_command(cmd)

    # Должно быть отправлено 2 кадра (по 1 для каждой камеры)
    assert len(dummy_client.uploaded_captures) == 2

    assert 5 in dummy_client.results
    res = dummy_client.results[5]
    assert res.status == "done"
    assert "2 кадр" in res.result_message
    assert res.debug_log_line is not None
    assert "[GPIO] Inst#10: light=0, flash=1 -> captured 2 frames" in res.debug_log_line


def test_handle_command_mutex_conflict_scheduled_capture_priority(
    orchestrator: EdgeOrchestrator,
    dummy_client: DummyEdgeClient,
    monkeypatch,
):
    """Проверка: если аппаратный мьютекс занят и не освобождается за таймаут, команда отклоняется."""
    orchestrator.hardware.acquire_lock(blocking=True)
    orig_acquire = orchestrator.hardware.acquire_lock
    # Мокаем таймаут до 0.05 с для ускорения теста
    monkeypatch.setattr(
        orchestrator.hardware,
        "acquire_lock",
        lambda blocking=True, timeout=10.0: orig_acquire(blocking=True, timeout=0.05),
    )

    cmd = CommandItem(
        id=6,
        command_type="SNAP",
        is_global=False,
        installation=CommandInstallation(
            id=10,
            light_gpio_pin=17,
            flash_gpio_pin=27,
            camera_hardware_ids=["mock-usb-cam-1"],
        ),
    )

    try:
        orchestrator.handle_command(cmd)
    finally:
        orchestrator.hardware.release_lock()

    assert len(dummy_client.uploaded_captures) == 0
    assert 6 in dummy_client.results
    res = dummy_client.results[6]
    assert res.status == "error"
    assert res.result_message == "Камера недоступна, попробуйте позже"


def test_handle_command_waits_for_scheduled_capture_lock(
    orchestrator: EdgeOrchestrator,
    dummy_client: DummyEdgeClient,
):
    """Проверка: если аппаратный мьютекс освобождается в течение таймаута, команда успешно выполняется."""
    orchestrator.hardware.acquire_lock(blocking=True)

    # Запускаем освобождение блокировки через 0.1 с в параллельном потоке
    def _release_soon():
        time.sleep(0.1)
        orchestrator.hardware.release_lock()

    t = threading.Thread(target=_release_soon)
    t.start()

    cmd = CommandItem(
        id=7,
        command_type="SNAP",
        is_global=False,
        installation=CommandInstallation(
            id=10,
            light_gpio_pin=17,
            flash_gpio_pin=27,
            camera_hardware_ids=["mock-usb-cam-1"],
        ),
    )

    orchestrator.handle_command(cmd)
    t.join()

    assert len(dummy_client.uploaded_captures) == 1
    assert 7 in dummy_client.results
    res = dummy_client.results[7]
    assert res.status == "done"


def test_orchestrator_distinct_frame_indices_per_installation_and_camera(
    orchestrator: EdgeOrchestrator,
):
    """Проверка раздельного frame_index для каждой пары (installation_id, camera_id)."""
    assert orchestrator._get_and_inc_frame_index(1, "cam-a") == 0
    assert orchestrator._get_and_inc_frame_index(1, "cam-a") == 1
    assert orchestrator._get_and_inc_frame_index(1, "cam-b") == 0
    assert orchestrator._get_and_inc_frame_index(2, "cam-a") == 0
    assert orchestrator._get_and_inc_frame_index(1, "cam-a") == 2


def test_scheduled_capture_buffered_to_queue_on_network_error(
    orchestrator: EdgeOrchestrator,
    dummy_client: DummyEdgeClient,
    test_config: EdgeConfig,
):
    """Проверка: при сетевом сбое плановый снимок сохраняется в офлайн-очередь."""
    dummy_client.fail_network_upload = True
    orchestrator.active_installations = [
        HeartbeatActiveInstallation(
            id=10,
            light_gpio_pin=17,
            flash_gpio_pin=27,
            camera_hardware_ids=["mock-usb-cam-1"],
        )
    ]

    assert get_queue_count(test_config.offline_queue_dir) == 0

    orchestrator.trigger_scheduled_capture()

    assert get_queue_count(test_config.offline_queue_dir) == 1


def test_orchestrator_stop_idempotent(orchestrator: EdgeOrchestrator):
    """Проверка идемпотентности метода stop(): повторные вызовы безопасны и не повторяют cleanup."""
    orchestrator.hardware.cleanup = MagicMock()
    assert orchestrator._stopped is False
    orchestrator.stop()
    assert orchestrator._stopped is True
    assert orchestrator.hardware.cleanup.call_count == 1

    # Повторный вызов
    orchestrator.stop()
    assert orchestrator._stopped is True
    assert orchestrator.hardware.cleanup.call_count == 1


def test_orchestrator_logger_no_duplicate_handlers():
    """Проверка отсутствия дублирующих обработчиков в edge_agent.orchestrator."""
    import logging

    from shared.logger import setup_logger

    root_logger = setup_logger("edge_agent", debug_mode=False)
    orch_logger = logging.getLogger("edge_agent.orchestrator")

    assert len(orch_logger.handlers) == 0
    assert len(root_logger.handlers) > 0


def test_stage8_9_snap_fast_mock_execution_and_poll_interval(
    orchestrator: EdgeOrchestrator,
    dummy_client: DummyEdgeClient,
):
    """Этап 8.9: Быстрое выполнение SNAP в mock-режиме (без time.sleep) и лог [SNAP] elapsed."""
    assert orchestrator.config.command_poll_interval_sec == 0.5
    cmd = CommandItem(
        id=89,
        command_type="SNAP",
        is_global=False,
        installation=CommandInstallation(
            id=1,
            light_gpio_pin=17,
            flash_gpio_pin=27,
            camera_hardware_ids=["mock-cam-fast"],
        ),
    )

    t0 = time.perf_counter()
    orchestrator.handle_command(cmd)
    t_elapsed = time.perf_counter() - t0

    assert 89 in dummy_client.results
    res = dummy_client.results[89]
    assert res.status == "done"
    assert "[SNAP] elapsed=" in res.debug_log_line
    assert t_elapsed < 1.0
