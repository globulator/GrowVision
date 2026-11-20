import json
import os
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
    """Возвращает количество ожидающих пакетов в офлайн-очереди (исключая failed/ и скрытые/служебные)."""
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
    """Атомарно сохраняет пакет кадра в локальную офлайн-очередь через временный каталог."""
    queue_dir.mkdir(parents=True, exist_ok=True)

    ts = int(time.time())
    pkg_id = f"{ts}_{metadata.installation_id}_{uuid.uuid4().hex[:8]}"

    # Атомарная запись: пишем во временный каталог .tmp_... затем os.replace
    tmp_dir = queue_dir / f".tmp_{pkg_id}"
    final_dir = queue_dir / pkg_id

    tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        meta_file = tmp_dir / "metadata.json"
        raw_file = tmp_dir / "raw.jpg"
        ann_file = tmp_dir / "ann.jpg"

        meta_file.write_text(metadata.model_dump_json(indent=2), encoding="utf-8")
        raw_file.write_bytes(raw_bytes)
        ann_file.write_bytes(annotated_bytes)

        os.replace(str(tmp_dir), str(final_dir))
    except Exception:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    logger.info(
        f"Кадр для установки {metadata.installation_id} атомарно сохранен в офлайн-очередь: {final_dir.name}"
    )
    return final_dir


def flush_queue(
    queue_dir: Path,
    upload_fn: Callable[[CaptureMeta, bytes, bytes], tuple[int, dict[str, Any]]],
    max_batch_size: int = 10,
) -> tuple[int, int, int]:
    """Выгружает пакеты из офлайн-очереди на сервер (не более max_batch_size за один проход).

    Возвращает (sent_count, skipped_count, failed_count).
    - 200/201: пакет удаляется
    - 409: пакет остается в очереди (камера не зарегистрирована)
    - 422/404: пакет перемещается в failed/ (с защитой failed/.gitkeep)
    - Сетевой сбой: выгрузка прерывается до следующего цикла
    """
    if not queue_dir.exists():
        return 0, 0, 0

    failed_dir = queue_dir / "failed"
    failed_dir.mkdir(parents=True, exist_ok=True)

    # Игнорируем файлы, скрытые каталоги (начинающиеся с '.') и папку failed
    items = [
        item
        for item in queue_dir.iterdir()
        if item.is_dir() and item.name != "failed" and not item.name.startswith(".")
    ]
    items.sort(key=lambda p: p.name)

    # Ограничение до max_batch_size (по умолчанию 10) пакетов за один проход
    items = items[:max_batch_size]

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
                shutil.rmtree(target, ignore_errors=True)
            shutil.move(str(item), str(target))
            failed += 1
            continue

        try:
            meta_dict = json.loads(meta_file.read_text(encoding="utf-8"))
            meta_dict["command_id"] = None
            meta = CaptureMeta.model_validate(meta_dict)
            raw_bytes = raw_file.read_bytes()
            ann_bytes = ann_file.read_bytes()
        except Exception as e:  # noqa: BLE001
            logger.error(f"Ошибка чтения пакета {item.name}: {e}. В failed/")
            target = failed_dir / item.name
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            shutil.move(str(item), str(target))
            failed += 1
            continue

        try:
            status_code, _ = upload_fn(meta, raw_bytes, ann_bytes)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Ошибка сети при отправке пакета {item.name}: {e}")
            break

        if status_code in (200, 201):
            logger.info(
                f"Пакет {item.name} успешно доставлен (код {status_code}). Удаление."
            )
            shutil.rmtree(item, ignore_errors=True)
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
                shutil.rmtree(target, ignore_errors=True)
            shutil.move(str(item), str(target))
            failed += 1
        else:
            logger.warning(
                f"Неожиданный статус код {status_code} при отправке пакета {item.name}. Прерываем."
            )
            break

    return sent, skipped, failed
