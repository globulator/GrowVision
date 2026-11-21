import contextlib
import logging

from aiogram import F, Router
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InputMediaPhoto,
    Message,
)

from clients.telegram_bot.api_client import GrowVisionApiClient
from clients.telegram_bot.keyboards.inline import get_control_panel_keyboard
from clients.telegram_bot.keyboards.paginator import build_paginator_keyboard
from clients.telegram_bot.utils.errors import ApiError, format_api_error
from clients.telegram_bot.utils.formatters import (
    format_area_display,
    format_debug_block,
)

logger = logging.getLogger(__name__)

router = Router(name="control")


# ================== Свет и реле (Developer) ==================


@router.message(F.text == "💡 Свет и реле")
async def handle_control_menu(
    message: Message,
    api_client: GrowVisionApiClient,
) -> None:
    """Выбор цели для управления светом/реле."""
    user_id = message.from_user.id if message.from_user else 0
    me = await api_client.get_me(user_id)
    if not me or me.get("role") != "developer":
        await message.answer(
            "Управление реле и светом доступно только для Разработчика."
        )
        return

    try:
        active_insts = await api_client.get_installations(
            user_id, include_archived=False
        )
        if not active_insts:
            await message.answer("Нет активных установок для управления светом.")
            return

        kb = build_paginator_keyboard(
            items=active_insts,
            page=0,
            page_size=5,
            entity="ctrl_pick",
            item_callback_prefix="ctrl:target",
            item_text_func=lambda i: f"🌱 {i.get('name')} (#{i.get('id')})",
            item_id_func=lambda i: i.get("id"),
            include_all_button=True,
            all_button_callback="ctrl:target:all",
            all_button_text="🌐 Все активные установки",
        )
        await message.answer(
            "💡 <b>Управление светом и реле</b>\n\nВыберите целевую установку или все активные:",
            reply_markup=kb,
        )
    except Exception as exc:  # noqa: BLE001
        err = format_api_error(exc)
        await message.answer(f"Ошибка загрузки установок:\n{err}")


@router.callback_query(F.data == "ctrl:menu")
async def handle_control_back_to_menu(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Возврат к выбору цели управления."""
    await callback.answer()
    user_id = callback.from_user.id if callback.from_user else 0
    try:
        active_insts = await api_client.get_installations(
            user_id, include_archived=False
        )
        kb = build_paginator_keyboard(
            items=active_insts,
            page=0,
            page_size=5,
            entity="ctrl_pick",
            item_callback_prefix="ctrl:target",
            item_text_func=lambda i: f"🌱 {i.get('name')} (#{i.get('id')})",
            item_id_func=lambda i: i.get("id"),
            include_all_button=True,
            all_button_callback="ctrl:target:all",
            all_button_text="🌐 Все активные установки",
        )
        if callback.message and isinstance(callback.message, Message):
            with contextlib.suppress(Exception):
                await callback.message.edit_text(
                    "💡 <b>Управление светом и реле</b>\n\nВыберите целевую установку или все активные:",
                    reply_markup=kb,
                )
    except Exception as exc:  # noqa: BLE001
        await callback.answer(f"Ошибка: {exc}", show_alert=True)


@router.callback_query(F.data.startswith("inst:control:"))
async def handle_control_direct_from_card(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Переход в управление реле прямо из карточки конкретной установки."""
    await callback.answer()
    parts = callback.data.split(":")
    inst_id = parts[2]
    user_id = callback.from_user.id if callback.from_user else 0

    inst_name = f"установкой #{inst_id}"
    with contextlib.suppress(Exception):
        inst = await api_client.get_installation(user_id, int(inst_id))
        inst_name = f"«{inst.get('name')}»"

    kb = get_control_panel_keyboard(target=inst_id)
    if callback.message and isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(
                f"💡 <b>Управление светом и реле</b> для {inst_name}:",
                reply_markup=kb,
            )


@router.callback_query(F.data.startswith("ctrl:target:"))
async def handle_control_target_selected(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Выбор цели (all или ID) и показ панели кнопок света."""
    await callback.answer()
    parts = callback.data.split(":")
    target = parts[2]
    user_id = callback.from_user.id if callback.from_user else 0

    if target == "all":
        target_title = "всеми активными установками"
    else:
        target_title = f"установкой #{target}"
        with contextlib.suppress(Exception):
            inst = await api_client.get_installation(user_id, int(target))
            target_title = f"«{inst.get('name')}»"

    kb = get_control_panel_keyboard(target=target)
    if callback.message and isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(
                f"💡 <b>Управление реле</b> для: {target_title}",
                reply_markup=kb,
            )


@router.callback_query(F.data.startswith("ctrl:light:"))
async def handle_control_light_action(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Включение/выключение основного света."""
    await callback.answer()
    parts = callback.data.split(":")
    # Поддерживаем форматы ctrl:light:<1|0>:<target> и ctrl:light:<target>:<on|off>
    p1 = parts[2]
    p2 = parts[3] if len(parts) > 3 else "1"
    if p1.lower() in ("1", "0", "on", "off", "true", "false"):
        state_bool = p1.lower() in ("1", "on", "true")
        target = p2
    else:
        state_bool = p2.lower() in ("1", "on", "true")
        target = p1
    user_id = callback.from_user.id if callback.from_user else 0

    state_text = "включение" if state_bool else "выключение"
    if callback.message and isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(
                f"⏳ Выполняется {state_text} основного света..."
            )

    try:
        resp = await api_client.control_light_main(user_id, target, state_bool)
        res_msg = resp.get("result_message", "Успешно")
        debug_log = resp.get("debug_log_line")

        # Проверяем debug_mode
        sys_status = await api_client.get_system_status(user_id)
        debug_on = sys_status.get("settings", {}).get("debug_mode", False)
        debug_str = (
            format_debug_block("POST /api/control/light/main", debug_log)
            if debug_on
            else ""
        )

        kb = get_control_panel_keyboard(target=target)
        if callback.message and isinstance(callback.message, Message):
            await callback.message.edit_text(
                f"💡 <b>Результат:</b> {res_msg}{debug_str}",
                reply_markup=kb,
            )
    except ApiError as exc:
        err = format_api_error(exc, is_control=True)
        kb = get_control_panel_keyboard(target=target)
        if callback.message and isinstance(callback.message, Message):
            await callback.message.edit_text(
                f"❌ Ошибка управления светом:\n{err}",
                reply_markup=kb,
            )


@router.callback_query(F.data.startswith("ctrl:flash:"))
async def handle_control_flash_action(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Включение/выключение вспышки."""
    await callback.answer()
    parts = callback.data.split(":")
    # Поддерживаем форматы ctrl:flash:<1|0>:<target> и ctrl:flash:<target>:<on|off>
    p1 = parts[2]
    p2 = parts[3] if len(parts) > 3 else "1"
    if p1.lower() in ("1", "0", "on", "off", "true", "false"):
        state_bool = p1.lower() in ("1", "on", "true")
        target = p2
    else:
        state_bool = p2.lower() in ("1", "on", "true")
        target = p1
    user_id = callback.from_user.id if callback.from_user else 0

    state_text = "включение" if state_bool else "выключение"
    if callback.message and isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(f"⏳ Выполняется {state_text} вспышки...")

    try:
        resp = await api_client.control_light_flash(user_id, target, state_bool)
        res_msg = resp.get("result_message", "Успешно")
        debug_log = resp.get("debug_log_line")

        sys_status = await api_client.get_system_status(user_id)
        debug_on = sys_status.get("settings", {}).get("debug_mode", False)
        debug_str = (
            format_debug_block("POST /api/control/light/flash", debug_log)
            if debug_on
            else ""
        )

        kb = get_control_panel_keyboard(target=target)
        if callback.message and isinstance(callback.message, Message):
            await callback.message.edit_text(
                f"⚡ <b>Результат:</b> {res_msg}{debug_str}",
                reply_markup=kb,
            )
    except ApiError as exc:
        err = format_api_error(exc, is_control=True)
        kb = get_control_panel_keyboard(target=target)
        if callback.message and isinstance(callback.message, Message):
            await callback.message.edit_text(
                f"❌ Ошибка управления вспышкой:\n{err}",
                reply_markup=kb,
            )


@router.callback_query(F.data.startswith("ctrl:relay:"))
async def handle_control_relay_test(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Кратковременный тест реле (3 секунды)."""
    await callback.answer()
    parts = callback.data.split(":")
    target = parts[2]
    user_id = callback.from_user.id if callback.from_user else 0

    if callback.message and isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(
                "⏳ <b>Выполняется тест реле (3 сек)...</b>\nПины включены для проверки."
            )

    try:
        resp = await api_client.control_relay_test(user_id, target)
        res_msg = resp.get("result_message", "Тест реле завершен")
        debug_log = resp.get("debug_log_line")

        sys_status = await api_client.get_system_status(user_id)
        debug_on = sys_status.get("settings", {}).get("debug_mode", False)
        debug_str = (
            format_debug_block("POST /api/control/relay-test", debug_log)
            if debug_on
            else ""
        )

        kb = get_control_panel_keyboard(target=target)
        if callback.message and isinstance(callback.message, Message):
            await callback.message.edit_text(
                f"🧪 <b>Результат теста:</b> {res_msg}{debug_str}",
                reply_markup=kb,
            )
    except ApiError as exc:
        err = format_api_error(exc, is_control=True)
        kb = get_control_panel_keyboard(target=target)
        if callback.message and isinstance(callback.message, Message):
            await callback.message.edit_text(
                f"❌ Ошибка теста реле:\n{err}",
                reply_markup=kb,
            )


# ================== Ручной снимок (Biologist / Developer) ==================


@router.message(F.text == "📸 Сделать снимок")
async def handle_snap_menu(
    message: Message,
    api_client: GrowVisionApiClient,
) -> None:
    """Меню выбора цели для ручного снимка."""
    user_id = message.from_user.id if message.from_user else 0
    me = await api_client.get_me(user_id)
    if not me or me.get("role") not in ("biologist", "developer"):
        await message.answer("Съёмка доступна для Биолога и Разработчика.")
        return

    try:
        active_insts = await api_client.get_installations(
            user_id, include_archived=False
        )
        if not active_insts:
            await message.answer("Нет активных установок для съёмки.")
            return

        kb = build_paginator_keyboard(
            items=active_insts,
            page=0,
            page_size=5,
            entity="snap_pick",
            item_callback_prefix="snap:target",
            item_text_func=lambda i: f"🌱 {i.get('name')} (#{i.get('id')})",
            item_id_func=lambda i: i.get("id"),
            include_all_button=True,
            all_button_callback="snap:target:all",
            all_button_text="🌐 Снять все установки",
        )
        await message.answer(
            "📸 <b>Ручная фотосъёмка растений</b>\n\nВыберите целевую установку или снимите все сразу:",
            reply_markup=kb,
        )
    except Exception as exc:  # noqa: BLE001
        err = format_api_error(exc)
        await message.answer(f"Ошибка загрузки установок:\n{err}")


@router.callback_query(F.data.startswith("inst:snap:"))
async def handle_snap_from_card(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Быстрый снимок конкретной установки из её карточки."""
    await callback.answer()
    parts = callback.data.split(":")
    inst_id = parts[2]
    # Перенаправляем на логику snap
    await _execute_snap(callback, api_client, target=inst_id)


@router.callback_query(F.data.startswith("snap:target:"))
async def handle_snap_target_selected(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Выбор цели снимка из списка."""
    await callback.answer()
    parts = callback.data.split(":")
    target = parts[2]
    await _execute_snap(callback, api_client, target=target)


async def _execute_snap(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
    target: str,
) -> None:
    """Выполнение съёмки кадра, скачивание и отправка альбомом."""
    user_id = callback.from_user.id if callback.from_user else 0
    me = await api_client.get_me(user_id)
    role = me.get("role", "guest") if me else "guest"

    # Показываем индикатор ожидания
    status_msg: Message | None = None
    if callback.message and isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            status_msg = await callback.message.answer(
                "⏳ <b>Делаю снимок, подождите...</b>\n"
                "Выполняется переключение реле, прогрев камеры и детекция нейросетью."
            )

    try:
        active_insts = await api_client.get_installations(
            user_id, include_archived=False
        )
        active_map = {i.get("id"): i.get("name") for i in active_insts}
        active_count = len(active_insts)

        resp = await api_client.take_snap(
            user_id, target=target, active_count=active_count
        )

        status_result = resp.get("status", "done")
        result_message = resp.get("result_message", "")
        captures = resp.get("captures", [])
        debug_log = resp.get("debug_log_line")

        if status_result == "error":
            # При ошибке съёмки выводим result_message
            if status_msg:
                await status_msg.edit_text(
                    f"❌ <b>Ошибка съёмки:</b> {result_message or 'Камера недоступна, попробуйте позже'}"
                )
            return

        # Удаляем индикатор ожидания
        if status_msg:
            with contextlib.suppress(Exception):
                await status_msg.delete()

        if not captures:
            target_chat = callback.message.chat.id if callback.message else user_id
            await callback.bot.send_message(
                target_chat,
                f"✅ Снимок выполнен, но кадры не были возвращены сервером: {result_message}",
            )
            return

        # Проверяем debug_mode для Developer
        debug_str = ""
        if role == "developer":
            with contextlib.suppress(Exception):
                sys_status = await api_client.get_system_status(user_id)
                if sys_status.get("settings", {}).get("debug_mode", False):
                    debug_str = format_debug_block("POST /api/control/snap", debug_log)

        # Маппинг камер
        cam_map = {}
        with contextlib.suppress(Exception):
            cams = await api_client.get_cameras(user_id)
            cam_map = {
                c.get("id"): c.get("hardware_id") or c.get("auto_name") for c in cams
            }

        target_chat = callback.message.chat.id if callback.message else user_id

        # Отправляем каждый снимок альбомом из двух фото (оригинал + разметка)
        for idx, cap in enumerate(captures):
            cap_inst_id = cap.get("installation_id")
            inst_name = active_map.get(cap_inst_id, f"Установка #{cap_inst_id}")
            cid = cap.get("camera_id")
            cam_hw_id = (
                cap.get("camera_hardware_id")
                or cam_map.get(cid)
                or (f"камера #{cid}" if cid else "cam")
            )
            cap_time = cap.get("captured_at", "—")

            if cap.get("area_display"):
                area_text = cap.get("area_display")
            else:
                area_cm2 = cap.get("total_area_cm2")
                area_px = cap.get("total_area_px")
                if area_cm2 is not None:
                    area_text = format_area_display(area_cm2, is_px=False)
                elif area_px is not None:
                    area_text = format_area_display(area_px, is_px=True)
                else:
                    area_text = "площадь не определена"

            is_last = idx == len(captures) - 1
            caption_lines = [
                f"🌱 <b>Установка:</b> {inst_name}",
                f"📷 <b>Камера:</b> <code>{cam_hw_id}</code>",
                f"🕒 <b>Время съёмки:</b> {cap_time} UTC",
                f"📐 <b>Площадь:</b> {area_text}",
            ]

            if is_last:
                caption_lines.append("\n<i>(Ручные снимки не входят в таймлапс)</i>")
                if debug_str:
                    caption_lines.append(debug_str)

            caption = "\n".join(caption_lines)

            # Скачиваем сырой и аннотированный кадры
            raw_url = cap.get("raw_url", "")
            ann_url = cap.get("annotated_url", "")

            raw_bytes = await api_client.download_file(user_id, raw_url)
            ann_bytes = await api_client.download_file(user_id, ann_url)

            media = [
                InputMediaPhoto(
                    media=BufferedInputFile(raw_bytes, filename=f"raw_{idx}.jpg"),
                    caption=caption,
                    parse_mode="HTML",
                ),
                InputMediaPhoto(
                    media=BufferedInputFile(ann_bytes, filename=f"ann_{idx}.jpg"),
                ),
            ]
            await callback.bot.send_media_group(chat_id=target_chat, media=media)

    except ApiError as exc:
        err = format_api_error(exc, is_control=True)
        if status_msg:
            await status_msg.edit_text(f"❌ Не удалось выполнить снимок:\n{err}")
        elif callback.message and isinstance(callback.message, Message):
            await callback.message.answer(f"❌ Не удалось выполнить снимок:\n{err}")
    except Exception as exc:  # noqa: BLE001
        err = format_api_error(exc, is_control=True)
        if status_msg:
            await status_msg.edit_text(f"❌ Не удалось выполнить снимок:\n{err}")
        elif callback.message and isinstance(callback.message, Message):
            await callback.message.answer(f"❌ Не удалось выполнить снимок:\n{err}")
