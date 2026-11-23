# Handoff: Хотфикс-этап 8.4.1 — Исправление багов живого теста Web SPA и Telegram-бота

## 1. Общие сведения
- **Этап:** 8.4.1 (Хотфикс)
- **Режим:** FULL
- **Ветка:** `fix/stage-8-web-and-bot`
- **Роль исполнителя:** Builder

## 2. Исправленные проблемы

### 2.1. Web SPA: SyntaxError и согласованность ES-модулей
- В `clients/web/js/rpi_setup.js` исправлен некорректный импорт: `import { showError, showToast } from './dialogs.js'` вместо несуществующего `showErrorDialog`. Вызовы `showErrorDialog(...)` заменены на `showError(...)`.
- В `clients/web/js/dialogs.js` добавлен алиас `export { showError as showErrorDialog }` для обратной совместимости.
- Устранён дефект [BUG-003] (дублирование `export { showError as showErrorDialog };` в `dialogs.js`), исключён SyntaxError по ECMAScript §16.2.3.1.
- Все локальные `import` и `export` во всех 9 модулях `clients/web/js/*.js` строго уникальны, согласованы и валидированы автоматическим тестом.

### 2.2. Web SPA: Уточнение текстов и Favicon
- На шаге 2 регистрации в `clients/web/index.html` добавлено чёткое разделение: срок действия кода (10 минут) и таймер повторной отправки (60 секунд).
- В `clients/web/index.html` добавлен встроенный Data URI SVG favicon ростка GrowVision. Запросы к `/favicon.ico` больше не возвращают 404 в консоли браузера.
- Инварианты сохранены: 0 эмодзи, 0 alert/confirm/prompt, 0 X-Client-Key, 0 внешних CDN.

### 2.3. Telegram-бот: Перехват коллбэка таймлапса и поддержка фото в галерее
- В `clients/telegram_bot/keyboards/paginator.py` кнопка «🎬 Сформировать таймлапс» формирует callback вида `frm:timelapse:{inst_id}` (или `frm:timelapse`).
- В `clients/telegram_bot/handlers/timelapse.py` обработчик `handle_video_installation_selected` зарегистрирован на `F.data.startswith("frm:timelapse") | F.data.startswith("inst:video:")` СТРОГО ДО обработчика навигации `handle_frames_gallery_nav`.
- Обработчик `handle_frames_gallery_nav` теперь фильтрует события: `F.data.startswith("frm:") & ~F.data.startswith("frm:timelapse")`, исключая ложный парсинг `parts[1]` как `int` и ошибку `ValueError: invalid literal for int()`.
- При вызове таймлапса из галереи, если сообщение содержало фото (`callback.message.photo`), фото корректно удаляется, а диалог выбора параметров видео выводится новым текстовым сообщением (предотвращая исключение `edit_text` на сообщении с фото).

### 2.4. Telegram-бот: Приглушение логов httpx и httpcore
- В `clients/telegram_bot/main.py` для логгеров `httpx` и `httpcore` установлен уровень `logging.WARNING`. Фоновый опрос очереди кодов раз в 2 секунды больше не спамит в консоль и журнал бота.

### 2.5. Тесты
- В `tests/e2e/test_stage8_web_e2e.py` добавлен тест `test_stage8_web_es_modules_static_import_export_validation` (статический парсинг всех import/export и проверка favicon).
- В `clients/telegram_bot/tests/test_timelapse_and_video.py` добавлен unit-тест `test_frm_timelapse_callback_routing_without_value_error` (проверка отсутствия ValueError и корректного старта сценария таймлапса).

## 3. Результаты тестов
- Server & E2E: 144 passed
- Edge Agent: 54 passed
- CLI: 51 passed
- Telegram Bot: 63 passed
- Суммарно: 312 passed
- Ruff check & format: PASSED
