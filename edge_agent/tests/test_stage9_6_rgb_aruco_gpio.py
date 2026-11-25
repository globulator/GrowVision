import json
import logging
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import pytest

from edge_agent.config import EdgeConfig
from edge_agent.hardware import HardwareManager
from edge_agent.live_stream import LiveStreamManager
from edge_agent.orchestrator import EdgeOrchestrator
from edge_agent.vision import VisionPipeline
from shared.schemas import (
    CommandInstallation,
    CommandItem,
    HeartbeatActiveInstallation,
)


def _make_aruco_and_plant_jpeg(dict_id: int, marker_id: int = 3) -> bytes:
    """Генерирует кадр с маркером ArUco указанного словаря и зелёным растением."""
    img = np.full((480, 640, 3), 50, dtype=np.uint8)
    aruco_dict = cv2.aruco.getPredefinedDictionary(dict_id)
    marker_size = 90
    if hasattr(cv2.aruco, "generateImageMarker"):
        marker_core = cv2.aruco.generateImageMarker(aruco_dict, marker_id, marker_size)
    else:
        marker_core = cv2.aruco.drawMarker(aruco_dict, marker_id, marker_size)
    pad = 14
    patch_gray = np.full(
        (marker_size + 2 * pad, marker_size + 2 * pad), 255, dtype=np.uint8
    )
    patch_gray[pad : pad + marker_size, pad : pad + marker_size] = marker_core
    marker_bgr = cv2.cvtColor(patch_gray, cv2.COLOR_GRAY2BGR)
    img[90 : 90 + marker_bgr.shape[0], 110 : 110 + marker_bgr.shape[1]] = marker_bgr

    # Зелёное растение справа
    cv2.circle(img, (440, 260), 65, (30, 190, 40), -1)
    ok, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    assert ok
    return enc.tobytes()


def test_gpio_logging_mock_and_rpi_lifecycle(caplog, monkeypatch):
    """[GPIO] логирует инициализацию, переключения и shutdown с длительностью в mock и rpi."""
    with caplog.at_level(logging.INFO, logger="edge_agent.hardware"):
        # 1. Режим mock
        hw_mock = HardwareManager(mode="mock", relay_active_high=True)
        hw_mock.set_light(17, True, source="init")
        hw_mock.set_flash(27, False, source="init")
        hw_mock.set_light(17, False, source="capture_warmup")
        hw_mock.set_flash(27, True, source="capture_warmup")
        hw_mock.set_pin(22, True, source="manual")
        time.sleep(0.03)
        hw_mock.cleanup()

    mock_msgs = [r.message for r in caplog.records if "[GPIO]" in r.message]
    assert any(
        "[GPIO] BCM 17 (light): UNINIT -> ON (level=HIGH, mode=mock, source=init)" in m
        for m in mock_msgs
    )
    assert any(
        "[GPIO] BCM 27 (flash): UNINIT -> OFF (level=LOW, mode=mock, source=init)" in m
        for m in mock_msgs
    )
    assert any(
        "[GPIO] BCM 17 (light): ON -> OFF (level=LOW, mode=mock, source=capture_warmup)"
        in m
        for m in mock_msgs
    )
    assert any(
        "[GPIO] BCM 27 (flash): OFF -> ON (level=HIGH, mode=mock, source=capture_warmup)"
        in m
        for m in mock_msgs
    )
    assert any(
        "[GPIO] BCM 22 (gpio): UNINIT -> ON (level=HIGH, mode=mock, source=manual)" in m
        for m in mock_msgs
    )
    assert any(
        "[GPIO] BCM 27 (flash): ON -> OFF (level=LOW, mode=mock, source=shutdown, duration="
        in m
        for m in mock_msgs
    )

    # 2. Режим rpi с замоканным gpiozero.OutputDevice и active_high=False
    caplog.clear()
    created_devices: dict[int, MagicMock] = {}

    def fake_output_device(pin, active_high=True, initial_value=False):
        dev = MagicMock()
        dev.is_active = bool(initial_value)
        dev.on.side_effect = lambda: setattr(dev, "is_active", True)
        dev.off.side_effect = lambda: setattr(dev, "is_active", False)
        created_devices[pin] = dev
        return dev

    fake_gpiozero = SimpleNamespace(OutputDevice=fake_output_device)
    monkeypatch.setitem(sys.modules, "gpiozero", fake_gpiozero)

    with caplog.at_level(logging.INFO, logger="edge_agent.hardware"):
        hw_rpi = HardwareManager(mode="rpi", relay_active_high=False)
        hw_rpi.set_light(17, True, source="init")
        hw_rpi.set_flash(27, True, source="manual")
        time.sleep(0.02)
        hw_rpi.cleanup()

    rpi_msgs = [r.message for r in caplog.records if "[GPIO]" in r.message]
    # При active_high=False логический ON соответствует физическому LOW
    assert any(
        "[GPIO] BCM 17 (light): UNINIT -> ON (level=LOW, mode=rpi, source=init)" in m
        for m in rpi_msgs
    )
    assert any(
        "[GPIO] BCM 27 (flash): UNINIT -> ON (level=LOW, mode=rpi, source=manual)" in m
        for m in rpi_msgs
    )
    assert any(
        "[GPIO] BCM 17 (light): ON -> OFF (level=HIGH, mode=rpi, source=shutdown, duration="
        in m
        for m in rpi_msgs
    )
    created_devices[17].off.assert_called()
    created_devices[17].close.assert_called()


def test_live_annotated_applies_rgb_before_cv_and_keeps_annotation_colors():
    """RGB применяется до CV-разметки, а цвета контура, ArUco и линеек остаются неизменными."""
    vision = VisionPipeline(mock_mode=True)
    mgr = LiveStreamManager(
        mode="mock", mock_cameras=True, client=MagicMock(), vision=vision
    )
    mgr.is_running = True
    mgr.active_camera_id = "mock-usb-cam-1"
    mgr.set_camera_context(inst_name="Бокс-1", culture_name="Салат", aruco_size_mm=50.0)
    mgr.set_rgb_gains(0.0, 1.0, 0.0)  # Обнуляем красный и синий каналы фона
    mgr.set_stream_mode("annotated")

    ann_bytes = mgr._generate_frame()
    assert ann_bytes is not None
    ann_img = cv2.imdecode(np.frombuffer(ann_bytes, np.uint8), cv2.IMREAD_COLOR)
    assert ann_img is not None

    # Фон был (48, 48, 48) -> после R=0, B=0 стал почти (0, 48, 0), то есть B и R близки к 0
    bg_patch = ann_img[440:470, 20:60]
    assert np.mean(bg_patch[:, :, 0]) < 10
    assert np.mean(bg_patch[:, :, 2]) < 10

    # Но сама разметка поверх осталась в своих неизменных цветах:
    # 1) Красная точка в центре растения (R > 190, B < 60, G < 60)
    assert np.any(
        (ann_img[:, :, 2] > 190) & (ann_img[:, :, 0] < 60) & (ann_img[:, :, 1] < 60)
    )
    # 2) Жёлтая рамка ArUco (R > 200, G > 200, B < 60)
    assert np.any(
        (ann_img[:, :, 2] > 200) & (ann_img[:, :, 1] > 200) & (ann_img[:, :, 0] < 60)
    )
    # 3) Фиолетовая линейка (BGR ~ 240, 32, 160)
    purple = (
        (np.abs(ann_img[:, :, 0].astype(int) - 240) < 40)
        & (np.abs(ann_img[:, :, 1].astype(int) - 32) < 40)
        & (np.abs(ann_img[:, :, 2].astype(int) - 160) < 40)
    )
    assert np.any(purple)


def test_orchestrator_applies_rgb_before_vision_on_snap_and_schedule(tmp_path: Path):
    """EdgeOrchestrator применяет RGB до CV при SNAP и плановой съёмке и ставит rgb_applied=True."""
    cfg = EdgeConfig(
        edge_api_key="k",
        hardware_mode="mock",
        mock_cameras=True,
        offline_queue_dir=tmp_path / "queue",
    )
    mock_client = MagicMock()
    mock_client.upload_capture.return_value = (201, {"capture_id": 1})
    orch = EdgeOrchestrator(config=cfg, client=mock_client)

    # Передаём RGB через START_LIVE (или SET_CAMERA_CONTROLS)
    orch.handle_command(
        CommandItem(
            id=1,
            command_type="START_LIVE",
            payload={
                "camera_hardware_id": "mock-usb-cam-1",
                "rgb": {"r": 0.0, "g": 1.0, "b": 0.0},
            },
        )
    )
    assert orch.live_stream.rgb_gains == (0.0, 1.0, 0.0)

    inst = CommandInstallation(
        id=1,
        name="Бокс RGB",
        culture_name="Базилик",
        light_gpio_pin=4,
        flash_gpio_pin=17,
        camera_hardware_ids=["mock-usb-cam-1"],
    )
    orch.active_installations = [
        HeartbeatActiveInstallation(
            id=1,
            name="Бокс RGB",
            culture_name="Базилик",
            light_gpio_pin=4,
            flash_gpio_pin=17,
            camera_hardware_ids=["mock-usb-cam-1"],
            camera_rgb={"mock-usb-cam-1": {"r": 0.0, "g": 1.0, "b": 0.0}},
        )
    ]

    with patch.object(
        orch.vision, "process_capture", wraps=orch.vision.process_capture
    ) as proc_spy:
        # 1. Тестовый снимок SNAP
        orch.handle_command(CommandItem(id=2, command_type="SNAP", installation=inst))
        # 2. Плановый снимок
        orch.trigger_scheduled_capture()

        assert proc_spy.call_count == 2
        assert mock_client.upload_capture.call_count == 2
        for call in mock_client.upload_capture.call_args_list:
            meta, raw_uploaded, ann_uploaded = call[0]
            assert meta.rgb_applied is True
            # Проверка, что raw_uploaded уже имеет обнулённый R и B каналы на сером фоне
            raw_img = cv2.imdecode(
                np.frombuffer(raw_uploaded, np.uint8), cv2.IMREAD_COLOR
            )
            bg = raw_img[440:470, 20:60]
            assert np.mean(bg[:, :, 0]) < 10
            assert np.mean(bg[:, :, 2]) < 10
            # На аннотированном кадре красная точка и жёлтая рамка ArUco сохранили исходный цвет
            ann_img = cv2.imdecode(
                np.frombuffer(ann_uploaded, np.uint8), cv2.IMREAD_COLOR
            )
            assert np.any(
                (ann_img[:, :, 2] > 190)
                & (ann_img[:, :, 0] < 60)
                & (ann_img[:, :, 1] < 60)
            )

    orch.stop()


def test_aruco_dicts_4x4_and_5x5_with_yellow_box_and_purple_px_rulers():
    """Распознавание ArUco DICT_4X4_50 и DICT_5X5_50 рисует жёлтую рамку и фиолетовые линейки в px."""
    vision = VisionPipeline(mock_mode=True)

    for dict_id in (cv2.aruco.DICT_4X4_50, cv2.aruco.DICT_5X5_50):
        frame = _make_aruco_and_plant_jpeg(dict_id=dict_id, marker_id=5)
        ann_bytes, aruco_area, _ = vision.process_capture(
            frame,
            inst_name="Стенд ArUco",
            culture_name="Мята",
            camera_name="cam-aruco",
            aruco_size_mm=50.0,
        )
        assert aruco_area is not None and aruco_area > 5000.0

        ann_img = cv2.imdecode(np.frombuffer(ann_bytes, np.uint8), cv2.IMREAD_COLOR)
        assert ann_img is not None

        # Вокруг маркера [90:208, 110:228] должна быть жёлтая рамка (BGR: 0, 255, 255)
        marker_region = ann_img[75:225, 95:245]
        yellow_mask = (
            (marker_region[:, :, 2] > 200)
            & (marker_region[:, :, 1] > 200)
            & (marker_region[:, :, 0] < 60)
        )
        assert np.any(yellow_mask), f"Нет жёлтой рамки вокруг маркера {dict_id}"

        # Рядом с маркером (слева и снизу) должны быть фиолетовые линейки (BGR: 240, 32, 160)
        ruler_region = ann_img[80:250, 40:245]
        purple_mask = (
            (np.abs(ruler_region[:, :, 0].astype(int) - 240) < 40)
            & (np.abs(ruler_region[:, :, 1].astype(int) - 32) < 40)
            & (np.abs(ruler_region[:, :, 2].astype(int) - 160) < 40)
        )
        assert np.any(purple_mask), f"Нет фиолетовых линеек вокруг маркера {dict_id}"


def test_live_stream_adaptive_fps_and_startup_grace():
    """LiveStreamManager: адаптивный FPS ~12.5 в annotated и защита стартового окна от ложного офлайна."""
    mgr = LiveStreamManager(mode="mock", mock_cameras=False, client=MagicMock())
    mgr.set_stream_mode("annotated")
    # Быстрая обработка 10 мс -> сон ~0.07 с (итого период 0.08 с = 12.5 FPS, а не 0.3 с)
    assert mgr._compute_loop_sleep(0.01) == pytest.approx(0.07, abs=0.005)
    # Долгая обработка 120 мс -> короткая адаптивная пауза без перегрузки CPU
    assert mgr._compute_loop_sleep(0.12) == pytest.approx(0.03, abs=0.005)

    # Стартовое окно после start(): временный online: false от ustreamer не ставит device_online=False
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    offline_state = json.dumps(
        {"ok": True, "result": {"source": {"online": False}}}
    ).encode()

    class _Resp:
        status = 200

        def read(self):
            return offline_state

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    def fake_urlopen(req, timeout=2.5):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if url.endswith("/state"):
            return _Resp()
        raise OSError("snapshot not ready yet")

    with (
        patch("shutil.which", return_value="/usr/bin/ustreamer"),
        patch("subprocess.Popen", return_value=fake_proc),
        patch("urllib.request.urlopen", side_effect=fake_urlopen),
    ):
        mgr.start("cam-grace-1", width=640, height=480, pixel_format="MJPG")
        try:
            mgr.startup_grace_sec = 5.0
            mgr._last_state_check_at = 0.0
            assert mgr._generate_frame() is None
            assert mgr.device_online is True  # в стартовом окне ещё True

            # После доставки хотя бы одного кадра обрыв фиксируется мгновенно
            mgr._has_delivered_frame = True
            mgr._last_state_check_at = 0.0
            assert mgr._generate_frame() is None
            assert mgr.device_online is False
        finally:
            mgr.stop()
