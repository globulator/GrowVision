import logging

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from clients.telegram_bot.utils.debug_logger import get_debug_logger

logger = logging.getLogger(__name__)

router = Router(name="common")
bug_router = Router(name="bug_reports")
fallback_router = Router(name="fallback")


@bug_router.message(F.text.startswith("*"))
async def handle_bug_note(message: Message, state: FSMContext) -> None:
    """Обработка заметок о багах через префикс * в любом FSM-состоянии без сброса FSM."""
    user_id = message.from_user.id if message.from_user else 0
    current_state = await state.get_state()
    note_text = (message.text or "").strip()

    debug_logger = get_debug_logger()
    bug_id = await debug_logger.arecord_bug(
        note=note_text, user_id=user_id, state=current_state
    )

    logger.info(f"Зафиксирована баг-заметка BUG-LOG-{bug_id} от пользователя {user_id}")
    await message.answer(f"Записано: BUG-LOG-{bug_id}")


# ================== Заглушки пагинации и служебных кнопок (Критерий 1) ==================


@router.callback_query(F.data.in_({"pag:noop", "frm:noop"}))
async def handle_noop_callbacks(callback: CallbackQuery) -> None:
    """Явный ответ на клики по служебным кнопкам без действия (Критерий 1)."""
    await callback.answer()


@fallback_router.message(StateFilter(None), F.text)
async def handle_unknown_message(message: Message) -> None:
    """Ответ на произвольный неизвестный текст вне активного FSM-состояния."""
    await message.answer("Не понял. Список команд: /help")
