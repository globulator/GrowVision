from clients.telegram_bot.keyboards.paginator import (
    build_frames_gallery_keyboard,
    build_paginator_keyboard,
)


def test_paginator_keyboard_pages():
    """Проверка генерации страниц списка и кнопок навигации."""
    items = [{"id": i, "name": f"Установка {i}"} for i in range(1, 13)]  # 12 элементов

    # Страница 0 (первые 5 элементов)
    kb0 = build_paginator_keyboard(
        items=items,
        page=0,
        page_size=5,
        entity="inst",
        item_callback_prefix="inst:view",
    )
    # 5 кнопок элементов + 1 ряд навигации (◀️ Назад [отключена], 1/3, Вперёд ▶️)
    assert len(kb0.inline_keyboard) == 6
    nav_row0 = kb0.inline_keyboard[-1]
    assert len(nav_row0) == 3
    assert nav_row0[0].callback_data == "pag:noop"  # Назад недоступна
    assert "1/3" in nav_row0[1].text
    assert nav_row0[2].callback_data == "pag:inst:1"  # Вперёд на стр 1

    # Страница 1 (средняя страница)
    kb1 = build_paginator_keyboard(
        items=items,
        page=1,
        page_size=5,
        entity="inst",
        item_callback_prefix="inst:view",
    )
    nav_row1 = kb1.inline_keyboard[-1]
    assert nav_row1[0].callback_data == "pag:inst:0"  # Назад на стр 0
    assert "2/3" in nav_row1[1].text
    assert nav_row1[2].callback_data == "pag:inst:2"  # Вперёд на стр 2

    # Страница 2 (последняя страница: 2 элемента)
    kb2 = build_paginator_keyboard(
        items=items,
        page=2,
        page_size=5,
        entity="inst",
        item_callback_prefix="inst:view",
    )
    assert len(kb2.inline_keyboard) == 3  # 2 элемента + ряд навигации
    nav_row2 = kb2.inline_keyboard[-1]
    assert nav_row2[0].callback_data == "pag:inst:1"  # Назад на стр 1
    assert "3/3" in nav_row2[1].text
    assert nav_row2[2].callback_data == "pag:noop"  # Вперёд недоступна


def test_paginator_callback_data_under_64_bytes():
    """Все callback_data пагинатора строго меньше 64 байт."""
    items = [{"id": 100500, "name": "Установка с длинным именем для проверки"}]
    kb = build_paginator_keyboard(
        items=items,
        page=0,
        page_size=5,
        entity="installation_long_entity",
        extra="extra_very_long_parameter_data",
        item_callback_prefix="inst_view_prefix",
        include_all_button=True,
    )
    for row in kb.inline_keyboard:
        for btn in row:
            byte_len = len(btn.callback_data.encode("utf-8"))
            assert byte_len <= 64, (
                f"Кнопка {btn.text} имеет callback_data > 64 байт: {btn.callback_data}"
            )


def test_paginator_single_page_hides_nav():
    """Если элементов не больше размера страницы, навигационные кнопки скрываются."""
    items = [{"id": 1, "name": "Тест 1"}, {"id": 2, "name": "Тест 2"}]
    kb = build_paginator_keyboard(items=items, page=0, page_size=5)
    # Только 2 кнопки элементов, без ряда навигации
    assert len(kb.inline_keyboard) == 2


def test_frames_gallery_keyboard_navigation_and_layers():
    """Проверка навигации и переключения слоёв в галерее кадров."""
    # Первый кадр (0 из 10)
    kb_first = build_frames_gallery_keyboard(
        current_index=0, total_frames=10, layer="r"
    )
    nav_row = kb_first.inline_keyboard[0]
    assert "1/10" in nav_row[2].text
    # Назад недоступно
    assert nav_row[0].callback_data == "frm:noop"
    assert nav_row[1].callback_data == "frm:noop"
    # Вперед доступно
    assert nav_row[3].callback_data == "frm:1:r"
    assert nav_row[4].callback_data == "frm:9:r"

    # Кнопка переключения слоя: сейчас raw ('r'), предлагает 'a' (разметка)
    layer_btn = kb_first.inline_keyboard[1][0]
    assert "Показать разметку" in layer_btn.text
    assert layer_btn.callback_data == "frm:0:a"

    # Промежуточный кадр (слой 'a')
    kb_mid = build_frames_gallery_keyboard(current_index=4, total_frames=10, layer="a")
    layer_btn_mid = kb_mid.inline_keyboard[1][0]
    assert "Показать оригинал" in layer_btn_mid.text
    assert layer_btn_mid.callback_data == "frm:4:r"
