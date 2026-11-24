# Отчёт о передаче этапа (Handoff): Этап 9.2 — Live-стрим uStreamer, ручные настройки камеры и выравнивание расписания съёмок

## Метаданные
- **Этап**: 9.2 («Live-стрим uStreamer, ручные настройки камеры и выравнивание расписания съёмок»)
- **Режим**: FULL
- **Ветка**: `stage-9/live-stream-camera-settings-and-schedule`
- **Исполнитель**: Builder
- **Приёмка**: QA, Coordinator

---

## 1. Выполненные задачи и критерии приёмки

### 1.1. Live-поток на базе uStreamer и управление камерой (`edge_agent/`)
- **Менеджер Live-стрима `LiveStreamManager` (`edge_agent/live_stream.py`)**:
  - Запуск стрима по требованию с локальным портом HTTP и проксированием через туннель сервера (`POST /api/edge/cameras/{hw_id}/live-frame`).
  - В боевом режиме (`mode == "rpi"` и наличие `ustreamer` в системе) запускается фоновый процесс `ustreamer` с параметрами устройства, разрешения и формата.
  - В тестовом/`mock` режиме (а также fallback) работает детерминированный внутренний генератор кадров на базе синтетического модуля без требования установленного `ustreamer`.
  - Автоматическая остановка стрима при отсутствии активности или подписчиков более 120 секунд (2 минуты).
- **Приоритет планового и ручного SNAP над стримом**:
  - При выполнении команды `SNAP` или фоновой автосъёмки вызывается `live_stream.set_paused(True)`.
  - Стрим временно переводится в статус паузы (`is_paused = True`), удерживая последний зафиксированный кадр и отдавая в телеметрию признак паузы, что предотвращает конфликт за устройство V4L2 между OpenCV и uStreamer. После завершения захвата статус паузы автоматически снимается.
- **Динамический парсинг V4L2 контролов и форматов (`edge_agent/camera_controls.py`)**:
  - Реализован парсинг `v4l2-ctl --list-ctrls-menus`: извлечение типов (`int`, `bool`, `menu`), диапазонов `min`/`max`/`step`/`default`/`value`, признака флага `flags=inactive` (`is_inactive: bool`), а также элементов выпадающих списков `menu_items` (`id`, `label`).
  - Запрос доступных форматов и разрешений через `v4l2-ctl --list-formats-ext` (`query_camera_formats_and_sizes`).
  - Применение настроек на лету через `v4l2-ctl --set-ctrl`: контролы, помеченные драйвером как `inactive`, блокируются от изменения.
  - При изменении разрешения или формата uStreamer безопасно перезапускается с новыми параметрами.
- **Программная цветокоррекция каналов R/G/B**:
  - Реализованы методы `set_rgb_gains` и `apply_rgb_gains` с поканальным масштабированием коэффициентов и отсечением `np.clip(..., 0, 255)`.

### 1.2. Low-Latency прокси Live-потока и расписание по ровным часам (`server/`, `shared/`)
- **Менеджер Live-сессий на сервере (`server/core/live_manager.py`)**:
  - Реализован класс `LiveCameraSession` и глобальный реестр `live_manager` с `asyncio.Condition` для уведомления клиентов Web UI.
  - In-memory буфер хранит строго 1 последний кадр без накопления устаревшей очереди. Промежуточные кадры отбрасываются, задержка минимизирована (десятки миллисекунд).
  - Сбор и отдача реальной телеметрии: фактический `fps`, задержка в миллисекундах `latency_ms` от временной метки захвата агентом, текущее разрешение и признак паузы.
- **API эндпоинты управления Live и камерами (`server/api/cameras.py`, `server/api/edge.py`)**:
  - `GET /api/cameras`: список камер системы открыт для ролей `developer` и `biologist`.
  - `POST /api/cameras/{id}/live/start`: старт Live-сессии и постановка команды `START_LIVE` для агента.
  - `POST /api/cameras/{id}/live/stop`: остановка стрима и постановка `STOP_LIVE`.
  - `GET /api/cameras/{id}/live/status`: получение текущего статуса, FPS, задержки, разрешения и статуса паузы.
  - `GET /api/cameras/{id}/live/snapshot`: скачивание свежего JPEG напрямую без записи в БД и таймлапс.
  - `GET /api/cameras/{id}/live/stream`: потоковая передача low-latency MJPEG через `StreamingResponse(multipart/x-mixed-replace)`.
  - `GET /api/cameras/{id}/controls`: возврат форматов, структурированных V4L2 контролов и коэффициентов RGB.
  - `PATCH /api/cameras/{id}/controls`: обновление настроек, сохранение в `system_settings` и отправка команды `SET_CAMERA_CONTROLS` агенту.
  - `POST /api/edge/cameras/{hw_id}/live-frame`: защищенный ключём `X-Edge-Key` эндпоинт приёма кадров от агента через NAT.
- **Математически строгое выравнивание расписания (`shared/schedule.py`)**:
  - Функция `compute_next_capture_at(last_captured_at, interval_hours, now)` рассчитывает время следующего кадра со строгим выравниванием по целым часам (например, при текущем времени 22:12 и интервале 2 ч -> следующий кадр строго в 23:00, затем в 01:00, 03:00 и т.д.).
  - В карточке установки (`GET /api/installations`, `GET /api/installations/{id}`) возвращается поле `next_capture_at` в формате ISO UTC для активных установок (для архивных — `None`).

### 1.3. Web UI (`clients/web/`)
- **Кнопка «Камеры» в шапке**:
  - Добавлена кнопка `#btn-cameras-menu` рядом с кнопкой RPi Setup.
  - Открывает модалку `#modal-cameras-menu` со списком подключенных камер, их статусом (Онлайн/Офлайн), привязкой к установкам, калибровочной сводкой и кнопками прямого перехода в Live и настройки.
- **Карточка установки в сетке**:
  - Добавлено отображение времени следующего планового кадра `След. кадр: HH:MM` (акцентная SVG-иконка часов).
  - Добавлена кнопка «Live» (SVG видеокамеры) рядом с галереей и тестом вспышки.
- **Модальное окно Live-режима (`#modal-camera-live`)**:
  - Центрированный адаптивный вьюпорт видеопотока.
  - Оверлей паузы (`#live-paused-overlay`): центрированная SVG-иконка паузы и текст «Стрим на паузе» при выполнении съемки.
  - Выдвижная полупрозрачная панель настроек в стиле камеры смартфона (`#live-settings-drawer`) с 4 папками-категориями:
    1. *Разрешение и формат* (переключение MJPEG / YUYV и доступных разрешений);
    2. *Экспозиция* (ручная/автоэкспозиция, выдержка, яркость, контраст, усиление);
    3. *Цвет и баланс* (баланс белого, насыщенность, ползунки поканальной RGB-коррекции `slider-rgb-r`, `slider-rgb-g`, `slider-rgb-b`);
    4. *Фокус и оптика* (автофокус первым, ручной фокус при отключенном автофокусе, зум, резкость, подавление мерцания 50 Гц).
  - Контролы с флагом `inactive` визуально затемняются и блокируются от редактирования.
  - Нижняя панель телеметрии: отображение реального FPS, задержки в мс, текущего разрешения и кнопка «Скачать тестовый снимок» (`#btn-live-download-snapshot`).
- **Соблюдение инвариантов UI**:
  - Строго 0 Unicode-эмодзи во всей директории `clients/web/` (исключительно inline SVG-иконки).
  - Отсутствие `window.alert`, `window.confirm`, `window.prompt`.

### 1.4. Telegram-бот (`clients/telegram_bot/`)
- В `format_installation_card` добавлено отображение строки `⏰ Следующий кадр: <ISO_DATETIME> UTC` для ролей `biologist` и `developer` при наличии активного расписания.

---

## 2. Результаты автоматизированного тестирования и линтинга

- **Тесты Edge Agent**:
  `edge_agent/.venv/bin/pytest edge_agent/tests` -> **73 passed in 2.96s** (включая 5 новых тестов жизненного цикла стрима, V4L2 флагов, RGB-коррекции и диспетчеризации команд).
- **Тесты Server**:
  `server/.venv/bin/pytest server/tests` -> **81 passed in 5.64s** (включая 4 новых теста расписания, прав доступа и проксирования кадров).
- **Тесты Telegram Bot**:
  `clients/telegram_bot/.venv/bin/pytest clients/telegram_bot/tests` -> **72 passed in 0.96s** (включая 2 новых теста форматирования времени следующего кадра).
- **Тесты CLI**:
  `clients/cli/.venv/bin/pytest clients/cli/tests` -> **51 passed in 0.65s**.
- **Тесты E2E**:
  `server/.venv/bin/pytest tests/e2e` -> **80 passed in 81.81s** (включая 2 новых сквозных теста веб-разметки, инвариантов и стриминга).
- **ИТОГО ТЕСТОВ**: **357 passed** (100% green, 0 failed).
- **Линтинг и форматирование**:
  `server/.venv/bin/ruff check .` -> **All checks passed!**
  `server/.venv/bin/ruff format --check .` -> **282 files already formatted**.

---

## 3. Затронутые файлы

### Новые файлы:
1. `shared/schedule.py` — выравнивание расписания по целым часам.
2. `server/core/live_manager.py` — серверный low-latency менеджер Live-сессий.
3. `server/tests/test_stage9_2_live_and_schedule.py` — тесты серверной логики стрима и расписания.
4. `edge_agent/camera_controls.py` — парсинг V4L2 контролов, форматы, классификация.
5. `edge_agent/live_stream.py` — менеджер Live-стрима ustreamer/mock, автостоп, пауза, RGB.
6. `edge_agent/tests/test_stage9_2_live_and_controls.py` — тесты агента для стрима и настроек.
7. `clients/web/js/cameras.js` — Web UI модуль меню камер, Live-режима и панели настроек.
8. `clients/telegram_bot/tests/test_stage9_2_bot_schedule.py` — тесты отображения следующего кадра в боте.
9. `tests/e2e/test_stage9_2_e2e.py` — E2E тесты Web UI разметки и сквозного стриминга.
10. `docs/handoff/stage-9-2-live-stream-camera-settings-and-schedule.md` — данный отчёт.

### Изменённые файлы:
1. `server/api/cameras.py` — эндпоинты Live-стрима (`start`, `stop`, `status`, `snapshot`, `stream`, `controls`).
2. `server/api/edge.py` — эндпоинт приёма кадра `POST /api/edge/cameras/{hw_id}/live-frame`.
3. `server/api/installations.py` — расчет и сериализация `next_capture_at`.
4. `edge_agent/client.py` — метод `send_live_frame`.
5. `edge_agent/orchestrator.py` — обработка команд `START_LIVE`, `STOP_LIVE`, `SET_CAMERA_CONTROLS`, пауза при snap.
6. `clients/web/index.html` — модалки камер и Live, кнопка в шапке (строго inline SVG).
7. `clients/web/style.css` — стили Live-вьюпорта, оверлея паузы, выдвижной панели настроек, телеметрии.
8. `clients/web/js/api.js` — API-методы для камер и Live.
9. `clients/web/js/units.js` — вывод времени следующего кадра и кнопка Live на карточке.
10. `clients/web/js/app.js` — инициализация модуля камер, передача действия Live.
11. `clients/telegram_bot/utils/formatters.py` — вывод `⏰ Следующий кадр:` в карточке установки.
