import html
from typing import Any


def format_area_display(area: float | None, is_px: bool = False) -> str:
    """Форматирует площадь растений для вывода."""
    if area is None:
        return "площадь не определена"
    if is_px:
        return f"{int(area)} px"
    return f"{area:.1f} см²"


def format_installation_card(
    inst: dict[str, Any],
    role: str = "guest",
    web_public_url: str | None = None,
) -> str:
    """Форматирует карточку установки в зависимости от роли пользователя."""
    name = html.escape(str(inst.get("name", "Без названия")))
    crop = html.escape(
        str(inst.get("culture_name") or inst.get("crop_type", "Не указана"))
    )
    is_archived = inst.get("is_archived", False)
    inst_id = inst.get("id", "?")

    # Площадь: читаем last_area_display от сервера
    if "last_area_display" in inst:
        area_str = inst.get("last_area_display") or "нет данных"
    else:
        # Резервный расчет для устаревших тестовых фикстур с last_capture
        last_capture = inst.get("last_capture") or {}
        area_cm2 = last_capture.get("total_area_cm2")
        area_px = last_capture.get("total_area_px")
        if area_cm2 is not None:
            area_str = f"{area_cm2:.1f} см²"
        elif area_px is not None:
            area_str = f"{int(area_px)} px"
        else:
            area_str = "нет данных"

    lines = [
        f"🌱 <b>Установка #{inst_id}: {name}</b>",
        f"🌾 <b>Культура:</b> {crop}",
        f"📐 <b>Последняя площадь:</b> {area_str}",
    ]

    if is_archived:
        lines.append("📦 <b>Статус:</b> В архиве")

    # Поля для Biologist и Developer
    if role in ("biologist", "developer"):
        aruco = (
            inst.get("aruco_size_mm")
            if "aruco_size_mm" in inst
            else inst.get("aruco_marker_size_mm")
        )
        if aruco is not None:
            aruco_str = (
                f"{float(aruco):.1f} мм"
                if isinstance(aruco, (int, float))
                else f"{aruco} мм"
            )
        else:
            aruco_str = "не задан (площадь в px)"
        lines.append(f"📏 <b>Размер ArUco:</b> {aruco_str}")

        created_at = inst.get("created_at")
        if created_at:
            created_str = (
                f"{created_at} UTC"
                if not str(created_at).endswith("Z") and "UTC" not in str(created_at)
                else str(created_at)
            )
            lines.append(f"🕒 <b>Создана:</b> {created_str}")

        delay = inst.get("capture_delay_sec")
        if delay is not None:
            delay_str = f"{float(delay):.1f} с"
        else:
            delay_str = "по умолчанию (общая)"
        lines.append(f"⏱ <b>Задержка съёмки:</b> {delay_str}")

        next_cap = inst.get("next_capture_at")
        if next_cap:
            next_str = (
                f"{next_cap} UTC"
                if not str(next_cap).endswith("Z") and "UTC" not in str(next_cap)
                else str(next_cap)
            )
            lines.append(f"⏰ <b>Следующий кадр:</b> {next_str}")

        cameras = inst.get("cameras", [])
        if cameras:
            cam_lines = []
            for c in cameras:
                raw_name = (
                    c.get("auto_name") or c.get("name") or f"Камера #{c.get('id')}"
                )
                cam_name = html.escape(str(raw_name))
                if role == "developer":
                    hw_id = html.escape(str(c.get("hardware_id", "")))
                    cam_lines.append(f"• {cam_name} (<code>{hw_id}</code>)")
                else:
                    cam_lines.append(f"• {cam_name}")
            lines.append("📷 <b>Привязанные камеры:</b>\n" + "\n".join(cam_lines))
        else:
            lines.append("📷 <b>Камеры:</b> не привязаны")

    # Поля только для Developer
    if role == "developer":
        light_pin = inst.get("light_gpio_pin", "?")
        flash_pin = inst.get("flash_gpio_pin", "?")
        lines.append(f"💡 <b>Пин света (GPIO):</b> {light_pin}")
        lines.append(f"⚡ <b>Пин вспышки (GPIO):</b> {flash_pin}")

    if web_public_url:
        web_link = f"{web_public_url.rstrip('/')}/timelapse/{inst_id}"
        lines.append(f"\n🌐 <b>Веб-плеер таймлапса:</b>\n<code>{web_link}</code>")

    return "\n".join(lines)


def format_system_status(status_data: dict[str, Any]) -> str:
    """Форматирует сводку о состоянии сервера и Raspberry Pi."""
    server_ver = status_data.get("server_version") or status_data.get("server", {}).get(
        "version", "—"
    )

    rpi_conn = status_data.get("rpi_connection")
    if rpi_conn is not None:
        online = rpi_conn == "online"
    else:
        online = status_data.get("rpi", {}).get("online", False)
    status_icon = "🟢 Online" if online else "🔴 Offline"

    lines = [
        "⚙️ <b>Статус системы GrowVision</b>",
        f"🖥 <b>Сервер:</b> v{server_ver}",
        f"🍓 <b>Связь с Raspberry Pi:</b> {status_icon}",
    ]

    last_hb = status_data.get("rpi_last_heartbeat_at") or status_data.get(
        "rpi", {}
    ).get("last_heartbeat")
    if last_hb:
        lines.append(f"🕒 <b>Последний опрос:</b> {last_hb} UTC")

    # Телеметрия RPi: проверяем rpi_telemetry или rpi
    telem = status_data.get("rpi_telemetry") or status_data.get("rpi") or {}

    hardware = telem.get("hardware") or {}
    board_model = hardware.get("board_model") or telem.get("board_model")
    if board_model:
        lines.append(f"📟 <b>Плата:</b> {board_model}")

    software = telem.get("software") or {}
    if software:
        edge_ver = software.get("edge_version", "—")
        model = software.get("model_name", "—")
        lines.append(f"📦 <b>ПО Edge:</b> v{edge_ver} (модель: {model})")

    if hardware:
        temp = hardware.get("cpu_temp_c")
        cpu_usage = hardware.get("cpu_usage_percent")
        ram_used = hardware.get("ram_used_mb")
        ram_total = hardware.get("ram_total_mb")
        disk_free = hardware.get("disk_free_gb")

        temp_str = f"{temp:.1f}°C" if temp is not None else "—"
        cpu_str = f"{cpu_usage:.1f}%" if cpu_usage is not None else "—"
        ram_str = f"{ram_used}/{ram_total} МБ" if ram_used and ram_total else "—"
        disk_str = f"{disk_free:.1f} ГБ" if disk_free is not None else "—"

        lines.extend(
            [
                f"🌡 <b>Температура CPU:</b> {temp_str}",
                f"⚡ <b>Загрузка CPU:</b> {cpu_str} | <b>RAM:</b> {ram_str}",
                f"💾 <b>Свободно на диске:</b> {disk_str}",
            ]
        )

    queue_count = telem.get("offline_queue_count", 0)
    lines.append(f"📥 <b>Неотправленных пакетов в очереди:</b> {queue_count}")

    settings = status_data.get("settings", {})
    interval = settings.get("capture_interval_hours", 1)
    debug = settings.get("debug_mode", False)
    debug_str = "Включен 🐞" if debug else "Выключен"

    capture_delay = settings.get("capture_delay_sec", 1.5)

    lines.extend(
        [
            f"⏱ <b>Интервал автосъёмки:</b> {interval} ч",
            f"⏱ <b>Задержка съёмки:</b> {capture_delay} с",
            f"🔍 <b>Debug-режим:</b> {debug_str}",
        ]
    )

    return "\n".join(lines)


def format_debug_block(command: str | None, log_line: str | None) -> str:
    """Форматирует блок DEBUG для отображения разработчику."""
    if not command and not log_line:
        return ""
    lines = ["\n🐞 <b>DEBUG:</b>"]
    if command:
        lines.append(f"<b>Команда:</b> <code>{html.escape(str(command))}</code>")
    if log_line:
        lines.append(f"<b>Лог:</b> <code>{html.escape(str(log_line))}</code>")
    return "\n".join(lines)


def format_tg_user_link(login: str | None, external_id: str | int | None) -> str:
    """Форматирует имя пользователя в кликабельную ссылку tg://user?id=..."""
    name = html.escape(str(login or "Пользователь"))
    if external_id is not None:
        ext_str = str(external_id).strip()
        if ext_str.isdigit():
            return f'<a href="tg://user?id={ext_str}">{name}</a>'
    return name
