from typing import Any

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
            buttons.append(
                [
                    InlineKeyboardButton(
                        text="⏱ Задержка съёмки",
                        callback_data=f"inst:delay:{inst_id}",
                    )
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
        # Для архивной установки Developer видит разархивацию, скачивание видео и удаление
        if role == "developer":
            buttons.append(
                [
                    InlineKeyboardButton(
                        text="♻️ Восстановить из архива",
                        callback_data=f"inst:unarchive:{inst_id}",
                    )
                ]
            )
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
    light_state: bool = False,
    flash_state: bool = False,
    *,
    light_is_rgb: bool = False,
    flash_is_rgb: bool = False,
    light_color: str = "#FFFFFF",
    flash_color: str = "#FFFFFF",
) -> InlineKeyboardMarkup:
    """Панель управления реле: кнопки-переключатели или вход в RGB HEX-меню."""
    light_next = 0 if light_state else 1
    light_icon = "🟢 ВКЛ" if light_state else "⚪️ ВЫКЛ"

    flash_next = 0 if flash_state else 1
    flash_icon = "🟢 ВКЛ" if flash_state else "⚪️ ВЫКЛ"

    if target != "all" and light_is_rgb:
        light_btn = InlineKeyboardButton(
            text=f"💡 Свет RGB ({light_color}): {light_icon}",
            callback_data=f"ctrl:rgb:light:{target}",
        )
    else:
        light_btn = InlineKeyboardButton(
            text=f"💡 Свет: {light_icon}",
            callback_data=f"ctrl:light:{light_next}:{target}",
        )

    if target != "all" and flash_is_rgb:
        flash_btn = InlineKeyboardButton(
            text=f"⚡ Вспышка RGB ({flash_color}): {flash_icon}",
            callback_data=f"ctrl:rgb:flash:{target}",
        )
    else:
        flash_btn = InlineKeyboardButton(
            text=f"⚡ Вспышка: {flash_icon}",
            callback_data=f"ctrl:flash:{flash_next}:{target}",
        )

    day_night_text = "☀️ Световой день (все)" if target == "all" else "☀️ Световой день"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [light_btn, flash_btn],
            [
                InlineKeyboardButton(
                    text="🧪 Тест реле (3 сек)",
                    callback_data=f"ctrl:relay:{target}",
                ),
                InlineKeyboardButton(
                    text="⏱ Задержка съёмки",
                    callback_data=f"ctrl:delay:{target}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=day_night_text,
                    callback_data=f"ctrl:dn:menu:{target}",
                )
            ],
            [InlineKeyboardButton(text="⬅️ К выбору цели", callback_data="ctrl:menu")],
        ]
    )


def get_day_night_menu_keyboard(
    target: str,
    enabled: bool,
) -> InlineKeyboardMarkup:
    """Подменю настройки цикла дня и ночи для всех или конкретной установки."""
    next_val = 0 if enabled else 1
    toggle_text = "⚪️ Выключить" if enabled else "🟢 Включить"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=toggle_text,
                    callback_data=f"ctrl:dn:toggle:{next_val}:{target}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🕒 Задать интервал",
                    callback_data=f"ctrl:dn:interval:{target}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад к управлению",
                    callback_data=f"ctrl:target:{target}",
                )
            ],
        ]
    )


def get_rgb_channel_menu_keyboard(
    target: str,
    channel: str,  # 'light' | 'flash'
    state: bool,
    color: str,
) -> InlineKeyboardMarkup:
    """Подменю RGB-лампы конкретной установки (статус + Вкл/Выкл + Задать HEX-цвет)."""
    next_val = 0 if state else 1
    toggle_text = "⚪️ Выключить" if state else "🟢 Включить"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{toggle_text} ({color})",
                    callback_data=f"ctrl:rgb_toggle:{channel}:{next_val}:{target}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎨 Задать HEX-цвет",
                    callback_data=f"ctrl:rgb_hex:{channel}:{target}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад к управлению",
                    callback_data=f"ctrl:target:{target}",
                )
            ],
        ]
    )


def get_snap_layer_keyboard(
    capture_id: int | str, current_layer: str = "raw"
) -> InlineKeyboardMarkup:
    """Клавиатура переключения слоя исходник/разметка для ручного снимка (Developer)."""
    if current_layer == "raw":
        btn_text = "🧠 Разметка"
        next_layer = "ann"
    else:
        btn_text = "🖼 Исходник"
        next_layer = "raw"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=btn_text,
                    callback_data=f"snap:layer:{capture_id}:{next_layer}",
                )
            ]
        ]
    )


def get_capture_delay_keyboard(
    target: str,
    is_installation: bool = False,
) -> InlineKeyboardMarkup:
    """Клавиатура пресетов задержки съёмки (Критерий 11)."""
    buttons = [
        [
            InlineKeyboardButton(text="0.5 с", callback_data=f"set_delay:{target}:0.5"),
            InlineKeyboardButton(text="1.5 с", callback_data=f"set_delay:{target}:1.5"),
            InlineKeyboardButton(text="3.0 с", callback_data=f"set_delay:{target}:3.0"),
            InlineKeyboardButton(text="5.0 с", callback_data=f"set_delay:{target}:5.0"),
        ]
    ]
    if is_installation:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="🔄 Использовать общую",
                    callback_data=f"set_delay:{target}:default",
                )
            ]
        )
    back_target = f"inst:view:{target}" if is_installation else "ctrl:menu"
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=back_target)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


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


def get_climate_menu_keyboard(
    summary: dict[str, Any],
    *,
    can_control: bool = False,
) -> InlineKeyboardMarkup:
    """Клавиатура панели «Климат и полив» (ESP)."""
    buttons: list[list[InlineKeyboardButton]] = []
    mode = "auto" if summary.get("mode") == "auto" else "manual"
    water_on = summary.get("water_state") == "on"
    fan_on = summary.get("fan_state") == "on"

    if can_control:
        if mode == "auto":
            buttons.append(
                [
                    InlineKeyboardButton(
                        text="🖐 Переключить на Ручной",
                        callback_data="clm:mode:manual",
                    )
                ]
            )
        else:
            buttons.append(
                [
                    InlineKeyboardButton(
                        text="🤖 Переключить на Авто",
                        callback_data="clm:mode:auto",
                    )
                ]
            )
            water_next = "off" if water_on else "on"
            fan_next = "off" if fan_on else "on"
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=f"💧 Полив: {'🟢 ВКЛ' if water_on else '⚪️ ВЫКЛ'}",
                        callback_data=f"clm:ctrl:water:{water_next}",
                    ),
                    InlineKeyboardButton(
                        text=f"🌬 Обдув: {'🟢 ВКЛ' if fan_on else '⚪️ ВЫКЛ'}",
                        callback_data=f"clm:ctrl:fan:{fan_next}",
                    ),
                ]
            )

        buttons.append(
            [
                InlineKeyboardButton(
                    text="⏱ Расписание полива",
                    callback_data="clm:sched:water",
                ),
                InlineKeyboardButton(
                    text="⏱ Расписание обдува",
                    callback_data="clm:sched:fan",
                ),
            ]
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    text="🧪 Внести замер pH",
                    callback_data="clm:ph",
                )
            ]
        )

    buttons.append(
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="clm:refresh")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)
