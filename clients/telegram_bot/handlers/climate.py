import contextlib
import html
import math
import re
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from clients.telegram_bot.api_client import GrowVisionApiClient
from clients.telegram_bot.keyboards.inline import (
    get_cancel_inline_keyboard,
    get_climate_menu_keyboard,
)
from clients.telegram_bot.states import ClimateStates
from clients.telegram_bot.utils.errors import ApiError, format_api_error

router = Router(name="climate")


def _can_control_role(me: dict[str, Any] | None) -> bool:
    return bool(me and me.get("role") in ("biologist", "developer"))


def format_climate_summary_text(
    summary: dict[str, Any],
    *,
    can_control: bool,
    note: str = "",
) -> str:
    """Форматирует карточку состояния ESP, датчиков, полива, вентиляции и расписания."""
    esp_status = "🟢 Онлайн" if summary.get("esp_online") else "🔴 Офлайн"
    mode = "auto" if summary.get("mode") == "auto" else "manual"
    mode_label = "🤖 Авто (по расписанию)" if mode == "auto" else "🖐 Ручной"

    temp = summary.get("temperature")
    hum = summary.get("humidity")
    ph = summary.get("ph")
    temp_str = f"{float(temp):.1f} °C" if temp is not None else "—"
    hum_str = f"{float(hum):.1f} %" if hum is not None else "—"
    ph_str = f"{float(ph):.2f}" if ph is not None else "—"

    water_str = "🟢 ВКЛ" if summary.get("water_state") == "on" else "⚪️ ВЫКЛ"
    fan_str = "🟢 ВКЛ" if summary.get("fan_state") == "on" else "⚪️ ВЫКЛ"
    if summary.get("water_pending"):
        water_str += " (ожидает ESP)"
    if summary.get("fan_pending"):
        fan_str += " (ожидает ESP)"

    sched = summary.get("schedule") or {}
    wi = sched.get("water_interval_hours", 10)
    wd = sched.get("water_duration_minutes", 1)
    fi = sched.get("fan_interval_hours", 5)
    fd = sched.get("fan_duration_minutes", 1)

    lines = []
    if note:
        lines.append(f"{note}\n")
    lines.extend(
        [
            "💧 <b>Климат и полив (ESP)</b>",
            f"Статус ESP: <b>{esp_status}</b>",
            f"Режим: <b>{mode_label}</b>",
            "",
            f"🌡 Температура: <b>{html.escape(temp_str)}</b>",
            f"💦 Влажность: <b>{html.escape(hum_str)}</b>",
            f"🧪 pH раствора: <b>{html.escape(ph_str)}</b>",
            "",
            f"💧 Полив: <b>{water_str}</b>",
            f"🌬 Вентиляция: <b>{fan_str}</b>",
            "",
            "⏱ <b>Расписание (Авто):</b>",
            f"• Полив: каждые <code>{wi} ч</code> по <code>{wd} мин</code>",
            f"• Обдув: каждые <code>{fi} ч</code> по <code>{fd} мин</code>",
        ]
    )
    if mode == "auto" and can_control:
        lines.append("\n<i>🔒 В авторежиме ручные тумблеры заблокированы.</i>")
    elif not can_control:
        lines.append("\n<i>Войдите как Биолог или Разработчик для управления.</i>")
    return "\n".join(lines)


@router.message(F.text == "💧 Климат и полив")
async def handle_climate_menu(
    message: Message,
    api_client: GrowVisionApiClient,
    state: FSMContext,
) -> None:
    """Открывает раздел «Климат и полив»."""
    await state.clear()
    user_id = message.from_user.id if message.from_user else 0
    try:
        me = await api_client.get_me(user_id)
        can_control = _can_control_role(me)
        summary = await api_client.get_climate_summary(user_id)
        kb = get_climate_menu_keyboard(summary, can_control=can_control)
        await message.answer(
            format_climate_summary_text(summary, can_control=can_control),
            reply_markup=kb,
        )
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"❌ Ошибка загрузки климата:\n{format_api_error(exc)}")


@router.callback_query(F.data.in_({"clm:refresh", "clm:cancel"}))
async def handle_climate_refresh(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
    state: FSMContext,
) -> None:
    """Обновляет карточку климата (и сбрасывает FSM при отмене)."""
    await callback.answer()
    await state.clear()
    user_id = callback.from_user.id if callback.from_user else 0
    try:
        me = await api_client.get_me(user_id)
        can_control = _can_control_role(me)
        summary = await api_client.get_climate_summary(user_id)
        kb = get_climate_menu_keyboard(summary, can_control=can_control)
        if callback.message and isinstance(callback.message, Message):
            with contextlib.suppress(Exception):
                await callback.message.edit_text(
                    format_climate_summary_text(summary, can_control=can_control),
                    reply_markup=kb,
                )
    except Exception as exc:  # noqa: BLE001
        if callback.message and isinstance(callback.message, Message):
            await callback.message.edit_text(
                f"❌ Ошибка обновления:\n{format_api_error(exc)}"
            )


@router.callback_query(F.data.startswith("clm:mode:"))
async def handle_climate_mode_switch(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Переключает режим Авто / Ручной."""
    user_id = callback.from_user.id if callback.from_user else 0
    mode = callback.data.split(":")[2]
    me = await api_client.get_me(user_id)
    if not _can_control_role(me):
        await callback.answer(
            "Управление доступно Биологу и Разработчику.", show_alert=True
        )
        return

    await callback.answer()
    try:
        summary = await api_client.set_climate_mode(user_id, mode)
        note = (
            "✅ Включён автоматический режим"
            if mode == "auto"
            else "✅ Включён ручной режим"
        )
        kb = get_climate_menu_keyboard(summary, can_control=True)
        if callback.message and isinstance(callback.message, Message):
            await callback.message.edit_text(
                format_climate_summary_text(summary, can_control=True, note=note),
                reply_markup=kb,
            )
    except ApiError as exc:
        if callback.message and isinstance(callback.message, Message):
            await callback.message.edit_text(
                f"❌ Ошибка смены режима:\n{format_api_error(exc)}"
            )


@router.callback_query(F.data.startswith("clm:ctrl:"))
async def handle_climate_manual_control(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Ручное переключение полива или вентиляции."""
    user_id = callback.from_user.id if callback.from_user else 0
    parts = callback.data.split(":")
    device, action = parts[2], parts[3]
    me = await api_client.get_me(user_id)
    if not _can_control_role(me):
        await callback.answer(
            "Управление доступно Биологу и Разработчику.", show_alert=True
        )
        return

    await callback.answer()
    try:
        await api_client.control_climate(user_id, device, action)
        summary = await api_client.get_climate_summary(user_id)
        dev_name = "Полив" if device == "water" else "Вентиляция"
        act_name = "ВКЛ" if action == "on" else "ВЫКЛ"
        note = f"✅ {dev_name}: команда «{act_name}» отправлена на ESP"
        kb = get_climate_menu_keyboard(summary, can_control=True)
        if callback.message and isinstance(callback.message, Message):
            await callback.message.edit_text(
                format_climate_summary_text(summary, can_control=True, note=note),
                reply_markup=kb,
            )
    except ApiError as exc:
        summary = await api_client.get_climate_summary(user_id)
        kb = get_climate_menu_keyboard(summary, can_control=True)
        if callback.message and isinstance(callback.message, Message):
            await callback.message.edit_text(
                format_climate_summary_text(
                    summary,
                    can_control=True,
                    note=f"❌ {format_api_error(exc, is_control=True)}",
                ),
                reply_markup=kb,
            )


@router.callback_query(F.data.startswith("clm:sched:"))
async def handle_climate_schedule_start(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
    state: FSMContext,
) -> None:
    """Запускает FSM-ввод расписания для полива или обдува."""
    user_id = callback.from_user.id if callback.from_user else 0
    me = await api_client.get_me(user_id)
    if not _can_control_role(me):
        await callback.answer(
            "Управление доступно Биологу и Разработчику.", show_alert=True
        )
        return

    await callback.answer()
    device = callback.data.split(":")[2]
    if device == "water":
        await state.set_state(ClimateStates.waiting_water_schedule)
        label = "полива"
        example = "10 1"
    else:
        await state.set_state(ClimateStates.waiting_fan_schedule)
        label = "вентиляции"
        example = "5 2"

    if callback.message and isinstance(callback.message, Message):
        await callback.message.edit_text(
            f"⏱ Введите два целых числа (>= 1) через пробел для расписания <b>{label}</b>:\n"
            f"<code>интервал_часы длительность_минуты</code>\n\n"
            f"Например: <code>{example}</code>",
            reply_markup=get_cancel_inline_keyboard("clm:cancel"),
        )


def _parse_schedule_pair(raw: str) -> tuple[int, int]:
    tokens = [t for t in re.split(r"[\s,;/]+", raw.strip()) if t]
    if len(tokens) != 2:
        raise ValueError("Нужно ввести ровно два целых числа через пробел.")
    try:
        interval_h = int(tokens[0])
        duration_m = int(tokens[1])
    except ValueError as exc:
        raise ValueError("Оба значения должны быть целыми числами.") from exc
    if interval_h < 1 or duration_m < 1:
        raise ValueError("Интервал (ч) и длительность (мин) должны быть >= 1.")
    return interval_h, duration_m


@router.message(ClimateStates.waiting_water_schedule)
@router.message(ClimateStates.waiting_fan_schedule)
async def process_climate_schedule_input(
    message: Message,
    api_client: GrowVisionApiClient,
    state: FSMContext,
) -> None:
    """Сохраняет новое расписание полива или вентиляции."""
    cur_state = await state.get_state()
    is_water = cur_state == ClimateStates.waiting_water_schedule.state
    raw = (message.text or "").strip()

    try:
        interval_h, duration_m = _parse_schedule_pair(raw)
    except ValueError as exc:
        await message.answer(
            f"❌ {exc}\nПовторите ввод (например: <code>6 2</code>):",
            reply_markup=get_cancel_inline_keyboard("clm:cancel"),
        )
        return

    user_id = message.from_user.id if message.from_user else 0
    try:
        if is_water:
            summary = await api_client.update_climate_schedule(
                user_id,
                water_interval_hours=interval_h,
                water_duration_minutes=duration_m,
            )
            note = f"✅ Расписание полива: каждые {interval_h} ч по {duration_m} мин"
        else:
            summary = await api_client.update_climate_schedule(
                user_id,
                fan_interval_hours=interval_h,
                fan_duration_minutes=duration_m,
            )
            note = f"✅ Расписание обдува: каждые {interval_h} ч по {duration_m} мин"

        await state.clear()
        kb = get_climate_menu_keyboard(summary, can_control=True)
        await message.answer(
            format_climate_summary_text(summary, can_control=True, note=note),
            reply_markup=kb,
        )
    except ApiError as exc:
        await message.answer(
            f"❌ Ошибка сохранения расписания:\n{format_api_error(exc)}",
            reply_markup=get_cancel_inline_keyboard("clm:cancel"),
        )


@router.callback_query(F.data == "clm:ph")
async def handle_climate_ph_start(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
    state: FSMContext,
) -> None:
    """Запускает FSM-ввод уровня pH."""
    user_id = callback.from_user.id if callback.from_user else 0
    me = await api_client.get_me(user_id)
    if not _can_control_role(me):
        await callback.answer(
            "Управление доступно Биологу и Разработчику.", show_alert=True
        )
        return

    await callback.answer()
    await state.set_state(ClimateStates.waiting_ph)
    if callback.message and isinstance(callback.message, Message):
        await callback.message.edit_text(
            "🧪 Введите измеренный уровень <b>pH</b> от <code>0</code> до <code>14</code> "
            "(например: <code>6.2</code>):",
            reply_markup=get_cancel_inline_keyboard("clm:cancel"),
        )


@router.message(ClimateStates.waiting_ph)
async def process_climate_ph_input(
    message: Message,
    api_client: GrowVisionApiClient,
    state: FSMContext,
) -> None:
    """Валидирует и сохраняет замер pH."""
    raw = (message.text or "").strip().replace(",", ".")
    try:
        val = float(raw)
        if not math.isfinite(val) or val < 0.0 or val > 14.0:
            raise ValueError
    except ValueError:
        await message.answer(
            "❌ Введите число в диапазоне от 0 до 14 (например: <code>6.2</code>):",
            reply_markup=get_cancel_inline_keyboard("clm:cancel"),
        )
        return

    user_id = message.from_user.id if message.from_user else 0
    try:
        await api_client.add_ph_reading(user_id, val)
        summary = await api_client.get_climate_summary(user_id)
        await state.clear()
        kb = get_climate_menu_keyboard(summary, can_control=True)
        await message.answer(
            format_climate_summary_text(
                summary,
                can_control=True,
                note=f"✅ Замер pH ({val:.2f}) сохранён",
            ),
            reply_markup=kb,
        )
    except ApiError as exc:
        await message.answer(
            f"❌ Ошибка сохранения pH:\n{format_api_error(exc)}",
            reply_markup=get_cancel_inline_keyboard("clm:cancel"),
        )
