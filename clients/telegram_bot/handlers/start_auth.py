import contextlib
import html
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from clients.telegram_bot.api_client import GrowVisionApiClient
from clients.telegram_bot.keyboards.inline import get_cancel_inline_keyboard
from clients.telegram_bot.keyboards.reply import (
    get_guest_reply_keyboard,
    get_menu_by_role,
)
from clients.telegram_bot.states import AuthStates
from clients.telegram_bot.utils.errors import ApiError, format_api_error

logger = logging.getLogger(__name__)

router = Router(name="start_auth")


@router.message(Command("start"))
async def handle_start(
    message: Message,
    state: FSMContext,
    api_client: GrowVisionApiClient,
) -> None:
    """Обработчик команды /start: определение роли и отображение главного меню."""
    await state.clear()
    user_id = message.from_user.id if message.from_user else 0

    try:
        me = await api_client.get_me(user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Ошибка проверки роли при /start: {exc}")
        me = None

    if me:
        role = me.get("role", "guest")
        raw_username = me.get("username", "Пользователь")
        username = html.escape(str(raw_username))
        if role == "developer":
            text = (
                f"👋 Здравствуйте, <b>{username}</b>!\n\n"
                f"Ваша роль: <b>Разработчик</b>. Доступно полное управление оборудованием, "
                f"камерами, установками и системными параметрами."
            )
        elif role == "biologist":
            text = (
                f"👋 Здравствуйте, <b>{username}</b>!\n\n"
                f"Ваша роль: <b>Биолог</b>. Доступно управление съёмкой, скачивание "
                f"видео-таймлапсов и настройка параметров растений."
            )
        else:
            text = f"👋 Здравствуйте, <b>{username}</b>!"
    else:
        role = "guest"
        text = (
            "👋 Добро пожаловать в систему автоматического мониторинга растений <b>GrowVision</b>!\n\n"
            "Вы находитесь в режиме <b>Гость</b>. Вы можете просматривать активные установки "
            "и плановые снимки растений.\n"
            "Для управления установками выполните вход через кнопку <b>[ 🔑 Войти ]</b> или команду /login."
        )

    await message.answer(
        text,
        reply_markup=get_menu_by_role(role),
    )


@router.message(Command("help"))
@router.message(F.text == "ℹ️ Справка")
async def handle_help(
    message: Message,
    api_client: GrowVisionApiClient,
) -> None:
    """Обработчик команды /help и кнопки 'ℹ️ Справка'."""
    version_str = ""
    try:
        ver_info = await api_client.get_version()
        if isinstance(ver_info, dict):
            v = ver_info.get("server_version") or ver_info.get("version")
            if v and v != "неизвестно":
                version_str = f"\n\nВерсия системы: <code>v{v}</code>"
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"Не удалось получить версию для справки: {exc}")

    help_text = (
        "📖 <b>Справка по системе GrowVision</b>\n\n"
        "🌱 <b>Роли пользователей:</b>\n"
        "• <b>Гость:</b> просмотр списка активных установок и плановых снимков таймлапса.\n"
        "• <b>Биолог:</b> ручная фотосъёмка растений, скачивание видео-таймлапса, "
        "редактирование названий и культур установок.\n"
        "• <b>Разработчик:</b> полное управление реле света и вспышки, сканирование и "
        "привязка камер, создание, редактирование и удаление установок, системные настройки.\n\n"
        "⌨️ <b>Основные команды:</b>\n"
        "/start — возврат в главное меню\n"
        "/help — данная справка\n"
        "/version — версия системы GrowVision\n"
        "/login — авторизация в системе\n"
        "/cancel — отмена текущего пошагового ввода"
        f"{version_str}"
    )
    await message.answer(help_text)


@router.message(Command("version"))
async def handle_version(
    message: Message,
    api_client: GrowVisionApiClient,
) -> None:
    """Обработчик команды /version: вывод версии системы."""
    try:
        ver_info = await api_client.get_version()
        if isinstance(ver_info, dict):
            v = (
                ver_info.get("server_version")
                or ver_info.get("version")
                or "неизвестно"
            )
        else:
            v = ver_info or "неизвестно"
        if v == "неизвестно":
            await message.answer(
                "🌱 Версия системы GrowVision: <code>неизвестно</code>"
            )
        else:
            await message.answer(f"🌱 Версия системы GrowVision: <code>v{v}</code>")
    except Exception as exc:  # noqa: BLE001
        err = format_api_error(exc)
        await message.answer(f"Не удалось получить версию системы:\n{err}")


@router.message(Command("cancel"))
@router.message(F.text == "❌ Отмена")
async def handle_cancel(
    message: Message,
    state: FSMContext,
    api_client: GrowVisionApiClient,
) -> None:
    """Обработчик команды /cancel и кнопки отмены: сброс FSM и возврат в меню."""
    await state.clear()
    user_id = message.from_user.id if message.from_user else 0
    me = await api_client.get_me(user_id)
    role = me.get("role", "guest") if me else "guest"

    await message.answer(
        "Действие отменено.",
        reply_markup=get_menu_by_role(role),
    )


@router.callback_query(F.data == "act:cancel")
@router.callback_query(F.data == "auth:cancel")
async def handle_cancel_callback(
    callback: CallbackQuery,
    state: FSMContext,
    api_client: GrowVisionApiClient,
) -> None:
    """Обработчик нажатия Inline-кнопки отмены."""
    await callback.answer()
    await state.clear()
    user_id = callback.from_user.id if callback.from_user else 0
    me = await api_client.get_me(user_id)
    role = me.get("role", "guest") if me else "guest"

    if callback.message and isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.delete()
        await callback.message.answer(
            "Действие отменено.",
            reply_markup=get_menu_by_role(role),
        )


# ================== Авторизация (двухэтапная) ==================


@router.message(Command("login"))
@router.message(F.text == "🔑 Войти")
async def start_login(
    message: Message,
    state: FSMContext,
) -> None:
    """Начало двухэтапной авторизации: запрос логина."""
    await state.set_state(AuthStates.waiting_login)
    await message.answer(
        "🔑 <b>Авторизация в GrowVision</b>\n\n"
        "Шаг 1 из 2. Введите ваш логин (имя и фамилию, например: <code>Андрей Бармин</code>):",
        reply_markup=get_cancel_inline_keyboard("auth:cancel"),
    )


@router.message(AuthStates.waiting_login)
async def process_login_username(
    message: Message,
    state: FSMContext,
) -> None:
    """Шаг 1: получение логина и переход к вводу пароля."""
    username = (message.text or "").strip()
    if not username:
        await message.answer(
            "Логин не может быть пустым. Введите ваш логин:",
            reply_markup=get_cancel_inline_keyboard("auth:cancel"),
        )
        return

    await state.update_data(username=username)
    await state.set_state(AuthStates.waiting_password)
    await message.answer(
        f"Логин: <b>{html.escape(username)}</b>\n\n"
        "Шаг 2 из 2. Введите пароль доступа:\n"
        "<i>(Сообщение с паролем будет сразу автоматически удалено из чата для безопасности)</i>",
        reply_markup=get_cancel_inline_keyboard("auth:cancel"),
    )


@router.message(AuthStates.waiting_password)
async def process_login_password(
    message: Message,
    state: FSMContext,
    api_client: GrowVisionApiClient,
) -> None:
    """Шаг 2: получение пароля, удаление сообщения из чата и вызов API логина."""
    password = (message.text or "").strip()

    # Сразу пытаемся удалить сообщение пользователя с паролем
    with contextlib.suppress(Exception):
        await message.delete()

    data = await state.get_data()
    username = data.get("username", "")
    user_id = message.from_user.id if message.from_user else 0

    if not password:
        await message.answer(
            "Пароль не может быть пустым. Введите пароль доступа:",
            reply_markup=get_cancel_inline_keyboard("auth:cancel"),
        )
        return

    try:
        resp = await api_client.login(
            user_id=user_id, username=username, password=password
        )
        await state.clear()
        role = resp.get("role", "guest")
        role_title = (
            "Биолог"
            if role == "biologist"
            else ("Разработчик" if role == "developer" else role)
        )

        await message.answer(
            f"✅ Вы успешно вошли как <b>{html.escape(username)}</b> (роль: <b>{role_title}</b>)!",
            reply_markup=get_menu_by_role(role),
        )
    except ApiError as exc:
        if exc.status_code == 401:
            await message.answer(
                "❌ <b>Неверный пароль.</b>\n"
                "Попробуйте ввести пароль ещё раз или нажмите [ ❌ Отмена ]:",
                reply_markup=get_cancel_inline_keyboard("auth:cancel"),
            )
            # Остаемся в состоянии waiting_password
            return
        await state.clear()
        err_msg = format_api_error(exc)
        await message.answer(
            f"Не удалось войти:\n{err_msg}",
            reply_markup=get_guest_reply_keyboard(),
        )
    except Exception as exc:  # noqa: BLE001
        await state.clear()
        err_msg = format_api_error(exc)
        await message.answer(
            f"Не удалось войти:\n{err_msg}",
            reply_markup=get_guest_reply_keyboard(),
        )


# ================== Профиль и Выход ==================


@router.message(Command("profile"))
@router.message(F.text.in_({"👤 Профиль", "👤 Профиль / Выход"}))
async def handle_profile(
    message: Message,
    api_client: GrowVisionApiClient,
) -> None:
    """Отображение профиля текущего пользователя с кнопкой выхода."""
    user_id = message.from_user.id if message.from_user else 0
    try:
        me = await api_client.get_me(user_id)
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"Ошибка получения профиля:\n{format_api_error(exc)}")
        return

    if not me:
        await message.answer(
            "Вы находитесь в режиме <b>Гость</b>.\n"
            "Для доступа к дополнительным функциям войдите через [ 🔑 Войти ].",
            reply_markup=get_guest_reply_keyboard(),
        )
        return

    login_val = me.get("login") or me.get("username") or "—"
    role = me.get("role", "guest")
    role_title = (
        "Биолог"
        if role == "biologist"
        else ("Разработчик" if role == "developer" else role)
    )

    auth_raw = me.get("authorized_at") or me.get("created_at")
    if auth_raw:
        auth_str = str(auth_raw)
        if not auth_str.endswith("Z") and "UTC" not in auth_str:
            auth_str = f"{auth_str} UTC"
    else:
        auth_str = "—"

    last_raw = me.get("last_active_at") or me.get("last_activity")
    if last_raw:
        last_str = str(last_raw)
        if not last_str.endswith("Z") and "UTC" not in last_str:
            last_str = f"{last_str} UTC"
    else:
        last_str = "—"

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    profile_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚪 Выйти из аккаунта", callback_data="auth:logout"
                )
            ]
        ]
    )

    text = (
        "👤 <b>Профиль пользователя</b>\n\n"
        f"<b>Логин:</b> {html.escape(str(login_val))}\n"
        f"<b>Роль:</b> {role_title}\n"
        f"<b>Время входа:</b> {auth_str}\n"
        f"<b>Последняя активность:</b> {last_str}"
    )
    await message.answer(text, reply_markup=profile_kb)


@router.callback_query(F.data == "auth:logout")
async def handle_logout(
    callback: CallbackQuery,
    state: FSMContext,
    api_client: GrowVisionApiClient,
) -> None:
    """Выход пользователя из аккаунта."""
    await callback.answer()
    await state.clear()
    user_id = callback.from_user.id if callback.from_user else 0

    with contextlib.suppress(Exception):
        await api_client.logout(user_id)

    if callback.message and isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.delete()
        await callback.message.answer(
            "🚪 Вы успешно вышли из системы.",
            reply_markup=get_guest_reply_keyboard(),
        )
