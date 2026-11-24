# Handoff: Этап 8.8 (Fix Web TG Auth)

- **Ветка**: `stage-8/fix-web-tg-auth`
- **Режим**: FAST
- **Цель**: Исправить процесс веб-авторизации через Telegram: удаление проверки личного пароля в `register/complete`, передача актуального `bot_username` с сервера через Telegram API `getMe` (с fallback), бесшовный переход от логина к верификации кодом в Web SPA, отслеживание статуса доставки кода через бота и валидация `/start` для числовых Telegram ID.

---

## 1. Выполненные изменения

### 1. Сервер: удаление проверки личного пароля и доставка реального bot_username
- **`server/config.py`**:
  - Добавлено поле `telegram_bot_token: str | None = Field(default=None, alias="TELEGRAM_BOT_TOKEN")`.
- **`server/core/bot_info.py`**:
  - Создан модуль с функцией `get_telegram_bot_username()`: запрашивает Telegram API `https://api.telegram.org/bot<TOKEN>/getMe`, кеширует username в памяти при успехе, а при отсутствии токена, сети или ошибке безопасно возвращает fallback (`BOT_USERNAME` из настроек или `"GrowVisionBot"`).
  - Предоставлена функция `set_cached_bot_username(username)` для изоляции тестов.
- **`server/api/system.py`** и **`server/api/auth.py`**:
  - В `GET /api/version` и `GET /api/auth/web/me` поле `bot_username` теперь использует `get_telegram_bot_username()`.
  - В `POST /api/auth/web/register/complete` и схеме `WebRegisterCompleteRequest` полностью удалена проверка `pwd = body.password.strip()` и ограничение на длину личного пароля. Личные пароли веб-пользователей упразднены; доступ регулируется Telegram и мастер-паролем/кодом приглашения.
- **`server/db/models.py`** и **`server/db/database.py`**:
  - В модель `WebVerificationCode` добавлена колонка `delivery_error = Column(String(255), nullable=True)`.
  - В `init_db` реализована безопасная автомиграция: `ALTER TABLE web_verification_codes ADD COLUMN delivery_error VARCHAR(255);`.
- **`server/api/auth.py`**:
  - Добавлен эндпоинт `POST /api/auth/web/pending-codes/{code_id}/fail` для фиксации ошибки доставки Telegram-ботом.
  - Добавлен эндпоинт `GET /api/auth/web/register/code-status?telegram_id=...` со статусами: `pending`, `delivered`, `delivery_error`, `not_found`.
  - В `resolve_telegram_contact`: для числового Telegram ID добавлена проверка наличия в `TelegramContact`. Если контакт отсутствует (пользователь не нажимал `/start` в боте), возвращается HTTP 400 с понятным требованием открыть бота и нажать `/start`.
  - В `auth_web_login`: незарегистрированные пользователи по числовому Telegram ID при отсутствии контакта также получают HTTP 400 с требованием нажать `/start`, а при наличии контакта — HTTP 403 `telegram_verification_required`.

### 2. Telegram-бот: отчёт об ошибках доставки кодов
- **`clients/telegram_bot/api_client.py`**:
  - Добавлен метод `fail_verification_code(code_id: int, error_message: str | None = None)`.
- **`clients/telegram_bot/main.py`**:
  - В фоновой задаче `poll_and_deliver_verification_codes` при исключении в `bot.send_message` вызывается `api_client.fail_verification_code(code_id, str(send_err))` для фиксации причины сбоя доставки на сервере.

### 3. Web SPA: бесшовный вход, видимость шагов и статус доставки
- **`clients/web/index.html`**:
  - Закрыт тег `<div class="form-group password-group">` перед `.form-hint.tg-bot-hint`.
- **`clients/web/style.css`**:
  - Добавлены явные правила отображения `.reg-step`, скрытия `.reg-step.hidden` и видимости `#modal-register.hidden`.
- **`clients/web/js/api.js`**:
  - Добавлен метод `getCodeStatus(telegramId)`.
- **`clients/web/js/auth.js`**:
  - В `handleLoginFormSubmit` при возврате 403 `telegram_verification_required` автоматически вызывается `api.registerStart(telegram)`, сохраняются `telegram_id`, `recommended_name` и введенный `accessCode`. Модалка регистрации открывается сразу на **Шаге 2** (ввод 6-значного кода).
  - На **Шаге 3** поля `reg-fullname` и `reg-access-code` предзаполняются сохранёнными значениями (`recommended_name` и `accessCode`).
  - Реализован опрос статуса доставки `startPollingCodeDelivery(telegramId)`: каждые 2 с опрашивает `api.getCodeStatus` и при обнаружении `delivery_error` выводит понятное предупреждение в `#reg-error-step2`.
  - Добавлен обработчик перехода со ссылки `#link-to-login` обратно на форму входа.
  - Сохранены инварианты: 0 Unicode-эмодзи, 0 `window.alert`/`confirm`/`prompt`.

### 4. Тесты
- В `tests/e2e/test_stage8_web_e2e.py` добавлен сквозной E2E-тест `test_stage8_8_fix_web_tg_auth_flow`:
  - Проверка реального `bot_username`.
  - Проверка возврата HTTP 400 при передаче числового ID без диалога с ботом.
  - Проверка очереди кодов и фиксации ошибки доставки `pending-codes/{id}/fail` -> `code-status`.
  - Проверка верификации и завершения регистрации без передачи личного пароля.
  - Проверка корректности структуры DOM в `index.html` и стилей в `style.css`.
