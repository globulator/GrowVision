"""Иерархия и расчёт состояний основного света и вспышки (общие режимы, цикл дня/ночи и индивидуальные настройки)."""

import json
from datetime import datetime, time
from typing import Any, Literal

from sqlalchemy.orm import Session

from server.core.state import app_state
from server.core.system_status import is_rpi_online
from server.db.database import upsert_system_setting
from server.db.models import EdgeCommand, Installation, SystemSetting
from shared.light_cycle import (
    DEFAULT_DAY_END,
    DEFAULT_DAY_START,
    DayNightPhase,
    is_day_phase,
    normalize_hhmm,
    validate_day_night_window,
)
from shared.pins import (
    DEFAULT_COLOR_HEX,
    format_gpio_pins,
    normalize_hex_color,
    parse_pins_setting,
    serialize_pins_setting,
)

LightMode = Literal["on", "default", "off"]

GLOBAL_LIGHT_MODE_KEY = "global_light_mode"
GLOBAL_FLASH_MODE_KEY = "global_flash_mode"
GLOBAL_DAY_NIGHT_ENABLED_KEY = "global_day_night_enabled"
GLOBAL_DAY_START_KEY = "global_day_start"
GLOBAL_DAY_END_KEY = "global_day_end"


def inst_light_state_key(inst_id: int) -> str:
    """Ключ SystemSetting для индивидуального состояния света установки."""
    return f"inst_light_state_{inst_id}"


def inst_flash_state_key(inst_id: int) -> str:
    """Ключ SystemSetting для индивидуального состояния вспышки установки."""
    return f"inst_flash_state_{inst_id}"


def inst_light_pins_key(inst_id: int) -> str:
    """Ключ SystemSetting для списка GPIO-пинов света установки."""
    return f"inst_light_pins_{inst_id}"


def inst_flash_pins_key(inst_id: int) -> str:
    """Ключ SystemSetting для списка GPIO-пинов вспышки установки."""
    return f"inst_flash_pins_{inst_id}"


def inst_light_color_key(inst_id: int) -> str:
    """Ключ SystemSetting для сохранённого HEX-цвета основного света установки."""
    return f"inst_light_color_{inst_id}"


def inst_flash_color_key(inst_id: int) -> str:
    """Ключ SystemSetting для сохранённого HEX-цвета вспышки установки."""
    return f"inst_flash_color_{inst_id}"


def inst_day_night_enabled_key(inst_id: int) -> str:
    """Ключ SystemSetting для включения индивидуального цикла дня и ночи установки."""
    return f"inst_day_night_enabled_{inst_id}"


def inst_day_start_key(inst_id: int) -> str:
    """Ключ SystemSetting для начала светового дня установки (HH:MM)."""
    return f"inst_day_start_{inst_id}"


def inst_day_end_key(inst_id: int) -> str:
    """Ключ SystemSetting для окончания светового дня установки (HH:MM)."""
    return f"inst_day_end_{inst_id}"


def _parse_bool_setting(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    norm = raw.strip().lower()
    if norm in ("true", "1", "yes", "on"):
        return True
    if norm in ("false", "0", "no", "off"):
        return False
    return default


def _read_settings_map(
    db: Session,
    keys: list[str],
    settings_cache: dict[str, str] | None = None,
) -> dict[str, str]:
    if not keys:
        return {}
    if settings_cache is not None:
        return {k: settings_cache[k] for k in keys if k in settings_cache}
    rows = db.query(SystemSetting).filter(SystemSetting.key.in_(keys)).all()
    return {row.key: row.value for row in rows if row.value is not None}


def _get_setting_value(
    db: Session,
    key: str,
    settings_cache: dict[str, str] | None = None,
) -> str | None:
    if settings_cache is not None:
        return settings_cache.get(key)
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    return row.value if row else None


def load_light_settings_cache(
    db: Session,
    inst_ids: list[int],
    *,
    include_global: bool = True,
    extra_keys: list[str] | None = None,
) -> dict[str, str]:
    """Загружает настройки света (глобальные и по списку установок) одним IN-запросом."""
    keys: list[str] = []
    if include_global:
        keys.extend(
            [
                GLOBAL_LIGHT_MODE_KEY,
                GLOBAL_FLASH_MODE_KEY,
                GLOBAL_DAY_NIGHT_ENABLED_KEY,
                GLOBAL_DAY_START_KEY,
                GLOBAL_DAY_END_KEY,
            ]
        )
    for inst_id in inst_ids:
        keys.extend(
            [
                inst_light_state_key(inst_id),
                inst_flash_state_key(inst_id),
                inst_light_pins_key(inst_id),
                inst_flash_pins_key(inst_id),
                inst_light_color_key(inst_id),
                inst_flash_color_key(inst_id),
                inst_day_night_enabled_key(inst_id),
                inst_day_start_key(inst_id),
                inst_day_end_key(inst_id),
            ]
        )
    if extra_keys:
        keys.extend(extra_keys)
    return _read_settings_map(db, keys)


def _safe_hhmm(raw: str | None, fallback: str) -> str:
    if raw is None:
        return fallback
    try:
        return normalize_hhmm(raw)
    except ValueError:
        return fallback


def _build_day_night_dict(
    enabled: bool,
    day_start: str,
    day_end: str,
    *,
    now_time: time | datetime | None = None,
) -> dict[str, Any]:
    if day_start == day_end:
        day_start = DEFAULT_DAY_START
        day_end = DEFAULT_DAY_END
    is_day = is_day_phase(day_start, day_end, now_time=now_time)
    phase: DayNightPhase = "day" if is_day else "night"
    return {
        "enabled": bool(enabled),
        "day_start": day_start,
        "day_end": day_end,
        "phase": phase,
        "is_day": is_day,
    }


def normalize_light_mode(raw: str | None) -> LightMode:
    """Нормализует строковый режим глобального света/вспышки к ('on', 'default', 'off')."""
    norm = (raw or "default").strip().lower()
    if norm == "on":
        return "on"
    if norm == "off":
        return "off"
    return "default"


def get_global_light_mode(
    db: Session,
    *,
    is_flash: bool = False,
    settings_cache: dict[str, str] | None = None,
) -> LightMode:
    """Возвращает глобальный режим света или вспышки из SystemSetting."""
    key = GLOBAL_FLASH_MODE_KEY if is_flash else GLOBAL_LIGHT_MODE_KEY
    raw = _get_setting_value(db, key, settings_cache=settings_cache)
    return normalize_light_mode(raw)


def set_global_light_mode(
    db: Session,
    mode: LightMode,
    *,
    is_flash: bool = False,
    now: datetime | None = None,
) -> None:
    """Сохраняет глобальный режим света или вспышки в SystemSetting."""
    key = GLOBAL_FLASH_MODE_KEY if is_flash else GLOBAL_LIGHT_MODE_KEY
    upsert_system_setting(db, key, normalize_light_mode(mode), now=now)


def get_global_day_night_settings(
    db: Session,
    *,
    now_time: time | datetime | None = None,
    settings_cache: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Возвращает глобальные настройки цикла дня и ночи из SystemSetting."""
    by_key = _read_settings_map(
        db,
        [GLOBAL_DAY_NIGHT_ENABLED_KEY, GLOBAL_DAY_START_KEY, GLOBAL_DAY_END_KEY],
        settings_cache=settings_cache,
    )
    enabled = _parse_bool_setting(by_key.get(GLOBAL_DAY_NIGHT_ENABLED_KEY), False)
    day_start = _safe_hhmm(by_key.get(GLOBAL_DAY_START_KEY), DEFAULT_DAY_START)
    day_end = _safe_hhmm(by_key.get(GLOBAL_DAY_END_KEY), DEFAULT_DAY_END)
    return _build_day_night_dict(enabled, day_start, day_end, now_time=now_time)


def set_global_day_night_settings(
    db: Session,
    *,
    enabled: bool | None = None,
    day_start: str | None = None,
    day_end: str | None = None,
    now: datetime | None = None,
    now_time: time | datetime | None = None,
) -> dict[str, Any]:
    """Валидирует и сохраняет глобальные настройки цикла дня и ночи в SystemSetting."""
    current = get_global_day_night_settings(db, now_time=now_time)
    next_enabled = current["enabled"] if enabled is None else bool(enabled)
    raw_start = current["day_start"] if day_start is None else day_start
    raw_end = current["day_end"] if day_end is None else day_end
    norm_start, norm_end = validate_day_night_window(raw_start, raw_end)

    upsert_system_setting(
        db,
        GLOBAL_DAY_NIGHT_ENABLED_KEY,
        "true" if next_enabled else "false",
        now=now,
    )
    upsert_system_setting(db, GLOBAL_DAY_START_KEY, norm_start, now=now)
    upsert_system_setting(db, GLOBAL_DAY_END_KEY, norm_end, now=now)
    return _build_day_night_dict(next_enabled, norm_start, norm_end, now_time=now_time)


def get_installation_day_night_settings(
    db: Session,
    inst_id: int,
    *,
    now_time: time | datetime | None = None,
    settings_cache: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Возвращает индивидуальные настройки цикла дня и ночи установки из SystemSetting."""
    k_en = inst_day_night_enabled_key(inst_id)
    k_st = inst_day_start_key(inst_id)
    k_ed = inst_day_end_key(inst_id)
    by_key = _read_settings_map(db, [k_en, k_st, k_ed], settings_cache=settings_cache)
    enabled = _parse_bool_setting(by_key.get(k_en), False)
    day_start = _safe_hhmm(by_key.get(k_st), DEFAULT_DAY_START)
    day_end = _safe_hhmm(by_key.get(k_ed), DEFAULT_DAY_END)
    return _build_day_night_dict(enabled, day_start, day_end, now_time=now_time)


def set_installation_day_night_settings(
    db: Session,
    inst_id: int,
    *,
    enabled: bool | None = None,
    day_start: str | None = None,
    day_end: str | None = None,
    now: datetime | None = None,
    now_time: time | datetime | None = None,
) -> dict[str, Any]:
    """Валидирует и сохраняет индивидуальные настройки цикла дня и ночи установки."""
    current = get_installation_day_night_settings(db, inst_id, now_time=now_time)
    next_enabled = current["enabled"] if enabled is None else bool(enabled)
    raw_start = current["day_start"] if day_start is None else day_start
    raw_end = current["day_end"] if day_end is None else day_end
    norm_start, norm_end = validate_day_night_window(raw_start, raw_end)

    upsert_system_setting(
        db,
        inst_day_night_enabled_key(inst_id),
        "true" if next_enabled else "false",
        now=now,
    )
    upsert_system_setting(db, inst_day_start_key(inst_id), norm_start, now=now)
    upsert_system_setting(db, inst_day_end_key(inst_id), norm_end, now=now)
    return _build_day_night_dict(next_enabled, norm_start, norm_end, now_time=now_time)


def get_individual_light_state(
    db: Session,
    inst_id: int,
    *,
    is_flash: bool = False,
    settings_cache: dict[str, str] | None = None,
) -> bool:
    """Возвращает индивидуальное состояние света/вспышки установки из SystemSetting."""
    key = inst_flash_state_key(inst_id) if is_flash else inst_light_state_key(inst_id)
    default = not is_flash
    raw = _get_setting_value(db, key, settings_cache=settings_cache)
    return _parse_bool_setting(raw, default)


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
    upsert_system_setting(db, key, "true" if state else "false", now=now)


def get_installation_channel_pins(
    db: Session,
    inst: Installation,
    *,
    is_flash: bool = False,
    settings_cache: dict[str, str] | None = None,
) -> list[int]:
    """Возвращает список GPIO-пинов света или вспышки установки (1 или 3 пина)."""
    key = inst_flash_pins_key(inst.id) if is_flash else inst_light_pins_key(inst.id)
    fallback = int(inst.flash_gpio_pin if is_flash else inst.light_gpio_pin)
    raw = _get_setting_value(db, key, settings_cache=settings_cache)
    return parse_pins_setting(raw, fallback)


def set_installation_channel_pins(
    db: Session,
    inst_id: int,
    pins: list[int],
    *,
    is_flash: bool = False,
    now: datetime | None = None,
) -> None:
    """Сохраняет список GPIO-пинов света или вспышки установки в SystemSetting."""
    key = inst_flash_pins_key(inst_id) if is_flash else inst_light_pins_key(inst_id)
    upsert_system_setting(db, key, serialize_pins_setting(pins), now=now)


def get_installation_channel_color(
    db: Session,
    inst_id: int,
    *,
    is_flash: bool = False,
    settings_cache: dict[str, str] | None = None,
) -> str:
    """Возвращает сохранённый HEX-цвет (#RRGGBB) света или вспышки установки."""
    key = inst_flash_color_key(inst_id) if is_flash else inst_light_color_key(inst_id)
    raw = _get_setting_value(db, key, settings_cache=settings_cache)
    if raw:
        try:
            return normalize_hex_color(raw)
        except ValueError:
            pass
    return DEFAULT_COLOR_HEX


def set_installation_channel_color(
    db: Session,
    inst_id: int,
    color: str,
    *,
    is_flash: bool = False,
    now: datetime | None = None,
) -> str:
    """Нормализует и сохраняет HEX-цвет (#RRGGBB) канала установки в SystemSetting."""
    norm = normalize_hex_color(color, default=DEFAULT_COLOR_HEX)
    key = inst_flash_color_key(inst_id) if is_flash else inst_light_color_key(inst_id)
    upsert_system_setting(db, key, norm, now=now)
    return norm


def delete_installation_light_settings(db: Session, inst_id: int) -> None:
    """Удаляет настройки света/пинов/цвета/цикла установки при её удалении."""
    keys = [
        inst_light_state_key(inst_id),
        inst_flash_state_key(inst_id),
        inst_light_pins_key(inst_id),
        inst_flash_pins_key(inst_id),
        inst_light_color_key(inst_id),
        inst_flash_color_key(inst_id),
        inst_day_night_enabled_key(inst_id),
        inst_day_start_key(inst_id),
        inst_day_end_key(inst_id),
    ]
    db.query(SystemSetting).filter(SystemSetting.key.in_(keys)).delete(
        synchronize_session="fetch"
    )


def compute_effective_state(global_mode: LightMode, individual_state: bool) -> bool:
    """Вычисляет итоговое состояние канала без цикла дня/ночи (используется для вспышки)."""
    if global_mode == "on":
        return True
    if global_mode == "off":
        return False
    return bool(individual_state)


def compute_effective_main_light_state(
    *,
    global_day_night_enabled: bool,
    global_is_day: bool,
    global_light_mode: LightMode,
    inst_day_night_enabled: bool,
    inst_is_day: bool,
    individual_light_state: bool,
) -> bool:
    """Вычисляет итоговое состояние основного света по 5-уровневой иерархии (уровни 2–5).

    Уровень 1 (временное аппаратное переопределение при съёмке/тесте реле)
    обрабатывается на уровне edge_agent с гарантированным возвратом.
    """
    if global_day_night_enabled:
        return bool(global_is_day)
    if global_light_mode == "on":
        return True
    if global_light_mode == "off":
        return False
    if inst_day_night_enabled:
        return bool(inst_is_day)
    return bool(individual_light_state)


def get_installation_light_snapshot(
    db: Session,
    inst: Installation,
    *,
    global_light_mode: LightMode | None = None,
    global_flash_mode: LightMode | None = None,
    global_day_night: dict[str, Any] | None = None,
    now_time: time | datetime | None = None,
    settings_cache: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Возвращает полный срез индивидуальных, глобальных и эффективных состояний установки."""
    cache = (
        settings_cache
        if settings_cache is not None
        else load_light_settings_cache(
            db,
            [inst.id],
            include_global=(
                global_light_mode is None
                or global_flash_mode is None
                or global_day_night is None
            ),
        )
    )
    g_light = (
        global_light_mode
        if global_light_mode is not None
        else get_global_light_mode(db, is_flash=False, settings_cache=cache)
    )
    g_flash = (
        global_flash_mode
        if global_flash_mode is not None
        else get_global_light_mode(db, is_flash=True, settings_cache=cache)
    )
    g_dn = (
        global_day_night
        if global_day_night is not None
        else get_global_day_night_settings(db, now_time=now_time, settings_cache=cache)
    )
    inst_dn = get_installation_day_night_settings(
        db, inst.id, now_time=now_time, settings_cache=cache
    )

    ind_light = get_individual_light_state(
        db, inst.id, is_flash=False, settings_cache=cache
    )
    ind_flash = get_individual_light_state(
        db, inst.id, is_flash=True, settings_cache=cache
    )

    eff_light = compute_effective_main_light_state(
        global_day_night_enabled=g_dn["enabled"],
        global_is_day=g_dn["is_day"],
        global_light_mode=g_light,
        inst_day_night_enabled=inst_dn["enabled"],
        inst_is_day=inst_dn["is_day"],
        individual_light_state=ind_light,
    )
    eff_flash = compute_effective_state(g_flash, ind_flash)

    day_night_locked = bool(g_dn["enabled"] or g_light != "default")
    if day_night_locked:
        light_locked = True
        light_lock_reason: str | None = "Заблокировано"
    elif inst_dn["enabled"]:
        light_locked = True
        light_lock_reason = "Световой день"
    else:
        light_locked = False
        light_lock_reason = None

    light_pins = get_installation_channel_pins(
        db, inst, is_flash=False, settings_cache=cache
    )
    flash_pins = get_installation_channel_pins(
        db, inst, is_flash=True, settings_cache=cache
    )
    light_color = get_installation_channel_color(
        db, inst.id, is_flash=False, settings_cache=cache
    )
    flash_color = get_installation_channel_color(
        db, inst.id, is_flash=True, settings_cache=cache
    )

    return {
        "individual_light_state": ind_light,
        "individual_flash_state": ind_flash,
        "light_state": eff_light,
        "flash_state": eff_flash,
        "global_light_mode": g_light,
        "global_flash_mode": g_flash,
        "global_day_night_enabled": g_dn["enabled"],
        "global_day_start": g_dn["day_start"],
        "global_day_end": g_dn["day_end"],
        "global_day_night_phase": g_dn["phase"],
        "global_light_locked": bool(g_dn["enabled"]),
        "day_night_enabled": inst_dn["enabled"],
        "day_start": inst_dn["day_start"],
        "day_end": inst_dn["day_end"],
        "day_night_phase": inst_dn["phase"],
        "day_night_locked": day_night_locked,
        "light_locked": light_locked,
        "light_lock_reason": light_lock_reason,
        "flash_locked": g_flash != "default",
        "light_pins": light_pins,
        "flash_pins": flash_pins,
        "light_is_rgb": len(light_pins) == 3,
        "flash_is_rgb": len(flash_pins) == 3,
        "light_color": light_color,
        "flash_color": flash_color,
    }


def compute_active_pin_states(
    db: Session,
    installations: list[Installation],
    *,
    global_light_mode: LightMode | None = None,
    global_flash_mode: LightMode | None = None,
    global_day_night: dict[str, Any] | None = None,
    now_time: time | datetime | None = None,
) -> dict[int, bool]:
    """Строит карту целевых GPIO-пинов (pin -> bool) для списка установок."""
    cache = load_light_settings_cache(
        db,
        [inst.id for inst in installations],
        include_global=(
            global_light_mode is None
            or global_flash_mode is None
            or global_day_night is None
        ),
    )
    g_light = (
        global_light_mode
        if global_light_mode is not None
        else get_global_light_mode(db, is_flash=False, settings_cache=cache)
    )
    g_flash = (
        global_flash_mode
        if global_flash_mode is not None
        else get_global_light_mode(db, is_flash=True, settings_cache=cache)
    )
    g_dn = (
        global_day_night
        if global_day_night is not None
        else get_global_day_night_settings(db, now_time=now_time, settings_cache=cache)
    )
    pin_states: dict[int, bool] = {}
    for inst in installations:
        snap = get_installation_light_snapshot(
            db,
            inst,
            global_light_mode=g_light,
            global_flash_mode=g_flash,
            global_day_night=g_dn,
            now_time=now_time,
            settings_cache=cache,
        )
        for pin in snap["light_pins"]:
            pin_states[pin] = snap["light_state"]
        for pin in snap["flash_pins"]:
            if pin not in snap["light_pins"]:
                pin_states[pin] = snap["flash_state"]
    return pin_states


def sync_app_state_pins(pin_states: dict[int, bool]) -> None:
    """Синхронизирует вычисленные целевые состояния пинов с app_state."""
    for pin, val in pin_states.items():
        app_state.set_pin_state(int(pin), bool(val))


def build_light_main_command_payload(
    db: Session,
    installations: list[Installation],
    *,
    now_time: time | datetime | None = None,
) -> tuple[dict[int, bool], dict[str, Any]]:
    """Формирует карту light-пинов и payload для команды LIGHT_MAIN."""
    cache = load_light_settings_cache(
        db, [item.id for item in installations], include_global=True
    )
    g_light = get_global_light_mode(db, is_flash=False, settings_cache=cache)
    g_flash = get_global_light_mode(db, is_flash=True, settings_cache=cache)
    g_dn = get_global_day_night_settings(db, now_time=now_time, settings_cache=cache)

    light_pin_states: dict[int, bool] = {}
    inst_states_map: dict[str, dict[str, Any]] = {}
    for item in installations:
        snap = get_installation_light_snapshot(
            db,
            item,
            global_light_mode=g_light,
            global_flash_mode=g_flash,
            global_day_night=g_dn,
            now_time=now_time,
            settings_cache=cache,
        )
        inst_states_map[str(item.id)] = {
            "light_gpio_pin": format_gpio_pins(snap["light_pins"]),
            "flash_gpio_pin": format_gpio_pins(snap["flash_pins"]),
            "light_pins": snap["light_pins"],
            "flash_pins": snap["flash_pins"],
            "light_color": snap["light_color"],
            "flash_color": snap["flash_color"],
            "light_state": snap["light_state"],
            "flash_state": snap["flash_state"],
        }
        for p in snap["light_pins"]:
            light_pin_states[p] = snap["light_state"]

    payload: dict[str, Any] = {
        "mode": g_light,
        "state": None,
        "pin_states": {str(p): v for p, v in light_pin_states.items()},
        "installation_states": inst_states_map,
    }
    return light_pin_states, payload


def build_single_installation_light_payload(
    snap: dict[str, Any] | None = None,
    *,
    state: bool | None = None,
    individual_state: bool | None = None,
    mode: LightMode | None = None,
    pins: list[int] | None = None,
    color: str | None = None,
) -> dict[str, Any]:
    """Формирует payload команды LIGHT_MAIN/LIGHT_FLASH для одной установки."""
    resolved_state = (
        bool(snap["light_state"]) if state is None and snap is not None else bool(state)
    )
    resolved_ind = (
        bool(snap["individual_light_state"])
        if individual_state is None and snap is not None
        else bool(individual_state)
    )
    resolved_mode = (
        snap["global_light_mode"] if mode is None and snap is not None else mode
    )
    resolved_pins = (
        list(snap["light_pins"])
        if pins is None and snap is not None
        else list(pins or [])
    )
    resolved_color = (
        snap["light_color"] if color is None and snap is not None else color
    )
    return {
        "state": resolved_state,
        "individual_state": resolved_ind,
        "mode": resolved_mode,
        "pins": resolved_pins,
        "color": resolved_color,
        "pin_states": {str(p): resolved_state for p in resolved_pins},
    }


def sync_day_night_phases_step(
    db: Session,
    *,
    now_time: time | datetime | None = None,
) -> dict[str, Any]:
    """Один шаг фоновой синхронизации фаз День/Ночь для активных установок.

    Обновляет app_state и ставит команду LIGHT_MAIN в EdgeCommand ТОЛЬКО при
    фактическом изменении целевого состояния хотя бы одного пина основного света
    и при условии, что Edge-агент онлайн.
    """
    active_insts = (
        db.query(Installation)
        .filter(Installation.is_archived.is_(False))
        .order_by(Installation.id.asc())
        .all()
    )
    if not active_insts:
        return {"changed": False, "command_id": None, "changed_pins": {}}

    light_pin_states, cmd_payload = build_light_main_command_payload(
        db, active_insts, now_time=now_time
    )
    existing_pins = app_state.get_all_pin_states()
    changed_pins: dict[int, bool] = {}

    for pin, new_val in light_pin_states.items():
        if pin not in existing_pins:
            app_state.set_pin_state(pin, new_val)
        elif bool(existing_pins[pin]) != bool(new_val):
            changed_pins[pin] = bool(new_val)
            app_state.set_pin_state(pin, new_val)

    if not changed_pins:
        return {"changed": False, "command_id": None, "changed_pins": {}}

    online, _ = is_rpi_online(db)
    cmd_id: int | None = None
    if online:
        cmd = EdgeCommand(
            command_type="LIGHT_MAIN",
            installation_id=None,
            is_global=True,
            payload=json.dumps(cmd_payload),
            status="pending",
        )
        db.add(cmd)
        db.commit()
        db.refresh(cmd)
        cmd_id = cmd.id

    return {
        "changed": True,
        "command_id": cmd_id,
        "changed_pins": changed_pins,
    }
