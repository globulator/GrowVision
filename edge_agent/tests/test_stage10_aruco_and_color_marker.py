import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import MagicMock

import cv2
import numpy as np

from edge_agent.config import EdgeConfig
from edge_agent.live_stream import LiveStreamManager
from edge_agent.orchestrator import EdgeOrchestrator
from edge_agent.vision import (
    VisionPipeline,
    _build_aruco_detector_params,
    draw_cv_annotations,
)
from shared.markers import generate_aruco_marker_svg, generate_color_marker_svg
from shared.schemas import CommandItem


def _render_svg_rects_to_bgr(svg_text: str, px_per_mm: float = 6.0) -> np.ndarray:
    """Растеризует прямоугольники <rect> из SVG в мм в BGR-изображение для проверки CV-детекторами."""
    root = ET.fromstring(svg_text)
    w_mm = float(root.attrib["width"].replace("mm", ""))
    h_mm = float(root.attrib["height"].replace("mm", ""))
    img_w = max(10, round(w_mm * px_per_mm))
    img_h = max(10, round(h_mm * px_per_mm))
    img = np.full((img_h, img_w, 3), 255, dtype=np.uint8)

    for elem in root.iter():
        if not elem.tag.endswith("rect"):
            continue
        fill = elem.attrib.get("fill", "").strip()
        if not fill.startswith("#") or len(fill) != 7:
            continue
        r = int(fill[1:3], 16)
        g = int(fill[3:5], 16)
        b = int(fill[5:7], 16)
        x = round(float(elem.attrib["x"]) * px_per_mm)
        y = round(float(elem.attrib["y"]) * px_per_mm)
        rw = max(1, round(float(elem.attrib["width"]) * px_per_mm))
        rh = max(1, round(float(elem.attrib["height"]) * px_per_mm))
        cv2.rectangle(img, (x, y), (x + rw - 1, y + rh - 1), (b, g, r), -1)

    return img


def _encode_png(img: np.ndarray) -> bytes:
    ok, enc = cv2.imencode(".png", img)
    assert ok
    return enc.tobytes()


def test_aruco_subpix_and_perspective_metric_calibration():
    """Проверяет CORNER_REFINE_SUBPIX и точный расчёт width_mm, height_mm, area_cm2 при перспективном наклоне."""
    params = _build_aruco_detector_params()
    assert params is not None
    assert params.cornerRefinementMethod == cv2.aruco.CORNER_REFINE_SUBPIX

    # Создаём плоскую сцену в масштабе 4 px = 1 мм (размер 300x200 мм -> 1200x800 px)
    plane = np.full((800, 1200, 3), 50, dtype=np.uint8)

    # ArUco 50x50 мм -> 200x200 px
    aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    marker_core = cv2.aruco.generateImageMarker(aruco_dict, 0, 200)
    pad = 24
    marker_patch = np.full((200 + 2 * pad, 200 + 2 * pad, 3), 255, dtype=np.uint8)
    marker_patch[pad : pad + 200, pad : pad + 200] = cv2.cvtColor(
        marker_core, cv2.COLOR_GRAY2BGR
    )
    plane[100 : 100 + marker_patch.shape[0], 100 : 100 + marker_patch.shape[1]] = (
        marker_patch
    )

    # Растение 100x80 мм (площадь = 80 см²) -> 400x320 px
    cv2.rectangle(plane, (560, 200), (960, 520), (30, 195, 40), -1)

    # Применяем заметное перспективное искажение и наклон камеры
    src_pts = np.array([[0, 0], [1200, 0], [1200, 800], [0, 800]], dtype=np.float32)
    dst_pts = np.array(
        [[140, 80], [1060, 140], [1160, 760], [60, 720]], dtype=np.float32
    )
    H = cv2.getPerspectiveTransform(src_pts, dst_pts)
    warped = cv2.warpPerspective(plane, H, (1200, 800), borderValue=(50, 50, 50))

    vp = VisionPipeline(mock_mode=True)
    _, aruco_area, measurements = vp.process_capture(
        _encode_png(warped), aruco_size_mm=50.0
    )
    assert aruco_area is not None and aruco_area > 500.0
    assert len(measurements) == 1

    m = measurements[0]
    assert m.width_px is not None and m.height_px is not None
    assert m.width_mm is not None and m.height_mm is not None and m.area_cm2 is not None
    # Истинные физические размеры: 100 мм x 80 мм, площадь 80 см²
    assert abs(m.width_mm - 100.0) <= 6.0
    assert abs(m.height_mm - 80.0) <= 6.0
    assert abs(m.area_cm2 - 80.0) <= 6.0

    # Проверка без ArUco: width_mm, height_mm, area_cm2 должны быть None, px сохраняются
    no_aruco_img = np.full((480, 640, 3), 45, dtype=np.uint8)
    cv2.rectangle(no_aruco_img, (200, 150), (360, 270), (20, 190, 30), -1)
    _, aruco_none, meas_none = vp.process_capture(_encode_png(no_aruco_img))
    assert aruco_none is None
    assert len(meas_none) == 1
    assert meas_none[0].area_px > 0
    assert meas_none[0].width_px is not None and meas_none[0].height_px is not None
    assert meas_none[0].width_mm is None
    assert meas_none[0].height_mm is None
    assert meas_none[0].area_cm2 is None


def test_draw_cv_annotations_plant_counter_and_units():
    """Проверяет вывод счётчика 'Обнаружено кустов: N' и единиц мм/см² (с ArUco) vs px (без ArUco)."""
    img = np.full((480, 640, 3), 40, dtype=np.uint8)
    cnt1 = np.array([[[100, 100]], [[200, 100]], [[200, 200]], [[100, 200]]])
    cnt2 = np.array([[[300, 120]], [[380, 120]], [[380, 220]], [[300, 220]]])
    contours = [(cnt1, 10000.0), (cnt2, 8000.0)]

    drawn_texts: list[str] = []

    class _FakeDraw:
        def textbbox(self, xy, text, font=None, stroke_width=0):
            return (0, 0, len(text) * 8, 14)

        def text(self, xy, text, **kwargs):
            drawn_texts.append(text)

    from unittest.mock import patch

    with patch("edge_agent.vision.ImageDraw.Draw", return_value=_FakeDraw()):
        aruco_corners = np.array(
            [[450, 50], [550, 50], [550, 150], [450, 150]], dtype=np.float32
        )
        draw_cv_annotations(
            img,
            contours,
            aruco_area_px=10000.0,
            aruco_size_mm=50.0,
            aruco_corners=aruco_corners,
        )

    assert "Обнаружено кустов: 2" in drawn_texts
    assert any(t.endswith(" мм") for t in drawn_texts)
    assert any("см²" in t for t in drawn_texts)

    # Без ArUco -> только px и счётчик кустов
    drawn_texts.clear()
    with patch("edge_agent.vision.ImageDraw.Draw", return_value=_FakeDraw()):
        draw_cv_annotations(img, contours)

    assert "Обнаружено кустов: 2" in drawn_texts
    assert any(t.endswith(" px") for t in drawn_texts)
    assert not any("см²" in t for t in drawn_texts)
    assert not any(t.endswith(" мм") for t in drawn_texts)


def test_svg_markers_render_and_detect_by_vision():
    """SVG ArUco и цветового маркера валидны и распознаются VisionPipeline при рендере."""
    aruco_svg = generate_aruco_marker_svg(size_mm=50.0)
    color_svg = generate_color_marker_svg(size_mm=50.0)
    assert 'width="70.00mm"' in aruco_svg
    assert 'id="scale-ruler"' in aruco_svg
    assert "#FF0000" in color_svg and "#00FF00" in color_svg and "#0000FF" in color_svg
    assert "#FFFFFF" in color_svg and "#808080" in color_svg

    vp = VisionPipeline(mock_mode=True)

    aruco_img = _render_svg_rects_to_bgr(aruco_svg, px_per_mm=6.0)
    aruco_area, aruco_corners = vp.detect_aruco_details(_encode_png(aruco_img))
    assert aruco_area is not None and aruco_corners is not None

    color_img = _render_svg_rects_to_bgr(color_svg, px_per_mm=5.0)
    cm_corners, cm_gains = vp.detect_color_marker_details(_encode_png(color_img))
    assert cm_corners is not None
    assert cm_gains == (1.0, 1.0, 1.0)


def test_color_marker_detection_blue_box_auto_gains_and_no_false_positives():
    """Проверка детекции цветового маркера (синяя рамка BGR 255,136,0), переворота 180°, отсутствия ложных срабатываний и расчёта RGB."""
    color_svg = generate_color_marker_svg(size_mm=50.0)
    marker_patch = _render_svg_rects_to_bgr(color_svg, px_per_mm=4.0)

    scene = np.full((600, 900, 3), 50, dtype=np.uint8)
    mh, mw = marker_patch.shape[:2]
    scene[40 : 40 + mh, 40 : 40 + mw] = marker_patch
    # Добавляем зелёное растение
    cv2.circle(scene, (680, 380), 80, (25, 190, 35), -1)

    # Имитируем цветовой перекос освещения: R*1.0, G*0.5, B*0.8
    tinted = scene.copy().astype(np.float32)
    tinted[:, :, 0] *= 0.8  # B
    tinted[:, :, 1] *= 0.5  # G
    tinted[:, :, 2] *= 1.0  # R
    tinted_u8 = np.clip(tinted, 0, 255).astype(np.uint8)

    vp = VisionPipeline(mock_mode=True)
    corners, gains = vp.detect_color_marker_details(_encode_png(tinted_u8))
    assert corners is not None
    assert gains is not None
    r_g, g_g, b_g = gains
    assert 0.0 <= r_g <= 1.0 and 0.0 <= g_g <= 1.0 and 0.0 <= b_g <= 1.0
    # Зелёный канал был занижен в 2 раза относительно R -> g должен быть 1.0, r ~ 0.5, b ~ 0.63
    assert g_g >= 0.95
    assert abs(r_g - 0.50) <= 0.12
    assert abs(b_g - 0.63) <= 0.12

    # Разметка рисует синюю рамку #0088FF (BGR: 255, 136, 0) и считает только 1 куст (зелёная плашка маркера исключена)
    ann_bytes, _, measurements = vp.process_capture(_encode_png(tinted_u8))
    assert len(measurements) == 1
    ann_img = cv2.imdecode(np.frombuffer(ann_bytes, np.uint8), cv2.IMREAD_COLOR)
    assert ann_img is not None
    roi = ann_img[30 : 50 + mh, 30 : 50 + mw]
    blue_frame_mask = (
        (np.abs(roi[:, :, 0].astype(int) - 255) <= 25)
        & (np.abs(roi[:, :, 1].astype(int) - 136) <= 25)
        & (roi[:, :, 2] <= 25)
    )
    assert np.any(blue_frame_mask)

    # Перевёрнутый на 180° маркер тоже детектируется
    rotated_180 = cv2.rotate(marker_patch, cv2.ROTATE_180)
    rot_scene = np.full((500, 800, 3), 240, dtype=np.uint8)
    rot_scene[80 : 80 + mh, 100 : 100 + mw] = rotated_180
    c_rot, g_rot = vp.detect_color_marker_details(_encode_png(rot_scene))
    assert c_rot is not None and g_rot is not None

    # Сцена только с листьями и фоном -> нет ложного срабатывания
    leaves_only = np.full((480, 640, 3), 60, dtype=np.uint8)
    cv2.circle(leaves_only, (200, 220), 70, (25, 180, 35), -1)
    cv2.rectangle(leaves_only, (340, 120), (520, 320), (20, 160, 40), -1)
    c_none, g_none = vp.detect_color_marker_details(_encode_png(leaves_only))
    assert c_none is None and g_none is None


def test_live_stream_color_marker_flag_and_auto_color_command(tmp_path: Path):
    """LiveStreamManager передаёт color_marker_detected, а AUTO_COLOR_CALIBRATE берёт исходный кадр без RGB."""
    color_svg = generate_color_marker_svg(size_mm=50.0)
    marker_patch = _render_svg_rects_to_bgr(color_svg, px_per_mm=4.0)
    scene = np.full((500, 800, 3), 50, dtype=np.uint8)
    mh, mw = marker_patch.shape[:2]
    scene[60 : 60 + mh, 80 : 80 + mw] = marker_patch
    tinted = scene.astype(np.float32)
    tinted[:, :, 0] *= 0.75
    tinted[:, :, 1] *= 0.60
    tinted[:, :, 2] *= 1.00
    raw_marker_jpeg = _encode_png(np.clip(tinted, 0, 255).astype(np.uint8))

    # 1. LiveStreamManager детектирует цветовой маркер на сыром кадре
    mgr = LiveStreamManager(mode="mock", mock_cameras=False, client=MagicMock())
    mgr._update_color_marker_status(raw_marker_jpeg, force=True)
    assert mgr.color_marker_detected is True

    # 2. EdgeOrchestrator AUTO_COLOR_CALIBRATE игнорирует старые rgb_gains (например (0.1, 0.1, 0.1)) и калибрует по сырому кадру
    cfg = EdgeConfig(
        edge_api_key="k",
        hardware_mode="mock",
        mock_cameras=True,
        offline_queue_dir=tmp_path / "queue",
    )
    mock_client = MagicMock()
    orch = EdgeOrchestrator(config=cfg, client=mock_client)
    orch.live_stream.is_running = True
    orch.live_stream.active_camera_id = "cam-color-1"
    orch.live_stream.set_rgb_gains(0.1, 0.1, 0.1)
    orch.live_stream.get_raw_frame_for_calibration = lambda hw_id: raw_marker_jpeg  # type: ignore[method-assign]

    orch.handle_command(
        CommandItem(
            id=77,
            command_type="AUTO_COLOR_CALIBRATE",
            payload={"camera_hardware_id": "cam-color-1"},
        )
    )
    res_call = mock_client.send_command_result.call_args[0][1]
    assert res_call.status == "done"
    assert res_call.data and "rgb" in res_call.data
    rgb_out = res_call.data["rgb"]
    assert rgb_out["g"] >= 0.95
    assert abs(rgb_out["r"] - 0.60) <= 0.12
    assert abs(rgb_out["b"] - 0.80) <= 0.12
    assert orch.live_stream.rgb_gains == (rgb_out["r"], rgb_out["g"], rgb_out["b"])

    # 3. Ошибка, если маркер не найден в кадре
    blank_jpeg = _encode_png(np.full((240, 320, 3), 50, dtype=np.uint8))
    orch.live_stream.get_raw_frame_for_calibration = lambda hw_id: blank_jpeg  # type: ignore[method-assign]
    mock_client.reset_mock()
    orch.handle_command(
        CommandItem(
            id=78,
            command_type="AUTO_COLOR_CALIBRATE",
            payload={"camera_hardware_id": "cam-color-1"},
        )
    )
    err_call = mock_client.send_command_result.call_args[0][1]
    assert err_call.status == "error"
    assert "не обнаружен" in err_call.result_message.lower()
    orch.stop()
