import math
from collections.abc import Callable
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def build_paginator_keyboard(
    items: list[Any],
    page: int,
    page_size: int = 5,
    entity: str = "item",
    extra: str = "",
    item_callback_prefix: str = "item",
    item_text_func: Callable[[Any], str] | None = None,
    item_id_func: Callable[[Any], str | int] | None = None,
    include_all_button: bool = False,
    all_button_callback: str = "act:all",
    all_button_text: str = "🌐 Все активные установки",
    bottom_buttons: list[list[InlineKeyboardButton]] | None = None,
) -> InlineKeyboardMarkup:
    """Универсальный построитель Inline-клавиатуры со списком элементов и пагинацией.

    Callback_data пагинатора строго <64 байт:
    pag:<entity>:<page>[:<extra>]
    """
    total_items = len(items)
    total_pages = max(1, math.ceil(total_items / page_size))
    current_page = max(0, min(page, total_pages - 1))

    keyboard: list[list[InlineKeyboardButton]] = []

    # Верхняя кнопка "Все активные установки" при необходимости
    if include_all_button:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=all_button_text, callback_data=all_button_callback
                )
            ]
        )

    # Элементы текущей страницы
    start_idx = current_page * page_size
    page_items = items[start_idx : start_idx + page_size]

    for item in page_items:
        text = (
            item_text_func(item)
            if item_text_func
            else str(item.get("name", item.get("id", "Элемент")))
        )
        item_id = (
            item_id_func(item)
            if item_id_func
            else (item.get("id") if isinstance(item, dict) else str(item))
        )
        cb_data = f"{item_callback_prefix}:{item_id}"
        if extra:
            cb_data = f"{cb_data}:{extra}"
        # Проверяем лимит 64 байта
        if len(cb_data.encode("utf-8")) > 64:
            cb_data = cb_data[:64]
        keyboard.append([InlineKeyboardButton(text=text, callback_data=cb_data)])

    # Кнопки навигации пагинатора (только если страниц больше 1)
    if total_pages > 1:
        nav_row: list[InlineKeyboardButton] = []
        extra_suffix = f":{extra}" if extra else ""

        # Кнопка "Назад"
        if current_page > 0:
            prev_cb = f"pag:{entity}:{current_page - 1}{extra_suffix}"
            nav_row.append(InlineKeyboardButton(text="◀️ Назад", callback_data=prev_cb))
        else:
            nav_row.append(InlineKeyboardButton(text="⏹", callback_data="pag:noop"))

        # Индикатор страницы
        nav_row.append(
            InlineKeyboardButton(
                text=f"{current_page + 1}/{total_pages}",
                callback_data="pag:noop",
            )
        )

        # Кнопка "Вперед"
        if current_page < total_pages - 1:
            next_cb = f"pag:{entity}:{current_page + 1}{extra_suffix}"
            nav_row.append(InlineKeyboardButton(text="Вперёд ▶️", callback_data=next_cb))
        else:
            nav_row.append(InlineKeyboardButton(text="⏹", callback_data="pag:noop"))

        keyboard.append(nav_row)

    # Дополнительные нижние кнопки
    if bottom_buttons:
        keyboard.extend(bottom_buttons)

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def build_frames_gallery_keyboard(
    current_index: int,
    total_frames: int,
    layer: str = "r",  # 'r' = raw, 'a' = annotated
    inst_id: int | None = None,
    can_build_video: bool = True,
) -> InlineKeyboardMarkup:
    """Клавиатура для листания плановых кадров таймлапса по 1 кадру.

    Callback_data:
    frm:<idx>:<layer>
    """
    keyboard: list[list[InlineKeyboardButton]] = []

    # Строка навигации: [ ⏮ В начало ] [ ◀️ Пред. ] [ X/N ] [ След. ▶️ ] [ В конец ⏭ ]
    nav_row: list[InlineKeyboardButton] = []

    # В начало
    if current_index > 0:
        nav_row.append(InlineKeyboardButton(text="⏮", callback_data=f"frm:0:{layer}"))
        nav_row.append(
            InlineKeyboardButton(
                text="◀️", callback_data=f"frm:{current_index - 1}:{layer}"
            )
        )
    else:
        nav_row.append(InlineKeyboardButton(text="⏹", callback_data="frm:noop"))
        nav_row.append(InlineKeyboardButton(text="⏹", callback_data="frm:noop"))

    nav_row.append(
        InlineKeyboardButton(
            text=f"{current_index + 1}/{total_frames}",
            callback_data="frm:noop",
        )
    )

    if current_index < total_frames - 1:
        nav_row.append(
            InlineKeyboardButton(
                text="▶️", callback_data=f"frm:{current_index + 1}:{layer}"
            )
        )
        nav_row.append(
            InlineKeyboardButton(
                text="⏭", callback_data=f"frm:{total_frames - 1}:{layer}"
            )
        )
    else:
        nav_row.append(InlineKeyboardButton(text="⏹", callback_data="frm:noop"))
        nav_row.append(InlineKeyboardButton(text="⏹", callback_data="frm:noop"))

    keyboard.append(nav_row)

    # Переключатель слоя: [ 🖼 Оригинал / 🧠 Разметка ]
    switch_layer = "a" if layer == "r" else "r"
    switch_text = "🧠 Показать разметку" if layer == "r" else "🖼 Показать оригинал"
    keyboard.append(
        [
            InlineKeyboardButton(
                text=switch_text,
                callback_data=f"frm:{current_index}:{switch_layer}",
            )
        ]
    )

    if can_build_video:
        cb_val = f"frm:timelapse:{inst_id}" if inst_id is not None else "frm:timelapse"
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="🎬 Сформировать таймлапс",
                    callback_data=cb_val,
                )
            ]
        )

    # Кнопка возврата к установке
    keyboard.append(
        [InlineKeyboardButton(text="⬅️ Закрыть галерею", callback_data="frm:close")]
    )

    return InlineKeyboardMarkup(inline_keyboard=keyboard)
