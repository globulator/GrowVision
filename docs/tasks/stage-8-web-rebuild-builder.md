# Задача: Этап 8.3 — Полная пересборка Web SPA с нуля (Builder)

**Ветка**: `stage-8/web-rebuild`  
**Назначение**: Полная пересборка веб-клиента GrowVision с нуля по новому `docs/WEB_SPEC.md` как лёгкого, эстетичного и безотказного плеера таймлапсов и просмотрщика тестовых снимков.

---

## Ключевые требования и критерии

### 1. Архитектура и база данных (`server/db/models.py`, `server/db/database.py`)
- Добавить поле `password_hash = Column(String(255), nullable=True)` в `User`.
- Добавить таблицу `telegram_contacts` (`TelegramContact`): `id`, `telegram_id` (BigInteger, unique, index), `username` (String(100), unique, nullable, index), `first_name`, `last_name`, `updated_at`.
- Добавить таблицу `web_verification_codes` (`WebVerificationCode`): `id`, `telegram_id` (BigInteger, index), `code` (String(6)), `created_at`, `expires_at`, `is_verified`, `verified_at`, `is_delivered`, `delivered_at`.
- В `server/db/database.py` (`init_db`) обеспечить безопасную миграцию добавления колонки `password_hash` в `users` и создания новых таблиц.

### 2. Серверная авторизация и регистрация (`server/api/auth.py`, `server/api/installations.py`)
- `POST /api/auth/telegram/contact` (под `X-Client-Key` бота): создание/обновление записи в `telegram_contacts`.
- `GET /api/auth/web/pending-codes` (под `X-Client-Key` бота): выборка кодов с `is_delivered == False` и `expires_at > now()`.
- `POST /api/auth/web/pending-codes/{id}/ack` (под `X-Client-Key` бота): отметка о доставке `is_delivered = True`.
- `POST /api/auth/web/register/start`: резолв `@username` или `telegram_id`, валидация контакта, генерация 6-значного кода, запись в очередь `web_verification_codes`.
- `POST /api/auth/web/register/verify`: верификация 6-значного кода.
- `POST /api/auth/web/register/complete`: `telegram_id`, `verification_code`, `full_name`, `access_code`, `password`. Проверка доступа, сжигание инвайта (для биолога), сохранение пользователя с `client_type="web"`, `external_id=str(telegram_id)`, `password_hash`, выставление HttpOnly-cookie `gv_session`.
- `POST /api/auth/web/login`: авторизация по `@username`/ID и личному паролю, выставление HttpOnly-cookie `gv_session`.
- `GET /api/auth/web/me`: возвращает `{"role": ..., "login": ..., "authenticated": bool}`.
- `POST /api/auth/web/logout`: деактивация сессии и удаление cookie `gv_session`.
- `GET /api/installations`: добавить поля `frames_count` (int) и `last_frame_url` (str | None).

### 3. Telegram-бот (`clients/telegram_bot/`)
- В `api_client.py`: методы `sync_telegram_contact`, `get_pending_verification_codes`, `ack_verification_code`.
- При получении сообщений от пользователей синхронизировать контакт (`telegram_id`, `username`, `first_name`, `last_name`).
- Фоновая корутина `poll_and_deliver_verification_codes`: опрос очереди кодов раз в ~2с, отправка пользователю сообщения с кодом и подтверждение доставки через ack.

### 4. Клиент Web SPA (`clients/web/`)
- Чистый HTML5, CSS3, ES-модули без Node.js, без сборщиков, без CDN.
- Жёсткий запрет на эмодзи: 0 Unicode-эмодзи, исключительно inline SVG-иконки.
- Никаких `alert/confirm/prompt`: стилизованные модалки с затемненным фоном (backdrop) и блокировкой прокрутки.
- Размытый растительный фон (`assets/background.svg`), responsive от 360 px, тач-таргеты от 44×44 px.
- Шапка: логотип GrowVision, бейдж текущей роли (`Гость`, `Биолог`, `Разработчик`), имя пользователя, слот для dev, SVG-кнопка входа/выхода.
- Панель раздела: заголовок «Установки», кнопка обновления (анимация вращения + сброс кеша превью).
- Карточки: отображение только активных установок; превью с SVG-заглушкой при 0 кадров/ошибке; для авторизованных — кнопки «Галерея» (disabled при 0 кадров) и «Тестовый снимок».
- Модалки входа и пошаговой регистрации (3 шага с таймером 60 с).
- Модалка «Галерея и таймлапс»:
  - Анимированная SVG-заглушка предзагрузки БЕЗ прогресс-бара; блокировка кнопок до завершения загрузки; отмена загрузок при закрытии.
  - 5 SVG-кнопок: `|<`, `<`, `▶/⏸`, `>`, `>|` с правилами блокировки границ и рестартом при Play на последнем кадре.
  - Циклическая кнопка камер (если камер > 1); тумблер слоя (только dev); поле длительности (fps >= 1).
  - Клиентский экспорт GIF (`gif_encoder.js` на Canvas, до 720 px, 0 серверных запросов); скачивание PNG при 1 кадре.
  - Мобильные свайпы и дабл-тапы; горячие клавиши (Пробел, стрелки, Home, End, Esc).
- Модалка «Тестовый снимок»: target строго Number(id), показ фото/времени/площади, тумблер слоя, кнопки «Снять ещё» и «Скачать PNG».
- Модалка ошибок: стилизованное окно с затемненным фоном, SVG-иконкой и кнопкой «ОК».
- Подвал: `GrowVision v<server_version>`.

### 5. Документация и тесты
- Обновить `docs/API_CONTRACT.md`, `docs/DB_SCHEMA.md`, `docs/USER_GUIDE.md`.
- Handoff: `docs/handoff/stage-8-rebuild.md`.
- Тесты: unit и e2e тесты на регистрацию, авторизацию, снимок, отсутствие эмодзи и alert/confirm/prompt.
- Версию в `VERSION` во время реализации НЕ поднимать (0.4.2).
