import contextlib
import html
import logging
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from clients.telegram_bot.api_client import GrowVisionApiClient
from clients.telegram_bot.keyboards.inline import get_cancel_inline_keyboard
from clients.telegram_bot.states import SessionStates
from clients.telegram_bot.utils.errors import format_api_error
from clients.telegram_bot.utils.formatters import format_tg_user_link

logger = logging.getLogger(__name__)

router = Router(name="sessions")


def get_sessions_main_keyboard() -> InlineKeyboardMarkup:
    """Главная клавиатура раздела Сессии."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎟 Создать код для биолога",
                    callback_data="sess:invite_create",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📋 Активные коды",
                    callback_data="sess:invite_list",
                ),
                InlineKeyboardButton(
                    text="💻 Список сессий",
                    callback_data="sess:list",
                ),
            ],
        ]
    )


@router.message(Command("sessions"))
@router.message(F.text == "👥 Сессии")
async def handle_sessions_menu(
    message: Message,
    api_client: GrowVisionApiClient,
) -> None:
    """Точка входа в раздел Сессии (только Developer)."""
    user_id = message.from_user.id if message.from_user else 0
    me = await api_client.get_me(user_id)
    if not me or me.get("role") != "developer":
        await message.answer(
            "Раздел управления сессиями доступен только для Разработчика."
        )
        return

    text = (
        "👥 <b>Управление сессиями и доступом</b>\n\n"
        "Здесь вы можете генерировать одноразовые коды доступа для биологов, "
        "просматривать активные и завершённые сессии, а также принудительно "
        "завершать доступ пользователей."
    )
    await message.answer(text, reply_markup=get_sessions_main_keyboard())


@router.callback_query(F.data == "sess:menu")
async def handle_sessions_menu_callback(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Возврат в главное меню раздела сессий."""
    await callback.answer()
    user_id = callback.from_user.id if callback.from_user else 0
    me = await api_client.get_me(user_id)
    if not me or me.get("role") != "developer":
        await callback.message.answer("Раздел доступен только для Разработчика.")
        return

    text = (
        "👥 <b>Управление сессиями и доступом</b>\n\n"
        "Здесь вы можете генерировать одноразовые коды доступа для биологов, "
        "просматривать активные и завершённые сессии, а также принудительно "
        "завершать доступ пользователей."
    )
    if callback.message and isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(
                text, reply_markup=get_sessions_main_keyboard()
            )


@router.callback_query(F.data == "sess:invite_create")
async def handle_create_invite(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Генерация одноразового кода для биолога."""
    await callback.answer()
    user_id = callback.from_user.id if callback.from_user else 0

    try:
        res = await api_client.create_invite(user_id, role="biologist")
    except Exception as exc:  # noqa: BLE001
        err = format_api_error(exc)
        if callback.message and isinstance(callback.message, Message):
            await callback.message.answer(f"Не удалось сгенерировать код:\n{err}")
        return

    code = res.get("code", "")
    invite_id = res.get("id")
    expires_at = res.get("expires_at", "")

    text = (
        "🎟 <b>Новый одноразовый код для биолога:</b>\n\n"
        f"Код: <code>{code}</code>\n"
        "Срок действия: <b>10 минут</b>\n"
        f"Истекает: <code>{expires_at}</code>\n"
        "Роль: <b>Биолог</b>\n\n"
        "Передайте этот код биологу для авторизации в боте.\n"
        "<i>Код станет недействительным сразу после первого успешного использования.</i>"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Отозвать этот код",
                    callback_data=f"sess:invite_revoke:{invite_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ К управлению сессиями",
                    callback_data="sess:menu",
                )
            ],
        ]
    )

    if callback.message and isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(text, reply_markup=kb)


@router.callback_query(F.data == "sess:invite_list")
async def handle_list_invites(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Отображение списка активных неиспользованных кодов."""
    await callback.answer()
    user_id = callback.from_user.id if callback.from_user else 0

    try:
        invites = await api_client.get_invites(user_id)
    except Exception as exc:  # noqa: BLE001
        err = format_api_error(exc)
        if callback.message and isinstance(callback.message, Message):
            await callback.message.answer(f"Не удалось загрузить список кодов:\n{err}")
        return

    if not invites:
        text = "📋 <b>Активные коды приглашений:</b>\n\nНет активных кодов доступа."
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🎟 Создать код",
                        callback_data="sess:invite_create",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ Назад",
                        callback_data="sess:menu",
                    )
                ],
            ]
        )
        if callback.message and isinstance(callback.message, Message):
            with contextlib.suppress(Exception):
                await callback.message.edit_text(text, reply_markup=kb)
        return

    text = "📋 <b>Активные одноразовые коды:</b>\n\n"
    keyboard_rows: list[list[InlineKeyboardButton]] = []
    for inv in invites:
        code = inv.get("code", "")
        inv_id = inv.get("id")
        exp = inv.get("expires_at", "")
        text += f"• Код: <code>{code}</code> (до {exp})\n"
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    text=f"❌ Отозвать {code}",
                    callback_data=f"sess:invite_revoke:{inv_id}",
                )
            ]
        )

    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text="🎟 Создать ещё код",
                callback_data="sess:invite_create",
            ),
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data="sess:menu",
            ),
        ]
    )

    if callback.message and isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(
                text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
            )


@router.callback_query(F.data.startswith("sess:invite_revoke:"))
async def handle_revoke_invite(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Отзыв неиспользованного кода."""
    await callback.answer()
    invite_id_str = callback.data.split(":")[-1]
    if not invite_id_str.isdigit():
        return
    invite_id = int(invite_id_str)
    user_id = callback.from_user.id if callback.from_user else 0

    try:
        await api_client.revoke_invite(user_id, invite_id)
        if callback.message and isinstance(callback.message, Message):
            await callback.message.answer("✅ Код доступа успешно отозван.")
    except Exception as exc:  # noqa: BLE001
        err = format_api_error(exc)
        if callback.message and isinstance(callback.message, Message):
            await callback.message.answer(f"Не удалось отозвать код:\n{err}")

    # Обновляем список кодов
    await handle_list_invites(callback, api_client)


@router.callback_query(F.data == "sess:list")
async def handle_list_sessions(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Отображение списка всех сессий."""
    await callback.answer()
    user_id = callback.from_user.id if callback.from_user else 0

    try:
        sessions = await api_client.get_sessions(user_id)
    except Exception as exc:  # noqa: BLE001
        err = format_api_error(exc)
        if callback.message and isinstance(callback.message, Message):
            await callback.message.answer(f"Не удалось загрузить список сессий:\n{err}")
        return

    if not sessions:
        text = "💻 <b>Список сессий:</b>\n\nСессии не найдены."
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="sess:menu")]
            ]
        )
        if callback.message and isinstance(callback.message, Message):
            with contextlib.suppress(Exception):
                await callback.message.edit_text(text, reply_markup=kb)
        return

    # Сортировка: активные сначала
    sorted_sessions = sorted(sessions, key=lambda s: not s.get("is_active", False))

    text = "💻 <b>Список всех сессий:</b>\n\n"
    keyboard_rows: list[list[InlineKeyboardButton]] = []

    for s in sorted_sessions:
        s_id = s.get("id")
        is_active = s.get("is_active", False)
        status_icon = "🟢" if is_active else "⚪️"
        login = s.get("login") or "Пользователь"
        role = s.get("role", "guest")
        ext_id = s.get("external_id")
        role_title = (
            "Биолог"
            if role == "biologist"
            else ("Разработчик" if role == "developer" else role)
        )

        user_link = format_tg_user_link(login, ext_id)
        text += f"{status_icon} #{s_id} {user_link} ({role_title}, {s.get('client_type')})\n"

        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    text=f"{status_icon} #{s_id} {login} ({role_title})",
                    callback_data=f"sess:view:{s_id}",
                )
            ]
        )

    keyboard_rows.append(
        [
            InlineKeyboardButton(text="🔄 Обновить", callback_data="sess:list"),
            InlineKeyboardButton(text="⬅️ Назад", callback_data="sess:menu"),
        ]
    )

    if callback.message and isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(
                text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
            )


@router.callback_query(F.data.startswith("sess:view:"))
async def handle_view_session(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Карточка конкретной сессии."""
    await callback.answer()
    session_id_str = callback.data.split(":")[-1]
    if not session_id_str.isdigit():
        return
    session_id = int(session_id_str)
    user_id = callback.from_user.id if callback.from_user else 0

    try:
        sessions = await api_client.get_sessions(user_id)
    except Exception as exc:  # noqa: BLE001
        err = format_api_error(exc)
        if callback.message and isinstance(callback.message, Message):
            await callback.message.answer(f"Ошибка загрузки данных сессии:\n{err}")
        return

    target_sess: dict[str, Any] | None = None
    for s in sessions:
        if s.get("id") == session_id:
            target_sess = s
            break

    if not target_sess:
        if callback.message and isinstance(callback.message, Message):
            await callback.message.answer("Сессия не найдена.")
        return

    login = target_sess.get("login") or "Пользователь"
    ext_id = target_sess.get("external_id")
    client_type = target_sess.get("client_type", "неизвестно")
    role = target_sess.get("role", "guest")
    role_title = (
        "Биолог"
        if role == "biologist"
        else ("Разработчик" if role == "developer" else role)
    )
    is_active = target_sess.get("is_active", False)

    status_str = "🟢 <b>Активна</b>" if is_active else "⚪️ <b>Завершена</b>"
    user_link = format_tg_user_link(login, ext_id)

    auth_at = target_sess.get("authorized_at") or "—"
    last_act = target_sess.get("last_active_at") or "—"
    logged_out_at = target_sess.get("logged_out_at") or "—"

    text = (
        f"👤 <b>Карточка сессии #{session_id}</b>\n\n"
        f"<b>Пользователь:</b> {user_link}\n"
        f"<b>Внешний ID:</b> <code>{html.escape(str(ext_id))}</code>\n"
        f"<b>Клиент:</b> {html.escape(client_type)}\n"
        f"<b>Роль:</b> {role_title}\n"
        f"<b>Статус:</b> {status_str}\n"
        f"<b>Время входа:</b> <code>{auth_at}</code>\n"
        f"<b>Последняя активность:</b> <code>{last_act}</code>\n"
        f"<b>Время завершения:</b> <code>{logged_out_at}</code>"
    )

    buttons: list[list[InlineKeyboardButton]] = []
    if is_active:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="🚫 Завершить сессию",
                    callback_data=f"sess:terminate:{session_id}",
                )
            ]
        )
    buttons.append(
        [
            InlineKeyboardButton(
                text="✏️ Переименовать",
                callback_data=f"sess:rename:{session_id}",
            )
        ]
    )
    buttons.append(
        [
            InlineKeyboardButton(
                text="⬅️ К списку сессий",
                callback_data="sess:list",
            )
        ]
    )

    if callback.message and isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(
                text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
            )


@router.callback_query(F.data.startswith("sess:rename:"))
async def handle_rename_session_start(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Запуск процесса переименования пользователя сессии (Developer)."""
    await callback.answer()
    session_id_str = callback.data.split(":")[-1]
    if not session_id_str.isdigit():
        return
    session_id = int(session_id_str)
    await state.set_state(SessionStates.waiting_rename_name)
    await state.update_data(rename_session_id=session_id)

    if callback.message and isinstance(callback.message, Message):
        await callback.message.answer(
            f"✏️ Введите новое отображаемое имя для пользователя сессии #{session_id} (до 64 символов):",
            reply_markup=get_cancel_inline_keyboard(f"sess:view:{session_id}"),
        )


@router.message(SessionStates.waiting_rename_name)
async def process_rename_session_name(
    message: Message,
    state: FSMContext,
    api_client: GrowVisionApiClient,
) -> None:
    """Обработка ввода нового имени пользователя сессии."""
    new_name = (message.text or "").strip()
    data = await state.get_data()
    session_id = data.get("rename_session_id")
    cancel_cb = f"sess:view:{session_id}" if session_id else "sess:list"

    if not new_name:
        await message.answer(
            "Имя пользователя не может быть пустым. Повторите ввод:",
            reply_markup=get_cancel_inline_keyboard(cancel_cb),
        )
        return

    if len(new_name) > 64:
        await message.answer(
            "Длина имени не должна превышать 64 символа. Повторите ввод:",
            reply_markup=get_cancel_inline_keyboard(cancel_cb),
        )
        return

    user_id = message.from_user.id if message.from_user else 0
    try:
        await api_client.rename_session(user_id, int(session_id), new_name)
        await state.clear()
        await message.answer(
            f"✅ Пользователь сессии #{session_id} успешно переименован в: <b>{html.escape(new_name)}</b>!"
        )
    except Exception as exc:  # noqa: BLE001
        err = format_api_error(exc)
        await message.answer(
            f"❌ Не удалось переименовать пользователя:\n{err}",
            reply_markup=get_cancel_inline_keyboard(cancel_cb),
        )


@router.callback_query(F.data.startswith("sess:terminate:"))
async def handle_terminate_session(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Принудительное завершение активной сессии."""
    await callback.answer()
    session_id_str = callback.data.split(":")[-1]
    if not session_id_str.isdigit():
        return
    session_id = int(session_id_str)
    user_id = callback.from_user.id if callback.from_user else 0

    try:
        await api_client.terminate_session(user_id, session_id)
        if callback.message and isinstance(callback.message, Message):
            await callback.message.answer(
                f"✅ Сессия #{session_id} успешно завершена (пользователь отключён)."
            )
    except Exception as exc:  # noqa: BLE001
        err = format_api_error(exc)
        if callback.message and isinstance(callback.message, Message):
            await callback.message.answer(f"Не удалось завершить сессию:\n{err}")

    # Обновляем карточку сессии
    await handle_view_session(callback, api_client)
