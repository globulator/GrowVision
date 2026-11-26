from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message

from clients.telegram_bot.handlers.climate import (
    handle_climate_manual_control,
    handle_climate_menu,
    handle_climate_mode_switch,
    handle_climate_ph_start,
    handle_climate_schedule_start,
    process_climate_ph_input,
    process_climate_schedule_input,
)
from clients.telegram_bot.states import ClimateStates
from clients.telegram_bot.utils.errors import ApiError


def _make_summary(mode: str = "manual", water_state: str = "off") -> dict:
    return {
        "esp_online": True,
        "esp_last_seen": "2026-11-26T10:00:00Z",
        "mode": mode,
        "temperature": 23.8,
        "humidity": 60.5,
        "ph": 6.1,
        "water_state": water_state,
        "fan_state": "off",
        "water_pending": False,
        "fan_pending": False,
        "schedule": {
            "water_interval_hours": 10,
            "water_duration_minutes": 1,
            "fan_interval_hours": 5,
            "fan_duration_minutes": 1,
        },
    }


def _make_fsm() -> FSMContext:
    storage = MemoryStorage()
    return FSMContext(
        storage=storage,
        key=StorageKey(bot_id=1, chat_id=100, user_id=42),
    )


def _make_message(text: str = "💧 Климат и полив") -> MagicMock:
    msg = MagicMock(spec=Message)
    msg.text = text
    msg.from_user = MagicMock(id=42)
    msg.answer = AsyncMock()
    msg.edit_text = AsyncMock()
    return msg


def _make_callback(data: str) -> MagicMock:
    cb = MagicMock()
    cb.data = data
    cb.from_user = MagicMock(id=42)
    cb.answer = AsyncMock()
    cb.message = _make_message()
    return cb


@pytest.mark.asyncio
async def test_bot_climate_menu_guest_and_biologist():
    state = _make_fsm()
    api = MagicMock()
    api.get_climate_summary = AsyncMock(return_value=_make_summary())

    # Гость видит данные, но без кнопок управления
    api.get_me = AsyncMock(return_value=None)
    msg_guest = _make_message()
    await handle_climate_menu(msg_guest, api, state)
    guest_text = msg_guest.answer.call_args[0][0]
    guest_kb = msg_guest.answer.call_args[1]["reply_markup"]
    guest_callbacks = [b.callback_data for row in guest_kb.inline_keyboard for b in row]
    assert "23.8 °C" in guest_text
    assert "clm:refresh" in guest_callbacks
    assert "clm:ctrl:water:on" not in guest_callbacks

    # Биолог видит кнопки режима, полива, обдува, расписания и pH
    api.get_me = AsyncMock(return_value={"role": "biologist"})
    msg_bio = _make_message()
    await handle_climate_menu(msg_bio, api, state)
    bio_kb = msg_bio.answer.call_args[1]["reply_markup"]
    bio_callbacks = [b.callback_data for row in bio_kb.inline_keyboard for b in row]
    assert "clm:mode:auto" in bio_callbacks
    assert "clm:ctrl:water:on" in bio_callbacks
    assert "clm:sched:water" in bio_callbacks
    assert "clm:ph" in bio_callbacks


@pytest.mark.asyncio
async def test_bot_climate_mode_and_manual_control():
    api = MagicMock()
    api.get_me = AsyncMock(return_value={"role": "developer"})
    api.set_climate_mode = AsyncMock(return_value=_make_summary(mode="auto"))

    cb_mode = _make_callback("clm:mode:auto")
    await handle_climate_mode_switch(cb_mode, api)
    api.set_climate_mode.assert_awaited_once_with(42, "auto")
    assert "автоматический режим" in cb_mode.message.edit_text.call_args[0][0].lower()

    # Ручное управление: ошибка 409 корректно отображается
    api.control_climate = AsyncMock(
        side_effect=ApiError(status_code=409, detail="Заблокировано в авторежиме")
    )
    api.get_climate_summary = AsyncMock(return_value=_make_summary(mode="auto"))
    cb_ctrl = _make_callback("clm:ctrl:water:on")
    await handle_climate_manual_control(cb_ctrl, api)
    assert "Заблокировано" in cb_ctrl.message.edit_text.call_args[0][0]


@pytest.mark.asyncio
async def test_bot_climate_schedule_and_ph_fsm():
    state = _make_fsm()
    api = MagicMock()
    api.get_me = AsyncMock(return_value={"role": "biologist"})
    api.update_climate_schedule = AsyncMock(return_value=_make_summary())
    api.add_ph_reading = AsyncMock(return_value={"status": "ok", "level": 6.3})
    api.get_climate_summary = AsyncMock(return_value=_make_summary())

    # Старт расписания полива -> невалидный ввод -> валидный ввод
    cb_sched = _make_callback("clm:sched:water")
    await handle_climate_schedule_start(cb_sched, api, state)
    assert await state.get_state() == ClimateStates.waiting_water_schedule.state

    bad_msg = _make_message("0 -1")
    await process_climate_schedule_input(bad_msg, api, state)
    assert await state.get_state() == ClimateStates.waiting_water_schedule.state

    ok_msg = _make_message("6 2")
    await process_climate_schedule_input(ok_msg, api, state)
    api.update_climate_schedule.assert_awaited_once_with(
        42, water_interval_hours=6, water_duration_minutes=2
    )
    assert await state.get_state() is None

    # Ввод pH -> вне диапазона -> валидный ввод с запятой
    cb_ph = _make_callback("clm:ph")
    await handle_climate_ph_start(cb_ph, api, state)
    assert await state.get_state() == ClimateStates.waiting_ph.state

    bad_ph = _make_message("15.5")
    await process_climate_ph_input(bad_ph, api, state)
    assert await state.get_state() == ClimateStates.waiting_ph.state

    ok_ph = _make_message("6,3")
    await process_climate_ph_input(ok_ph, api, state)
    api.add_ph_reading.assert_awaited_once_with(42, 6.3)
    assert await state.get_state() is None
