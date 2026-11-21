import httpx
import pytest
from aiogram.types import Update

from clients.telegram_bot.config import BotConfig
from clients.telegram_bot.main import build_dispatcher
from clients.telegram_bot.tests.conftest import (
    create_mock_api_client,
    create_test_message,
)


@pytest.mark.asyncio
async def test_start_command_guest(bot_config: BotConfig, mock_bot):
    """Проверка /start для Гостя (неавторизованный пользователь)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/me":
            return httpx.Response(401, json={"detail": "Unauthorized"})
        return httpx.Response(404)

    api_client = create_mock_api_client(bot_config, handler)
    dp = build_dispatcher(bot_config, api_client)

    msg = create_test_message("/start")
    update = Update(update_id=1, message=msg)

    await dp.feed_update(mock_bot, update)

    mock_bot.send_message.assert_called_once()
    call_kwargs = mock_bot.send_message.call_args[1]
    text = call_kwargs["text"]
    reply_markup = call_kwargs["reply_markup"]

    assert "Гость" in text
    button_texts = [b.text for row in reply_markup.keyboard for b in row]
    assert "🔑 Войти" in button_texts
    assert "🌱 Установки" in button_texts


@pytest.mark.asyncio
async def test_start_command_biologist(bot_config: BotConfig, mock_bot):
    """Проверка /start для роли Biologist."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/me":
            return httpx.Response(
                200,
                json={"username": "Анна Сидорова", "role": "biologist"},
            )
        return httpx.Response(404)

    api_client = create_mock_api_client(bot_config, handler)
    dp = build_dispatcher(bot_config, api_client)

    msg = create_test_message("/start")
    update = Update(update_id=2, message=msg)

    await dp.feed_update(mock_bot, update)

    mock_bot.send_message.assert_called_once()
    call_kwargs = mock_bot.send_message.call_args[1]
    text = call_kwargs["text"]
    reply_markup = call_kwargs["reply_markup"]

    assert "Анна Сидорова" in text
    assert "Биолог" in text
    button_texts = [b.text for row in reply_markup.keyboard for b in row]
    assert "📸 Сделать снимок" in button_texts
    assert "🎬 Таймлапс (видео)" in button_texts
    assert "💡 Свет и реле" not in button_texts


@pytest.mark.asyncio
async def test_start_command_developer(bot_config: BotConfig, mock_bot):
    """Проверка /start для роли Developer."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/me":
            return httpx.Response(
                200,
                json={"username": "Денис Разработчик", "role": "developer"},
            )
        return httpx.Response(404)

    api_client = create_mock_api_client(bot_config, handler)
    dp = build_dispatcher(bot_config, api_client)

    msg = create_test_message("/start")
    update = Update(update_id=3, message=msg)

    await dp.feed_update(mock_bot, update)

    mock_bot.send_message.assert_called_once()
    call_kwargs = mock_bot.send_message.call_args[1]
    text = call_kwargs["text"]
    reply_markup = call_kwargs["reply_markup"]

    assert "Разработчик" in text
    button_texts = [b.text for row in reply_markup.keyboard for b in row]
    assert "💡 Свет и реле" in button_texts
    assert "📷 Камеры" in button_texts
    assert "⚙️ Система" in button_texts


@pytest.mark.asyncio
async def test_two_step_login_flow_with_password_deletion(
    bot_config: BotConfig, mock_bot
):
    """Проверка полного цикла двухэтапной авторизации с автоматическим удалением пароля."""
    login_called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal login_called
        if request.url.path == "/api/auth/login":
            login_called = True
            return httpx.Response(
                200,
                json={"status": "ok", "role": "biologist", "username": "Мария"},
            )
        return httpx.Response(404)

    api_client = create_mock_api_client(bot_config, handler)
    dp = build_dispatcher(bot_config, api_client)

    # 1. Нажатие кнопки '🔑 Войти'
    msg1 = create_test_message("🔑 Войти", message_id=10)
    await dp.feed_update(mock_bot, Update(update_id=10, message=msg1))
    call1_text = mock_bot.send_message.call_args[1]["text"]
    assert "Шаг 1 из 2" in call1_text

    # 2. Шаг 1: ввод логина
    msg2 = create_test_message("Мария", message_id=11)
    await dp.feed_update(mock_bot, Update(update_id=11, message=msg2))
    call2_text = mock_bot.send_message.call_args[1]["text"]
    assert "Шаг 2 из 2" in call2_text

    # 3. Шаг 2: ввод пароля
    msg3 = create_test_message("SecretPass123", message_id=12)
    await dp.feed_update(mock_bot, Update(update_id=12, message=msg3))

    # Сообщение с паролем должно быть немедленно удалено из чата!
    mock_bot.delete_message.assert_called_once_with(chat_id=12345, message_id=12)

    assert login_called is True
    call3_text = mock_bot.send_message.call_args[1]["text"]
    assert "Вы успешно вошли как <b>Мария</b>" in call3_text
    assert "Биолог" in call3_text
