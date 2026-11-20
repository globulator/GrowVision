import json
import shutil
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from shared.logger import get_logger
from shared.schemas import CaptureMeta

logger = get_logger("edge_agent.offline_queue")


def get_queue_count(queue_dir: Path) -> int:
    """Возвращает количество ожидающих пакетов в офлайн-очереди (исключая failed/)."""
    if not queue_dir.exists():
        return 0

    count = 0
    for item in queue_dir.iterdir():
        if item.is_dir() and item.name != "failed" and not item.name.startswith("."):
            count += 1
    return count


def save_to_queue(
    queue_dir: Path,
    metadata: CaptureMeta,
    raw_bytes: bytes,
    annotated_bytes: bytes,
) -> Path:
    """Сохраняет пакет кадра в локальную офлайн-очередь."""
    queue_dir.mkdir(parents=True, exist_ok=True)

    # Имя директории: {timestamp}_{installation_id}_{uuid}
    ts = int(time.time())
    pkg_id = f"{ts}_{metadata.installation_id}_{uuid.uuid4().hex[:8]}"
    pkg_dir = queue_dir / pkg_id
    pkg_dir.mkdir(parents=True, exist_ok=True)

    meta_file = pkg_dir / "metadata.json"
    raw_file = pkg_dir / "raw.jpg"
    ann_file = pkg_dir / "ann.jpg"

    meta_file.write_text(metadata.model_dump_json(indent=2), encoding="utf-8")
    raw_file.write_bytes(raw_bytes)
    ann_file.write_bytes(annotated_bytes)

    logger.info(
        f"Кадр для установки {metadata.installation_id} сохранен в офлайн-очередь: {pkg_dir.name}"
    )
    return pkg_dir


def flush_queue(
    queue_dir: Path,
    upload_fn: Callable[[CaptureMeta, bytes, bytes], tuple[int, dict[str, Any]]],
) -> tuple[int, int, int]:
    """Выгружает пакеты из офлайн-очереди на сервер.

    Возвращает (sent_count, skipped_count, failed_count).
    - 200/201: пакет удаляется
    - 409: пакет остается в очереди (камера не зарегистрирована)
    - 422/404: пакет перемещается в failed/
    - Сетевой сбой: выгрузка прерывается до следующего цикла
    """
    if not queue_dir.exists():
        return 0, 0, 0

    failed_dir = queue_dir / "failed"
    failed_dir.mkdir(parents=True, exist_ok=True)

    items = [
        item
        for item in queue_dir.iterdir()
        if item.is_dir() and item.name != "failed" and not item.name.startswith(".")
    ]
    items.sort(key=lambda p: p.name)

    sent = 0
    skipped = 0
    failed = 0

    for item in items:
        meta_file = item / "metadata.json"
        raw_file = item / "raw.jpg"
        ann_file = item / "ann.jpg"

        if not (meta_file.exists() and raw_file.exists() and ann_file.exists()):
            logger.warning(
                f"Поврежденный пакет в очереди: {item.name}. Перемещение в failed/"
            )
            target = failed_dir / item.name
            if target.exists():
                shutil.rmtree(target)
            shutil.move(str(item), str(target))
            failed += 1
            continue

        try:
            meta_dict = json.loads(meta_file.read_text(encoding="utf-8"))
            # Для кадров из offline_queue command_id должен быть null
            meta_dict["command_id"] = None
            meta = CaptureMeta.model_validate(meta_dict)
            raw_bytes = raw_file.read_bytes()
            ann_bytes = ann_file.read_bytes()
        except Exception as e:  # noqa: BLE001
            logger.error(f"Ошибка чтения пакета {item.name}: {e}. В failed/")
            target = failed_dir / item.name
            if target.exists():
                shutil.rmtree(target)
            shutil.move(str(item), str(target))
            failed += 1
            continue

        try:
            status_code, _ = upload_fn(meta, raw_bytes, ann_bytes)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Ошибка сети при отправке пакета {item.name}: {e}")
            # Сетевой сбой — прерываем выгрузку до следующего цикла
            break

        if status_code in (200, 201):
            logger.info(
                f"Пакет {item.name} успешно доставлен (код {status_code}). Удаление."
            )
            shutil.rmtree(item)
            sent += 1
        elif status_code == 409:
            logger.warning(
                f"Сервер вернул 409 для пакета {item.name} (камера не зарегистрирована). Оставляем в очереди."
            )
            skipped += 1
        elif status_code in (404, 422):
            logger.error(
                f"Сервер отклонил пакет {item.name} с кодом {status_code}. Перемещение в failed/"
            )
            target = failed_dir / item.name
            if target.exists():
                shutil.rmtree(target)
            shutil.move(str(item), str(target))
            failed += 1
        else:
            logger.warning(
                f"Неожиданный статус код {status_code} при отправке пакета {item.name}. Прерываем."
            )
            break

    return sent, skipped, failed
