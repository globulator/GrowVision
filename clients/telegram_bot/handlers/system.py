import contextlib
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from clients.telegram_bot.api_client import GrowVisionApiClient
from clients.telegram_bot.keyboards.inline import get_quick_interval_keyboard
from clients.telegram_bot.states import SystemSettingsStates
from clients.telegram_bot.utils.errors import ApiError, format_api_error
from clients.telegram_bot.utils.formatters import format_system_status

logger = logging.getLogger(__name__)

router = Router(name="system")


def get_system_status_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для сводки статуса системы."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Обновить", callback_data="sys:refresh"),
                InlineKeyboardButton(
                    text="⏱ Изменить интервал съёмки",
                    callback_data="sys:edit_interval",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🐞 Вкл/Выкл Debug-режим",
                    callback_data="sys:toggle_debug",
                )
            ],
        ]
    )


@router.message(F.text == "⚙️ Система")
async def handle_system_menu(
    message: Message,
    api_client: GrowVisionApiClient,
) -> None:
    """Точка входа: статус системы (только Developer)."""
    user_id = message.from_user.id if message.from_user else 0
    me = await api_client.get_me(user_id)
    if not me or me.get("role") != "developer":
        await message.answer("Системный раздел доступен только для Разработчика.")
        return

    try:
        status_data = await api_client.get_system_status(user_id)
        text = format_system_status(status_data)
        await message.answer(text, reply_markup=get_system_status_keyboard())
    except Exception as exc:  # noqa: BLE001
        err = format_api_error(exc)
        await message.answer(f"Не удалось получить статус системы:\n{err}")


@router.callback_query(F.data == "sys:refresh")
@router.callback_query(F.data == "sys:status")
async def handle_system_refresh(
    callback: CallbackQuery,
    state: FSMContext,
    api_client: GrowVisionApiClient,
) -> None:
    """Обновление статуса системы."""
    await callback.answer()
    await state.clear()
    user_id = callback.from_user.id if callback.from_user else 0

    try:
        status_data = await api_client.get_system_status(user_id)
        text = format_system_status(status_data)
        if callback.message and isinstance(callback.message, Message):
            with contextlib.suppress(Exception):
                await callback.message.edit_text(
                    text, reply_markup=get_system_status_keyboard()
                )
    except Exception as exc:  # noqa: BLE001
        await callback.answer(f"Ошибка: {format_api_error(exc)}", show_alert=True)


@router.callback_query(F.data == "sys:edit_interval")
async def handle_edit_interval_start(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Начало настройки интервала автосъёмки."""
    await callback.answer()
    await state.set_state(SystemSettingsStates.waiting_interval)

    text = (
        "⏱ <b>Настройка интервала автосъёмки</b>\n\n"
        "Выберите интервал кнопкой ниже или введите целое число часов (≥ 1):"
    )
    if callback.message and isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(
                text, reply_markup=get_quick_interval_keyboard()
            )


@router.callback_query(F.data.startswith("sys:set_int:"))
async def handle_quick_interval_selected(
    callback: CallbackQuery,
    state: FSMContext,
    api_client: GrowVisionApiClient,
) -> None:
    """Выбор интервала по быстрой кнопке."""
    await callback.answer()
    parts = callback.data.split(":")
    hours = int(parts[2])
    user_id = callback.from_user.id if callback.from_user else 0
    await _apply_interval(callback.message, state, api_client, hours, user_id=user_id)


@router.message(SystemSettingsStates.waiting_interval)
async def handle_interval_text_input(
    message: Message,
    state: FSMContext,
    api_client: GrowVisionApiClient,
) -> None:
    """Ввод интервала числом в часах."""
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) < 1:
        await message.answer(
            "Интервал должен быть целым числом часов ≥ 1 (например, 2 или 6). Повторите ввод:"
        )
        return
    hours = int(raw)
    user_id = message.from_user.id if message.from_user else 0
    await _apply_interval(message, state, api_client, hours, user_id=user_id)


async def _apply_interval(
    target_msg: Message | None,
    state: FSMContext,
    api_client: GrowVisionApiClient,
    hours: int,
    user_id: int,
) -> None:
    """Применяет новый интервал через API и выводит предупреждение при наличии."""
    await state.clear()

    try:
        resp = await api_client.update_system_settings(
            user_id, capture_interval_hours=hours
        )
        warning_msg = resp.get("warning")
        warning_str = (
            f"\n\n⚠️ <i>Предупреждение: {warning_msg}</i>" if warning_msg else ""
        )

        status_data = await api_client.get_system_status(user_id)
        status_text = format_system_status(status_data)

        full_text = (
            f"✅ Интервал автосъёмки успешно изменён на <b>{hours} ч</b>.{warning_str}\n\n"
            f"{status_text}"
        )
        if target_msg:
            await target_msg.answer(
                full_text, reply_markup=get_system_status_keyboard()
            )
    except ApiError as exc:
        err = format_api_error(exc)
        if target_msg:
            await target_msg.answer(
                f"❌ Не удалось изменить интервал:\n{err}",
                reply_markup=get_system_status_keyboard(),
            )


@router.callback_query(F.data == "sys:toggle_debug")
async def handle_toggle_debug(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Переключение режима отладки (debug_mode)."""
    await callback.answer()
    user_id = callback.from_user.id if callback.from_user else 0

    try:
        cur_status = await api_client.get_system_status(user_id)
        cur_debug = cur_status.get("settings", {}).get("debug_mode", False)
        new_debug = not cur_debug

        await api_client.update_system_settings(user_id, debug_mode=new_debug)

        status_data = await api_client.get_system_status(user_id)
        status_text = format_system_status(status_data)
        mode_str = "включен 🐞" if new_debug else "выключен"

        if callback.message and isinstance(callback.message, Message):
            with contextlib.suppress(Exception):
                await callback.message.edit_text(
                    f"ℹ️ Debug-режим <b>{mode_str}</b>.\n\n{status_text}",
                    reply_markup=get_system_status_keyboard(),
                )
    except ApiError as exc:
        await callback.answer(f"Ошибка: {format_api_error(exc)}", show_alert=True)
