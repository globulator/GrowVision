# clients/telegram_bot/tests/test_stage9_2_bot_schedule.py
from clients.telegram_bot.utils.formatters import format_installation_card


def test_installation_card_shows_next_capture_for_authorized_roles():
    """Тест отображения времени следующего кадра в карточке установки."""
    inst_data = {
        "id": 1,
        "name": "Бокс #1",
        "culture_name": "Базилик",
        "is_archived": False,
        "last_area_display": "15.4 см²",
        "next_capture_at": "2026-11-25T15:00:00Z",
    }

    # 1. Биолог видит время следующего кадра
    card_bio = format_installation_card(inst_data, role="biologist")
    assert "⏰ <b>Следующий кадр:</b> 2026-11-25T15:00:00Z" in card_bio

    # 2. Разработчик видит время следующего кадра
    card_dev = format_installation_card(inst_data, role="developer")
    assert "⏰ <b>Следующий кадр:</b> 2026-11-25T15:00:00Z" in card_dev

    # 3. Гость НЕ видит служебное время следующего кадра
    card_guest = format_installation_card(inst_data, role="guest")
    assert "Следующий кадр" not in card_guest


def test_installation_card_omits_next_capture_when_none():
    """Тест: если next_capture_at отсутствует (None), строка не выводится."""
    inst_data = {
        "id": 2,
        "name": "Архивный бокс",
        "culture_name": "Мята",
        "is_archived": True,
        "last_area_display": "нет данных",
        "next_capture_at": None,
    }

    card_bio = format_installation_card(inst_data, role="biologist")
    assert "Следующий кадр" not in card_bio
