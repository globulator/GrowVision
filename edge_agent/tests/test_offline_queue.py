from pathlib import Path

from edge_agent.offline_queue import flush_queue, get_queue_count, save_to_queue
from shared.schemas import CaptureMeta, PlantMeasurementMeta


def test_save_and_count_queue_atomic(tmp_queue_dir: Path):
    """Проверка атомарного сохранения кадра в очередь и подсчета количества."""
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

    # Временных папок оставаться не должно
    tmp_folders = list(tmp_queue_dir.glob(".tmp_*"))
    assert len(tmp_folders) == 0

    assert get_queue_count(tmp_queue_dir) == 1


def test_flush_queue_batch_limit_10(tmp_queue_dir: Path):
    """Проверка ограничения выгрузки: не более 10 пакетов за один проход."""
    # Создаем 15 пакетов
    for i in range(15):
        meta = CaptureMeta(
            installation_id=i,
            camera_hardware_id="cam-1",
            captured_at=f"2026-11-20T12:{i:02d}:00Z",
            is_manual=False,
        )
        save_to_queue(tmp_queue_dir, meta, b"raw", b"ann")

    assert get_queue_count(tmp_queue_dir) == 15

    upload_calls = []

    def upload_mock(m, _r, _a):
        upload_calls.append(m.installation_id)
        return 201, {"capture_id": m.installation_id}

    sent, _skipped, _failed = flush_queue(tmp_queue_dir, upload_mock, max_batch_size=10)
    assert sent == 10
    assert len(upload_calls) == 10
    assert get_queue_count(tmp_queue_dir) == 5  # Осталось 5 пакетов


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


def test_flush_queue_preserves_failed_gitkeep(tmp_queue_dir: Path):
    """Проверка перемещения в failed/ с сохранением файла .gitkeep."""
    failed_dir = tmp_queue_dir / "failed"
    failed_dir.mkdir(parents=True, exist_ok=True)
    gitkeep = failed_dir / ".gitkeep"
    gitkeep.touch()

    meta = CaptureMeta(
        installation_id=999,
        camera_hardware_id="cam-1",
        captured_at="2026-11-20T12:00:00Z",
        is_manual=False,
    )
    pkg = save_to_queue(tmp_queue_dir, meta, b"raw", b"ann")

    def upload_mock(_m, _r, _a):
        return 422, {"detail": "Validation error"}

    _sent, _skipped, failed = flush_queue(tmp_queue_dir, upload_mock)
    assert failed == 1
    assert gitkeep.exists()
    assert (failed_dir / pkg.name).exists()
