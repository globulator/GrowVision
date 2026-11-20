from pathlib import Path

from edge_agent.offline_queue import flush_queue, get_queue_count, save_to_queue
from shared.schemas import CaptureMeta, PlantMeasurementMeta


def test_save_and_count_queue(tmp_queue_dir: Path):
    """Проверка сохранения кадра в очередь и подсчета количества."""
    assert get_queue_count(tmp_queue_dir) == 0

    meta = CaptureMeta(
        installation_id=1,
        camera_hardware_id="cam-1",
        captured_at="2026-11-20T12:00:00Z",
        is_manual=False,
        aruco_area_px=1500.0,
        command_id=None,
        measurements=[PlantMeasurementMeta(plant_index=0, area_px=5000.0)],
    )

    pkg_dir = save_to_queue(tmp_queue_dir, meta, b"raw_data", b"ann_data")
    assert pkg_dir.exists()
    assert (pkg_dir / "metadata.json").exists()
    assert (pkg_dir / "raw.jpg").exists()
    assert (pkg_dir / "ann.jpg").exists()

    assert get_queue_count(tmp_queue_dir) == 1


def test_flush_queue_success_200_201(tmp_queue_dir: Path):
    """Проверка успешной выгрузки (200/201) с удалением пакета из очереди."""
    meta = CaptureMeta(
        installation_id=1,
        camera_hardware_id="cam-1",
        captured_at="2026-11-20T12:00:00Z",
        is_manual=False,
    )
    save_to_queue(tmp_queue_dir, meta, b"raw", b"ann")
    assert get_queue_count(tmp_queue_dir) == 1

    def upload_mock(m, _r, _a):
        assert m.command_id is None
        return 201, {"capture_id": 42}

    sent, skipped, failed = flush_queue(tmp_queue_dir, upload_mock)
    assert sent == 1
    assert skipped == 0
    assert failed == 0
    assert get_queue_count(tmp_queue_dir) == 0


def test_flush_queue_conflict_409(tmp_queue_dir: Path):
    """Проверка ответа 409 (камера не зарегистрирована): пакет остается в очереди."""
    meta = CaptureMeta(
        installation_id=1,
        camera_hardware_id="unregistered_cam",
        captured_at="2026-11-20T12:00:00Z",
        is_manual=False,
    )
    pkg = save_to_queue(tmp_queue_dir, meta, b"raw", b"ann")

    def upload_mock(_m, _r, _a):
        return 409, {"detail": "Камера не зарегистрирована"}

    sent, skipped, failed = flush_queue(tmp_queue_dir, upload_mock)
    assert sent == 0
    assert skipped == 1
    assert failed == 0
    assert get_queue_count(tmp_queue_dir) == 1
    assert pkg.exists()


def test_flush_queue_invalid_422_404_moved_to_failed(tmp_queue_dir: Path):
    """Проверка ответа 422/404: пакет перемещается в failed/."""
    meta = CaptureMeta(
        installation_id=999,
        camera_hardware_id="cam-1",
        captured_at="2026-11-20T12:00:00Z",
        is_manual=False,
    )
    pkg = save_to_queue(tmp_queue_dir, meta, b"raw", b"ann")

    def upload_mock(_m, _r, _a):
        return 422, {"detail": "Validation error"}

    sent, skipped, failed = flush_queue(tmp_queue_dir, upload_mock)
    assert sent == 0
    assert skipped == 0
    assert failed == 1
    assert get_queue_count(tmp_queue_dir) == 0
    # Проверяем наличие в failed/
    failed_dir = tmp_queue_dir / "failed" / pkg.name
    assert failed_dir.exists()


def test_flush_queue_corrupted_packet_moved_to_failed(tmp_queue_dir: Path):
    """Проверка поврежденного пакета: перемещение в failed/."""
    broken_pkg = tmp_queue_dir / "broken_pkg_001"
    broken_pkg.mkdir()
    # Нет raw.jpg и ann.jpg, битый json
    (broken_pkg / "metadata.json").write_text("{broken", encoding="utf-8")

    _sent, _skipped, failed = flush_queue(tmp_queue_dir, lambda _m, _r, _a: (200, {}))
    assert failed == 1
    assert (tmp_queue_dir / "failed" / "broken_pkg_001").exists()
