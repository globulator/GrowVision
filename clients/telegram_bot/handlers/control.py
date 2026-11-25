import contextlib
import html
import logging
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InputMediaPhoto,
    Message,
)

from clients.telegram_bot.api_client import GrowVisionApiClient
from clients.telegram_bot.keyboards.inline import (
    get_cancel_inline_keyboard,
    get_control_panel_keyboard,
    get_rgb_channel_menu_keyboard,
    get_snap_layer_keyboard,
)
from clients.telegram_bot.keyboards.paginator import build_paginator_keyboard
from clients.telegram_bot.states import ControlColorStates
from clients.telegram_bot.utils.errors import ApiError, format_api_error
from clients.telegram_bot.utils.formatters import (
    format_area_display,
    format_debug_block,
)
from clients.telegram_bot.utils.pins import (
    DEFAULT_COLOR_HEX,
    normalize_hex_color,
    parse_gpio_pins,
)

logger = logging.getLogger(__name__)

router = Router(name="control")

# Локальное состояние реле: target -> {"light": bool, "flash": bool}
_relay_states: dict[str, dict[str, bool]] = {}
# Состояние реле по GPIO-пинам: pin -> bool
_pin_states: dict[int, bool] = {}
# Привязка установок к пинам: target -> {"light": pin, "flash": pin}
_target_pins: dict[str, dict[str, int]] = {}
# RGB-метаданные каналов установки: target -> {"light_is_rgb", "flash_is_rgb", "light_color", "flash_color", ...}
_target_meta: dict[str, dict[str, Any]] = {}


def _extract_pins_list(raw_list: Any, raw_pin: Any) -> list[int]:
    if isinstance(raw_list, list) and raw_list:
        return [int(p) for p in raw_list]
    if raw_pin is not None:
        try:
            return parse_gpio_pins(raw_pin)
        except ValueError:
            pass
    return []


def _store_installation_meta(target: str, inst: dict[str, Any]) -> None:
    l_pins = _extract_pins_list(inst.get("light_pins"), inst.get("light_gpio_pin"))
    f_pins = _extract_pins_list(inst.get("flash_pins"), inst.get("flash_gpio_pin"))
    if l_pins and f_pins:
        _target_pins[target] = {"light": l_pins[0], "flash": f_pins[0]}
    elif (
        inst.get("light_gpio_pin") is not None
        and inst.get("flash_gpio_pin") is not None
    ):
        with contextlib.suppress(TypeError, ValueError):
            _target_pins[target] = {
                "light": int(inst["light_gpio_pin"]),
                "flash": int(inst["flash_gpio_pin"]),
            }

    light_is_rgb = bool(inst.get("light_is_rgb")) or len(l_pins) == 3
    flash_is_rgb = bool(inst.get("flash_is_rgb")) or len(f_pins) == 3
    light_color = normalize_hex_color(
        inst.get("light_color"), default=DEFAULT_COLOR_HEX
    )
    flash_color = normalize_hex_color(
        inst.get("flash_color"), default=DEFAULT_COLOR_HEX
    )
    _target_meta[target] = {
        "name": inst.get("name") or f"Установка #{target}",
        "light_pins": l_pins,
        "flash_pins": f_pins,
        "light_gpio_pin": inst.get("light_gpio_pin"),
        "flash_gpio_pin": inst.get("flash_gpio_pin"),
        "light_is_rgb": light_is_rgb,
        "flash_is_rgb": flash_is_rgb,
        "light_color": light_color,
        "flash_color": flash_color,
    }
    if "light_state" in inst:
        _relay_states.setdefault(target, {})["light"] = bool(inst["light_state"])
        for p in l_pins:
            _pin_states[p] = bool(inst["light_state"])
    if "flash_state" in inst:
        _relay_states.setdefault(target, {})["flash"] = bool(inst["flash_state"])
        for p in f_pins:
            _pin_states[p] = bool(inst["flash_state"])


def _get_target_rgb_kwargs(target: str) -> dict[str, Any]:
    if target == "all":
        return {}
    meta = _target_meta.get(target, {})
    return {
        "light_is_rgb": bool(meta.get("light_is_rgb", False)),
        "flash_is_rgb": bool(meta.get("flash_is_rgb", False)),
        "light_color": meta.get("light_color", DEFAULT_COLOR_HEX),
        "flash_color": meta.get("flash_color", DEFAULT_COLOR_HEX),
    }


async def _sync_pins_for_target(
    api_client: GrowVisionApiClient, user_id: int, target: str
) -> None:
    """Синхронизирует номера пинов, RGB-флаги, цвета и состояния реле с сервером."""
    if target == "all":
        with contextlib.suppress(Exception):
            insts = await api_client.get_installations(user_id, include_archived=False)
            for item in insts:
                _store_installation_meta(str(item.get("id")), item)
    else:
        with contextlib.suppress(Exception):
            inst = await api_client.get_installation(user_id, int(target))
            _store_installation_meta(target, inst)


def _get_target_relay_states(target: str) -> tuple[bool, bool]:
    """Возвращает (light_state, flash_state) для target с учётом пинов."""
    pins = _target_pins.get(target)
    st = _relay_states.get(target, {})
    if pins:
        light_pin = pins.get("light")
        flash_pin = pins.get("flash")
        light_state = (
            _pin_states.get(light_pin, st.get("light", False))
            if light_pin is not None
            else st.get("light", False)
        )
        flash_state = (
            _pin_states.get(flash_pin, st.get("flash", False))
            if flash_pin is not None
            else st.get("flash", False)
        )
        return light_state, flash_state
    return st.get("light", False), st.get("flash", False)


def _update_relay_pin_state(
    target: str,
    is_flash: bool,
    state_bool: bool,
    pin_states_from_api: dict[str, Any] | None = None,
) -> None:
    """Обновляет состояние пина и синхронизирует все связанные установки."""
    key = "flash" if is_flash else "light"

    if pin_states_from_api:
        for p_str, val in pin_states_from_api.items():
            if str(p_str).isdigit():
                _pin_states[int(p_str)] = bool(val)

    if target == "all":
        _relay_states.setdefault("all", {})[key] = state_bool
        for t, pins in _target_pins.items():
            pin = pins.get(key)
            if pin is not None:
                _pin_states[pin] = state_bool
            _relay_states.setdefault(t, {})[key] = state_bool
    else:
        _relay_states.setdefault(target, {})[key] = state_bool
        pins = _target_pins.get(target)
        if pins and pins.get(key) is not None:
            pin = pins[key]
            _pin_states[pin] = state_bool
            for other_t, other_pins in _target_pins.items():
                if other_pins.get(key) == pin:
                    _relay_states.setdefault(other_t, {})[key] = state_bool


# ================== Свет и реле (Developer) ==================


@router.message(F.text == "💡 Свет и реле")
async def handle_control_menu(
    message: Message,
    api_client: GrowVisionApiClient,
) -> None:
    """Выбор цели для управления светом/реле."""
    user_id = message.from_user.id if message.from_user else 0
    me = await api_client.get_me(user_id)
    if not me or me.get("role") != "developer":
        await message.answer(
            "Управление реле и светом доступно только для Разработчика."
        )
        return

    try:
        active_insts = await api_client.get_installations(
            user_id, include_archived=False
        )
        if not active_insts:
            await message.answer("Нет активных установок для управления светом.")
            return

        kb = build_paginator_keyboard(
            items=active_insts,
            page=0,
            page_size=5,
            entity="ctrl_pick",
            item_callback_prefix="ctrl:target",
            item_text_func=lambda i: f"🌱 {i.get('name')} (#{i.get('id')})",
            item_id_func=lambda i: i.get("id"),
            include_all_button=True,
            all_button_callback="ctrl:target:all",
            all_button_text="🌐 Все активные установки",
        )
        await message.answer(
            "💡 <b>Управление светом и реле</b>\n\nВыберите целевую установку или все активные:",
            reply_markup=kb,
        )
    except Exception as exc:  # noqa: BLE001
        err = format_api_error(exc)
        await message.answer(f"Ошибка загрузки установок:\n{err}")


@router.callback_query(F.data == "ctrl:menu")
async def handle_control_back_to_menu(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Возврат к выбору цели управления."""
    await callback.answer()
    user_id = callback.from_user.id if callback.from_user else 0
    try:
        active_insts = await api_client.get_installations(
            user_id, include_archived=False
        )
        kb = build_paginator_keyboard(
            items=active_insts,
            page=0,
            page_size=5,
            entity="ctrl_pick",
            item_callback_prefix="ctrl:target",
            item_text_func=lambda i: f"🌱 {i.get('name')} (#{i.get('id')})",
            item_id_func=lambda i: i.get("id"),
            include_all_button=True,
            all_button_callback="ctrl:target:all",
            all_button_text="🌐 Все активные установки",
        )
        if callback.message and isinstance(callback.message, Message):
            with contextlib.suppress(Exception):
                await callback.message.edit_text(
                    "💡 <b>Управление светом и реле</b>\n\nВыберите целевую установку или все активные:",
                    reply_markup=kb,
                )
    except Exception as exc:  # noqa: BLE001
        await callback.answer(f"Ошибка: {exc}", show_alert=True)


@router.callback_query(F.data.startswith("inst:control:"))
async def handle_control_direct_from_card(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Переход в управление реле прямо из карточки конкретной установки."""
    await callback.answer()
    parts = callback.data.split(":")
    inst_id = parts[2]
    user_id = callback.from_user.id if callback.from_user else 0

    inst_name = f"установкой #{inst_id}"
    with contextlib.suppress(Exception):
        inst = await api_client.get_installation(user_id, int(inst_id))
        inst_name = f"«{inst.get('name')}»"

    await _sync_pins_for_target(api_client, user_id, inst_id)
    light_st, flash_st = _get_target_relay_states(inst_id)
    kb = get_control_panel_keyboard(
        target=inst_id,
        light_state=light_st,
        flash_state=flash_st,
        **_get_target_rgb_kwargs(inst_id),
    )
    if callback.message and isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(
                f"💡 <b>Управление светом и реле</b> для {inst_name}:",
                reply_markup=kb,
            )


@router.callback_query(F.data.startswith("ctrl:target:"))
async def handle_control_target_selected(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
    state: FSMContext,
) -> None:
    """Выбор цели (all или ID) и показ панели кнопок света."""
    await callback.answer()
    await state.clear()
    parts = callback.data.split(":")
    target = parts[2]
    user_id = callback.from_user.id if callback.from_user else 0

    if target == "all":
        target_title = "всеми активными установками"
    else:
        target_title = f"установкой #{target}"
        with contextlib.suppress(Exception):
            inst = await api_client.get_installation(user_id, int(target))
            target_title = f"«{inst.get('name')}»"

    await _sync_pins_for_target(api_client, user_id, target)
    light_st, flash_st = _get_target_relay_states(target)
    kb = get_control_panel_keyboard(
        target=target,
        light_state=light_st,
        flash_state=flash_st,
        **_get_target_rgb_kwargs(target),
    )
    if callback.message and isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(
                f"💡 <b>Управление реле</b> для: {target_title}",
                reply_markup=kb,
            )


def _format_rgb_submenu_text(target: str, channel: str, note: str = "") -> str:
    meta = _target_meta.get(target, {})
    inst_name = html.escape(str(meta.get("name") or f"Установка #{target}"))
    light_st, flash_st = _get_target_relay_states(target)
    is_flash = channel == "flash"
    st = flash_st if is_flash else light_st
    color = meta.get("flash_color" if is_flash else "light_color", DEFAULT_COLOR_HEX)
    pins = meta.get("flash_gpio_pin" if is_flash else "light_gpio_pin", "?")
    title = "⚡ Вспышка RGB" if is_flash else "💡 Основной свет RGB"
    status_str = "🟢 ВКЛ" if st else "⚪️ ВЫКЛ"
    prefix = f"{note}\n\n" if note else ""
    return (
        f"{prefix}<b>{title}</b> — {inst_name}\n"
        f"Статус: <b>{status_str}</b>\n"
        f"Текущий цвет: <code>{html.escape(str(color))}</code>\n"
        f"GPIO-пины: <code>{html.escape(str(pins))}</code>"
    )


@router.callback_query(F.data.startswith("ctrl:rgb:"))
async def handle_control_rgb_submenu(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
    state: FSMContext,
) -> None:
    """Открывает подменю RGB-лампы конкретной установки."""
    await callback.answer()
    await state.clear()
    parts = callback.data.split(":")
    channel = parts[2]
    target = parts[3]
    user_id = callback.from_user.id if callback.from_user else 0

    await _sync_pins_for_target(api_client, user_id, target)
    light_st, flash_st = _get_target_relay_states(target)
    is_flash = channel == "flash"
    st = flash_st if is_flash else light_st
    meta = _target_meta.get(target, {})
    color = meta.get("flash_color" if is_flash else "light_color", DEFAULT_COLOR_HEX)

    kb = get_rgb_channel_menu_keyboard(target, channel, st, color)
    if callback.message and isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(
                _format_rgb_submenu_text(target, channel),
                reply_markup=kb,
            )


@router.callback_query(F.data.startswith("ctrl:rgb_toggle:"))
async def handle_control_rgb_toggle(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Вкл/Выкл RGB-лампы с сохранением запомненного HEX-цвета."""
    await callback.answer()
    parts = callback.data.split(":")
    channel = parts[2]
    state_bool = parts[3] in ("1", "on", "true")
    target = parts[4]
    is_flash = channel == "flash"
    user_id = callback.from_user.id if callback.from_user else 0

    try:
        resp = (
            await api_client.control_light_flash(user_id, target, state_bool)
            if is_flash
            else await api_client.control_light_main(user_id, target, state_bool)
        )
        _update_relay_pin_state(
            target,
            is_flash=is_flash,
            state_bool=state_bool,
            pin_states_from_api=resp.get("pin_states"),
        )
        if resp.get("color"):
            _target_meta.setdefault(target, {})[
                "flash_color" if is_flash else "light_color"
            ] = resp["color"]

        light_st, flash_st = _get_target_relay_states(target)
        st = flash_st if is_flash else light_st
        color = _target_meta.get(target, {}).get(
            "flash_color" if is_flash else "light_color", DEFAULT_COLOR_HEX
        )
        kb = get_rgb_channel_menu_keyboard(target, channel, st, color)
        res_msg = resp.get("result_message", "Готово")
        if callback.message and isinstance(callback.message, Message):
            await callback.message.edit_text(
                _format_rgb_submenu_text(target, channel, note=f"✅ {res_msg}"),
                reply_markup=kb,
            )
    except ApiError as exc:
        err = format_api_error(exc, is_control=True)
        light_st, flash_st = _get_target_relay_states(target)
        st = flash_st if is_flash else light_st
        color = _target_meta.get(target, {}).get(
            "flash_color" if is_flash else "light_color", DEFAULT_COLOR_HEX
        )
        kb = get_rgb_channel_menu_keyboard(target, channel, st, color)
        if callback.message and isinstance(callback.message, Message):
            await callback.message.edit_text(
                f"❌ Ошибка:\n{err}",
                reply_markup=kb,
            )


@router.callback_query(F.data.startswith("ctrl:rgb_hex:"))
async def handle_control_rgb_hex_start(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Переходит к вводу HEX-кода цвета RGB-лампы."""
    await callback.answer()
    parts = callback.data.split(":")
    channel = parts[2]
    target = parts[3]

    await state.set_state(ControlColorStates.waiting_hex)
    await state.update_data(target=target, channel=channel)

    ch_title = "вспышки" if channel == "flash" else "основного света"
    if callback.message and isinstance(callback.message, Message):
        await callback.message.edit_text(
            f"🎨 Введите HEX-цвет для {ch_title} в формате <code>#RRGGBB</code> "
            "(например: <code>#FF8800</code> или <code>00FFAA</code>):",
            reply_markup=get_cancel_inline_keyboard(f"ctrl:rgb:{channel}:{target}"),
        )


@router.message(ControlColorStates.waiting_hex)
async def process_control_rgb_hex_input(
    message: Message,
    state: FSMContext,
    api_client: GrowVisionApiClient,
) -> None:
    """Обрабатывает ввод HEX-цвета и применяет его через API."""
    data = await state.get_data()
    target = str(data.get("target", "1"))
    channel = str(data.get("channel", "light"))
    is_flash = channel == "flash"
    raw_hex = (message.text or "").strip()

    try:
        norm_hex = normalize_hex_color(raw_hex)
    except ValueError as exc:
        await message.answer(
            f"{exc}\nПовторите ввод в формате <code>#RRGGBB</code>:",
            reply_markup=get_cancel_inline_keyboard(f"ctrl:rgb:{channel}:{target}"),
        )
        return

    user_id = message.from_user.id if message.from_user else 0
    try:
        resp = (
            await api_client.control_light_flash(
                user_id, target, state=None, color=norm_hex
            )
            if is_flash
            else await api_client.control_light_main(
                user_id, target, state=None, color=norm_hex
            )
        )
        applied_hex = resp.get("color") or norm_hex
        _target_meta.setdefault(target, {})[
            "flash_color" if is_flash else "light_color"
        ] = applied_hex
        await state.clear()
        await _sync_pins_for_target(api_client, user_id, target)
        light_st, flash_st = _get_target_relay_states(target)
        st = flash_st if is_flash else light_st
        kb = get_rgb_channel_menu_keyboard(target, channel, st, applied_hex)
        await message.answer(
            _format_rgb_submenu_text(
                target,
                channel,
                note=f"🎨 Цвет обновлён: <code>{applied_hex}</code>",
            ),
            reply_markup=kb,
        )
    except ApiError as exc:
        err = format_api_error(exc, is_control=True)
        await message.answer(
            f"❌ Ошибка установки цвета:\n{err}",
            reply_markup=get_cancel_inline_keyboard(f"ctrl:rgb:{channel}:{target}"),
        )


@router.callback_query(F.data.startswith("ctrl:light:"))
async def handle_control_light_action(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Включение/выключение основного света."""
    await callback.answer()
    parts = callback.data.split(":")
    # Поддерживаем форматы ctrl:light:<1|0>:<target> и ctrl:light:<target>:<on|off>
    p1 = parts[2]
    p2 = parts[3] if len(parts) > 3 else "1"
    if p1.lower() in ("1", "0", "on", "off", "true", "false"):
        state_bool = p1.lower() in ("1", "on", "true")
        target = p2
    else:
        state_bool = p2.lower() in ("1", "on", "true")
        target = p1
    user_id = callback.from_user.id if callback.from_user else 0

    state_text = "включение" if state_bool else "выключение"
    if callback.message and isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(
                f"⏳ Выполняется {state_text} основного света..."
            )

    try:
        resp = await api_client.control_light_main(user_id, target, state_bool)
        _update_relay_pin_state(
            target,
            is_flash=False,
            state_bool=state_bool,
            pin_states_from_api=resp.get("pin_states"),
        )
        res_msg = resp.get("result_message", "Успешно")
        debug_info = resp.get("debug_info")
        debug_log = (
            debug_info.get("log_line")
            if isinstance(debug_info, dict)
            else resp.get("debug_log_line")
        )

        # Проверяем debug_mode
        sys_status = await api_client.get_system_status(user_id)
        debug_on = sys_status.get("settings", {}).get("debug_mode", False) or bool(
            debug_info
        )
        debug_str = (
            format_debug_block("POST /api/control/light/main", debug_log)
            if debug_on
            else ""
        )

        light_st, flash_st = _get_target_relay_states(target)
        kb = get_control_panel_keyboard(
            target=target,
            light_state=light_st,
            flash_state=flash_st,
            **_get_target_rgb_kwargs(target),
        )
        if callback.message and isinstance(callback.message, Message):
            await callback.message.edit_text(
                f"💡 <b>Результат:</b> {res_msg}{debug_str}",
                reply_markup=kb,
            )
    except ApiError as exc:
        err = format_api_error(exc, is_control=True)
        light_st, flash_st = _get_target_relay_states(target)
        kb = get_control_panel_keyboard(
            target=target,
            light_state=light_st,
            flash_state=flash_st,
            **_get_target_rgb_kwargs(target),
        )
        if callback.message and isinstance(callback.message, Message):
            await callback.message.edit_text(
                f"❌ Ошибка управления светом:\n{err}",
                reply_markup=kb,
            )


@router.callback_query(F.data.startswith("ctrl:flash:"))
async def handle_control_flash_action(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Включение/выключение вспышки."""
    await callback.answer()
    parts = callback.data.split(":")
    # Поддерживаем форматы ctrl:flash:<1|0>:<target> и ctrl:flash:<target>:<on|off>
    p1 = parts[2]
    p2 = parts[3] if len(parts) > 3 else "1"
    if p1.lower() in ("1", "0", "on", "off", "true", "false"):
        state_bool = p1.lower() in ("1", "on", "true")
        target = p2
    else:
        state_bool = p2.lower() in ("1", "on", "true")
        target = p1
    user_id = callback.from_user.id if callback.from_user else 0

    state_text = "включение" if state_bool else "выключение"
    if callback.message and isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(f"⏳ Выполняется {state_text} вспышки...")

    try:
        resp = await api_client.control_light_flash(user_id, target, state_bool)
        _update_relay_pin_state(
            target,
            is_flash=True,
            state_bool=state_bool,
            pin_states_from_api=resp.get("pin_states"),
        )
        res_msg = resp.get("result_message", "Успешно")
        debug_info = resp.get("debug_info")
        debug_log = (
            debug_info.get("log_line")
            if isinstance(debug_info, dict)
            else resp.get("debug_log_line")
        )

        sys_status = await api_client.get_system_status(user_id)
        debug_on = sys_status.get("settings", {}).get("debug_mode", False) or bool(
            debug_info
        )
        debug_str = (
            format_debug_block("POST /api/control/light/flash", debug_log)
            if debug_on
            else ""
        )

        light_st, flash_st = _get_target_relay_states(target)
        kb = get_control_panel_keyboard(
            target=target,
            light_state=light_st,
            flash_state=flash_st,
            **_get_target_rgb_kwargs(target),
        )
        if callback.message and isinstance(callback.message, Message):
            await callback.message.edit_text(
                f"⚡ <b>Результат:</b> {res_msg}{debug_str}",
                reply_markup=kb,
            )
    except ApiError as exc:
        err = format_api_error(exc, is_control=True)
        light_st, flash_st = _get_target_relay_states(target)
        kb = get_control_panel_keyboard(
            target=target,
            light_state=light_st,
            flash_state=flash_st,
            **_get_target_rgb_kwargs(target),
        )
        if callback.message and isinstance(callback.message, Message):
            await callback.message.edit_text(
                f"❌ Ошибка управления вспышкой:\n{err}",
                reply_markup=kb,
            )


@router.callback_query(F.data.startswith("ctrl:relay:"))
async def handle_control_relay_test(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Кратковременный тест реле (3 секунды)."""
    await callback.answer()
    parts = callback.data.split(":")
    target = parts[2]
    user_id = callback.from_user.id if callback.from_user else 0

    if callback.message and isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(
                "⏳ <b>Выполняется тест реле (3 сек)...</b>\nПины включены для проверки."
            )

    try:
        resp = await api_client.control_relay_test(user_id, target)
        res_msg = resp.get("result_message", "Тест реле завершен")
        debug_info = resp.get("debug_info")
        debug_log = (
            debug_info.get("log_line")
            if isinstance(debug_info, dict)
            else resp.get("debug_log_line")
        )

        sys_status = await api_client.get_system_status(user_id)
        debug_on = sys_status.get("settings", {}).get("debug_mode", False) or bool(
            debug_info
        )
        debug_str = (
            format_debug_block("POST /api/control/relay-test", debug_log)
            if debug_on
            else ""
        )

        light_st, flash_st = _get_target_relay_states(target)
        kb = get_control_panel_keyboard(
            target=target,
            light_state=light_st,
            flash_state=flash_st,
            **_get_target_rgb_kwargs(target),
        )
        if callback.message and isinstance(callback.message, Message):
            await callback.message.edit_text(
                f"🧪 <b>Результат теста:</b> {res_msg}{debug_str}",
                reply_markup=kb,
            )
    except ApiError as exc:
        err = format_api_error(exc, is_control=True)
        light_st, flash_st = _get_target_relay_states(target)
        kb = get_control_panel_keyboard(
            target=target,
            light_state=light_st,
            flash_state=flash_st,
            **_get_target_rgb_kwargs(target),
        )
        if callback.message and isinstance(callback.message, Message):
            await callback.message.edit_text(
                f"❌ Ошибка теста реле:\n{err}",
                reply_markup=kb,
            )


# ================== Ручной снимок (Biologist / Developer) ==================


@router.message(F.text == "📸 Сделать снимок")
async def handle_snap_menu(
    message: Message,
    api_client: GrowVisionApiClient,
) -> None:
    """Меню выбора цели для ручного снимка."""
    user_id = message.from_user.id if message.from_user else 0
    me = await api_client.get_me(user_id)
    if not me or me.get("role") not in ("biologist", "developer"):
        await message.answer("Съёмка доступна для Биолога и Разработчика.")
        return

    try:
        active_insts = await api_client.get_installations(
            user_id, include_archived=False
        )
        if not active_insts:
            await message.answer("Нет активных установок для съёмки.")
            return

        kb = build_paginator_keyboard(
            items=active_insts,
            page=0,
            page_size=5,
            entity="snap_pick",
            item_callback_prefix="snap:target",
            item_text_func=lambda i: f"🌱 {i.get('name')} (#{i.get('id')})",
            item_id_func=lambda i: i.get("id"),
            include_all_button=True,
            all_button_callback="snap:target:all",
            all_button_text="🌐 Снять все установки",
        )
        await message.answer(
            "📸 <b>Ручная фотосъёмка растений</b>\n\nВыберите целевую установку или снимите все сразу:",
            reply_markup=kb,
        )
    except Exception as exc:  # noqa: BLE001
        err = format_api_error(exc)
        await message.answer(f"Ошибка загрузки установок:\n{err}")


@router.callback_query(F.data.startswith("inst:snap:"))
async def handle_snap_from_card(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Быстрый снимок конкретной установки из её карточки."""
    await callback.answer()
    parts = callback.data.split(":")
    inst_id = parts[2]
    # Перенаправляем на логику snap
    await _execute_snap(callback, api_client, target=inst_id)


@router.callback_query(F.data.startswith("snap:target:"))
async def handle_snap_target_selected(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Выбор цели снимка из списка."""
    await callback.answer()
    parts = callback.data.split(":")
    target = parts[2]
    await _execute_snap(callback, api_client, target=target)


async def _execute_snap(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
    target: str,
) -> None:
    """Выполнение съёмки кадра, скачивание и отправка альбомом."""
    user_id = callback.from_user.id if callback.from_user else 0
    me = await api_client.get_me(user_id)
    role = me.get("role", "guest") if me else "guest"

    # Показываем индикатор ожидания
    status_msg: Message | None = None
    if callback.message and isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            status_msg = await callback.message.answer(
                "⏳ <b>Делаю снимок, подождите...</b>\n"
                "Выполняется переключение реле, прогрев камеры и детекция нейросетью."
            )

    try:
        active_insts = await api_client.get_installations(
            user_id, include_archived=False
        )
        active_map = {i.get("id"): i.get("name") for i in active_insts}
        active_count = len(active_insts)

        resp = await api_client.take_snap(
            user_id, target=target, active_count=active_count
        )

        status_result = resp.get("status", "done")
        result_message = resp.get("result_message", "")
        captures = resp.get("captures", [])
        debug_info = resp.get("debug_info")
        debug_log = (
            debug_info.get("log_line")
            if isinstance(debug_info, dict)
            else resp.get("debug_log_line")
        )

        if status_result == "error":
            # При ошибке съёмки выводим result_message
            if status_msg:
                await status_msg.edit_text(
                    f"❌ <b>Ошибка съёмки:</b> {result_message or 'Камера недоступна, попробуйте позже'}"
                )
            return

        # Удаляем индикатор ожидания
        if status_msg:
            with contextlib.suppress(Exception):
                await status_msg.delete()

        if not captures:
            target_chat = callback.message.chat.id if callback.message else user_id
            await callback.bot.send_message(
                target_chat,
                f"✅ Снимок выполнен, но кадры не были возвращены сервером: {result_message}",
            )
            return

        # Проверяем debug_mode для Developer
        debug_str = ""
        if role == "developer":
            with contextlib.suppress(Exception):
                sys_status = await api_client.get_system_status(user_id)
                if sys_status.get("settings", {}).get("debug_mode", False) or bool(
                    debug_info
                ):
                    debug_str = format_debug_block("POST /api/control/snap", debug_log)

        # Маппинг камер
        cam_map = {}
        with contextlib.suppress(Exception):
            cams = await api_client.get_cameras(user_id)
            cam_map = {
                c.get("id"): c.get("hardware_id") or c.get("auto_name") for c in cams
            }

        target_chat = callback.message.chat.id if callback.message else user_id

        # Отправляем каждый снимок альбомом из двух фото (оригинал + разметка)
        for idx, cap in enumerate(captures):
            cap_inst_id = cap.get("installation_id")
            inst_name = active_map.get(cap_inst_id, f"Установка #{cap_inst_id}")
            cid = cap.get("camera_id")
            cam_hw_id = (
                cap.get("camera_hardware_id")
                or cam_map.get(cid)
                or (f"камера #{cid}" if cid else "cam")
            )
            cap_time = cap.get("captured_at", "—")

            if cap.get("area_display"):
                area_text = cap.get("area_display")
            else:
                area_cm2 = cap.get("total_area_cm2")
                area_px = cap.get("total_area_px")
                if area_cm2 is not None:
                    area_text = format_area_display(area_cm2, is_px=False)
                elif area_px is not None:
                    area_text = format_area_display(area_px, is_px=True)
                else:
                    area_text = "площадь не определена"

            is_last = idx == len(captures) - 1
            caption_lines = [
                f"🌱 <b>Установка:</b> {html.escape(str(inst_name))}",
                f"📷 <b>Камера:</b> <code>{html.escape(str(cam_hw_id))}</code>",
                f"🕒 <b>Время съёмки:</b> {cap_time} UTC",
                f"📐 <b>Площадь:</b> {area_text}",
            ]

            if is_last:
                caption_lines.append("\n<i>(Ручные снимки не входят в таймлапс)</i>")
                if debug_str:
                    caption_lines.append(debug_str)

            caption = "\n".join(caption_lines)

            # Скачиваем сырой кадр
            raw_url = cap.get("raw_url", "")
            raw_bytes = await api_client.download_file(user_id, raw_url)

            if role == "developer":
                # Разработчик получает фото с inline-кнопкой переключения «Исходник / Разметка»
                cap_id = (
                    cap.get("snapshot_id")
                    or cap.get("capture_id")
                    or cap.get("id")
                    or cap_inst_id
                    or 1
                )
                snap_kb = get_snap_layer_keyboard(cap_id, current_layer="raw")
                await callback.bot.send_photo(
                    chat_id=target_chat,
                    photo=BufferedInputFile(raw_bytes, filename=f"raw_{idx}.jpg"),
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=snap_kb,
                )
            else:
                # Биолог получает только raw-снимок без разметки и без кнопок переключения
                await callback.bot.send_photo(
                    chat_id=target_chat,
                    photo=BufferedInputFile(raw_bytes, filename=f"raw_{idx}.jpg"),
                    caption=caption,
                    parse_mode="HTML",
                )

    except ApiError as exc:
        err = format_api_error(exc, is_control=True)
        if status_msg:
            await status_msg.edit_text(f"❌ Не удалось выполнить снимок:\n{err}")
        elif callback.message and isinstance(callback.message, Message):
            await callback.message.answer(f"❌ Не удалось выполнить снимок:\n{err}")
    except Exception as exc:  # noqa: BLE001
        err = format_api_error(exc, is_control=True)
        if status_msg:
            await status_msg.edit_text(f"❌ Не удалось выполнить снимок:\n{err}")
        elif callback.message and isinstance(callback.message, Message):
            await callback.message.answer(f"❌ Не удалось выполнить снимок:\n{err}")


@router.callback_query(F.data.startswith("snap:layer:"))
async def handle_toggle_snap_layer(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Переключение между сырым кадром и разметкой на сообщении со снимком (Developer)."""
    parts = callback.data.split(":")
    if len(parts) < 4:
        await callback.answer()
        return
    cap_id = parts[2]
    target_layer = parts[3]  # "raw" или "ann"
    user_id = callback.from_user.id if callback.from_user else 0

    try:
        cap_info = await api_client.get_capture(user_id, cap_id)
        url_field = "annotated_url" if target_layer == "ann" else "raw_url"
        img_url = cap_info.get(url_field) or cap_info.get("raw_url", "")
        img_bytes = await api_client.download_file(user_id, img_url)

        snap_kb = get_snap_layer_keyboard(cap_id, current_layer=target_layer)
        if callback.message and isinstance(callback.message, Message):
            caption = callback.message.caption or ""
            media = InputMediaPhoto(
                media=BufferedInputFile(
                    img_bytes, filename=f"snap_{target_layer}_{cap_id}.jpg"
                ),
                caption=caption,
                parse_mode="HTML",
            )
            await callback.message.edit_media(media=media, reply_markup=snap_kb)
        await callback.answer()
    except ApiError as exc:
        logger.warning(f"Ошибка переключения слоя снимка: {exc}")
        if exc.status_code == 404:
            await callback.answer(
                "Тестовый снимок устарел (прошло более 10 минут). Сделайте новый снимок.",
                show_alert=True,
            )
        else:
            await callback.answer(
                f"Не удалось переключить слой: {exc}", show_alert=True
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Ошибка переключения слоя снимка: {exc}")
        await callback.answer(f"Не удалось переключить слой: {exc}", show_alert=True)
