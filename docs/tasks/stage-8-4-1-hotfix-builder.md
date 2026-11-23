# Задача: Хотфикс-этап 8.4.1 / Исправление багов живого теста Web SPA и Telegram-бота
Режим: FULL
Ветка: fix/stage-8-web-and-bot

Цель:
Исправить баги, выявленные во время живого теста:
1. SyntaxError в `clients/web/js/rpi_setup.js`: неверное имя импорта из `dialogs.js` (`showErrorDialog` вместо `showError`).
2. Проверить все `import` / `export` во всех `clients/web/js/*.js` на полное совпадение имён.
3. Уточнить текст на шаге 2 регистрации в Web SPA: таймер 60 секунд — для повторной отправки кода, а срок действия кода — 10 минут (`expires_in: 600`).
4. Добавить Data URI SVG favicon в `clients/web/index.html`, чтобы убрать 404 на `/favicon.ico` в консоли браузера.
5. Telegram-бот: устранить перехват коллбэка «Сформировать таймлапс» (`frm:timelapse:...`) обработчиком навигации галереи кадров (`handle_frames_gallery_nav` в `clients/telegram_bot/handlers/paginator.py` / `timelapse.py`).
6. Telegram-бот: приглушить спам логов фонового опроса `httpx` / `httpcore` (установить `logging.WARNING`).
7. Добавить автоматические тесты (статическая валидация ES-модулей в `tests/e2e/`, unit-тест коллбэка таймлапса в `clients/telegram_bot/tests/`).
8. Handoff: `docs/handoff/stage-8-4-1-hotfix.md`.
