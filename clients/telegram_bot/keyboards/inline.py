from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def get_cancel_inline_keyboard(
    callback_data: str = "act:cancel",
) -> InlineKeyboardMarkup:
    """Кнопка отмены текущего действия FSM."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data=callback_data)]
        ]
    )


def get_confirm_inline_keyboard(
    action: str,
    target_id: int | str,
) -> InlineKeyboardMarkup:
    """Клавиатура подтверждения опасного действия (архивация, удаление)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, подтвердить",
                    callback_data=f"cnf:{action}:{target_id}:yes",
                ),
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=f"cnf:{action}:{target_id}:no",
                ),
            ]
        ]
    )


def get_installation_card_keyboard(
    inst_id: int,
    role: str = "guest",
    is_archived: bool = False,
) -> InlineKeyboardMarkup:
    """Клавиатура действий в карточке установки с учетом роли и статуса архива."""
    buttons: list[list[InlineKeyboardButton]] = []

    # Просмотр плановых кадров таймлапса доступен всем
    buttons.append(
        [
            InlineKeyboardButton(
                text="🖼 Кадры таймлапса",
                callback_data=f"inst:frames:{inst_id}",
            )
        ]
    )

    if not is_archived:
        if role in ("biologist", "developer"):
            buttons.append(
                [
                    InlineKeyboardButton(
                        text="📸 Сделать снимок",
                        callback_data=f"inst:snap:{inst_id}",
                    ),
                    InlineKeyboardButton(
                        text="🎬 Скачать видео",
                        callback_data=f"inst:video:{inst_id}",
                    ),
                ]
            )

        if role == "biologist":
            buttons.append(
                [
                    InlineKeyboardButton(
                        text="✏️ Изменить название / культуру",
                        callback_data=f"inst:edit:{inst_id}",
                    )
                ]
            )

        if role == "developer":
            buttons.append(
                [
                    InlineKeyboardButton(
                        text="💡 Свет / Вспышка / Тест",
                        callback_data=f"inst:control:{inst_id}",
                    ),
                    InlineKeyboardButton(
                        text="📷 Камеры установки",
                        callback_data=f"inst:cameras:{inst_id}",
                    ),
                ]
            )
            buttons.append(
                [
                    InlineKeyboardButton(
                        text="✏️ Редактировать",
                        callback_data=f"inst:edit:{inst_id}",
                    ),
                    InlineKeyboardButton(
                        text="📦 В архив",
                        callback_data=f"inst:archive:{inst_id}",
                    ),
                ]
            )
            buttons.append(
                [
                    InlineKeyboardButton(
                        text="🗑 Удалить",
                        callback_data=f"inst:delete:{inst_id}",
                    )
                ]
            )
    else:
        # Для архивной установки Developer видит скачивание видео и удаление
        if role == "developer":
            buttons.append(
                [
                    InlineKeyboardButton(
                        text="🎬 Скачать видео",
                        callback_data=f"inst:video:{inst_id}",
                    ),
                    InlineKeyboardButton(
                        text="🗑 Удалить",
                        callback_data=f"inst:delete:{inst_id}",
                    ),
                ]
            )

    buttons.append(
        [InlineKeyboardButton(text="⬅️ К списку установок", callback_data="inst:list")]
    )

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_control_panel_keyboard(
    target: str,  # 'all' или str(installation_id)
) -> InlineKeyboardMarkup:
    """Панель управления светом, вспышкой и тестом реле."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💡 Свет: ВКЛ",
                    callback_data=f"ctrl:light:1:{target}",
                ),
                InlineKeyboardButton(
                    text="💡 Свет: ВЫКЛ",
                    callback_data=f"ctrl:light:0:{target}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⚡ Вспышка: ВКЛ",
                    callback_data=f"ctrl:flash:1:{target}",
                ),
                InlineKeyboardButton(
                    text="⚡ Вспышка: ВЫКЛ",
                    callback_data=f"ctrl:flash:0:{target}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🧪 Тест реле (3 сек)",
                    callback_data=f"ctrl:relay:{target}",
                )
            ],
            [InlineKeyboardButton(text="⬅️ К выбору цели", callback_data="ctrl:menu")],
        ]
    )


def get_edit_fields_keyboard(
    inst_id: int,
    role: str = "developer",
) -> InlineKeyboardMarkup:
    """Клавиатура выбора поля для редактирования установки."""
    buttons: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="🏷 Название",
                callback_data=f"edit:fld:name:{inst_id}",
            ),
            InlineKeyboardButton(
                text="🌾 Культура",
                callback_data=f"edit:fld:crop_type:{inst_id}",
            ),
        ]
    ]

    if role == "developer":
        buttons.append(
            [
                InlineKeyboardButton(
                    text="💡 Пин света",
                    callback_data=f"edit:fld:light_pin:{inst_id}",
                ),
                InlineKeyboardButton(
                    text="⚡ Пин вспышки",
                    callback_data=f"edit:fld:flash_pin:{inst_id}",
                ),
            ]
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    text="📏 Размер ArUco",
                    callback_data=f"edit:fld:aruco:{inst_id}",
                ),
                InlineKeyboardButton(
                    text="🗑 Сбросить ArUco",
                    callback_data=f"edit:fld:reset_aruco:{inst_id}",
                ),
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="⬅️ Отмена",
                callback_data=f"inst:view:{inst_id}",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_quick_interval_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура быстрых кнопок выбора интервала автосъёмки."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="1 ч", callback_data="sys:set_int:1"),
                InlineKeyboardButton(text="2 ч", callback_data="sys:set_int:2"),
                InlineKeyboardButton(text="4 ч", callback_data="sys:set_int:4"),
            ],
            [
                InlineKeyboardButton(text="6 ч", callback_data="sys:set_int:6"),
                InlineKeyboardButton(text="12 ч", callback_data="sys:set_int:12"),
                InlineKeyboardButton(text="24 ч", callback_data="sys:set_int:24"),
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="sys:status")],
        ]
    )
