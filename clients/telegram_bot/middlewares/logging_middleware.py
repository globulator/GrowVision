import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, TelegramObject

from clients.telegram_bot.utils.debug_logger import get_debug_logger


class DebugLoggingMiddleware(BaseMiddleware):
    """Aiogram Middleware для фиксации всех действий пользователя в DebugLogger."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        debug_logger = get_debug_logger()
        state: FSMContext | None = data.get("state")
        current_state = await state.get_state() if state else None

        api_client = data.get("api_client")
        from_user = getattr(event, "from_user", None)
        if api_client and from_user and from_user.id:
            with contextlib.suppress(Exception):
                asyncio.create_task(
                    api_client.sync_telegram_contact(
                        telegram_id=from_user.id,
                        username=from_user.username,
                        first_name=from_user.first_name,
                        last_name=from_user.last_name,
                    )
                )

        if isinstance(event, Message):
            user_id = event.from_user.id if event.from_user else 0
            text = (
                "***"
                if current_state and "password" in current_state.lower()
                else (event.text or event.caption or "")
            )
            await debug_logger.alog_event(
                "incoming_message",
                user_id=user_id,
                state=current_state,
                text=text,
                message_id=event.message_id,
            )
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id if event.from_user else 0
            await debug_logger.alog_event(
                "callback_query",
                user_id=user_id,
                state=current_state,
                data=event.data,
            )

        try:
            return await handler(event, data)
        except Exception as exc:
            err_text = (
                "***"
                if current_state and "password" in current_state.lower()
                else str(exc)
            )
            await debug_logger.alog_event(
                "handler_error",
                state=current_state,
                error=err_text,
                error_type=type(exc).__name__,
            )
            raise
