from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def get_guest_reply_keyboard() -> ReplyKeyboardMarkup:
    """Нижнее меню для неавторизованного пользователя (Гость)."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🌱 Установки")],
            [KeyboardButton(text="🔑 Войти"), KeyboardButton(text="ℹ️ Справка")],
        ],
        resize_keyboard=True,
    )


def get_biologist_reply_keyboard() -> ReplyKeyboardMarkup:
    """Нижнее меню для роли Biologist."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🌱 Установки"),
                KeyboardButton(text="📸 Сделать снимок"),
            ],
            [
                KeyboardButton(text="🎬 Таймлапс (видео)"),
                KeyboardButton(text="👤 Профиль / Выход"),
            ],
            [KeyboardButton(text="ℹ️ Справка")],
        ],
        resize_keyboard=True,
    )


def get_developer_reply_keyboard() -> ReplyKeyboardMarkup:
    """Нижнее меню для роли Developer."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🌱 Установки"),
                KeyboardButton(text="📷 Камеры"),
            ],
            [
                KeyboardButton(text="💡 Свет и реле"),
                KeyboardButton(text="📸 Сделать снимок"),
            ],
            [
                KeyboardButton(text="🎬 Таймлапс (видео)"),
                KeyboardButton(text="⚙️ Система"),
            ],
            [
                KeyboardButton(text="👥 Сессии"),
                KeyboardButton(text="👤 Профиль / Выход"),
            ],
            [KeyboardButton(text="ℹ️ Справка")],
        ],
        resize_keyboard=True,
    )


def get_menu_by_role(role: str) -> ReplyKeyboardMarkup:
    """Возвращает Reply-клавиатуру в зависимости от роли."""
    if role == "developer":
        return get_developer_reply_keyboard()
    if role == "biologist":
        return get_biologist_reply_keyboard()
    return get_guest_reply_keyboard()
