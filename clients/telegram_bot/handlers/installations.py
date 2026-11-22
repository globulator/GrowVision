import contextlib
import html
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
from clients.telegram_bot.config import BotConfig
from clients.telegram_bot.keyboards.inline import (
    get_cancel_inline_keyboard,
    get_capture_delay_keyboard,
    get_confirm_inline_keyboard,
    get_edit_fields_keyboard,
    get_installation_card_keyboard,
)
from clients.telegram_bot.keyboards.paginator import build_paginator_keyboard
from clients.telegram_bot.states import (
    CaptureDelayStates,
    InstallationCreateStates,
    InstallationEditStates,
)
from clients.telegram_bot.utils.errors import ApiError, format_api_error
from clients.telegram_bot.utils.formatters import format_installation_card

logger = logging.getLogger(__name__)

router = Router(name="installations")


async def render_installations_list(
    user_id: int,
    api_client: GrowVisionApiClient,
    page: int = 0,
    show_archived: bool = False,
) -> tuple[str, InlineKeyboardMarkup]:
    """Формирует текст и пагинатор списка установок."""
    me = await api_client.get_me(user_id)
    role = me.get("role", "guest") if me else "guest"

    # Гость и Биолог видят только активные установки
    if role != "developer":
        show_archived = False

    installations = await api_client.get_installations(
        user_id, include_archived=show_archived
    )
    if show_archived:
        installations = [i for i in installations if i.get("is_archived") is True]

    title = (
        "📦 <b>Архивные установки</b>"
        if show_archived
        else "🌱 <b>Активные установки</b>"
    )

    bottom_buttons: list[list[InlineKeyboardButton]] = []
    if role == "developer":
        # Переключатель Активные/Архивные
        if show_archived:
            bottom_buttons.append(
                [
                    InlineKeyboardButton(
                        text="📂 Показать активные установки",
                        callback_data="pag:inst:0:act",
                    )
                ]
            )
        else:
            bottom_buttons.append(
                [
                    InlineKeyboardButton(
                        text="➕ Создать установку",
                        callback_data="inst:create",
                    ),
                    InlineKeyboardButton(
                        text="📦 Показать архивные",
                        callback_data="pag:inst:0:arc",
                    ),
                ]
            )

    if not installations:
        empty_text = (
            f"{title}\n\nСписок архивных установок пуст."
            if show_archived
            else f"{title}\n\nСписок активных установок пуст."
        )
        return empty_text, InlineKeyboardMarkup(inline_keyboard=bottom_buttons)

    extra_flag = "arc" if show_archived else "act"
    kb = build_paginator_keyboard(
        items=installations,
        page=page,
        page_size=5,
        entity="inst",
        extra=extra_flag,
        item_callback_prefix="inst:view",
        item_text_func=lambda i: (
            f"🌱 {i.get('name', 'Установка')} ({i.get('culture_name') or i.get('crop_type', '—')})"
        ),
        item_id_func=lambda i: i.get("id"),
        bottom_buttons=bottom_buttons,
    )
    return f"{title}\nВыберите из списка установок для просмотра карточки:", kb


@router.message(F.text == "🌱 Установки")
async def handle_installations_menu(
    message: Message,
    api_client: GrowVisionApiClient,
) -> None:
    """Точка входа из главного меню: список установок."""
    user_id = message.from_user.id if message.from_user else 0
    try:
        text, kb = await render_installations_list(
            user_id, api_client, page=0, show_archived=False
        )
        await message.answer(text, reply_markup=kb)
    except Exception as exc:  # noqa: BLE001
        err = format_api_error(exc)
        await message.answer(f"Не удалось загрузить список установок:\n{err}")


@router.callback_query(F.data == "inst:list")
async def handle_installations_back_to_list(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Возврат к активному списку установок."""
    await callback.answer()
    user_id = callback.from_user.id if callback.from_user else 0
    try:
        text, kb = await render_installations_list(
            user_id, api_client, page=0, show_archived=False
        )
        if callback.message and isinstance(callback.message, Message):
            with contextlib.suppress(Exception):
                await callback.message.edit_text(text, reply_markup=kb)
    except Exception as exc:  # noqa: BLE001
        await callback.answer(f"Ошибка: {exc}", show_alert=True)


@router.callback_query(F.data.startswith("pag:inst:"))
async def handle_installations_pagination(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Обработка перелистывания страниц списка установок."""
    await callback.answer()
    parts = callback.data.split(":")
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    show_archived = len(parts) > 3 and parts[3] == "arc"

    user_id = callback.from_user.id if callback.from_user else 0
    try:
        text, kb = await render_installations_list(
            user_id, api_client, page=page, show_archived=show_archived
        )
        if callback.message and isinstance(callback.message, Message):
            with contextlib.suppress(Exception):
                await callback.message.edit_text(text, reply_markup=kb)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Ошибка пагинации установок: {exc}")


# ================== Карточка установки ==================


@router.callback_query(F.data.startswith("inst:view:"))
async def handle_installation_view(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
    config: BotConfig,
) -> None:
    """Просмотр карточки установки."""
    await callback.answer()
    parts = callback.data.split(":")
    inst_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    user_id = callback.from_user.id if callback.from_user else 0

    try:
        inst = await api_client.get_installation(user_id, inst_id)
        me = await api_client.get_me(user_id)
        role = me.get("role", "guest") if me else "guest"
        is_archived = inst.get("is_archived", False)

        card_text = format_installation_card(
            inst, role=role, web_public_url=config.web_public_url
        )
        kb = get_installation_card_keyboard(inst_id, role=role, is_archived=is_archived)

        if callback.message and isinstance(callback.message, Message):
            with contextlib.suppress(Exception):
                await callback.message.edit_text(card_text, reply_markup=kb)
    except ApiError as exc:
        err = format_api_error(exc)
        if callback.message and isinstance(callback.message, Message):
            await callback.message.edit_text(f"Не удалось открыть установку:\n{err}")


# ================== Создание установки (Developer) ==================


@router.callback_query(F.data == "inst:create")
async def start_create_installation(
    callback: CallbackQuery,
    state: FSMContext,
    api_client: GrowVisionApiClient,
) -> None:
    """Начало мастера создания установки."""
    await callback.answer()
    user_id = callback.from_user.id if callback.from_user else 0
    me = await api_client.get_me(user_id)
    if not me or me.get("role") != "developer":
        await callback.answer(
            "Создавать установки может только Разработчик", show_alert=True
        )
        return

    await state.clear()
    await state.set_state(InstallationCreateStates.waiting_name)

    text = (
        "➕ <b>Создание новой установки</b>\n\n"
        "Шаг 1 из 5. Введите название установки (например: <code>Стеллаж А1</code>):"
    )
    if callback.message and isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.delete()
        await callback.message.answer(
            text, reply_markup=get_cancel_inline_keyboard("inst:list")
        )


@router.message(InstallationCreateStates.waiting_name)
async def process_create_name(
    message: Message,
    state: FSMContext,
    api_client: GrowVisionApiClient,
) -> None:
    name = (message.text or "").strip()
    if not name:
        await message.answer(
            "Название не может быть пустым. Введите название установки:",
            reply_markup=get_cancel_inline_keyboard("inst:list"),
        )
        return

    user_id = message.from_user.id if message.from_user else 0
    try:
        existing = await api_client.get_installations(user_id, include_archived=True)
        if any(inst.get("name") == name for inst in existing):
            await message.answer(
                f"Имя установки «{html.escape(name)}» уже занято. Введите другое название:",
                reply_markup=get_cancel_inline_keyboard("inst:list"),
            )
            return
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Ошибка проверки уникальности имени: {exc}")

    await state.update_data(name=name)
    await state.set_state(InstallationCreateStates.waiting_crop)
    await message.answer(
        f"Название: <b>{html.escape(name)}</b>\n\n"
        "Шаг 2 из 5. Введите выращиваемую культуру (например: <code>Базилик зеленый</code>):",
        reply_markup=get_cancel_inline_keyboard("inst:list"),
    )


@router.message(InstallationCreateStates.waiting_crop)
async def process_create_crop(message: Message, state: FSMContext) -> None:
    crop = (message.text or "").strip()
    if not crop:
        await message.answer(
            "Культура не может быть пустой. Введите название культуры:",
            reply_markup=get_cancel_inline_keyboard("inst:list"),
        )
        return
    await state.update_data(crop_type=crop)
    await state.set_state(InstallationCreateStates.waiting_light_pin)
    await message.answer(
        f"Культура: <b>{html.escape(crop)}</b>\n\n"
        "Шаг 3 из 5. Введите GPIO-пин основного света на Raspberry Pi (целое число, например: <code>17</code>):",
        reply_markup=get_cancel_inline_keyboard("inst:list"),
    )


@router.message(InstallationCreateStates.waiting_light_pin)
async def process_create_light_pin(message: Message, state: FSMContext) -> None:
    raw_pin = (message.text or "").strip()
    if not raw_pin.isdigit() or not (0 <= int(raw_pin) <= 53):
        await message.answer(
            "Пин света должен быть целым числом в диапазоне от 0 до 53 (например, 17). Повторите ввод:",
            reply_markup=get_cancel_inline_keyboard("inst:list"),
        )
        return
    light_pin = int(raw_pin)
    await state.update_data(light_gpio_pin=light_pin)
    await state.set_state(InstallationCreateStates.waiting_flash_pin)
    await message.answer(
        f"Пин света: <b>GPIO {light_pin}</b>\n\n"
        "Шаг 4 из 5. Введите GPIO-пин вспышки на Raspberry Pi (целое число от 0 до 53, например: <code>27</code>):",
        reply_markup=get_cancel_inline_keyboard("inst:list"),
    )


@router.message(InstallationCreateStates.waiting_flash_pin)
async def process_create_flash_pin(message: Message, state: FSMContext) -> None:
    raw_pin = (message.text or "").strip()
    if not raw_pin.isdigit() or not (0 <= int(raw_pin) <= 53):
        await message.answer(
            "Пин вспышки должен быть целым числом в диапазоне от 0 до 53 (например, 27). Повторите ввод:",
            reply_markup=get_cancel_inline_keyboard("inst:list"),
        )
        return
    flash_pin = int(raw_pin)
    data = await state.get_data()
    if flash_pin == data.get("light_gpio_pin"):
        await message.answer(
            "Пин вспышки не может совпадать с пином основного света! Введите другой пин:",
            reply_markup=get_cancel_inline_keyboard("inst:list"),
        )
        return

    await state.update_data(flash_gpio_pin=flash_pin)
    await state.set_state(InstallationCreateStates.waiting_aruco)

    aruco_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⏭ Пропустить",
                    callback_data="inst:skip_aruco",
                )
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="inst:list")],
        ]
    )
    await message.answer(
        f"Пин вспышки: <b>GPIO {flash_pin}</b>\n\n"
        "Шаг 5 из 5. Введите размер стороны маркера ArUco в миллиметрах (например: <code>50.0</code>) "
        "или нажмите [ Пропустить ]:",
        reply_markup=aruco_kb,
    )


async def _finish_create_installation(
    target_msg: Message,
    state: FSMContext,
    api_client: GrowVisionApiClient,
    config: BotConfig,
    aruco_val: float | None,
    user_id: int,
) -> None:
    data = await state.get_data()
    await state.clear()

    payload = {
        "name": data["name"],
        "culture_name": data["crop_type"],
        "light_gpio_pin": data["light_gpio_pin"],
        "flash_gpio_pin": data["flash_gpio_pin"],
        "aruco_size_mm": aruco_val,
    }

    try:
        created = await api_client.create_installation(user_id, payload)
        inst_id = created.get("id")
        card_text = format_installation_card(
            created, role="developer", web_public_url=config.web_public_url
        )
        kb = get_installation_card_keyboard(
            inst_id, role="developer", is_archived=False
        )
        await target_msg.answer(
            f"✅ Установка «<b>{html.escape(data['name'])}</b>» успешно создана!\n\n{card_text}",
            reply_markup=kb,
        )
    except ApiError as exc:
        err = format_api_error(exc)
        await target_msg.answer(
            f"❌ Не удалось создать установку:\n{err}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ К списку установок", callback_data="inst:list"
                        )
                    ]
                ]
            ),
        )


@router.callback_query(
    InstallationCreateStates.waiting_aruco, F.data == "inst:skip_aruco"
)
async def process_create_skip_aruco(
    callback: CallbackQuery,
    state: FSMContext,
    api_client: GrowVisionApiClient,
    config: BotConfig,
) -> None:
    """Пропуск ввода маркера ArUco (размер не задан, расчет площади в px)."""
    await callback.answer()
    user_id = callback.from_user.id if callback.from_user else 0
    if callback.message and isinstance(callback.message, Message):
        await _finish_create_installation(
            callback.message,
            state,
            api_client,
            config,
            aruco_val=None,
            user_id=user_id,
        )


@router.message(InstallationCreateStates.waiting_aruco)
async def process_create_aruco_text(
    message: Message,
    state: FSMContext,
    api_client: GrowVisionApiClient,
    config: BotConfig,
) -> None:
    """Ввод размера маркера ArUco числом."""
    raw = (message.text or "").strip().replace(",", ".")
    try:
        val = float(raw)
        if val <= 0:
            raise ValueError()
    except ValueError:
        await message.answer(
            "Размер маркера должен быть положительным числом (например, 50.0) или нажмите [ Пропустить ]:"
        )
        return

    user_id = message.from_user.id if message.from_user else 0
    await _finish_create_installation(
        message, state, api_client, config, aruco_val=val, user_id=user_id
    )


# ================== Редактирование установки ==================


@router.callback_query(F.data.startswith("inst:edit:"))
async def handle_installation_edit_menu(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Меню выбора поля для редактирования установки."""
    await callback.answer()
    parts = callback.data.split(":")
    inst_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    user_id = callback.from_user.id if callback.from_user else 0

    me = await api_client.get_me(user_id)
    role = me.get("role", "guest") if me else "guest"
    if role not in ("biologist", "developer"):
        await callback.answer("Недостаточно прав", show_alert=True)
        return

    kb = get_edit_fields_keyboard(inst_id, role=role)
    if callback.message and isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(
                "✏️ <b>Редактирование установки</b>\n\nВыберите поле для изменения:",
                reply_markup=kb,
            )


@router.callback_query(F.data.startswith("edit:fld:"))
async def handle_edit_field_selected(
    callback: CallbackQuery,
    state: FSMContext,
    api_client: GrowVisionApiClient,
    config: BotConfig,
) -> None:
    """Выбор конкретного поля для редактирования."""
    await callback.answer()
    parts = callback.data.split(":")
    field = parts[2]
    inst_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
    user_id = callback.from_user.id if callback.from_user else 0

    if field == "reset_aruco":
        try:
            updated = await api_client.update_installation(
                user_id, inst_id, {"aruco_size_mm": None}
            )
            card_text = format_installation_card(
                updated, role="developer", web_public_url=config.web_public_url
            )
            kb = get_installation_card_keyboard(
                inst_id, role="developer", is_archived=False
            )
            if callback.message and isinstance(callback.message, Message):
                await callback.message.edit_text(
                    f"✅ Маркер ArUco сброшен.\n\n{card_text}", reply_markup=kb
                )
        except ApiError as exc:
            err = format_api_error(exc)
            await callback.answer(f"Ошибка: {err}", show_alert=True)
        return

    await state.clear()
    await state.set_state(InstallationEditStates.waiting_value)
    await state.update_data(field=field, inst_id=inst_id)

    prompts = {
        "name": "Введите новое название установки:",
        "crop_type": "Введите новое название культуры:",
        "light_pin": "Введите новый GPIO-пин основного света (целое число):",
        "flash_pin": "Введите новый GPIO-пин вспышки (целое число):",
        "aruco": "Введите новый размер стороны ArUco в мм (число > 0):",
    }
    prompt = prompts.get(field, "Введите новое значение:")
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Отмена", callback_data=f"inst:view:{inst_id}"
                )
            ]
        ]
    )

    if callback.message and isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(prompt, reply_markup=cancel_kb)


@router.message(InstallationEditStates.waiting_value)
async def process_edit_value(
    message: Message,
    state: FSMContext,
    api_client: GrowVisionApiClient,
    config: BotConfig,
) -> None:
    """Обработка ввода нового значения для поля установки."""
    data = await state.get_data()
    field = data.get("field")
    inst_id = data.get("inst_id")
    val_text = (message.text or "").strip()
    user_id = message.from_user.id if message.from_user else 0

    patch_payload: dict = {}
    try:
        if field == "name":
            if not val_text:
                raise ValueError("Название не может быть пустым")
            patch_payload["name"] = val_text
        elif field == "crop_type":
            if not val_text:
                raise ValueError("Культура не может быть пустой")
            patch_payload["culture_name"] = val_text
        elif field == "light_pin":
            if not val_text.isdigit() or not (0 <= int(val_text) <= 53):
                raise ValueError("Пин света должен быть целым числом от 0 до 53")
            patch_payload["light_gpio_pin"] = int(val_text)
        elif field == "flash_pin":
            if not val_text.isdigit() or not (0 <= int(val_text) <= 53):
                raise ValueError("Пин вспышки должен быть целым числом от 0 до 53")
            patch_payload["flash_gpio_pin"] = int(val_text)
        elif field == "delay":
            if val_text.lower() in ("default", "none", "общая", "сброс"):
                patch_payload["capture_delay_sec"] = None
            else:
                v = float(
                    val_text.replace(",", ".").replace("с", "").replace("s", "").strip()
                )
                if not (0.0 <= v <= 30.0):
                    raise ValueError(
                        "Задержка съёмки должна быть в диапазоне 0.0..30.0 с"
                    )
                patch_payload["capture_delay_sec"] = v
        elif field == "aruco":
            v = float(val_text.replace(",", "."))
            if v <= 0:
                raise ValueError("Размер ArUco должен быть > 0")
            patch_payload["aruco_size_mm"] = v
    except ValueError as ve:
        await message.answer(f"Ошибка ввода: {ve}. Повторите ввод:")
        return

    await state.clear()
    try:
        updated = await api_client.update_installation(user_id, inst_id, patch_payload)
        me = await api_client.get_me(user_id)
        role = me.get("role", "guest") if me else "guest"
        card_text = format_installation_card(
            updated, role=role, web_public_url=config.web_public_url
        )
        kb = get_installation_card_keyboard(
            inst_id, role=role, is_archived=updated.get("is_archived", False)
        )
        await message.answer(
            f"✅ Изменения успешно сохранены!\n\n{card_text}", reply_markup=kb
        )
    except ApiError as exc:
        err = format_api_error(exc)
        await message.answer(
            f"❌ Не удалось обновить установку:\n{err}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ К карточке установки",
                            callback_data=f"inst:view:{inst_id}",
                        )
                    ]
                ]
            ),
        )


# ================== Архивация и удаление (Developer) ==================


@router.callback_query(F.data.startswith("inst:archive:"))
async def handle_archive_prompt(callback: CallbackQuery) -> None:
    await callback.answer()
    parts = callback.data.split(":")
    inst_id = int(parts[2])
    kb = get_confirm_inline_keyboard("archive", inst_id)
    if callback.message and isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(
                "📦 <b>Перевод установки в архив</b>\n\n"
                "Установка будет скрыта из активного мониторинга, автосъёмка прекратится. "
                "Вы уверены?",
                reply_markup=kb,
            )


@router.callback_query(F.data.startswith("inst:delete:"))
async def handle_delete_prompt(callback: CallbackQuery) -> None:
    await callback.answer()
    parts = callback.data.split(":")
    inst_id = int(parts[2])
    kb = get_confirm_inline_keyboard("delete", inst_id)
    if callback.message and isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(
                "🗑 <b>Удаление установки</b>\n\n"
                "Установка будет удалена безвозвратно. Если у неё уже есть снимки, "
                "удаление будет заблокировано сервером (в этом случае используйте архив).\n\n"
                "Вы подтверждаете удаление?",
                reply_markup=kb,
            )


@router.callback_query(F.data.startswith("cnf:"))
async def handle_confirm_action(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Обработка нажатия Да/Отмена на экранах подтверждения."""
    await callback.answer()
    parts = callback.data.split(":")
    # cnf:<action>:<target_id>:<yes|no>
    action = parts[1]
    inst_id = int(parts[2])
    confirmed = parts[3] == "yes"
    user_id = callback.from_user.id if callback.from_user else 0

    if not confirmed:
        # Отмена подтверждения: возвращаемся в карточку
        if callback.message and isinstance(callback.message, Message):
            callback.data = f"inst:view:{inst_id}"
            # перенаправляем на просмотр карточки
            await handle_installation_view(
                callback,
                api_client,
                config=BotConfig(bot_token="", server_url="", client_api_key=""),
            )
        return

    if action == "archive":
        try:
            await api_client.archive_installation(user_id, inst_id)
            if callback.message and isinstance(callback.message, Message):
                await callback.message.edit_text(
                    "📦 Установка успешно переведена в архив.",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text="⬅️ К списку установок",
                                    callback_data="inst:list",
                                )
                            ]
                        ]
                    ),
                )
        except ApiError as exc:
            err = format_api_error(exc)
            if callback.message and isinstance(callback.message, Message):
                await callback.message.edit_text(f"Ошибка архивации:\n{err}")

    elif action == "delete":
        try:
            await api_client.delete_installation(user_id, inst_id)
            if callback.message and isinstance(callback.message, Message):
                await callback.message.edit_text(
                    "🗑 Установка успешно удалена.",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text="⬅️ К списку установок",
                                    callback_data="inst:list",
                                )
                            ]
                        ]
                    ),
                )
        except ApiError as exc:
            err = format_api_error(exc)
            buttons = [
                [
                    InlineKeyboardButton(
                        text="⬅️ К карточке установки",
                        callback_data=f"inst:view:{inst_id}",
                    )
                ]
            ]
            if exc.status_code == 409:
                buttons.insert(
                    0,
                    [
                        InlineKeyboardButton(
                            text="📦 Перевести в архив",
                            callback_data=f"inst:archive:{inst_id}",
                        )
                    ],
                )
            if callback.message and isinstance(callback.message, Message):
                await callback.message.edit_text(
                    f"❌ Не удалось удалить установку:\n{err}",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                )


# ================== Камеры установки (Критерий 1) ==================


@router.callback_query(F.data.startswith("inst:cameras:"))
async def handle_installation_cameras(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Вывод списка привязанных к установке камер с их статусом (Критерий 1)."""
    await callback.answer()
    parts = callback.data.split(":")
    inst_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    user_id = callback.from_user.id if callback.from_user else 0

    try:
        inst = await api_client.get_installation(user_id, inst_id)
        inst_name = inst.get("name", f"Установка #{inst_id}")
        cameras = inst.get("cameras", [])

        lines = [f"📷 <b>Камеры установки «{html.escape(str(inst_name))}»</b>\n"]
        buttons: list[list[InlineKeyboardButton]] = []

        if not cameras:
            lines.append("К этой установке пока не привязано ни одной камеры.")
            lines.append("Вы можете привязать свободные камеры через раздел «Камеры».")
        else:
            lines.append(f"Всего привязано камер: <b>{len(cameras)}</b>\n")
            for c in cameras:
                cname = c.get("auto_name") or c.get("name") or f"Камера #{c.get('id')}"
                hw_id = c.get("hardware_id", "")
                online = c.get("is_online", True)
                status_str = "🟢 онлайн" if online else "🔴 оффлайн"
                cid = c.get("id")
                hw_part = (
                    f" | Hardware: <code>{html.escape(str(hw_id))}</code>"
                    if hw_id
                    else ""
                )
                lines.append(
                    f"• <b>{html.escape(str(cname))}</b> — {status_str}\n"
                    f"  ID: <code>{cid}</code>{hw_part}"
                )
                buttons.append(
                    [
                        InlineKeyboardButton(
                            text=f"⚙️ Камера #{cid} ({status_str})",
                            callback_data=f"cam:view:{cid}",
                        )
                    ]
                )

        buttons.append(
            [
                InlineKeyboardButton(
                    text="📷 Все камеры системы",
                    callback_data="cam:list",
                )
            ]
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    text="⬅️ К карточке установки",
                    callback_data=f"inst:view:{inst_id}",
                )
            ]
        )

        full_text = "\n".join(lines)
        if callback.message and isinstance(callback.message, Message):
            with contextlib.suppress(Exception):
                await callback.message.edit_text(
                    full_text,
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                )
    except Exception as exc:  # noqa: BLE001
        err = format_api_error(exc)
        if callback.message and isinstance(callback.message, Message):
            await callback.message.edit_text(
                f"Не удалось получить список камер установки:\n{err}",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="⬅️ К карточке установки",
                                callback_data=f"inst:view:{inst_id}",
                            )
                        ]
                    ]
                ),
            )


# ================== Задержка съёмки (Критерий 11) ==================


@router.callback_query(F.data.startswith("inst:delay:"))
@router.callback_query(F.data.startswith("edit:fld:delay:"))
async def handle_installation_delay_prompt(
    callback: CallbackQuery,
    state: FSMContext,
    api_client: GrowVisionApiClient,
) -> None:
    """Экран настройки задержки съёмки для конкретной установки."""
    await callback.answer()
    parts = callback.data.split(":")
    inst_id = int(parts[-1])
    user_id = callback.from_user.id if callback.from_user else 0

    try:
        inst = await api_client.get_installation(user_id, inst_id)
        inst_name = inst.get("name", f"Установка #{inst_id}")
        cur_delay = inst.get("capture_delay_sec")
        delay_display = (
            f"{cur_delay} с" if cur_delay is not None else "общая (по умолчанию)"
        )

        await state.set_state(CaptureDelayStates.waiting_delay)
        await state.update_data(target=str(inst_id), is_installation=True)

        text = (
            f"⏱ <b>Задержка перед съёмкой установки «{html.escape(str(inst_name))}»</b>\n\n"
            f"Текущее значение: <b>{delay_display}</b>\n\n"
            "Выберите пресет кнопкой ниже или введите значение числом от 0.0 до 30.0 с:"
        )
        kb = get_capture_delay_keyboard(target=str(inst_id), is_installation=True)

        if callback.message and isinstance(callback.message, Message):
            with contextlib.suppress(Exception):
                await callback.message.edit_text(text, reply_markup=kb)
    except Exception as exc:  # noqa: BLE001
        err = format_api_error(exc)
        if callback.message and isinstance(callback.message, Message):
            await callback.message.edit_text(
                f"Ошибка получения данных установки:\n{err}"
            )


@router.callback_query(F.data.startswith("ctrl:delay:"))
async def handle_control_global_delay_prompt(
    callback: CallbackQuery,
    state: FSMContext,
    api_client: GrowVisionApiClient,
) -> None:
    """Экран настройки задержки съёмки из меню управления (общая задержка)."""
    await callback.answer()
    parts = callback.data.split(":")
    target = parts[2]
    user_id = callback.from_user.id if callback.from_user else 0

    # Если target - это ID установки, настраиваем установку
    if target.isdigit():
        inst_id = int(target)
        inst = await api_client.get_installation(user_id, inst_id)
        inst_name = inst.get("name", f"Установка #{inst_id}")
        cur_delay = inst.get("capture_delay_sec")
        delay_display = (
            f"{cur_delay} с" if cur_delay is not None else "общая (по умолчанию)"
        )

        await state.set_state(CaptureDelayStates.waiting_delay)
        await state.update_data(target=str(inst_id), is_installation=True)

        text = (
            f"⏱ <b>Задержка перед съёмкой установки «{html.escape(str(inst_name))}»</b>\n\n"
            f"Текущее значение: <b>{delay_display}</b>\n\n"
            "Выберите пресет кнопкой ниже или введите значение числом от 0.0 до 30.0 с:"
        )
        kb = get_capture_delay_keyboard(target=str(inst_id), is_installation=True)
    else:
        # Глобальная настройка
        sys_status = await api_client.get_system_status(user_id)
        cur_delay = sys_status.get("settings", {}).get("capture_delay_sec", 1.5)

        await state.set_state(CaptureDelayStates.waiting_delay)
        await state.update_data(target="global", is_installation=False)

        text = (
            "⏱ <b>Глобальная задержка перед съёмкой (свет/вспышка)</b>\n\n"
            f"Текущее значение: <b>{cur_delay} с</b>\n\n"
            "Выберите пресет кнопкой ниже или введите значение числом от 0.0 до 30.0 с:"
        )
        kb = get_capture_delay_keyboard(target="global", is_installation=False)

    if callback.message and isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(text, reply_markup=kb)


@router.callback_query(F.data.startswith("set_delay:"))
async def handle_set_delay_preset(
    callback: CallbackQuery,
    state: FSMContext,
    api_client: GrowVisionApiClient,
    config: BotConfig,
) -> None:
    """Обработка выбора пресета задержки съёмки."""
    await callback.answer()
    parts = callback.data.split(":")
    target = parts[1]
    val_str = parts[2]
    user_id = callback.from_user.id if callback.from_user else 0

    await state.clear()

    try:
        if target.isdigit():
            inst_id = int(target)
            if val_str == "default":
                patch_payload = {"capture_delay_sec": None}
                res_text = "Задержка съёмки сброшена на общую системную настройку."
            else:
                delay_sec = float(val_str)
                patch_payload = {"capture_delay_sec": delay_sec}
                res_text = f"Задержка съёмки успешно установлена: <b>{delay_sec} с</b>."

            updated = await api_client.update_installation(
                user_id, inst_id, patch_payload
            )
            me = await api_client.get_me(user_id)
            role = me.get("role", "guest") if me else "guest"
            card_text = format_installation_card(
                updated, role=role, web_public_url=config.web_public_url
            )
            kb = get_installation_card_keyboard(
                inst_id, role=role, is_archived=updated.get("is_archived", False)
            )
            if callback.message and isinstance(callback.message, Message):
                await callback.message.edit_text(
                    f"✅ {res_text}\n\n{card_text}", reply_markup=kb
                )
        else:
            delay_sec = float(val_str)
            await api_client.update_system_settings(
                user_id, capture_delay_sec=delay_sec
            )
            from clients.telegram_bot.keyboards.inline import get_control_panel_keyboard

            kb = get_control_panel_keyboard(target="all")
            if callback.message and isinstance(callback.message, Message):
                await callback.message.edit_text(
                    f"✅ Глобальная задержка съёмки установлена на <b>{delay_sec} с</b>.",
                    reply_markup=kb,
                )
    except Exception as exc:  # noqa: BLE001
        err = format_api_error(exc)
        if callback.message and isinstance(callback.message, Message):
            await callback.message.answer(f"❌ Ошибка установки задержки:\n{err}")


@router.message(CaptureDelayStates.waiting_delay)
async def process_capture_delay_input(
    message: Message,
    state: FSMContext,
    api_client: GrowVisionApiClient,
    config: BotConfig,
) -> None:
    """Обработка ввода задержки съёмки сообщением (0.0..30.0 с без сброса FSM)."""
    raw_text = (message.text or "").strip()
    data = await state.get_data()
    target = data.get("target", "global")
    is_inst = data.get("is_installation", False)
    user_id = message.from_user.id if message.from_user else 0

    # Парсим значение
    val_clean = raw_text.replace(",", ".").replace("с", "").replace("s", "").strip()
    try:
        val_sec = float(val_clean)
        if not (0.0 <= val_sec <= 30.0):
            raise ValueError("Число вне диапазона")
    except ValueError:
        kb = get_capture_delay_keyboard(target=target, is_installation=is_inst)
        await message.answer(
            "❌ Значение задержки должно быть числом от 0.0 до 30.0 с (например, <code>1.5</code>).\n"
            "Повторите ввод:",
            reply_markup=kb,
        )
        return

    await state.clear()

    try:
        if is_inst and target.isdigit():
            inst_id = int(target)
            patch_payload = {"capture_delay_sec": val_sec}
            updated = await api_client.update_installation(
                user_id, inst_id, patch_payload
            )
            me = await api_client.get_me(user_id)
            role = me.get("role", "guest") if me else "guest"
            card_text = format_installation_card(
                updated, role=role, web_public_url=config.web_public_url
            )
            kb = get_installation_card_keyboard(
                inst_id, role=role, is_archived=updated.get("is_archived", False)
            )
            await message.answer(
                f"✅ Задержка съёмки успешно установлена: <b>{val_sec} с</b>.\n\n{card_text}",
                reply_markup=kb,
            )
        else:
            await api_client.update_system_settings(user_id, capture_delay_sec=val_sec)
            from clients.telegram_bot.keyboards.inline import get_control_panel_keyboard

            kb = get_control_panel_keyboard(target="all")
            await message.answer(
                f"✅ Глобальная задержка съёмки установлена на <b>{val_sec} с</b>.",
                reply_markup=kb,
            )
    except Exception as exc:  # noqa: BLE001
        err = format_api_error(exc)
        await message.answer(f"❌ Ошибка установки задержки:\n{err}")


# ================== Заглушки пагинации и служебных кнопок (Критерий 1) ==================


@router.callback_query(F.data.in_({"pag:noop", "frm:noop"}))
async def handle_noop_callbacks(callback: CallbackQuery) -> None:
    """Явный ответ на клики по служебным кнопкам без действия (Критерий 1)."""
    await callback.answer()
