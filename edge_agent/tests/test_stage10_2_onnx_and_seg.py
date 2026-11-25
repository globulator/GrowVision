import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from edge_agent.config import DEFAULT_MODEL_PATH, REPO_ROOT, load_config
from edge_agent.live_stream import LiveStreamManager
from edge_agent.orchestrator import EdgeOrchestrator
from edge_agent.vision import (
    VisionPipeline,
    _compute_auto_rgb_gains,
    _postprocess_onnx_mask,
    _resolve_onnx_input_size,
)
from shared.schemas import (
    CommandInstallation,
    CommandItem,
    HeartbeatActiveInstallation,
    HeartbeatResponse,
)


def _encode_jpg(img: np.ndarray) -> bytes:
    ok, enc = cv2.imencode(".jpg", img)
    assert ok
    return enc.tobytes()


def test_onnx_model_path_config_and_backward_compatibility(tmp_path: Path, monkeypatch):
    """ONNX_MODEL_PATH имеет приоритет над MODEL_PATH, а по умолчанию используется plant_seg.onnx."""
    env_file = tmp_path / ".env"
    env_file.write_text("EDGE_API_KEY=test-key\n", encoding="utf-8")

    monkeypatch.delenv("ONNX_MODEL_PATH", raising=False)
    monkeypatch.delenv("MODEL_PATH", raising=False)
    monkeypatch.delenv("MODELS_DIR", raising=False)
    monkeypatch.delenv("MODEL_NAME", raising=False)

    cfg_def = load_config(env_file)
    assert cfg_def.model_path == DEFAULT_MODEL_PATH
    assert (
        cfg_def.onnx_model_path
        == REPO_ROOT / "edge_agent" / "models" / "plant_seg.onnx"
    )

    monkeypatch.setenv("MODEL_PATH", "edge_agent/models/legacy.onnx")
    cfg_legacy = load_config(env_file)
    assert (
        cfg_legacy.onnx_model_path
        == REPO_ROOT / "edge_agent" / "models" / "legacy.onnx"
    )

    monkeypatch.setenv("ONNX_MODEL_PATH", "edge_agent/models/custom_plant.onnx")
    cfg_custom = load_config(env_file)
    assert (
        cfg_custom.onnx_model_path
        == REPO_ROOT / "edge_agent" / "models" / "custom_plant.onnx"
    )


def test_onnx_input_size_resolution_and_postprocess():
    """Автоопределение (H, W) из shape входа ONNX, фолбэк 256x256 и постобработка логитов/2-каналов."""
    assert _resolve_onnx_input_size([1, 3, 320, 480]) == (320, 480, "NCHW")
    assert _resolve_onnx_input_size([1, 192, 256, 3]) == (192, 256, "NHWC")
    assert _resolve_onnx_input_size(["batch", 3, None, None]) == (256, 256, "NCHW")
    assert _resolve_onnx_input_size(None) == (256, 256, "NCHW")

    # Одноканальные логиты (вне [0, 1]) -> сигмоида + порог 0.5
    logits_1ch = np.full((1, 1, 64, 64), -5.0, dtype=np.float32)
    logits_1ch[0, 0, 16:48, 16:48] = 5.0
    mask_1ch = _postprocess_onnx_mask(logits_1ch, orig_w=128, orig_h=128)
    assert mask_1ch.shape == (128, 128)
    assert mask_1ch[64, 64] == 255
    assert mask_1ch[4, 4] == 0

    # Двухканальные логиты (фон vs растение) -> softmax + порог 0.5
    logits_2ch = np.zeros((1, 2, 64, 64), dtype=np.float32)
    logits_2ch[0, 0, :, :] = 4.0
    logits_2ch[0, 1, :, :] = -4.0
    logits_2ch[0, 0, 20:44, 20:44] = -4.0
    logits_2ch[0, 1, 20:44, 20:44] = 4.0
    mask_2ch = _postprocess_onnx_mask(logits_2ch, orig_w=128, orig_h=128)
    assert mask_2ch[64, 64] == 255
    assert mask_2ch[4, 4] == 0


def test_onnx_lazy_loading_caching_and_measurements(tmp_path: Path):
    """ONNX-сессия лениво загружается, кэшируется, правильно препроцессит и считает площадь."""
    model_file = tmp_path / "plant_seg.onnx"
    model_file.write_bytes(b"fake-onnx-binary")

    fake_input = MagicMock()
    fake_input.name = "images"
    fake_input.shape = [1, 3, 128, 128]

    # Маска на 128x128: белый квадрат 32x32 в центре (~1/16 площади кадра)
    pred = np.full((1, 1, 128, 128), -6.0, dtype=np.float32)
    pred[0, 0, 48:80, 48:80] = 6.0

    fake_session = MagicMock()
    fake_session.get_inputs.return_value = [fake_input]
    fake_session.run.return_value = [pred]

    # Красный кадр (в HSV он не зелёный -> HSV нашёл бы 0 кустов, но ONNX найдёт 1 куст)
    red_scene = np.full((480, 640, 3), (20, 20, 200), dtype=np.uint8)
    raw_bytes = _encode_jpg(red_scene)

    with (
        patch("edge_agent.vision.ORT_AVAILABLE", True),
        patch("edge_agent.vision.ort") as mock_ort,
    ):
        mock_ort.InferenceSession.return_value = fake_session
        vp = VisionPipeline(model_path=model_file, mock_mode=False)

        assert vp._session is None
        ann_1, meas_1 = vp.segment_plants(raw_bytes, segmentation_mode="auto")
        ann_2, meas_2 = vp.segment_plants(raw_bytes, segmentation_mode="onnx")

        # InferenceSession создан ровно 1 раз (лениво + кэширование)
        mock_ort.InferenceSession.assert_called_once_with(
            str(model_file),
            providers=["CPUExecutionProvider"],
        )
        assert vp.get_model_name() == "plant_seg.onnx"
        assert len(meas_1) == 1
        assert len(meas_2) == 1
        assert meas_1[0].area_px > 10000.0
        assert ann_1 != raw_bytes and ann_2 != raw_bytes

        # Проверка формата входного тензора: float32 NCHW [0..1]
        run_feed = fake_session.run.call_args[0][1]
        tensor = run_feed["images"]
        assert tensor.shape == (1, 3, 128, 128)
        assert tensor.dtype == np.float32
        assert 0.0 <= float(tensor.min()) and float(tensor.max()) <= 1.0

        # В режиме 'hsv' тот же красный кадр НЕ использует ONNX и даёт 0 кустов
        _, meas_hsv = vp.segment_plants(raw_bytes, segmentation_mode="hsv")
        assert meas_hsv == []
        assert fake_session.run.call_count == 2


def test_mock_mode_auto_uses_onnx_when_model_exists_and_hsv_when_missing(
    tmp_path: Path,
):
    """При mock_mode=True и существующем .onnx mode='auto' использует ONNX, а без файла уходит в HSV."""
    model_file = tmp_path / "plant_seg.onnx"
    model_file.write_bytes(b"fake-onnx-binary")

    fake_input = MagicMock()
    fake_input.name = "images"
    fake_input.shape = [1, 3, 64, 64]
    pred = np.full((1, 1, 64, 64), -6.0, dtype=np.float32)
    pred[0, 0, 20:44, 20:44] = 6.0

    fake_session = MagicMock()
    fake_session.get_inputs.return_value = [fake_input]
    fake_session.run.return_value = [pred]

    # Красный кадр не даёт контуров в HSV, но даёт 1 контур через ONNX-маску
    red_scene = np.full((240, 320, 3), (20, 20, 200), dtype=np.uint8)
    red_bytes = _encode_jpg(red_scene)

    with (
        patch("edge_agent.vision.ORT_AVAILABLE", True),
        patch("edge_agent.vision.ort") as mock_ort,
    ):
        mock_ort.InferenceSession.return_value = fake_session
        vp_with_model = VisionPipeline(model_path=model_file, mock_mode=True)
        _, meas_onnx = vp_with_model.segment_plants(red_bytes, segmentation_mode="auto")
        assert len(meas_onnx) == 1
        mock_ort.InferenceSession.assert_called_once_with(
            str(model_file),
            providers=["CPUExecutionProvider"],
        )

    # При отсутствии файла в mock_mode=True mode='auto' безопасно уходит в HSV без падения
    green_scene = np.full((240, 320, 3), 25, dtype=np.uint8)
    cv2.circle(green_scene, (160, 120), 40, (25, 190, 35), -1)
    vp_missing = VisionPipeline(model_path=tmp_path / "absent.onnx", mock_mode=True)
    _, meas_red = vp_missing.segment_plants(red_bytes, segmentation_mode="auto")
    assert meas_red == []
    _, meas_green = vp_missing.segment_plants(
        _encode_jpg(green_scene), segmentation_mode="auto"
    )
    assert len(meas_green) == 1


def test_onnx_missing_or_corrupted_falls_back_to_hsv_with_warning(
    tmp_path: Path, caplog
):
    """При отсутствии или повреждении .onnx даже в mode='onnx' происходит безопасный фолбэк в HSV с warning."""
    scene = np.full((480, 640, 3), 30, dtype=np.uint8)
    cv2.circle(scene, (320, 240), 50, (25, 190, 35), -1)
    raw_bytes = _encode_jpg(scene)

    # 1. Отсутствующий файл
    missing_vp = VisionPipeline(model_path=tmp_path / "missing.onnx", mock_mode=True)
    with caplog.at_level(logging.WARNING):
        _, meas_missing = missing_vp.segment_plants(raw_bytes, segmentation_mode="onnx")
    assert len(meas_missing) == 1
    assert any(
        "не найден" in rec.message or "HSV" in rec.message for rec in caplog.records
    )

    # 2. Повреждённый .onnx (InferenceSession кидает RuntimeError)
    corrupt_file = tmp_path / "corrupt.onnx"
    corrupt_file.write_bytes(b"not-a-valid-onnx")
    caplog.clear()
    with (
        patch("edge_agent.vision.ORT_AVAILABLE", True),
        patch("edge_agent.vision.ort") as mock_ort,
        caplog.at_level(logging.WARNING),
    ):
        mock_ort.InferenceSession.side_effect = RuntimeError("Invalid protobuf")
        corrupt_vp = VisionPipeline(model_path=corrupt_file, mock_mode=False)
        _, meas_corrupt = corrupt_vp.segment_plants(raw_bytes, segmentation_mode="onnx")
        assert len(meas_corrupt) == 1
        assert corrupt_vp.get_model_name() == "none"
        assert any("HSV" in rec.message for rec in caplog.records)


def test_hsv_morphological_close_merges_shaded_plant_petiole():
    """Морфологическое закрытие в HSV склеивает куст с тёмным черешком/тенью внутри в 1 контур."""
    scene = np.full((480, 640, 3), 25, dtype=np.uint8)
    # Рисуем куст из двух крупных долей с узкой тёмной тенью (3 px) между ними
    cv2.circle(scene, (300, 240), 42, (25, 190, 35), -1)
    cv2.circle(scene, (370, 240), 42, (25, 190, 35), -1)
    cv2.line(scene, (335, 190), (335, 290), (20, 25, 20), 3)

    vp = VisionPipeline(mock_mode=True)
    _, measurements = vp.segment_plants(_encode_jpg(scene), segmentation_mode="hsv")
    assert len(measurements) == 1
    assert measurements[0].area_px > 6000.0


def test_auto_rgb_gains_neutral_white_balance_without_red_shift():
    """Автоцветокоррекция по нейтральным плашкам не уводит белый фон в красный при цветных плашках."""
    # Белая и серая плашки нейтральны (R == G == B), несмотря на яркие R/G/B плашки
    gains = _compute_auto_rgb_gains(
        p_r=[240.0, 20.0, 20.0],
        p_g=[20.0, 240.0, 20.0],
        p_b=[20.0, 20.0, 240.0],
        p_w=[235.0, 235.0, 235.0],
        p_gray=[128.0, 128.0, 128.0],
    )
    assert gains == (1.0, 1.0, 1.0)

    # При синем/фиолетовом перекосе (R/G ниже B) красный не завышается выше нейтрального баланса
    gains_cool = _compute_auto_rgb_gains(
        p_r=[200.0, 20.0, 30.0],
        p_g=[20.0, 180.0, 30.0],
        p_b=[20.0, 20.0, 240.0],
        p_w=[180.0, 180.0, 230.0],
        p_gray=[100.0, 100.0, 130.0],
    )
    assert gains_cool[0] == 1.0 and gains_cool[1] == 1.0
    assert gains_cool[2] < 0.9


def test_orchestrator_and_live_stream_sync_segmentation_mode(tmp_path: Path):
    """EdgeOrchestrator и LiveStreamManager синхронизируют segmentation_mode из heartbeat/команд и передают в CV."""
    cfg = load_config(None)
    cfg.edge_api_key = "k"
    cfg.hardware_mode = "mock"
    cfg.mock_cameras = True
    cfg.offline_queue_dir = tmp_path / "queue"

    mock_client = MagicMock()
    mock_client.send_heartbeat.return_value = HeartbeatResponse(
        capture_interval_hours=1,
        debug_mode=False,
        active_installations=[
            HeartbeatActiveInstallation(
                id=1,
                name="Box 1",
                light_gpio_pin=4,
                flash_gpio_pin=5,
                camera_hardware_ids=["cam-seg-1"],
                camera_segmentation_modes={"cam-seg-1": "hsv"},
            )
        ],
    )
    mock_client.upload_capture.return_value = (201, {"capture_id": 1})

    orch = EdgeOrchestrator(config=cfg, client=mock_client)
    orch.live_stream.is_running = True
    orch.live_stream.active_camera_id = "cam-seg-1"
    orch.perform_heartbeat()
    assert orch.live_stream.segmentation_mode == "hsv"

    # SET_CAMERA_CONTROLS переключает segmentation_mode на 'onnx' в реальном времени
    orch.handle_command(
        CommandItem(
            id=11,
            command_type="SET_CAMERA_CONTROLS",
            payload={"camera_hardware_id": "cam-seg-1", "segmentation_mode": "onnx"},
        )
    )
    assert orch.live_stream.segmentation_mode == "onnx"

    # SNAP и плановая съёмка передают segmentation_mode в vision.process_capture
    with patch.object(
        orch.vision, "process_capture", wraps=orch.vision.process_capture
    ) as spy_pc:
        orch.handle_command(
            CommandItem(
                id=12,
                command_type="SNAP",
                installation=CommandInstallation(
                    id=1,
                    name="Box 1",
                    light_gpio_pin=4,
                    flash_gpio_pin=5,
                    camera_hardware_ids=["cam-seg-1"],
                    camera_segmentation_modes={"cam-seg-1": "onnx"},
                ),
            )
        )
        assert spy_pc.call_args.kwargs["segmentation_mode"] == "onnx"

        orch.live_stream.set_segmentation_mode("hsv")
        orch.trigger_scheduled_capture()
        assert spy_pc.call_args.kwargs["segmentation_mode"] == "hsv"

    # LiveStreamManager обновляет segmentation_mode из ответа send_live_frame
    mgr = LiveStreamManager(mode="mock", mock_cameras=True, client=MagicMock())
    mgr.set_segmentation_mode("onnx")
    assert mgr.segmentation_mode == "onnx"
    mgr.set_segmentation_mode("invalid")
    assert mgr.segmentation_mode == "auto"
