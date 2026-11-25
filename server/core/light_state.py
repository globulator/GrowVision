"""Иерархия и расчёт состояний основного света и вспышки (общие режимы и индивидуальные настройки)."""

from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy.orm import Session

from server.core.state import app_state
from server.db.models import Installation, SystemSetting

LightMode = Literal["on", "default", "off"]

GLOBAL_LIGHT_MODE_KEY = "global_light_mode"
GLOBAL_FLASH_MODE_KEY = "global_flash_mode"


def inst_light_state_key(inst_id: int) -> str:
    """Ключ SystemSetting для индивидуального состояния света установки."""
    return f"inst_light_state_{inst_id}"


def inst_flash_state_key(inst_id: int) -> str:
    """Ключ SystemSetting для индивидуального состояния вспышки установки."""
    return f"inst_flash_state_{inst_id}"


def _upsert_setting(
    db: Session, key: str, value: str, now: datetime | None = None
) -> None:
    ts = now or datetime.now(UTC)
    existing = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if existing:
        existing.value = value
        existing.updated_at = ts
    else:
        db.add(SystemSetting(key=key, value=value, updated_at=ts))


def _parse_bool_setting(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    norm = raw.strip().lower()
    if norm in ("true", "1", "yes", "on"):
        return True
    if norm in ("false", "0", "no", "off"):
        return False
    return default


def normalize_light_mode(raw: str | None) -> LightMode:
    """Нормализует строковый режим глобального света/вспышки к ('on', 'default', 'off')."""
    norm = (raw or "default").strip().lower()
    if norm == "on":
        return "on"
    if norm == "off":
        return "off"
    return "default"


def get_global_light_mode(db: Session, *, is_flash: bool = False) -> LightMode:
    """Возвращает глобальный режим света или вспышки из SystemSetting."""
    key = GLOBAL_FLASH_MODE_KEY if is_flash else GLOBAL_LIGHT_MODE_KEY
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    return normalize_light_mode(row.value if row else None)


def set_global_light_mode(
    db: Session,
    mode: LightMode,
    *,
    is_flash: bool = False,
    now: datetime | None = None,
) -> None:
    """Сохраняет глобальный режим света или вспышки в SystemSetting."""
    key = GLOBAL_FLASH_MODE_KEY if is_flash else GLOBAL_LIGHT_MODE_KEY
    _upsert_setting(db, key, normalize_light_mode(mode), now=now)


def get_individual_light_state(
    db: Session, inst_id: int, *, is_flash: bool = False
) -> bool:
    """Возвращает индивидуальное состояние света/вспышки установки из SystemSetting."""
    key = inst_flash_state_key(inst_id) if is_flash else inst_light_state_key(inst_id)
    default = not is_flash
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    return _parse_bool_setting(row.value if row else None, default)


def set_individual_light_state(
    db: Session,
    inst_id: int,
    state: bool,
    *,
    is_flash: bool = False,
    now: datetime | None = None,
) -> None:
    """Сохраняет индивидуальное состояние света/вспышки установки в SystemSetting."""
    key = inst_flash_state_key(inst_id) if is_flash else inst_light_state_key(inst_id)
    _upsert_setting(db, key, "true" if state else "false", now=now)


def compute_effective_state(global_mode: LightMode, individual_state: bool) -> bool:
    """Вычисляет итоговое состояние канала с учётом глобального переопределения."""
    if global_mode == "on":
        return True
    if global_mode == "off":
        return False
    return bool(individual_state)


def get_installation_light_snapshot(
    db: Session,
    inst: Installation,
    *,
    global_light_mode: LightMode | None = None,
    global_flash_mode: LightMode | None = None,
) -> dict[str, Any]:
    """Возвращает полный срез индивидуальных, глобальных и эффективных состояний установки."""
    g_light = (
        global_light_mode
        if global_light_mode is not None
        else get_global_light_mode(db, is_flash=False)
    )
    g_flash = (
        global_flash_mode
        if global_flash_mode is not None
        else get_global_light_mode(db, is_flash=True)
    )
    ind_light = get_individual_light_state(db, inst.id, is_flash=False)
    ind_flash = get_individual_light_state(db, inst.id, is_flash=True)
    eff_light = compute_effective_state(g_light, ind_light)
    eff_flash = compute_effective_state(g_flash, ind_flash)
    return {
        "individual_light_state": ind_light,
        "individual_flash_state": ind_flash,
        "light_state": eff_light,
        "flash_state": eff_flash,
        "global_light_mode": g_light,
        "global_flash_mode": g_flash,
        "light_locked": g_light != "default",
        "flash_locked": g_flash != "default",
    }


def compute_active_pin_states(
    db: Session,
    installations: list[Installation],
    *,
    global_light_mode: LightMode | None = None,
    global_flash_mode: LightMode | None = None,
) -> dict[int, bool]:
    """Строит карту целевых GPIO-пинов (pin -> bool) для списка установок."""
    g_light = (
        global_light_mode
        if global_light_mode is not None
        else get_global_light_mode(db, is_flash=False)
    )
    g_flash = (
        global_flash_mode
        if global_flash_mode is not None
        else get_global_light_mode(db, is_flash=True)
    )
    pin_states: dict[int, bool] = {}
    for inst in installations:
        snap = get_installation_light_snapshot(
            db,
            inst,
            global_light_mode=g_light,
            global_flash_mode=g_flash,
        )
        pin_states[inst.light_gpio_pin] = snap["light_state"]
        if inst.flash_gpio_pin != inst.light_gpio_pin:
            pin_states[inst.flash_gpio_pin] = snap["flash_state"]
    return pin_states


def sync_app_state_pins(pin_states: dict[int, bool]) -> None:
    """Синхронизирует вычисленные целевые состояния пинов с app_state."""
    for pin, val in pin_states.items():
        app_state.set_pin_state(int(pin), bool(val))
