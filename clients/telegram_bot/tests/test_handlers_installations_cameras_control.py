import httpx
import pytest
from aiogram.types import Update

from clients.telegram_bot.config import BotConfig
from clients.telegram_bot.main import build_dispatcher
from clients.telegram_bot.tests.conftest import (
    create_mock_api_client,
    create_test_message,
)
from clients.telegram_bot.tests.test_timelapse_and_video import (
    create_test_callback_query,
)


@pytest.mark.asyncio
async def test_installations_list_and_card_view(bot_config: BotConfig, mock_bot):
    """Проверка открытия списка установок и просмотра карточки."""
    sample_inst = {
        "id": 1,
        "name": "Базилик Тест",
        "crop_type": "Зелень",
        "is_archived": False,
        "light_gpio_pin": 17,
        "flash_gpio_pin": 27,
        "aruco_marker_size_mm": 40.0,
        "cameras": [{"id": 1, "name": "Cam 1", "hardware_id": "usb-cam-1"}],
        "last_capture": {"total_area_cm2": 45.2},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth/me":
            return httpx.Response(200, json={"role": "developer", "username": "Dev"})
        if path == "/api/installations":
            return httpx.Response(200, json=[sample_inst])
        if path == "/api/installations/1":
            return httpx.Response(200, json=sample_inst)
        return httpx.Response(404)

    api_client = create_mock_api_client(bot_config, handler)
    dp = build_dispatcher(bot_config, api_client)

    # 1. Запрос списка через '🌱 Установки'
    msg = create_test_message("🌱 Установки")
    await dp.feed_update(mock_bot, Update(update_id=1, message=msg))

    mock_bot.send_message.assert_called()
    list_text = mock_bot.send_message.call_args[1]["text"]
    assert "Активные установки" in list_text

    # 2. Клик на просмотр карточки: inst:view:1
    cb = create_test_callback_query("inst:view:1")
    await dp.feed_update(mock_bot, Update(update_id=2, callback_query=cb))

    mock_bot.edit_message_text.assert_called()
    card_text = mock_bot.edit_message_text.call_args[1]["text"]
    assert "Базилик Тест" in card_text
    assert "Пин света (GPIO):</b> 17" in card_text
    assert "Пин вспышки (GPIO):</b> 27" in card_text
    assert "40.0 мм" in card_text
    assert "45.2 см²" in card_text


@pytest.mark.asyncio
async def test_installation_create_flow(bot_config: BotConfig, mock_bot):
    """Проверка пошагового мастера создания установки (5 шагов)."""
    created_payload = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal created_payload
        path = request.url.path
        if path == "/api/auth/me":
            return httpx.Response(200, json={"role": "developer", "username": "Dev"})
        if path == "/api/installations" and request.method == "GET":
            return httpx.Response(200, json=[])
        if path == "/api/installations" and request.method == "POST":
            import json

            created_payload = json.loads(request.read().decode())
            return httpx.Response(
                201,
                json={
                    "id": 99,
                    "name": created_payload["name"],
                    "culture_name": created_payload["culture_name"],
                    "is_archived": False,
                    "light_gpio_pin": created_payload["light_gpio_pin"],
                    "flash_gpio_pin": created_payload["flash_gpio_pin"],
                    "aruco_size_mm": created_payload["aruco_size_mm"],
                },
            )
        return httpx.Response(404)

    api_client = create_mock_api_client(bot_config, handler)
    dp = build_dispatcher(bot_config, api_client)

    # 1. Нажатие "Создать установку"
    cb = create_test_callback_query("inst:create")
    await dp.feed_update(mock_bot, Update(update_id=10, callback_query=cb))
    assert "Шаг 1 из 5" in mock_bot.send_message.call_args[1]["text"]

    # 2. Шаг 1: ввод названия
    msg1 = create_test_message("Мята Стеллаж", message_id=11)
    await dp.feed_update(mock_bot, Update(update_id=11, message=msg1))
    assert "Шаг 2 из 5" in mock_bot.send_message.call_args[1]["text"]

    # 3. Шаг 2: ввод культуры
    msg2 = create_test_message("Мята перечная", message_id=12)
    await dp.feed_update(mock_bot, Update(update_id=12, message=msg2))
    assert "Шаг 3 из 5" in mock_bot.send_message.call_args[1]["text"]

    # 4. Шаг 3: ввод пина света
    msg3 = create_test_message("22", message_id=13)
    await dp.feed_update(mock_bot, Update(update_id=13, message=msg3))
    assert "Шаг 4 из 5" in mock_bot.send_message.call_args[1]["text"]

    # 5. Шаг 4: ввод пина вспышки
    msg4 = create_test_message("23", message_id=14)
    await dp.feed_update(mock_bot, Update(update_id=14, message=msg4))
    assert "Шаг 5 из 5" in mock_bot.send_message.call_args[1]["text"]

    # 6. Шаг 5: пропуск ArUco
    cb_aruco = create_test_callback_query("inst:skip_aruco")
    await dp.feed_update(mock_bot, Update(update_id=15, callback_query=cb_aruco))

    assert created_payload is not None
    assert created_payload["name"] == "Мята Стеллаж"
    assert created_payload["culture_name"] == "Мята перечная"
    assert created_payload["light_gpio_pin"] == 22
    assert created_payload["flash_gpio_pin"] == 23
    assert created_payload["aruco_size_mm"] is None

    final_text = mock_bot.send_message.call_args[1]["text"]
    assert "успешно создана" in final_text
    assert "не задан" in final_text


@pytest.mark.asyncio
async def test_control_light_and_relay(bot_config: BotConfig, mock_bot):
    """Проверка управления реле света/вспышки и теста реле."""
    command_log = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/system/status":
            return httpx.Response(200, json={"settings": {"debug_mode": True}})
        if path == "/api/control/light/main":
            command_log.append("light_main")
            return httpx.Response(
                200,
                json={
                    "status": "done",
                    "result_message": "Свет включен",
                    "debug_log_line": "GPIO 17 ON",
                },
            )
        if path == "/api/control/relay-test":
            command_log.append("relay_test")
            return httpx.Response(
                200,
                json={
                    "status": "done",
                    "result_message": "Тест реле завершен",
                    "debug_log_line": "RELAY 3s OK",
                },
            )
        return httpx.Response(404)

    api_client = create_mock_api_client(bot_config, handler)
    dp = build_dispatcher(bot_config, api_client)

    # 1. Включение света
    cb1 = create_test_callback_query("ctrl:light:1:1")
    await dp.feed_update(mock_bot, Update(update_id=20, callback_query=cb1))
    assert "light_main" in command_log
    res_text = mock_bot.edit_message_text.call_args[1]["text"]
    assert "Свет включен" in res_text
    assert "DEBUG:" in res_text

    # 2. Тест реле
    cb2 = create_test_callback_query("ctrl:relay:1")
    await dp.feed_update(mock_bot, Update(update_id=21, callback_query=cb2))
    assert "relay_test" in command_log
    res_text2 = mock_bot.edit_message_text.call_args[1]["text"]
    assert "Тест реле завершен" in res_text2


@pytest.mark.asyncio
async def test_control_snap_sends_album(bot_config: BotConfig, mock_bot):
    """Проверка ручного снимка: для биолога send_photo (только raw), для разработчика send_media_group (raw+annotated)."""

    current_role = "biologist"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth/me":
            return httpx.Response(
                200, json={"role": current_role, "username": "Tester"}
            )
        if path == "/api/installations":
            return httpx.Response(200, json=[{"id": 1, "name": "Рукола"}])
        if path == "/api/control/snap":
            return httpx.Response(
                200,
                json={
                    "status": "done",
                    "result_message": "Снимок готов",
                    "captures": [
                        {
                            "installation_id": 1,
                            "camera_hardware_id": "usb-cam-1",
                            "captured_at": "2026-11-21T12:00:00Z",
                            "raw_url": "/media/raw1.jpg",
                            "annotated_url": "/media/ann1.jpg",
                            "total_area_cm2": 33.7,
                        }
                    ],
                },
            )
        if path in ("/media/raw1.jpg", "/media/ann1.jpg"):
            return httpx.Response(200, content=b"\xff\xd8\xfffake_jpeg\xff\xd9")
        return httpx.Response(404)

    api_client = create_mock_api_client(bot_config, handler)
    dp = build_dispatcher(bot_config, api_client)

    # 1. Проверяем биолога: только raw через send_photo
    cb = create_test_callback_query("inst:snap:1")
    await dp.feed_update(mock_bot, Update(update_id=30, callback_query=cb))

    mock_bot.send_photo.assert_called_once()
    mock_bot.send_media_group.assert_not_called()
    caption = mock_bot.send_photo.call_args[1]["caption"]
    assert "Рукола" in caption
    assert "usb-cam-1" in caption
    assert "33.7 см²" in caption
    assert "Ручные снимки не входят в таймлапс" in caption

    # 2. Проверяем разработчика: send_photo с кнопкой переключения слоя
    current_role = "developer"
    mock_bot.send_photo.reset_mock()
    mock_bot.send_media_group.reset_mock()

    cb_dev = create_test_callback_query("inst:snap:1")
    await dp.feed_update(mock_bot, Update(update_id=31, callback_query=cb_dev))

    mock_bot.send_photo.assert_called_once()
    dev_caption = mock_bot.send_photo.call_args[1]["caption"]
    assert "Рукола" in dev_caption
    assert "33.7 см²" in dev_caption
    reply_markup = mock_bot.send_photo.call_args[1]["reply_markup"]
    assert reply_markup is not None
    assert "Разметка" in reply_markup.inline_keyboard[0][0].text


@pytest.mark.asyncio
async def test_system_status_and_interval(bot_config: BotConfig, mock_bot):
    """Проверка сводки статуса системы и изменения интервала с warning."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth/me":
            return httpx.Response(200, json={"role": "developer", "username": "Dev"})
        if path == "/api/system/status":
            return httpx.Response(
                200,
                json={
                    "server": {"version": "0.2.1"},
                    "rpi": {
                        "online": True,
                        "last_heartbeat": "2026-11-21T12:30:00Z",
                        "software": {
                            "edge_version": "0.2.1",
                            "model_name": "lettuce_seg_v1.onnx",
                        },
                        "hardware": {
                            "cpu_temp_c": 48.5,
                            "cpu_usage_percent": 12.3,
                            "ram_used_mb": 512,
                            "ram_total_mb": 4096,
                            "disk_free_gb": 22.4,
                        },
                        "offline_queue_count": 0,
                    },
                    "settings": {"capture_interval_hours": 1, "debug_mode": False},
                },
            )
        if path == "/api/system/settings" and request.method == "PATCH":
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "capture_interval_hours": 4,
                    "warning": "Изменение интервала приведет к неравномерному шагу таймлапса",
                },
            )
        return httpx.Response(404)

    api_client = create_mock_api_client(bot_config, handler)
    dp = build_dispatcher(bot_config, api_client)

    # 1. Запрос меню системы
    msg = create_test_message("⚙️ Система")
    await dp.feed_update(mock_bot, Update(update_id=40, message=msg))

    mock_bot.send_message.assert_called()
    sys_text = mock_bot.send_message.call_args[1]["text"]
    assert "Статус системы" in sys_text
    assert "🟢 Online" in sys_text
    assert "48.5°C" in sys_text
    assert "lettuce_seg_v1.onnx" in sys_text

    # 2. Выбор интервала 4 ч через быструю кнопку
    cb = create_test_callback_query("sys:set_int:4")
    await dp.feed_update(mock_bot, Update(update_id=41, callback_query=cb))

    applied_text = mock_bot.send_message.call_args[1]["text"]
    assert "изменён на <b>4 ч</b>" in applied_text
    assert "неравномерному шагу таймлапса" in applied_text
