# Карточка QA: Хотфикс-этап 8.4.1 / Исправление багов живого теста Web SPA и Telegram-бота
Режим: FULL
Ветка: fix/stage-8-web-and-bot

Критерии приёмки:
1. Web SPA:
   - В `clients/web/js/rpi_setup.js` импортируется `showError` (нет SyntaxError).
   - Все локальные `import`/`export` во всех файлах `clients/web/js/*.js` согласованы и существуют.
   - На шаге 2 регистрации в Web SPA чётко разделены: срок действия кода (10 минут) и таймер повторной отправки (60 секунд).
   - В `clients/web/index.html` прописан Data URI favicon (нет ошибки 404).
   - 0 эмодзи, 0 alert/confirm/prompt, 0 X-Client-Key, 0 CDN.
2. Telegram-бот:
   - Нажатие кнопки «Сформировать таймлапс» (`frm:timelapse:...`) не перехватывается навигацией галереи (`frm:...`) и не вызывает `ValueError: not enough values to unpack`.
   - В логах бота отсутствуют регулярные INFO/DEBUG сообщения от `httpx` и `httpcore` при фоновом опросе очереди кодов (уровень `WARNING`).
3. Тесты и линтер:
   - Тест статической валидации ES-модулей в `tests/e2e/`.
   - Unit-тест маршрутизации коллбэка `frm:timelapse:...` в `clients/telegram_bot/tests/`.
   - Зеленый прогон всех тестов, ruff check и format.
