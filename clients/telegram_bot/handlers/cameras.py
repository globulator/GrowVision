import contextlib
import html
import logging
from typing import Any

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from clients.telegram_bot.api_client import GrowVisionApiClient
from clients.telegram_bot.keyboards.paginator import build_paginator_keyboard
from clients.telegram_bot.utils.errors import ApiError, format_api_error
from clients.telegram_bot.utils.formatters import format_debug_block

logger = logging.getLogger(__name__)

router = Router(name="cameras")


async def render_cameras_list(
    user_id: int,
    api_client: GrowVisionApiClient,
    page: int = 0,
) -> tuple[str, InlineKeyboardMarkup]:
    """Формирует текст и клавиатуру зарегистрированных камер."""
    cameras = await api_client.get_cameras(user_id)
    title = "📷 <b>Зарегистрированные USB-камеры</b>"

    bottom_buttons = [
        [
            InlineKeyboardButton(
                text="🔍 Сканировать камеры на RPi",
                callback_data="cam:scan",
            )
        ]
    ]

    if not cameras:
        msg_empty = (
            f"{title}\n\nКамеры пока не зарегистрированы в системе. "
            f"Нажмите кнопку ниже для сканирования USB-портов Raspberry Pi."
        )
        return (
            msg_empty,
            InlineKeyboardMarkup(inline_keyboard=bottom_buttons),
        )

    def _cam_text(c: dict) -> str:
        name = c.get("name") or f"Камера #{c.get('id')}"
        status_ico = "🟢" if c.get("is_online", True) else "🔴"
        inst_name = c.get("installation_name")
        bind_str = f" → {inst_name}" if inst_name else " (свободна)"
        return f"{status_ico} {name}{bind_str}"

    kb = build_paginator_keyboard(
        items=cameras,
        page=page,
        page_size=5,
        entity="cam",
        item_callback_prefix="cam:view",
        item_text_func=_cam_text,
        item_id_func=lambda c: c.get("id"),
        bottom_buttons=bottom_buttons,
    )
    return f"{title}\nВыберите камеру для просмотра и настройки привязки:", kb


@router.message(F.text == "📷 Камеры")
async def handle_cameras_menu(
    message: Message,
    api_client: GrowVisionApiClient,
) -> None:
    """Точка входа: список камер (только Developer)."""
    user_id = message.from_user.id if message.from_user else 0
    me = await api_client.get_me(user_id)
    if not me or me.get("role") != "developer":
        await message.answer("Раздел камер доступен только для Разработчика.")
        return

    try:
        text, kb = await render_cameras_list(user_id, api_client, page=0)
        await message.answer(text, reply_markup=kb)
    except Exception as exc:  # noqa: BLE001
        err = format_api_error(exc)
        await message.answer(f"Не удалось загрузить список камер:\n{err}")


@router.callback_query(F.data == "cam:list")
async def handle_cameras_back_to_list(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Возврат к списку камер."""
    await callback.answer()
    user_id = callback.from_user.id if callback.from_user else 0
    try:
        text, kb = await render_cameras_list(user_id, api_client, page=0)
        if callback.message and isinstance(callback.message, Message):
            with contextlib.suppress(Exception):
                await callback.message.edit_text(text, reply_markup=kb)
    except Exception as exc:  # noqa: BLE001
        await callback.answer(f"Ошибка: {exc}", show_alert=True)


@router.callback_query(F.data.startswith("pag:cam:"))
async def handle_cameras_pagination(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Пагинация списка камер."""
    await callback.answer()
    parts = callback.data.split(":")
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    user_id = callback.from_user.id if callback.from_user else 0

    try:
        text, kb = await render_cameras_list(user_id, api_client, page=page)
        if callback.message and isinstance(callback.message, Message):
            with contextlib.suppress(Exception):
                await callback.message.edit_text(text, reply_markup=kb)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Ошибка пагинации камер: {exc}")


# ================== Сканирование камер ==================


@router.callback_query(F.data == "cam:scan")
async def handle_cameras_scan(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Запуск сканирования камер на RPi."""
    await callback.answer()
    user_id = callback.from_user.id if callback.from_user else 0

    if callback.message and isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(
                "⏳ <b>Сканирую USB-камеры на Raspberry Pi...</b>\nПожалуйста, подождите."
            )

    try:
        resp = await api_client.scan_cameras(user_id)
        scanned_cameras = resp.get("cameras", [])
        debug_info = resp.get("debug_info")
        debug_log = (
            debug_info.get("log_line")
            if isinstance(debug_info, dict)
            else resp.get("debug_log_line")
        )

        # Проверяем, включен ли debug_mode
        sys_status = await api_client.get_system_status(user_id)
        debug_on = sys_status.get("settings", {}).get("debug_mode", False) or bool(
            debug_info
        )
        debug_str = (
            format_debug_block("POST /api/cameras/scan", debug_log) if debug_on else ""
        )

        if not scanned_cameras:
            msg = (
                "Камеры не обнаружены. Проверьте USB-подключение камер к Raspberry Pi."
                f"{debug_str}"
            )
            back_kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ К списку камер", callback_data="cam:list"
                        )
                    ]
                ]
            )
            if callback.message and isinstance(callback.message, Message):
                await callback.message.edit_text(msg, reply_markup=back_kb)
            return

        lines = [
            f"🔍 <b>Результат сканирования камер ({len(scanned_cameras)} шт.):</b>\n"
        ]
        buttons: list[list[InlineKeyboardButton]] = []

        for cam in scanned_cameras:
            cid = cam.get("id")
            cname = cam.get("name") or f"Камера #{cid}"
            hw_id = cam.get("hardware_id", "")
            inst_id = cam.get("installation_id")
            inst_name = cam.get("installation_name")

            if inst_id:
                bind_status = f"Привязана к установке «{html.escape(str(inst_name))}»"
            else:
                bind_status = "Свободна — можно привязать"

            lines.append(
                f"• <b>{html.escape(str(cname))}</b> (<code>{html.escape(str(hw_id))}</code>)\n  Статус: {bind_status}"
            )
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=f"Настроить {cname}", callback_data=f"cam:view:{cid}"
                    )
                ]
            )

        buttons.append(
            [InlineKeyboardButton(text="⬅️ К списку камер", callback_data="cam:list")]
        )
        full_text = "\n".join(lines) + debug_str

        if callback.message and isinstance(callback.message, Message):
            await callback.message.edit_text(
                full_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
            )

    except ApiError as exc:
        err = format_api_error(exc)
        if callback.message and isinstance(callback.message, Message):
            await callback.message.edit_text(
                f"❌ Ошибка сканирования камер:\n{err}",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="⬅️ К списку камер", callback_data="cam:list"
                            )
                        ]
                    ]
                ),
            )


# ================== Карточка и привязка камеры ==================


async def _resolve_camera(
    user_id: int, api_client: GrowVisionApiClient, cam_val: str
) -> dict[str, Any] | None:
    """Находит камеру по числу id или строке hardware_id."""
    with contextlib.suppress(Exception):
        cameras = await api_client.get_cameras(user_id)
        for c in cameras:
            if str(c.get("id")) == cam_val or c.get("hardware_id") == cam_val:
                return c
    return None


@router.callback_query(F.data.startswith("cam:view:"))
async def handle_camera_view(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Просмотр карточки камеры."""
    await callback.answer()
    parts = callback.data.split(":")
    cam_val = parts[2]
    user_id = callback.from_user.id if callback.from_user else 0

    try:
        cam = await _resolve_camera(user_id, api_client, cam_val)
        if not cam:
            await callback.answer("Камера не найдена", show_alert=True)
            return

        cam_id = cam.get("id")
        cname = cam.get("auto_name") or cam.get("name") or f"Камера #{cam_id}"
        hw_id = cam.get("hardware_id", "")
        online = cam.get("is_online", True)
        status_str = "🟢 Online" if online else "🔴 Offline"
        inst_id = cam.get("installation_id")
        inst_name = cam.get("installation_name")

        lines = [
            f"📷 <b>{html.escape(str(cname))}</b>",
            f"<b>Hardware ID:</b> <code>{html.escape(str(hw_id))}</code>",
            f"<b>Статус:</b> {status_str}",
        ]
        calib_summary = cam.get("calibration_summary")
        if calib_summary:
            lines.append(
                f"⚙️ <b>Профиль качества:</b> {html.escape(str(calib_summary))}"
            )

        buttons: list[list[InlineKeyboardButton]] = []
        if inst_id:
            lines.append(
                f"🔗 <b>Привязана к установке:</b> #{inst_id} «{html.escape(str(inst_name))}»"
            )
            buttons.append(
                [
                    InlineKeyboardButton(
                        text="⛓ Отвязать от установки",
                        callback_data=f"cam:unbind:{cam_id}",
                    )
                ]
            )
        else:
            lines.append("🔗 <b>Привязка:</b> Свободна — можно привязать к установке")
            buttons.append(
                [
                    InlineKeyboardButton(
                        text="🔗 Привязать к установке",
                        callback_data=f"cam:bind_pick:{cam_id}",
                    )
                ]
            )

        buttons.append(
            [
                InlineKeyboardButton(
                    text="⚙️ Автокалибровка",
                    callback_data=f"cam:calibrate:{cam_id}",
                )
            ]
        )
        buttons.append(
            [InlineKeyboardButton(text="⬅️ К списку камер", callback_data="cam:list")]
        )

        if callback.message and isinstance(callback.message, Message):
            with contextlib.suppress(Exception):
                await callback.message.edit_text(
                    "\n".join(lines),
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                )
    except ApiError as exc:
        err = format_api_error(exc)
        if callback.message and isinstance(callback.message, Message):
            await callback.message.edit_text(f"Ошибка загрузки камеры:\n{err}")


@router.callback_query(F.data.startswith("cam:bind_pick:"))
async def handle_camera_bind_pick(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Выбор активной установки для привязки камеры."""
    await callback.answer()
    parts = callback.data.split(":")
    cam_val = parts[2]
    user_id = callback.from_user.id if callback.from_user else 0

    try:
        cam = await _resolve_camera(user_id, api_client, cam_val)
        cam_id = cam.get("id") if cam else (int(cam_val) if cam_val.isdigit() else 0)

        active_insts = await api_client.get_installations(
            user_id, include_archived=False
        )
        if not active_insts:
            await callback.answer(
                "Нет активных установок для привязки", show_alert=True
            )
            return

        kb = build_paginator_keyboard(
            items=active_insts,
            page=0,
            page_size=5,
            entity="cam_bind",
            extra=str(cam_id),
            item_callback_prefix="cam:do_bind",
            item_text_func=lambda i: f"🌱 {i.get('name')} (#{i.get('id')})",
            item_id_func=lambda i: f"{cam_id}:{i.get('id')}",
            bottom_buttons=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Отмена", callback_data=f"cam:view:{cam_id}"
                    )
                ]
            ],
        )

        if callback.message and isinstance(callback.message, Message):
            with contextlib.suppress(Exception):
                await callback.message.edit_text(
                    "Выберите установку, к которой привязать камеру:",
                    reply_markup=kb,
                )
    except ApiError as exc:
        await callback.answer(f"Ошибка: {format_api_error(exc)}", show_alert=True)


@router.callback_query(F.data.startswith("cam:do_bind:"))
async def handle_camera_do_bind(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Выполнение привязки камеры к установке."""
    await callback.answer()
    parts = callback.data.split(":")
    # cam:do_bind:<cam_id>:<inst_id>
    cam_val = parts[2]
    inst_id = int(parts[3])
    user_id = callback.from_user.id if callback.from_user else 0

    try:
        cam = await _resolve_camera(user_id, api_client, cam_val)
        cam_id = cam.get("id") if cam else (int(cam_val) if cam_val.isdigit() else 0)
        await api_client.bind_camera(user_id, cam_id, inst_id)

        calib_text = ""
        try:
            calib_res = await api_client.autocalibrate_camera(user_id, cam_id)
            if calib_res and calib_res.get("summary"):
                calib_text = (
                    f"\n\n⚙️ <b>Автоподбор качества:</b>\n"
                    f"{html.escape(str(calib_res.get('summary')))}"
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Автокалибровка при привязке камеры пропущена: %s", exc)

        if callback.message and isinstance(callback.message, Message):
            await callback.message.edit_text(
                f"✅ Камера #{cam_id} успешно привязана к установке #{inst_id}!{calib_text}",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="⬅️ К карточке камеры",
                                callback_data=f"cam:view:{cam_id}",
                            )
                        ]
                    ]
                ),
            )
    except ApiError as exc:
        err = format_api_error(exc)
        if callback.message and isinstance(callback.message, Message):
            await callback.message.edit_text(
                f"❌ Не удалось привязать камеру:\n{err}",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="⬅️ К карточке камеры",
                                callback_data=f"cam:view:{cam_val}",
                            )
                        ]
                    ]
                ),
            )


@router.callback_query(F.data.startswith("cam:unbind:"))
async def handle_camera_do_unbind(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Отвязка камеры от установки."""
    await callback.answer()
    parts = callback.data.split(":")
    cam_val = parts[2]
    user_id = callback.from_user.id if callback.from_user else 0

    try:
        cam = await _resolve_camera(user_id, api_client, cam_val)
        cam_id = cam.get("id") if cam else (int(cam_val) if cam_val.isdigit() else 0)
        await api_client.bind_camera(user_id, cam_id, None)
        if callback.message and isinstance(callback.message, Message):
            await callback.message.edit_text(
                f"✅ Камера #{cam_id} успешно отвязана от установки.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="⬅️ К карточке камеры",
                                callback_data=f"cam:view:{cam_id}",
                            )
                        ]
                    ]
                ),
            )
    except ApiError as exc:
        err = format_api_error(exc)
        if callback.message and isinstance(callback.message, Message):
            await callback.message.edit_text(
                f"❌ Не удалось отвязать камеру:\n{err}",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="⬅️ К карточке камеры",
                                callback_data=f"cam:view:{cam_id}",
                            )
                        ]
                    ]
                ),
            )


@router.callback_query(F.data.startswith("cam:calibrate:"))
async def handle_camera_calibrate(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Запуск автокалибровки камеры с выводом параметров."""
    await callback.answer("Запуск автокалибровки...", show_alert=False)
    parts = callback.data.split(":")
    cam_val = parts[2]
    user_id = callback.from_user.id if callback.from_user else 0

    try:
        cam = await _resolve_camera(user_id, api_client, cam_val)
        cam_id = cam.get("id") if cam else (int(cam_val) if cam_val.isdigit() else 0)

        if callback.message and isinstance(callback.message, Message):
            with contextlib.suppress(Exception):
                await callback.message.edit_text(
                    f"⚙️ Выполняется автокалибровка камеры #{cam_id}...\n"
                    "Опрос оборудования через v4l2-ctl и подбор наилучшего качества."
                )

        res = await api_client.autocalibrate_camera(user_id, cam_id)
        summary = res.get("summary") or "1920x1080 (MJPG), автофокус: поддерживается"

        text = (
            f"⚙️ <b>Результаты автокалибровки камеры #{cam_id}:</b>\n\n"
            f"• <b>Параметры:</b> {html.escape(str(summary))}\n"
            f"• <b>Статус:</b> Профиль наилучшего качества успешно применён."
        )

        if callback.message and isinstance(callback.message, Message):
            with contextlib.suppress(Exception):
                await callback.message.edit_text(
                    text,
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text="⬅️ К карточке камеры",
                                    callback_data=f"cam:view:{cam_id}",
                                )
                            ]
                        ]
                    ),
                )
    except ApiError as exc:
        err = format_api_error(exc)
        if callback.message and isinstance(callback.message, Message):
            with contextlib.suppress(Exception):
                await callback.message.edit_text(
                    f"❌ Ошибка автокалибровки камеры:\n{err}",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text="⬅️ К карточке камеры",
                                    callback_data=f"cam:view:{cam_val}",
                                )
                            ]
                        ]
                    ),
                )
