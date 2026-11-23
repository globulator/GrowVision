# Журнал контроля качества (QA Log)

## 1. Открытые дефекты и замечания
- **[BUG-003]** | Критичность: `CRITICAL` | Статус: `OPEN`
  - **Обнаружен в коммите:** `019bdf2` (ветка `fix/stage-8-web-and-bot`)
  - **Где:** `clients/web/js/dialogs.js:33` и `clients/web/js/dialogs.js:94`
  - **Суть проблемы:** В модуле `clients/web/js/dialogs.js` дважды присутствует объявление `export { showError as showErrorDialog };` (на строке 33 и на строке 94). По спецификации ECMAScript (§16.2.3.1 Static Semantics: Early Errors) дублирование экспортируемых идентификаторов в одном модуле вызывает `SyntaxError: Duplicate export of 'showErrorDialog'`. Из-за этого браузеры при загрузке `dialogs.js` падают с синтаксической ошибкой, и всё веб-приложение Web SPA не инициализируется.
  - **Ожидаемое поведение:** Экспорт алиаса `showErrorDialog` должен быть объявлен ровно один раз (или отсутствовать, так как в `rpi_setup.js` импорт переведён на `showError`). В файле `dialogs.js` не должно быть дублирующихся строк `export { showError as showErrorDialog };`.
  - **Фактическое поведение:** На строке 33 и строке 94 дублируется строка `export { showError as showErrorDialog };`. Тест `tests/e2e/test_stage8_web_e2e.py::test_stage8_web_es_modules_static_import_export_validation` падает с `AssertionError: В dialogs.js обнаружены дублирующиеся экспорты: ['showErrorDialog'] (SyntaxError в ES-модулях)`.

---

## 2. Исправленные дефекты (верифицировано QA)
- **[STAGE-8.4] [web, server, scripts] Помощник подключения Raspberry Pi в Web SPA для разработчика, bootstrap-скрипт и скрипты деплоя (верифицировано QA)**
  - **Коммиты:** `0ec13b1` (реализация) (ветка `stage-8/rpi-setup-and-deploy`)
  - **Ключевые проверенные критерии:**
    1. Права доступа: эндпоинты `GET /api/edge/setup/defaults` и `POST /api/edge/setup/token` доступны строго для роли `developer` (401 для анонимов, 403 для `guest`/`biologist`).
    2. Безопасность и одноразовость токена: токен создается в in-memory хранилище (`_setup_tokens`) с TTL 600 с (10 мин); `GET /api/edge/setup/{token}` при первом обращении немедленно сжигает токен (удаляет из памяти) и возвращает bash-скрипт (`text/x-shellscript`); повторный запрос возвращает 404 Not Found; истёкший токен возвращает 404 Not Found; в БД, логах и шаблонах исключено сохранение `netbird_setup_key`; подстановка параметров в скрипт строго экранируется через `shlex.quote`.
    3. Универсальные скрипты деплоя: `scripts/gv-update.sh` (chmod +x, единое обновление `git pull --ff-only`, селективный pip в `.venv` и перезапуск активных systemd служб) и `scripts/install_server.sh` (chmod +x, чистая установка Linux-хоста); синтаксис `bash -n scripts/gv-update.sh scripts/install_server.sh` чист; исключены реальные личные IP/пароли/ключи.
    4. Web SPA помощник: кнопка `#btn-rpi-setup` в шапке отображается только для роли `developer` с SVG-иконкой платы и точкой статуса Edge-агента; модальное окно `#modal-rpi-setup` отображает живой статус (опрос каждые 3 с с остановкой при закрытии), форму предзаполнения URL сервера, парольное поле NetBird Setup Key с SVG-глазом видимости, генерацию одноразовой команды `curl --noproxy '*' -fsSL ... | bash` с копированием в буфер обмена (fallback на `execCommand`) и сворачиваемую памятку; 0 эмодзи (только SVG), 0 `alert/confirm/prompt` (диалоги и тосты), 0 `X-Client-Key` на фронтенде, 0 внешних CDN.
    5. Качество и тесты: быстродействие тестов (<3 с на тест, весь набор 310 тестов пройден за ~85 с, без sleep), чистый `git status`, линтер `ruff check .` и `ruff format --check .` чистые, версия в `VERSION` сохранена (`0.5.0`).
  - **Результат тестирования:** 310 passed (Server & E2E 143, Edge 54, Bot 62, CLI 51), ruff clean, git status clean.

- **[STAGE-8.3] [web, server, clients/telegram_bot] Полная пересборка Web SPA с нуля по docs/WEB_SPEC.md (верифицировано QA)**
  - **Коммиты:** `8742557` (реализация) (ветка `stage-8/web-rebuild`)
  - **Ключевые проверенные критерии:**
    1. Автономность и чистота Web SPA: пересборка с нуля строго по docs/WEB_SPEC.md (без Node.js/npm, без сборщиков, без внешних CDN/шрифтов); 0 Unicode-эмодзи во всем `clients/web/` (исключительно inline SVG); 0 системных вызовов `alert/confirm/prompt` (стилизованные модалки `#modal-error`, `#modal-session-expired` с backdrop, блокировкой скролла и тосты); 0 утечек `X-Client-Key` (авторизация на HttpOnly-куках `gv_session`). Локальный векторный фон `assets/background.svg` с SVG-фильтрами.
    2. Регистрация и авторизация через Telegram: модели БД `telegram_contacts` (кэш юзернеймов и telegram_id), `web_verification_codes` (очередь 6-значных кодов с TTL 60 с), колонка `password_hash` в `User`; серверные эндпоинты `/telegram/contact`, `/web/pending-codes`, `/web/pending-codes/{id}/ack` (под X-Client-Key бота); 3-шаговая регистрация `/web/register/start` (резолв юзернейма -> telegram_id), `/web/register/verify` (проверка 6-значного кода), `/web/register/complete` (сохранение `external_id=str(telegram_id)`, сжигание инвайта биолога, хэш PBKDF2-HMAC-SHA256, сессионная кука `gv_session`); вход `/web/login` по @username или ID с личным паролем; перехватчик 401 со сбросом роли в Гость и модалкой истечения сессии.
    3. Telegram-бот: фоновая синхронизация контактов при входящих сообщениях; фоновый опрос очереди кодов `poll_and_deliver_verification_codes` раз в 2 с с отправкой кода в чат и подтверждением доставки.
    4. Шапка, карточки установок, подвал: постоянный бейдж роли (`Гость` / `Биолог` / `Разработчик`), имя пользователя, SVG-кнопка входа/выхода, слот разработчика; заголовок «Установки» и кнопка обновления списка с SVG-анимацией спиннера и сбросом кеша превью; `GET /api/installations` отдаёт `frames_count` и `last_frame_url`; карточки отображают последнее фото или анимированную SVG-заглушку ожидания камеры; для гостя только просмотр, для авторизованных — кнопки «Галерея» (disabled при 0 кадров) и «Тестовый снимок». Подвал с версией сервера из `GET /api/version`.
    5. Модалка «Галерея и таймлапс»: анимированная SVG-заглушка предзагрузки БЕЗ прогресс-бара; блокировка элементов управления до окончания загрузки; очистка памяти и отмена загрузок при закрытии; при 0 кадров — модалка «Кадров пока нет»; 5 SVG-кнопок `|<`, `<`, `▶/⏸`, `>`, `>|` с блокировкой крайних кадров и рестартом при Play на последнем; при 1 кадре кнопки навигации отключены, кнопка «Скачать» сохраняет PNG; циклическая кнопка камер; тумблер слоя сегментации (только dev); поле длительности (fps >= 1); нативный клиентский GIF-экспорт на Canvas (`gif_encoder.js`, до 720 px, 0 сетевых запросов); мобильные жесты (свайпы, дабл-тапы) и горячие клавиши (Пробел, стрелки, Home, End, Esc).
    6. Тестовый снимок: передача `target` строго как целое число `Number(instId)`, анимация ожидания, показ снимка, площади и местного времени (`toLocaleString('ru-RU')`), тумблер слоя для разработчика, кнопки «Снять ещё» и «Скачать PNG».
    7. Качество и тесты: быстродействие тестов (<3 с на тест, весь набор 305 тестов <85 с, без sleep), чистый `git status`, линтер `ruff check .` и `ruff format --check .` чистые, версия в `VERSION` сохранена (`0.4.2`).
  - **Результат тестирования:** 305 passed (Server & E2E 138, Edge 54, Bot 62, CLI 51), ruff clean, git status clean.

- **[STAGE-8.2] [web, telegram_bot, server, edge_agent, cli] Фиксы Web SPA, разархивация, отказ от alert/confirm и эмодзи, фикс прокси и логирования (верифицировано QA)**
  - **Коммиты:** `602e697` (реализация) (ветка `stage-8/web-bot-fixes`)
  - **Ключевые проверенные критерии:**
    1. Прокси: `trust_env=False` явно задан в `httpx.AsyncClient` (Telegram-бот) и `httpx.Client` (CLI); тесты с установленным `ALL_PROXY=socks://127.0.0.1:10808` успешно пройдены (`test_cli_client_trust_env_false`, `test_api_client_trust_env_false`).
    2. Web SPA типизация `target`: в `clients/web/js/api.js` параметр нормализуется как `target === 'all' ? 'all' : Number(target)` (исключает 422 в Pydantic).
    3. Разархивация установок: серверный эндпоинт `POST /api/installations/{id}/unarchive` (только Developer, 401 для анонимов, 403 для bio/guest; `is_archived=False`, свет по умолчанию ON, вспышка OFF, возврат warnings при пересечении пинов); кнопка «Восстановить» в Web SPA с подтверждением, показом warnings и подсказкой настроить камеру; кнопка `[ ♻️ Восстановить из архива ]` в карточке бота с подтверждением, выводом warnings и кнопками быстрого перехода к камерам и пинам.
    4. Плеер Web SPA: 4 кнопки навигации (`|<`, `<`, `>`, `>|`) со стилизованными inline SVG; воспроизведение с последнего кадра без зацикливания запускается с 1-го кадра; время кадра отображается в локальном формате браузера (`toLocaleString('ru-RU') (местное время)`).
    5. Отказ от alert/confirm/prompt: модуль `clients/web/js/dialogs.js` (`showToast`, `showConfirm`, `showPrompt`) с модалками `#modal-confirm` и `#modal-prompt`; в `clients/web/` 0 вызовов `alert`, `confirm`, `prompt`.
    6. Отказ от эмодзи в Web SPA: 0 эмодзи во всех файлах `clients/web/` (все заменены на лаконичные inline SVG-иконки).
    7. Edge-агент: устранено дублирование логов оркестратора (`shared/logger.py` `logger.propagate = False`, дочерний логгер без дублирующих handler'ов); метод `stop()` сделан идемпотентным (флаг `_stopped`); unit-тесты `test_orchestrator_stop_idempotent` и `test_orchestrator_logger_no_duplicate_handlers` пройдены.
    8. Качество и тесты: быстродействие тестов (<3 с на тест, весь набор 303 тестов пройден за ~85 с, без sleep), чистый `git status`, линтер `ruff check .` и `ruff format --check .` чистые, версия в `VERSION` сохранена (`0.4.1`).
  - **Результат тестирования:** 303 passed (Server & E2E 136, Edge 54, Bot 62, CLI 51), ruff clean, git status clean.

- **[STAGE-8.1] [web, telegram_bot, edge_agent, server] Компактный UX, объединение галереи и таймлапса, совпадающие пины света/вспышки, свет по умолчанию ON, фикс системного прокси (верифицировано QA)**
  - **Коммиты:** `3ae8ee5` (реализация) (ветка `stage-8/ux-polish-and-fixes`)
  - **Ключевые проверенные критерии:**
    1. Edge-агент: в `edge_agent/client.py` для `httpx.Client` явно задан `trust_env=False`, исключающий перехват трафика системным прокси (`ALL_PROXY`, `HTTP_PROXY`).
    2. GPIO и свет: снято ограничение на совпадение пинов `light_gpio_pin == flash_gpio_pin` (`CheckConstraint` в `models.py` убран, при совпадении формируется warning вместо 409). В `edge_agent/hardware.py` при совпадающих пинах свет при съёмке не гаснет; при разных пинах — гаснет, импульс вспышки, возврат в ON. Свет по умолчанию включен (`True`) при создании установки и старте системы (`lifespan`).
    3. Права Гостя: `GET /api/installations` для роли `guest` принудительно возвращает `include_archived=False`. В Web SPA и Telegram-боте для Гостя скрыт переключатель архива.
    4. Web SPA: удалена отдельная вкладка «Плеер», у Гостя активна только вкладка «Установки». На карточке установки выводится превью последнего кадра (`last_frame_raw_url`) и кнопка «Галерея». Объединённая модалка «Галерея и таймлапс»: плавная загрузка с индикатором, просмотр, плеер, скачивание MP4, двусторонняя связка `duration = frames / fps` и `fps = frames / duration` с валидацией `fps >= 1`. Добавлена вкладка «Свет и реле» для групповых действий. Локальный векторный фон `assets/background.svg` с blur без внешних CDN.
    5. Telegram-бот: пункт «Таймлапс (видео)» убран из reply-меню; кнопка «🎬 Сформировать таймлапс» перенесена в просмотр кадров (Галерею). 3-шаговый сценарий FSM: длительность -> FPS (`fps >= 1`) -> экран сводки с подтверждением -> отправка MP4.
    6. Качество и тесты: быстродействие тестов (<3 с на тест, весь набор 295 тестов <60-75 с, без sleep), чистый `git status`, линтер `ruff check .` и `ruff format --check .` чистые, версия в `VERSION` сохранена (`0.4.0`).
  - **Результат тестирования:** 295 passed (Server & E2E 134, Edge 52, Bot 59, CLI 50), ruff clean, git status clean.

- **[STAGE-8] [server, clients/web] Полноценный Web SPA-клиент (верифицировано QA)**
  - **Коммиты:** `726e851` (реализация), `333c6a3` (state) (ветка `stage-8/web-spa`)
  - **Ключевые проверенные критерии:**
    1. Сервер и веб-авторизация: раздача статики `clients/web/` смонтирована на `/` через `StaticFiles` (роуты `/api/*` и `/media/*` не нарушены); безопасная сессионная `HttpOnly`-cookie `gv_session` (SameSite=Lax, HMAC-подпись, `client_type="web"`); вход по паролю dev -> `developer`, по инвайт-коду -> `biologist`; `needs_login` без сжигания кода при первом входе; повторный вход подтягивает имя; проверка cookie в `get_current_user/optional` без `X-Client-Key`; выход (`/web/logout`) с удалением куки и деактивацией сессии.
    2. Проверка изоляции секретов: в `clients/web/` секретный ключ `X-Client-Key` отсутствует (`grep -rn "X-Client-Key" clients/web/` пуст).
    3. Web SPA-клиент: чистые HTML5, CSS3 и ES-модули без сборщиков, Node.js, внешних библиотек и CDN; адаптивность от 360 px, аккуратный русский интерфейс, XSS-защита через `textContent` / экранирование; ролевой доступ (Гость, Биолог, Разработчик).
    4. Установки: создание/редактирование с валидацией GPIO (0..53) и предупреждением об общих пинах, архивация, удаление с подтверждением, ручной снимок (для dev с тумблером разметки, для bio — только raw).
    5. Плеер таймлапса по `docs/WEB_SPEC.md`: автоопределение камер с порогом 10 мин, параллельная предзагрузка до 6 картинок с прогресс-баром и блокировкой управления, Play/Pause, ползунок, шаги, скорости 1x/2x/4x, зацикливание, слои `🖼 Оригинал` / `🧠 Разметка`, локальное время браузера, экспорт MP4.
    6. Камеры, реле (тумблеры с синхронизацией по пинам, тест реле), сессии (генерация инвайтов с копированием, отзыв, список, переименование, кик), система (телеметрия RPi, debug_mode, auto_capture, задержка съёмки).
    7. 💻 Вкладка «Терминал» (Разработчик): интерактивная веб-консоль GrowVision (`help`, `status`, `unit`, `cam`, `light`, `flash`, `snap`, `relay-test`, `auth`, `sys`, `clear`) с историей команд `↑/↓`.
  - **Результат тестирования:** 294 passed (Server & E2E 134, Edge 51, Bot 59, CLI 50), ruff clean, git status clean.

- **[STAGE-7.4] [server, edge_agent, telegram_bot, cli] Общие GPIO-пины с программной синхронизацией и переименование пользователей (верифицировано QA)**
  - **Коммиты:** `edce6b3` (реализация), `343065c` (state) (ветка `stage-7/shared-pins-and-rename`)
  - **Ключевые проверенные критерии:**
    1. БД и модели: удалены уникальные индексы `uq_active_light_pin` и `uq_active_flash_pin` (один GPIO-пин разрешён в нескольких активных установках одновременно); сохранена проверка `light_gpio_pin != flash_gpio_pin` внутри установки; безопасный `DROP INDEX IF EXISTS` в `init_db`.
    2. Программная синхронизация реле по пину: `set_pin_state` / `get_pin_state` в `server/core/state.py`; эндпоинты `GET /api/control/relays` и `GET /api/control/relays/{installation_id}`; фиксация состояний пинов в ответах команд управления; синхронизация тумблеров реле в Telegram-боте и CLI между всеми установками с общими пинами.
    3. Предупреждение о занятом пине: при создании (`unit add`) и редактировании (`unit edit`) установки операция не блокируется, выводится предупреждение: «⚠️ Пин X уже занят («Установка 1»). Реле будут общими.» (при нескольких — перечисление через запятую) в API, боте и CLI.
    4. Переименование пользователей: developer может переименовать любого пользователя (`PATCH /api/auth/sessions/{session_id}`, кнопка «✏️ Переименовать» в карточке сессии бота, `auth rename` в CLI); biologist — только себя (`PATCH /api/auth/me`, кнопка «✏️ Изменить имя» в профиле /profile бота; чужого — 403 Forbidden). Строгая валидация (1..64 символов), экранирование `html.escape`.
  - **Результат тестирования:** 290 passed (Server & E2E 130, Edge 51, Bot 59, CLI 50), ruff clean, git status clean.

- **[STAGE-7.3] [server, edge_agent, telegram_bot, cli] Авторизация по коду, сессии, CV-разметка, UX-полировка и фиксы раунда 5 (верифицировано QA)**
  - **Коммиты:** `6383ff5` (реализация), `d6a0385` (state) (ветка `stage-7/auth-and-polish`)
  - **Ключевые проверенные критерии:**
    1. Модели БД: `User.is_active` (default True), `User.logged_out_at` (nullable DateTime), таблица `invite_codes` (`InviteCode`, TTL 10 мин, 6-8 симв., role='biologist'), безопасный `ALTER TABLE` в `init_db`.
    2. Серверная логика: в `deps.py` авторизованными признаются только `is_active == True`; в `POST /api/auth/login` поле `login` опционально; CLI принимает только developer_password; Telegram — developer_password или активный invite-code. При первом входе без логина отдаётся `{"status": "needs_login", "role": role}` без сжигания кода. При успешном логине с именем код сжигается. Эндпоинты developer: `POST/GET/DELETE /api/auth/invites`, `GET /api/auth/sessions`, `POST /api/auth/sessions/{id}/terminate`, `GET /api/control/captures/{id}`, `duration_sec` в сборке видео.
    3. Telegram-бот: `/login` сразу запрашивает пароль/код (сообщение удаляется, логи маскируются), при `needs_login` запрос Имени и Фамилии, при повторном — логин подтягивается сам; имена кликабельны (`<a href="tg://user?id={external_id}">{html.escape(login)}</a>`); раздел «👥 Сессии» для developer (создание кода в `<code>`, список кодов с отзывом, список сессий, кнопка кика).
    4. CLI: вход разрешён только developer (пароль разработчика); команды `auth invite create|list|revoke`, `auth sessions`, `auth kick <id>`.
    5. Исправлены 3 бага живого теста: убрана мутация frozen callback.data в кнопке отмены удаления установки; приоритетное чтение `area_display` в галерее таймлапса; непустой `debug_log_line` при сканировании камер.
    6. CV-разметка: фиолетовые линейки BGR (240, 32, 160) слева и снизу от куста в см/px; красная точка в центре куста; темная обводка ко всем элементам; адаптивные метаданные в углах кадра.
    7. Mock ArUco: параметр `mock_overlay_aruco` (default=True) в конфиге, действует строго в `HARDWARE_MODE=mock`.
    8. UX в боте: ручной снимок (для dev одно фото с inline-переключателем «🧠 Разметка» / «🖼 Исходник», для bio — только raw); переключатели реле («💡 Свет: 🟢/⚪️», «⚡ Вспышка: 🟢/⚪️»); интерактивный выбор длительности таймлапса (рекомендованное от 10 fps, пресеты 5/10/30 с и ручной ввод).
  - **Результат тестирования:** 279 passed (Server & E2E 126, Edge 51, Bot 55, CLI 47), ruff clean, git status clean.

- **[STAGE-7.2] [telegram_bot, server, edge_agent, cli] Комплексные исправления раунд 4 (верифицировано QA)**
  - **Коммит:** `b2e4643` (ветка `stage-7/bot-fixes-4`)
  - **Ключевые проверенные критерии:**
    1. Кнопка «📷 Камеры установки» (`inst:cameras:{id}`) выводит привязанные онлайн/оффлайн камеры с кнопками управления и возврата к карточке (`inst:view:{id}`); зарегистрированы callback-заглушки `frm:close`, `frm:noop`, `pag:noop`.
    2. Галерея плановых кадров парсит `res.get("frames", [])` с фолбэком на list; гость видит только последний кадр без листания; биолог и разработчик — полную галерею с пагинацией и слоями.
    3. Блок `🐞 DEBUG` читает `debug_info.get("log_line")` («Команда:», «Лог:») с экранированием.
    4. Валидация GPIO `0 <= pin <= 53` в мастере и редактировании без сброса состояния FSM.
    5. Фильтрация архива: отображаются только `is_archived is True` при `show_archived=True`.
    6. Ручной снимок: для биолога — только `raw` через `send_photo`; для разработчика — альбом `raw` + `annotated`.
    7. `/version` извлекает `server_version`/`version` без «vнеизвестно», пример логина «Андрей Бармин», экранирование `username`.
    8. Зависимость `aiohttp-socks>=0.8.0`, при `TelegramUnauthorizedError` логирование `CRITICAL` и немедленный выход (`sys.exit(1)`).
    9. Удаление архивной установки с кадрами разрешено (`DELETE /api/installations/{id}` с очисткой БД и диска), для неархивной сохраняется `409 Conflict`.
    10. Сканирование камер: возвращаются только реально обнаруженные камеры (отключенные помечаются оффлайн и остаются в БД).
    11. Паритет `capture_delay_sec` (Server ↔ Edge ↔ Bot ↔ CLI): системная настройка 0.0..30.0 с (default 1.5 с), установка, бот меню и CLI команды.
    12. Edge: захват мьютекса `acquire_lock(blocking=True, timeout=10.0)`, раздельный `frame_index` с 0, ступенчатая формула `compute_plant_radius` и вращение листьев.
  - **Результат тестирования:** 268 passed (Edge 49, CLI 45, Bot 51, Server & E2E 123), ruff clean, git status clean.

- **[BUG-001]** | Критичность: `MAJOR` | Статус: `FIXED`
  - **Обнаружен в коммите:** `a155d98` -> **Исправлен и подтверждён в коммите:** `4e155b8`
  - **Где:** `clients/telegram_bot/api_client.py:login` (`POST /api/auth/login`)
  - **Суть проблемы:** Передавалось поле `"username"` вместо `"login"` по спецификации `API_CONTRACT.md` п. 4.1 и `BOT_SPEC.md` п. 5.1, из-за чего сервер возвращал 422 Unprocessable Entity и авторизация в боте была невозможна.
  - **Результат повторной проверки:** В `clients/telegram_bot/api_client.py` скорректирован ключ payload на `"login"`. Повторные сквозные e2e-тесты подтвердили успешный двухэтапный вход, удаление пароля из чата, переключение ролей и выход. Регрессионный тест в `tests/e2e/test_bot_e2e.py::test_bot_e2e_auth_lifecycle` успешно пройден.

- **[BUG-002]** | Критичность: `MAJOR` | Статус: `FIXED`
  - **Обнаружен в коммите:** `a155d98` -> **Исправлен и подтверждён в коммите:** `4e155b8`
  - **Где:** `clients/telegram_bot/api_client.py` (`POST /api/control/snap`, `POST /api/control/light/main`, `POST /api/control/light/flash`, `POST /api/control/relay-test`)
  - **Суть проблемы:** Передавался ключ `"installation_id"` (или `None`) вместо обязательного поля `"target"` по спецификации `API_CONTRACT.md` п. 4.4, из-за чего сервер возвращал 422 Unprocessable Entity и команды управления светом, реле и ручной съёмкой были невозможны.
  - **Результат повторной проверки:** Реализован хелпер `_resolve_target`, в тело запросов передаётся поле `target: int | Literal["all"]`. Повторные сквозные e2e-тесты подтвердили корректное управление основным светом, вспышкой, тестом реле и ручной съёмкой snap с альбомом из 2 фото. Регрессионные тесты `test_bot_e2e_control_and_snap` и `test_bot_e2e_installations_and_cameras` в `tests/e2e/test_bot_e2e.py` успешно пройдены.
- **[MAJOR] [server/core/system_status.py] Статус онлайна Raspberry Pi определялся по устаревшей схеме `rpi_last_heartbeat_at` вместо `updated_at` записи `rpi_last_telemetry`, нарушался порядок проверок эндпоинтов управления.**
  - **Эндпоинты:** `POST /api/control/*`, `POST /api/cameras/scan`, `GET /api/system/status`.
  - **Исправление:** Реализована единая функция `is_rpi_online(db)` по `rpi_last_telemetry.updated_at` (сравнение в UTC <= 90 с). Ключ `rpi_last_heartbeat_at` как ключ таблицы `system_settings` полностью исключён. Обеспечен строгий порядок проверок: авторизация (401/403) -> валидация тела (422) -> валидация target (404/409) -> статус онлайна RPi (503). В `wait_command_result` добавлен немедленный возврат 504 при статусе timeout. В timelapse video добавлен таймаут ffmpeg 600 с, пропуск отсутствующих файлов и чистый 500 detail.
- **[MAJOR] [server/api/control.py] Значение `target: true` в JSON запросах эндпоинтов управления не валидируется и неявно приводится к целому числу 1.**
  - **Эндпоинты:** `POST /api/control/snap`, `POST /api/control/light/main`, `POST /api/control/light/flash`, `POST /api/control/relay-test`.
  - **Исправление:** В моделях `SnapRequest`, `LightRequest`, `RelayTestRequest` добавлен параметр `mode="before"` в `@field_validator("target", mode="before")` и явная проверка `isinstance(v, bool)` с генерацией `ValueError("target не может быть boolean")` -> 422 Unprocessable Content. Верифицировано в регрессионных e2e-тестах.
- **[MAJOR] [clients/cli/commands/system.py] Парсинг телеметрии Raspberry Pi в команде `system status` не поддерживал вложенную структуру `software`/`hardware` ответа сервера.**
  - **Эндпоинт/Команда:** `system status` (Client API `GET /api/system/status`).
  - **Исправление:** Реализован парсинг вложенных словарей `software` и `hardware` с fallback на плоский формат. Аптайм форматируется только при наличии числового значения. Добавлены unit-тесты в `clients/cli/tests/test_system.py` и сквозной e2e-тест `test_cli_e2e_system_status_telemetry` в `tests/e2e/test_cli_e2e.py`. Все поля телеметрии отображаются корректно.
- **[MAJOR] [edge_agent, clients/cli] Доработки боевого режима Edge-агента для Raspberry Pi 5 (rpi) и обработки ошибок команд в CLI.**
  - **Компоненты:** `edge_agent`, `clients/cli`.
  - **Исправление:**
    1. Реализован строгий боевой режим (`HARDWARE_MODE=rpi`): исключены молчаливые подмены (падение без `gpiozero`/`lgpio`, ошибка при отсутствии `cv2`, `measurements: []` и передача сырого кадра в `annotated_image` без выдуманных площадей при сбое или отсутствии ONNX-модели с логированием `ERROR`).
    2. Сканирование камер переведено на поиск в `/dev/v4l/by-id/*-video-index0` со стабильным `hardware_id` и открытием по `os.path.realpath`.
    3. Добавлен прогрев камер `CAMERA_WARMUP_FRAMES` (5) и задержка переключения реле `RELAY_SWITCH_DELAY_MS` (200 мс).
    4. Логика реле: основной свет в норме ВКЛЮЧЕН (`light=1, flash=0`), на время кадра `light=0, flash=1`, возврат в исходное в `finally`. Поддержана полярность `RELAY_ACTIVE_HIGH`.
    5. Команда SNAP снимает все камеры установки (для target="all" — все активные установки); статус `done` при наличии хотя бы 1 кадра.
    6. Индексация растений упорядочена: сортировка контуров по убыванию площади, нумерация с 0 подряд.
    7. Офлайн-очередь: асинхронный сброс в отдельном потоке (до 10 пакетов под Lock), атомарная запись пакета через замену временной папки, 3 попытки `send_command_result`, таймаут загрузки кадра 60 с, защита `failed/.gitkeep`.
    8. Планировщик автосъемки переведен на астрономическую сетку UTC (`HH % N == 0 and MM == 0`).
    9. В CLI команды `camera scan`, `snap take`, `light main/flash/test` при `status == "error"` выводят ошибку в stderr и завершаются с кодом 1; `system status` выводит `model_name`.
    10. Устранены реальные задержки `sleep` в тестах, БД изолирована в тестах e2e (в корне нет `growvision.db*`, `git status --short` чист). Регрессионный набор (180 тестов) пройден успешно.
- **[MAJOR] [edge_agent] Переключение реле на каждом Heartbeat, сброс состояния света после RELAY_TEST и некорректный резолв путей к модели.**
  - **Компонент:** `edge_agent`.
  - **Исправление:**
    1. Инициализация реле переведена на отслеживание новых установок (`_initialized_installations`) под неблокирующим аппаратным мьютексом (`acquire_lock(blocking=False)`); повторный heartbeat не меняет пины, ручные команды `LIGHT_MAIN off` сохраняются.
    2. В `HardwareManager` реализован метод `get_pin_state(pin) -> bool`, команда `RELAY_TEST` фиксирует исходные состояния пинов и восстанавливает их в блоке `finally` (включённый свет остаётся включённым).
    3. Относительные пути (`MODEL_PATH`, `OFFLINE_QUEUE_DIR`) резолвятся относительно корня репозитория (`REPO_ROOT`); зафиксирован единый путь к модели `edge_agent/models/lettuce_seg_v1.onnx` во всех конфигурациях и документации; в `DEPLOYMENT_RPI.md` строка `<OWNER>` заменена на «ваш аккаунт GitHub».
    4. Добавлены unit-тесты (39 тестов), полный регрессионный набор (184 теста) пройден успешно.

---

## 3. Рекомендации по улучшению UX и новым проверкам
- *(Выполнено в stage-4/fixes)* Устранено дублирование логики очистки таймаутов в `server/api/edge.py` (`edge_claim_command`) вызовом `expire_stale_commands(db, now)` из `server.core.timeouts`.
