# Handoff Report: Этап 8.3 — Полная пересборка Web SPA с нуля по docs/WEB_SPEC.md

**Ветка**: `stage-8/web-rebuild`  
**Исполнитель**: `builder`  
**Роли**: `@builder` -> `@qa`, `@coordinator`  
**Статус**: Готов к приёмке (READY_FOR_QA)

---

## 1. Обзор изменений

В рамках этапа 8.3 клиент `clients/web/` был полностью пересобран с нуля строго в соответствии с актуализированной спецификацией `docs/WEB_SPEC.md` как лёгкий, эстетичный и безотказный просмотрщик таймлапсов и тестовых снимков.

### 1.1. База данных (`server/db/models.py`, `server/db/database.py`)
- В модель `User` добавлено поле `password_hash = Column(String(255), nullable=True)`.
- Добавлена модель `TelegramContact` (`telegram_contacts`): связка `telegram_id` (BigInteger, unique, index) и `username` (lowercase без `@`, unique, nullable, index) для однозначного резолва `@username` в постоянный ID.
- Добавлена модель `WebVerificationCode` (`web_verification_codes`): очередь 6-значных кодов подтверждения для доставки через Telegram-бота.
- В `server/db/database.py` (`init_db`) обеспечена автоматическая и безопасная миграция колонок и таблиц SQLite.

### 1.2. Серверная часть (`server/api/auth.py`, `server/api/installations.py`)
- Добавлен `POST /api/auth/telegram/contact` (под `X-Client-Key` бота): создание и обновление контакта пользователя.
- Добавлены `GET /api/auth/web/pending-codes` и `POST /api/auth/web/pending-codes/{id}/ack` (под `X-Client-Key` бота): опрос очереди неотправленных кодов и подтверждение доставки.
- Реализована пошаговая регистрация в Web SPA:
  - `POST /api/auth/web/register/start`: валидация ввода `@username` или ID через `telegram_contacts`, генерация 6-значного кода (TTL 10 мин), постановка в очередь.
  - `POST /api/auth/web/register/verify`: проверка 6-значного кода с отметкой `is_verified = True`.
  - `POST /api/auth/web/register/complete`: проверка инвайт-кода биолога (сжигание) или мастер-пароля разработчика, хэширование личного пароля с солью (`PBKDF2-HMAC-SHA256`), сохранение пользователя с `client_type="web"`, `external_id=str(telegram_id)`, `password_hash`, выставление подписанной `HttpOnly`-cookie `gv_session`.
- Реализован вход `POST /api/auth/web/login`: авторизация по Telegram (`@username` или ID) и личному паролю, rate-limiting (5 попыток в минуту), выставление cookie `gv_session`.
- `GET /api/auth/web/me` возвращает профиль авторизованного пользователя (`{"authenticated": true, ...}`) или статус гостя (`{"authenticated": false, "role": "guest"}`).
- `POST /api/auth/web/logout`: деактивация сессии в БД и удаление cookie `gv_session`.
- В `GET /api/installations` добавлены поля `frames_count` (int) и `last_frame_url` (str | None).

### 1.3. Telegram-бот (`clients/telegram_bot/`)
- В `api_client.py` добавлены методы `sync_telegram_contact`, `get_pending_verification_codes`, `ack_verification_code`.
- В `logging_middleware.py` добавлена автоматическая фоновая синхронизация контактов при любых входящих сообщениях от пользователей в Telegram.
- В `main.py` запущена фоновая корутина `poll_and_deliver_verification_codes`: опрос очереди кодов раз в 2 секунды, отправка сообщений пользователям и отправка подтверждений доставки (`ack`).

### 1.4. Web SPA (`clients/web/`)
- **Полная автономность:** Чистые HTML5, CSS3 и нативные ES-модули без Node.js, без сборщиков, без внешних CDN или шрифтов.
- **Жёсткий запрет на эмодзи:** 0 Unicode-эмодзи во всех файлах `clients/web/`, используются исключительно векторные inline SVG.
- **Отказ от системных alert/confirm/prompt:** Модуль `dialogs.js`, все уведомления выводятся через модальные окна с затемнением фона (`backdrop`) и блокировкой скролла (`body.modal-open`).
- **Стеклянная тема (glassmorphism):** Размытый локальный растительный фон `assets/background.svg`, тач-таргеты не менее 44×44 px, адаптивность от 360 px.
- **Верхняя шапка и панель раздела:**
  - Логотип `GrowVision`;
  - Постоянный индикатор роли: **`Гость`**, **`Биолог`**, **`Разработчик`**;
  - Имя пользователя рядом с бейджем;
  - Зарезервированный слот для разработчика «Подключить RPi»;
  - SVG-кнопка входа/выхода;
  - Заголовок «Установки» и кнопка обновления с анимацией вращения и сбросом кеша превью.
- **Карточки установок:**
  - Отображаются только активные установки;
  - Превью: последнее фото или живая анимированная SVG-заглушка ожидания камеры при 0 кадров или ошибке;
  - Название и культура растения;
  - Для Гостя: только просмотр карточки;
  - Для авторизованных: кнопки «Галерея» (disabled при 0 кадров) и «Тестовый снимок».
- **Модалка «Галерея и таймлапс»:**
  - Анимированная SVG-заглушка предзагрузки БЕЗ прогресс-бара;
  - Блокировка всех элементов управления до окончания загрузки выборки;
  - 5 SVG-кнопок: `|<`, `<`, `▶/⏸`, `>`, `>|` с блокировкой граничных кадров и рестартом при Play на последнем кадре;
  - При 1 кадре кнопки навигации и ползунок disabled, кнопка «Скачать» сохраняет PNG;
  - Циклическая кнопка камер (при наличии нескольких камер);
  - Тумблер слоя `Оригинал / Разметка` (только для разработчика);
  - Поле длительности с валидацией FPS ≥ 1;
  - Нативный клиентский экспорт GIF на Canvas (`gif_encoder.js`, до 720 px, 0 серверных запросов);
  - Мобильные жесты (свайпы, двойной тап по левой/правой половине) и клавиатура (Пробел, стрелки, Home, End, Esc).
- **Модалка «Тестовый снимок»:**
  - Параметр `target` передается строго как `int` (Number);
  - Анимация ожидания съёмки, фото, местное время (`toLocaleString('ru-RU')`), площадь, тумблер слоя, кнопки «Снять ещё» и «Скачать PNG».

---

## 2. Результаты тестов и проверок

1. **Unit-тесты сервера**: `server/.venv/bin/pytest server/tests`
   - Все 69 тестов пройдены (включая обновленный `test_stage8_web_auth.py`).
2. **Unit-тесты edge_agent**: `edge_agent/.venv/bin/pytest edge_agent/tests`
   - Все 54 теста пройдены.
3. **Unit-тесты CLI**: `clients/cli/.venv/bin/pytest clients/cli/tests`
   - Все 51 тест пройден.
4. **Unit-тесты бота**: `clients/telegram_bot/.venv/bin/pytest clients/telegram_bot/tests`
   - Все 62 теста пройдены.
5. **E2E тесты**: `server/.venv/bin/pytest tests/e2e`
   - Все 68 тестов пройдены (включая обновленный `test_stage8_web_e2e.py`).
6. **Линтер и формат**:
   - `server/.venv/bin/ruff check .` — без замечаний.
   - `server/.venv/bin/ruff format --check .` — 218 файлов оформлены идеально.
7. **Проверки Web SPA**:
   - 0 эмодзи в `clients/web/`
   - 0 вызовов `alert/confirm/prompt`
   - 0 внешних CDN
   - 0 заголовков `X-Client-Key`
8. **Версия**:
   - `VERSION` сохранена на 0.4.2 (бамп до 0.5.0 выполнит Coordinator).
