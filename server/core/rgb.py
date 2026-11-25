import io
from pathlib import Path
from typing import Any

from PIL import Image
from sqlalchemy.orm import Session

from server.db.models import Capture


def clamp_rgb_channel(val: Any, default: float = 1.0) -> float:
    """Ограничивает коэффициент канала R/G/B диапазоном [0.0, 1.0]."""
    try:
        num = float(val)
    except (TypeError, ValueError):
        return default
    return round(max(0.0, min(1.0, num)), 4)


def clamp_rgb_dict(rgb: dict[str, Any] | None) -> dict[str, float]:
    """Нормализует словарь RGB-коэффициентов в диапазон [0.0, 1.0]."""
    if not isinstance(rgb, dict):
        return {"r": 1.0, "g": 1.0, "b": 1.0}
    return {
        "r": clamp_rgb_channel(rgb.get("r", 1.0)),
        "g": clamp_rgb_channel(rgb.get("g", 1.0)),
        "b": clamp_rgb_channel(rgb.get("b", 1.0)),
    }


def is_identity_rgb(rgb: dict[str, float]) -> bool:
    """Возвращает True, если все каналы равны 1.0."""
    return (
        abs(float(rgb.get("r", 1.0)) - 1.0) < 1e-4
        and abs(float(rgb.get("g", 1.0)) - 1.0) < 1e-4
        and abs(float(rgb.get("b", 1.0)) - 1.0) < 1e-4
    )


def apply_rgb_to_jpeg_bytes(
    jpeg_bytes: bytes, r: float = 1.0, g: float = 1.0, b: float = 1.0
) -> bytes:
    """Применяет коэффициенты R/G/B (0.0..1.0) к JPEG-байтам через Pillow."""
    rc = clamp_rgb_channel(r)
    gc = clamp_rgb_channel(g)
    bc = clamp_rgb_channel(b)
    if is_identity_rgb({"r": rc, "g": gc, "b": bc}):
        return jpeg_bytes

    try:
        with Image.open(io.BytesIO(jpeg_bytes)) as img:
            rgb_img = img.convert("RGB")
            channels = list(rgb_img.split())
            for idx, gain in enumerate((rc, gc, bc)):
                if abs(gain - 1.0) >= 1e-4:
                    lut = [min(255, max(0, round(i * gain))) for i in range(256)]
                    channels[idx] = channels[idx].point(lut)
            merged = Image.merge("RGB", tuple(channels))
            out = io.BytesIO()
            merged.save(out, format="JPEG", quality=92)
            return out.getvalue()
    except Exception:  # noqa: BLE001
        return jpeg_bytes


def get_rgb_variant_rel_path(raw_image_path: str) -> str:
    """Возвращает относительный путь к RGB-варианту кадра, не затрагивая оригинал raw_...jpg."""
    p = Path(raw_image_path)
    rgb_name = f"rgb_{p.name}"
    if p.parent == Path("."):
        return rgb_name
    return (p.parent / rgb_name).as_posix()


def resolve_media_abs_path(image_path: str, base_media_dir: Path) -> Path:
    """Преобразует относительный путь ('media/1/raw_1.jpg') в абсолютный внутри media_dir."""
    p = Path(image_path)
    if p.parts and p.parts[0] == "media":
        return base_media_dir / Path(*p.parts[1:])
    return base_media_dir / p


def resolve_capture_display_rel_path(raw_image_path: str, base_media_dir: Path) -> str:
    """Возвращает путь к RGB-копии кадра, если она существует на диске, иначе путь к оригиналу."""
    if not raw_image_path:
        return raw_image_path
    rgb_rel = get_rgb_variant_rel_path(raw_image_path)
    rgb_abs = resolve_media_abs_path(rgb_rel, base_media_dir)
    if rgb_abs.is_file():
        return rgb_rel
    return raw_image_path


def apply_rgb_to_capture_file(
    raw_image_path: str, rgb: dict[str, float], base_media_dir: Path
) -> bool:
    """Создаёт или удаляет RGB-копию кадра на основе нетронутого оригинала raw_...jpg."""
    if not raw_image_path:
        return False
    orig_abs = resolve_media_abs_path(raw_image_path, base_media_dir)
    rgb_rel = get_rgb_variant_rel_path(raw_image_path)
    rgb_abs = resolve_media_abs_path(rgb_rel, base_media_dir)

    if is_identity_rgb(rgb):
        if rgb_abs.is_file():
            rgb_abs.unlink(missing_ok=True)
        return True

    if not orig_abs.is_file():
        return False

    orig_bytes = orig_abs.read_bytes()
    filtered_bytes = apply_rgb_to_jpeg_bytes(
        orig_bytes, r=rgb["r"], g=rgb["g"], b=rgb["b"]
    )
    rgb_abs.parent.mkdir(parents=True, exist_ok=True)
    rgb_abs.write_bytes(filtered_bytes)
    return True


def apply_rgb_to_camera_captures(
    db: Session,
    camera_id: int,
    rgb: dict[str, float],
    base_media_dir: Path,
) -> int:
    """Переприменяет программную RGB-коррекцию ко всем снимкам камеры, сохраняя оригиналы raw_...jpg."""
    captures = db.query(Capture).filter(Capture.camera_id == camera_id).all()
    updated = 0
    for cap in captures:
        if apply_rgb_to_capture_file(cap.raw_image_path, rgb, base_media_dir):
            updated += 1
    return updated
