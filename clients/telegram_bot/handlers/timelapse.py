import contextlib
import logging
import os
from datetime import datetime
from pathlib import Path

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)

from clients.telegram_bot.api_client import GrowVisionApiClient
from clients.telegram_bot.keyboards.paginator import (
    build_frames_gallery_keyboard,
    build_paginator_keyboard,
)
from clients.telegram_bot.states import TimelapseStates
from clients.telegram_bot.utils.errors import ApiError, format_api_error
from clients.telegram_bot.utils.formatters import format_area_display

logger = logging.getLogger(__name__)

router = Router(name="timelapse")

# Множество user_id пользователей, у которых прямо сейчас собирается видео (защита от параллельного запуска)
_user_video_locks: set[int] = set()


# ================== Просмотр кадров (галерея) ==================


def _has_concurrent_cameras(frames: list[dict]) -> bool:
    """Проверяет, есть ли соседние кадры с разными camera_id в пределах 10 минут."""
    if len(frames) < 2:
        return False

    def _parse_ts(f: dict) -> float:
        raw_ts = f.get("captured_at", "")
        try:
            # ISO format: 2026-11-20T10:00:00Z
            clean_ts = raw_ts.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean_ts)
            return dt.timestamp()
        except Exception:  # noqa: BLE001
            return 0.0

    sorted_frames = sorted(frames, key=_parse_ts)
    for i in range(len(sorted_frames) - 1):
        f1 = sorted_frames[i]
        f2 = sorted_frames[i + 1]
        c1 = f1.get("camera_id")
        c2 = f2.get("camera_id")
        if c1 != c2:
            t1 = _parse_ts(f1)
            t2 = _parse_ts(f2)
            if abs(t2 - t1) <= 600:  # 10 минут = 600 с
                return True
    return False


@router.callback_query(F.data.startswith("inst:frames:"))
async def handle_open_frames_gallery(
    callback: CallbackQuery,
    state: FSMContext,
    api_client: GrowVisionApiClient,
) -> None:
    """Открытие галереи плановых кадров таймлапса."""
    await callback.answer()
    parts = callback.data.split(":")
    inst_id = int(parts[2])
    user_id = callback.from_user.id if callback.from_user else 0

    try:
        frames = await api_client.get_timelapse_frames(user_id, inst_id)
        if not frames:
            if callback.message and isinstance(callback.message, Message):
                await callback.message.edit_text(
                    "Плановых кадров пока нет. Они появятся после первой автосъёмки.",
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
            return

        inst = await api_client.get_installation(user_id, inst_id)
        inst_name = inst.get("name", f"Установка #{inst_id}")

        # Проверяем наличие параллельной съёмки на несколько камер
        if _has_concurrent_cameras(frames):
            # Предлагаем пользователю выбрать камеру
            camera_ids = sorted(
                {f.get("camera_id") for f in frames if f.get("camera_id") is not None}
            )
            buttons: list[list[InlineKeyboardButton]] = []
            for cid in camera_ids:
                buttons.append(
                    [
                        InlineKeyboardButton(
                            text=f"📷 Камера #{cid}",
                            callback_data=f"frm_cam:{inst_id}:{cid}",
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

            await state.update_data(
                all_frames=frames, inst_id=inst_id, inst_name=inst_name
            )
            if callback.message and isinstance(callback.message, Message):
                await callback.message.edit_text(
                    f"🌱 Установка «<b>{inst_name}</b>» снималась одновременно на несколько камер.\n"
                    "Выберите камеру для просмотра таймлапса ракурса:",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                )
            return

        # Камера одна или без пересечений по времени: открываем непрерывную ленту
        await _show_frame_in_gallery(
            callback=callback,
            state=state,
            api_client=api_client,
            inst_id=inst_id,
            inst_name=inst_name,
            frames=frames,
            frame_idx=len(frames) - 1,  # Открываем самый свежий кадр
            layer="r",
        )

    except ApiError as exc:
        err = format_api_error(exc)
        if callback.message and isinstance(callback.message, Message):
            await callback.message.edit_text(f"Ошибка загрузки кадров:\n{err}")


@router.callback_query(F.data.startswith("frm_cam:"))
async def handle_gallery_camera_selected(
    callback: CallbackQuery,
    state: FSMContext,
    api_client: GrowVisionApiClient,
) -> None:
    """Выбор ракурса конкретной камеры для просмотра галереи."""
    await callback.answer()
    parts = callback.data.split(":")
    inst_id = int(parts[1])
    cam_id = int(parts[2])

    data = await state.get_data()
    all_frames = data.get("all_frames", [])
    inst_name = data.get("inst_name", f"Установка #{inst_id}")

    filtered_frames = [f for f in all_frames if f.get("camera_id") == cam_id]
    if not filtered_frames:
        await callback.answer("Кадров для выбранной камеры нет", show_alert=True)
        return

    await _show_frame_in_gallery(
        callback=callback,
        state=state,
        api_client=api_client,
        inst_id=inst_id,
        inst_name=inst_name,
        frames=filtered_frames,
        frame_idx=len(filtered_frames) - 1,
        layer="r",
    )


async def _show_frame_in_gallery(
    callback: CallbackQuery,
    state: FSMContext,
    api_client: GrowVisionApiClient,
    inst_id: int,
    inst_name: str,
    frames: list[dict],
    frame_idx: int,
    layer: str = "r",
) -> None:
    """Отображает кадр в галерее с навигационной клавиатурой."""
    user_id = callback.from_user.id if callback.from_user else 0
    total_frames = len(frames)
    current_idx = max(0, min(frame_idx, total_frames - 1))
    target_frame = frames[current_idx]

    # Сохраняем в FSM для последующего листания
    await state.set_state(TimelapseStates.browsing_frames)
    await state.update_data(
        gallery_frames=frames,
        inst_id=inst_id,
        inst_name=inst_name,
        current_idx=current_idx,
        current_layer=layer,
    )

    cam_id = target_frame.get("camera_id", "cam")
    cap_time = target_frame.get("captured_at", "—")
    area_cm2 = target_frame.get("total_area_cm2")
    area_px = target_frame.get("total_area_px")
    if area_cm2 is not None:
        area_text = format_area_display(area_cm2, is_px=False)
    elif area_px is not None:
        area_text = format_area_display(area_px, is_px=True)
    else:
        area_text = "площадь не определена"

    layer_title = "Оригинал" if layer == "r" else "Разметка нейросети"
    caption = (
        f"🌱 <b>Установка:</b> {inst_name}\n"
        f"📷 <b>Камера:</b> #{cam_id}\n"
        f"🕒 <b>Время съёмки:</b> {cap_time} UTC\n"
        f"📐 <b>Площадь:</b> {area_text}\n"
        f"🖼 <b>Слой:</b> {layer_title}\n"
        f"Кадр {current_idx + 1} из {total_frames}"
    )

    url_field = "raw_url" if layer == "r" else "annotated_url"
    img_url = target_frame.get(url_field, target_frame.get("raw_url", ""))

    img_bytes = await api_client.download_file(user_id, img_url)
    kb = build_frames_gallery_keyboard(current_idx, total_frames, layer=layer)

    chat_id = callback.message.chat.id if callback.message else user_id

    # При первом открытии удаляем предыдущее текстовое сообщение и отправляем фото
    if (
        callback.message
        and isinstance(callback.message, Message)
        and not callback.data.startswith("frm:")
    ):
        with contextlib.suppress(Exception):
            await callback.message.delete()
        await callback.bot.send_photo(
            chat_id=chat_id,
            photo=BufferedInputFile(img_bytes, filename=f"frame_{current_idx}.jpg"),
            caption=caption,
            parse_mode="HTML",
            reply_markup=kb,
        )
    else:
        # Листание через edit_message_media
        if callback.message and isinstance(callback.message, Message):
            media = InputMediaPhoto(
                media=BufferedInputFile(img_bytes, filename=f"frame_{current_idx}.jpg"),
                caption=caption,
                parse_mode="HTML",
            )
            with contextlib.suppress(Exception):
                await callback.message.edit_media(media=media, reply_markup=kb)


@router.callback_query(F.data.startswith("frm:"))
async def handle_frames_gallery_nav(
    callback: CallbackQuery,
    state: FSMContext,
    api_client: GrowVisionApiClient,
) -> None:
    """Обработка листания кадров и переключения слоёв в галерее."""
    await callback.answer()
    parts = callback.data.split(":")
    action = parts[1]

    if action == "noop":
        return

    data = await state.get_data()
    frames = data.get("gallery_frames")
    inst_id = data.get("inst_id")
    inst_name = data.get("inst_name", "")

    if action == "close":
        # Закрываем галерею: удаляем фото и отправляем карточку установки
        await state.clear()
        if callback.message and isinstance(callback.message, Message):
            with contextlib.suppress(Exception):
                await callback.message.delete()
            # Перенаправляем на карточку установки
            from clients.telegram_bot.keyboards.inline import (
                get_installation_card_keyboard,
            )
            from clients.telegram_bot.utils.formatters import format_installation_card

            user_id = callback.from_user.id if callback.from_user else 0
            inst = await api_client.get_installation(user_id, inst_id)
            me = await api_client.get_me(user_id)
            role = me.get("role", "guest") if me else "guest"
            card_text = format_installation_card(inst, role=role)
            kb = get_installation_card_keyboard(
                inst_id, role=role, is_archived=inst.get("is_archived", False)
            )
            await callback.message.answer(card_text, reply_markup=kb)
        return

    if not frames or inst_id is None:
        if callback.message and isinstance(callback.message, Message):
            await callback.message.answer(
                "Сессия просмотра устарела, откройте кадры заново.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="⬅️ К карточке установки",
                                callback_data=f"inst:view:{inst_id}"
                                if inst_id
                                else "inst:list",
                            )
                        ]
                    ]
                ),
            )
        return

    new_idx = int(parts[1])
    layer = parts[2] if len(parts) > 2 else "r"

    await _show_frame_in_gallery(
        callback=callback,
        state=state,
        api_client=api_client,
        inst_id=inst_id,
        inst_name=inst_name,
        frames=frames,
        frame_idx=new_idx,
        layer=layer,
    )


# ================== Сборка и отправка видео (Biologist / Developer) ==================


@router.message(F.text == "🎬 Таймлапс (видео)")
async def handle_timelapse_video_menu(
    message: Message,
    api_client: GrowVisionApiClient,
) -> None:
    """Точка входа из нижнего меню: выбор установки для видео."""
    user_id = message.from_user.id if message.from_user else 0
    me = await api_client.get_me(user_id)
    if not me or me.get("role") not in ("biologist", "developer"):
        await message.answer("Сборка видео доступна для Биолога и Разработчика.")
        return

    role = me.get("role")
    include_archived = role == "developer"
    try:
        insts = await api_client.get_installations(
            user_id, include_archived=include_archived
        )
        if not insts:
            await message.answer("Нет доступных установок для сборки видео.")
            return

        kb = build_paginator_keyboard(
            items=insts,
            page=0,
            page_size=5,
            entity="vid_pick",
            item_callback_prefix="inst:video",
            item_text_func=lambda i: f"🌱 {i.get('name')} (#{i.get('id')})",
            item_id_func=lambda i: i.get("id"),
            include_all_button=False,  # Для видео кнопки 'все' нет!
        )
        await message.answer(
            "🎬 <b>Сборка видео-таймлапса</b>\n\nВыберите установку для получения видео:",
            reply_markup=kb,
        )
    except Exception as exc:  # noqa: BLE001
        err = format_api_error(exc)
        await message.answer(f"Ошибка загрузки установок:\n{err}")


@router.callback_query(F.data.startswith("inst:video:"))
async def handle_video_installation_selected(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Выбор установки для видео (или переход из карточки)."""
    await callback.answer()
    parts = callback.data.split(":")
    inst_id = int(parts[2])
    user_id = callback.from_user.id if callback.from_user else 0

    # Проверка защиты от параллельного запуска у одного пользователя
    if user_id in _user_video_locks:
        await callback.answer("Видео уже собирается, подождите", show_alert=True)
        return

    try:
        inst = await api_client.get_installation(user_id, inst_id)
        cameras = inst.get("cameras", [])

        # Если привязано больше одной камеры — обязательный выбор камеры!
        if len(cameras) > 1:
            buttons: list[list[InlineKeyboardButton]] = []
            for c in cameras:
                cid = c.get("id")
                cname = c.get("name") or f"Камера #{cid}"
                buttons.append(
                    [
                        InlineKeyboardButton(
                            text=f"📷 {cname}",
                            callback_data=f"vid_cam:{inst_id}:{cid}",
                        )
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

            if callback.message and isinstance(callback.message, Message):
                await callback.message.edit_text(
                    f"К установке «<b>{inst.get('name')}</b>» привязано несколько камер.\n"
                    "Выберите камеру для сборки таймлапса:",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                )
            return

        # Камера одна или нет привязанных камер -> сразу запускаем сборку
        selected_cam_id = cameras[0].get("id") if len(cameras) == 1 else None
        await _execute_video_pipeline(
            callback=callback,
            api_client=api_client,
            inst_id=inst_id,
            inst_name=inst.get("name", f"Установка #{inst_id}"),
            camera_id=selected_cam_id,
        )

    except ApiError as exc:
        err = format_api_error(exc)
        if callback.message and isinstance(callback.message, Message):
            await callback.message.edit_text(f"Не удалось подготовить видео:\n{err}")


@router.callback_query(F.data.startswith("vid_cam:"))
async def handle_video_camera_selected(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
) -> None:
    """Выбрана конкретная камера для сборки видео."""
    await callback.answer()
    parts = callback.data.split(":")
    inst_id = int(parts[1])
    cam_id = int(parts[2])
    user_id = callback.from_user.id if callback.from_user else 0

    if user_id in _user_video_locks:
        await callback.answer("Видео уже собирается, подождите", show_alert=True)
        return

    inst = await api_client.get_installation(user_id, inst_id)
    await _execute_video_pipeline(
        callback=callback,
        api_client=api_client,
        inst_id=inst_id,
        inst_name=inst.get("name", f"Установка #{inst_id}"),
        camera_id=cam_id,
    )


async def _execute_video_pipeline(
    callback: CallbackQuery,
    api_client: GrowVisionApiClient,
    inst_id: int,
    inst_name: str,
    camera_id: int | None,
) -> None:
    """Выполняет полный пайплайн сборки видео с циклом прореживания (до 5 попыток)."""
    user_id = callback.from_user.id if callback.from_user else 0
    _user_video_locks.add(user_id)

    status_msg: Message | None = None
    if callback.message and isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            status_msg = await callback.message.answer(
                "⏳ <b>Собираю видео таймлапса.</b>\n"
                "Для длинных таймлапсов это может занять несколько минут..."
            )

    try:
        # Попытка 0: полный таймлапс со step = 1
        content_type, payload, _size = await api_client.download_timelapse_video_stream(
            user_id=user_id,
            inst_id=inst_id,
            camera_id=camera_id,
            step=1,
        )

        chat_id = callback.message.chat.id if callback.message else user_id

        # Случай 1: Плановый кадр всего один (image/jpeg)
        if content_type == "image/jpeg" and isinstance(payload, bytes):
            if status_msg:
                with contextlib.suppress(Exception):
                    await status_msg.delete()
            await callback.bot.send_photo(
                chat_id=chat_id,
                photo=BufferedInputFile(payload, filename="single_frame.jpg"),
                caption=f"Плановый кадр пока один, видео не собрано.\nУстановка: {inst_name}",
            )
            return

        # Случай 2: Полное видео уложилось в лимит
        if content_type == "video/mp4" and isinstance(payload, Path):
            if status_msg:
                with contextlib.suppress(Exception):
                    await status_msg.delete()
            try:
                caption = f"🎬 <b>Таймлапс:</b> {inst_name}"
                if camera_id:
                    caption += f" (камера #{camera_id})"
                await callback.bot.send_video(
                    chat_id=chat_id,
                    video=FSInputFile(str(payload)),
                    caption=caption,
                    parse_mode="HTML",
                    supports_streaming=True,
                )
            finally:
                with contextlib.suppress(Exception):
                    os.unlink(payload)
            return

        # Случай 3: Видео превышает лимит 50 МБ -> запускаем цикл прореживания (до 5 попыток)
        steps = [2, 4, 8, 16, 32]
        success = False

        for k, step in enumerate(steps, start=1):
            if k == 1:
                status_text = (
                    "Видео получилось слишком большим для Telegram (лимит 50 МБ).\n"
                    f"🔄 <b>Попытка {k} из 5:</b> собираю заново с прореживанием, "
                    f"берётся каждый {step}-й кадр..."
                )
            else:
                status_text = (
                    f"🔄 <b>Попытка {k} из 5:</b> видео снова слишком большое, "
                    f"собираю заново, каждый {step}-й кадр..."
                )

            if status_msg:
                with contextlib.suppress(Exception):
                    await status_msg.edit_text(status_text)

            ctype, p_path, _sz = await api_client.download_timelapse_video_stream(
                user_id=user_id,
                inst_id=inst_id,
                camera_id=camera_id,
                step=step,
            )

            if ctype == "video/mp4" and isinstance(p_path, Path):
                # Успешно уложились в лимит!
                if status_msg:
                    with contextlib.suppress(Exception):
                        await status_msg.delete()
                try:
                    caption = (
                        f"🎬 <b>Таймлапс:</b> {inst_name}.\n"
                        f"Прореженная версия: каждый {step}-й кадр.\n"
                        f"<i>(Полную версию можно скачать через консольный клиент: snap video {inst_id})</i>"
                    )
                    await callback.bot.send_video(
                        chat_id=chat_id,
                        video=FSInputFile(str(p_path)),
                        caption=caption,
                        parse_mode="HTML",
                        supports_streaming=True,
                    )
                finally:
                    with contextlib.suppress(Exception):
                        os.unlink(p_path)
                success = True
                break

        if not success:
            fail_text = (
                f"Не удалось уложить видео в лимит Telegram (50 МБ) даже при каждом 32-м кадре.\n"
                f"Полную версию можно скачать через консольный клиент (CLI):\n"
                f"<code>snap video {inst_id}</code>"
            )
            if status_msg:
                await status_msg.edit_text(fail_text)
            else:
                await callback.bot.send_message(chat_id, fail_text)

    except ApiError as exc:
        err = format_api_error(exc)
        if exc.status_code == 404:
            err = (
                f"{exc.detail or 'Плановые снимки не найдены'}.\n"
                "Плановые снимки появятся после первой автосъёмки, ручные снимки в таймлапс не входят."
            )
        elif exc.status_code == 422 and "камер" in exc.detail.lower():
            err = f"{exc.detail}\nВыберите камеру или скачайте видео через CLI: snap video {inst_id} --camera-id <id>"

        if status_msg:
            await status_msg.edit_text(f"❌ Не удалось собрать видео:\n{err}")
        else:
            await callback.bot.send_message(
                callback.message.chat.id if callback.message else user_id,
                f"❌ Не удалось собрать видео:\n{err}",
            )
    except Exception as exc:  # noqa: BLE001
        err = format_api_error(exc)
        if status_msg:
            await status_msg.edit_text(f"❌ Ошибка сборки видео:\n{err}")
    finally:
        _user_video_locks.discard(user_id)
