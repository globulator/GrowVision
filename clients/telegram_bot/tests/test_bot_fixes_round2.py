import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage, StorageKey
from aiogram.types import Chat, Message, Update, User

from clients.telegram_bot.api_client import GrowVisionApiClient
from clients.telegram_bot.config import BotConfig
from clients.telegram_bot.main import build_dispatcher, setup_bot_commands
from clients.telegram_bot.states import InstallationCreateStates
from clients.telegram_bot.tools.bug_log import cmd_list, cmd_show, cmd_tail
from clients.telegram_bot.utils.debug_logger import (
    DebugLogger,
    set_debug_logger,
)
from clients.telegram_bot.utils.formatters import format_installation_card


@pytest.fixture
def clean_debug_logger(tmp_path: Path):
    """Изолированный DebugLogger во временной директории."""
    logs_dir = tmp_path / "logs"
    logger = DebugLogger(log_dir=logs_dir, enabled=True)
    return logger


def test_format_installation_card_all_fields():
    """Проверка отображения всех ключей ответа API в карточке установки."""
    inst_data = {
        "id": 42,
        "name": "Тестовый стеллаж",
        "culture_name": "Руккола",
        "is_archived": False,
        "last_area_display": "65.4 см²",
        "aruco_size_mm": 50.0,
        "created_at": "2026-11-20T10:00:00Z",
        "light_gpio_pin": 17,
        "flash_gpio_pin": 27,
        "cameras": [
            {
                "id": 1,
                "auto_name": "Камера 1 (Верхняя)",
                "hardware_id": "usb-cam-1",
                "is_online": True,
            }
        ],
    }

    # Для гостя
    guest_card = format_installation_card(inst_data, role="guest")
    assert "Установка #42: Тестовый стеллаж" in guest_card
    assert "Культура:</b> Руккола" in guest_card
    assert "Последняя площадь:</b> 65.4 см²" in guest_card
    assert "ArUco" not in guest_card
    assert "GPIO" not in guest_card

    # Для биолога
    bio_card = format_installation_card(inst_data, role="biologist")
    assert "Размер ArUco:</b> 50.0 мм" in bio_card
    assert "Создана:</b> 2026-11-20T10:00:00Z" in bio_card
    assert "Камера 1 (Верхняя)" in bio_card
    assert "usb-cam-1" not in bio_card  # hardware_id скрыт для биолога
    assert "GPIO" not in bio_card

    # Для разработчика
    dev_card = format_installation_card(
        inst_data, role="developer", web_public_url="http://grow.local"
    )
    assert "Пин света (GPIO):</b> 17" in dev_card
    assert "Пин вспышки (GPIO):</b> 27" in dev_card
    assert "<code>usb-cam-1</code>" in dev_card
    assert "http://grow.local/timelapse/42" in dev_card

    # Сброшенный ArUco
    inst_no_aruco = dict(inst_data)
    inst_no_aruco["aruco_size_mm"] = None
    inst_no_aruco["last_area_display"] = "1250 px"
    dev_card_no_aruco = format_installation_card(inst_no_aruco, role="developer")
    assert "не задан (площадь в px)" in dev_card_no_aruco
    assert "Последняя площадь:</b> 1250 px" in dev_card_no_aruco


@pytest.mark.asyncio
async def test_profile_displays_valid_dates_and_login(
    bot_config: BotConfig, mock_bot: MagicMock
):
    """Профиль выводит login, authorized_at, last_active_at без ложных прочерков."""
    me_resp = {
        "login": "anna_bio",
        "role": "biologist",
        "authorized_at": "2026-11-21T09:30:00Z",
        "last_active_at": "2026-11-21T09:45:00Z",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/me":
            return httpx.Response(200, json=me_resp)
        return httpx.Response(404)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=bot_config.server_url
    )
    api_client = GrowVisionApiClient(bot_config, client=client)
    dp = build_dispatcher(bot_config, api_client)

    user = User(id=777, is_bot=False, first_name="Anna")
    chat = Chat(id=777, type="private")
    msg = Message(
        message_id=1,
        date=1700000000,
        chat=chat,
        from_user=user,
        text="/profile",
    ).as_(mock_bot)

    await dp.feed_update(mock_bot, Update(update_id=1, message=msg))

    mock_bot.send_message.assert_called_once()
    profile_text = mock_bot.send_message.call_args[1]["text"]

    assert "Логин:</b> anna_bio" in profile_text
    assert "Роль:</b> Биолог" in profile_text
    assert "Время входа:</b> 2026-11-21T09:30:00Z" in profile_text
    assert "Последняя активность:</b> 2026-11-21T09:45:00Z" in profile_text
    assert "—" not in profile_text

    await api_client.close()


@pytest.mark.asyncio
async def test_installation_create_rejects_duplicate_name_step1(
    bot_config: BotConfig, mock_bot: MagicMock
):
    """На шаге 1 дубликат имени отклоняется с просьбой ввести другое, FSM не сбрасывается."""
    existing_installations = [
        {"id": 1, "name": "Базилик 1", "culture_name": "Базилик", "is_archived": False},
        {"id": 2, "name": "Архивная Мята", "culture_name": "Мята", "is_archived": True},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth/me":
            return httpx.Response(200, json={"role": "developer", "login": "dev"})
        if path == "/api/installations" and request.method == "GET":
            return httpx.Response(200, json=existing_installations)
        return httpx.Response(404)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=bot_config.server_url
    )
    api_client = GrowVisionApiClient(bot_config, client=client)
    dp = build_dispatcher(bot_config, api_client)

    user = User(id=888, is_bot=False, first_name="DevUser")
    chat = Chat(id=888, type="private")

    # Устанавливаем FSM-состояние waiting_name
    storage: MemoryStorage = dp.fsm.storage
    fsm_ctx = FSMContext(
        storage=storage,
        key=StorageKey(bot_id=mock_bot.id, chat_id=888, user_id=888),
    )
    await fsm_ctx.set_state(InstallationCreateStates.waiting_name)

    # 1. Пользователь вводит занятое имя (активной установки)
    msg10 = Message(
        message_id=10,
        date=1700000000,
        chat=chat,
        from_user=user,
        text="Базилик 1",
    ).as_(mock_bot)
    await dp.feed_update(mock_bot, Update(update_id=10, message=msg10))

    ans = mock_bot.send_message.call_args[1]["text"]
    assert "уже занято" in ans
    assert "Введите другое название" in ans

    # Проверяем, что FSM остался на waiting_name
    state_after = await fsm_ctx.get_state()
    assert state_after == InstallationCreateStates.waiting_name.state

    # 2. Пользователь вводит занятое имя (архивной установки)
    msg11 = Message(
        message_id=11,
        date=1700000001,
        chat=chat,
        from_user=user,
        text="Архивная Мята",
    ).as_(mock_bot)
    await dp.feed_update(mock_bot, Update(update_id=11, message=msg11))

    ans_arc = mock_bot.send_message.call_args[1]["text"]
    assert "уже занято" in ans_arc
    assert await fsm_ctx.get_state() == InstallationCreateStates.waiting_name.state

    # 3. Пользователь вводит свободное имя -> переход на шаг 2
    msg12 = Message(
        message_id=12,
        date=1700000002,
        chat=chat,
        from_user=user,
        text="Новый Тархун",
    ).as_(mock_bot)
    await dp.feed_update(mock_bot, Update(update_id=12, message=msg12))

    ans_ok = mock_bot.send_message.call_args[1]["text"]
    assert "Шаг 2 из 5" in ans_ok
    assert await fsm_ctx.get_state() == InstallationCreateStates.waiting_crop.state

    await api_client.close()


@pytest.mark.asyncio
async def test_unknown_message_fallback(bot_config: BotConfig, mock_bot: MagicMock):
    """Произвольный неизвестный текст вне FSM возвращает подсказку /help."""
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
        base_url=bot_config.server_url,
    )
    api_client = GrowVisionApiClient(bot_config, client=client)
    dp = build_dispatcher(bot_config, api_client)

    user = User(id=999, is_bot=False, first_name="User")
    chat = Chat(id=999, type="private")
    msg = Message(
        message_id=20,
        date=1700000000,
        chat=chat,
        from_user=user,
        text="Привет, как дела?",
    ).as_(mock_bot)

    await dp.feed_update(mock_bot, Update(update_id=20, message=msg))

    mock_bot.send_message.assert_called_once()
    ans = mock_bot.send_message.call_args[1]["text"]
    assert ans == "Не понял. Список команд: /help"

    await api_client.close()


@pytest.mark.asyncio
async def test_bug_report_message_preserves_fsm(
    bot_config: BotConfig, mock_bot: MagicMock, tmp_path: Path
):
    """Сообщение на * записывает BUG-LOG-N с 20 событиями и не прерывает FSM."""
    logs_dir = tmp_path / "bot_logs"
    debug_logger = DebugLogger(log_dir=logs_dir, enabled=True)
    set_debug_logger(debug_logger)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
        base_url=bot_config.server_url,
    )
    api_client = GrowVisionApiClient(bot_config, client=client)
    dp = build_dispatcher(bot_config, api_client)

    user = User(id=555, is_bot=False, first_name="Tester")
    chat = Chat(id=555, type="private")

    storage: MemoryStorage = dp.fsm.storage
    fsm_ctx = FSMContext(
        storage=storage,
        key=StorageKey(bot_id=mock_bot.id, chat_id=555, user_id=555),
    )
    await fsm_ctx.set_state(InstallationCreateStates.waiting_light_pin)

    # Имитируем несколько событий в логгере
    for i in range(5):
        debug_logger.log_event("test_step", user_id=555, step=i)

    # Отправляем заметку о баге на *
    bug_msg = Message(
        message_id=30,
        date=1700000000,
        chat=chat,
        from_user=user,
        text="*непонятно какой пин указывать",
    ).as_(mock_bot)

    await dp.feed_update(mock_bot, Update(update_id=30, message=bug_msg))

    mock_bot.send_message.assert_called_once()
    ans = mock_bot.send_message.call_args[1]["text"]
    assert ans.startswith("Записано: BUG-LOG-")

    # FSM остался на месте!
    assert await fsm_ctx.get_state() == InstallationCreateStates.waiting_light_pin.state

    # Проверяем сохранение в bug_notes.jsonl
    notes_file = logs_dir / "bug_notes.jsonl"
    assert notes_file.exists()
    records = [
        json.loads(line)
        for line in notes_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(records) >= 1
    rec = records[-1]
    assert rec["note"] == "*непонятно какой пин указывать"
    assert rec["user_id"] == 555
    assert rec["state"] == InstallationCreateStates.waiting_light_pin.state
    assert len(rec["recent_events"]) > 0

    await api_client.close()


def test_debug_logger_masking_and_rotation(tmp_path: Path):
    """DebugLogger маскирует пароли, токены и очищает файлы старше 7 дней."""
    logs_dir = tmp_path / "logs"
    logger = DebugLogger(log_dir=logs_dir, enabled=True)

    # Логируем события с паролем и API-ключом
    logger.log_event(
        "auth_attempt",
        user_id=123,
        password="my_super_password",
        client_api_key="SecretKey999",
        safe_info="ok",
    )

    events = logger.get_recent_events()
    assert len(events) == 1
    ev_data = events[0]["data"]
    assert ev_data["password"] == "***"
    assert ev_data["client_api_key"] == "***"
    assert ev_data["safe_info"] == "ok"

    # Запись баг-репорта
    bid = logger.record_bug("*тестовый баг", user_id=123)
    assert bid == 1

    # Проверка CLI утилиты на этих данных
    assert cmd_list(logs_dir) == 0
    assert cmd_show(1, logs_dir) == 0
    assert cmd_tail(10, logs_dir) == 0


@pytest.mark.asyncio
async def test_setup_bot_commands_sets_required_list():
    """setup_bot_commands устанавливает 5 обязательных команд."""
    mock_bot = MagicMock()
    mock_bot.set_my_commands = AsyncMock()

    await setup_bot_commands(mock_bot)

    mock_bot.set_my_commands.assert_called_once()
    cmds = mock_bot.set_my_commands.call_args[0][0]
    cmd_names = [c.command for c in cmds]
    assert cmd_names == ["start", "help", "version", "login", "cancel"]
