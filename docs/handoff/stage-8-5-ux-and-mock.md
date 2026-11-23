# Handoff: Этап 8.5 — Унификация входа без личного пароля, исправление FSM и 30 FPS в боте, галерея и тестовый снимок Web SPA, mock-изоляция

## 1. Цели и задачи этапа
Реализация требований Этапа 8.5:
1. **Унификация веб-авторизации**:
   - Полное удаление `password_hash` из БД (`users`), SQLAlchemy-моделей, схем Pydantic и документации.
   - Вход в Web SPA по `DEVELOPER_PASSWORD` (роль `developer`) или одноразовому коду инвайта биолога (`biologist`) без необходимости создания и ввода личного пароля.
   - Автоматическая синхронизация отображаемого имени (`login`) из Telegram-контакта (`first_name` + `last_name` или `username`).
   - Стилизованная модалка подтверждения выхода `#modal-confirm` без использования `window.confirm`.
2. **Telegram-бот**:
   - Кнопка «Отмена» на всех шагах FSM создания и редактирования установки (`inst:cancel` и `inst:cancel_edit:{inst_id}`) с безопасной очисткой FSM при возврате в меню.
   - Сохранение текущего шага FSM при невалидном пользовательском вводе и ошибках API.
   - Таймлапс: длительность рассчитывается строго под 30 FPS без пресетов 5/10/15/30с. При превышении 30 FPS выполняется равномерное прореживание с уведомлением: «Собрано видео с частотой 30 FPS (прореживание 1 из {step} для соблюдения лимита Telegram)».
3. **Web SPA**:
   - Исправлена ошибка 404 при загрузке галереи кадров: обращение по актуальному маршруту `/api/timelapse/${id}/frames`.
   - Исправлено отображение тестового снимка: корректное чтение `raw_url`, `annotated_url`, `area_display`, `captured_at` из массива `res.captures[0]`.
   - Единый стиль медиа-заглушек в карточках установок, галерее и тестовом снимке с фоном `#161e29`, SVG-иконками и лёгким дружелюбным юмором («Растение ещё спит», «Камера в отпуске», «Грядки пока пусты»).
   - Информационная плашка `#server-offline-banner` при сетевом обрыве связи с сервером.
   - CSS/SVG-анимация жизненного цикла растения (семечко -> росток -> взрослое растение) во время съёмки тестового кадра и предзагрузки кадров галереи.
   - Строгие инварианты: 0 Unicode-эмодзи, 0 alert/confirm/prompt, 0 X-Client-Key, 0 внешних CDN / библиотек.
4. **Edge-агент и Mock-изоляция**:
   - `HARDWARE_MODE=auto` по умолчанию: автоопределение реальной Raspberry Pi (`/proc/device-tree/model` или `/sys/firmware/devicetree/base/model` / GPIO-устройства) с автоматическим переключением в `mock` на обычном ПК.
   - В Web SPA (`rpi_setup.js`) заблокирован выбор боевого режима `rpi`, если сервер сообщает об отсутствии реального оборудования (`is_real_rpi === false`).
   - Изоляция данных в mock-режиме: сервер при `MOCK_MODE=True` использует изолированную SQLite БД `growvision_mock.db` с синхронизацией пользователей, инвайтов и телеграм-контактов из `growvision.db` через `ATTACH DATABASE`.
   - Беспарольный `sudo` для перезапуска служб в `scripts/install_server.sh` (`/etc/sudoers.d/growvision`).

---

## 2. Изменённые и созданные файлы

### Сервер и БД:
- `server/db/models.py`: удалена колонка `password_hash` из таблицы `users`.
- `server/db/database.py`: удалена миграция `password_hash`, добавлена синхронизация учетных записей и инвайтов в `growvision_mock.db` при старте в mock-режиме.
- `server/config.py`: добавлена поддержка `mock_mode: bool = Field(default=False, alias="MOCK_MODE")`.
- `server/api/auth.py`: вход `auth_web_login` без личного пароля (по `DEVELOPER_PASSWORD` или коду инвайта биолога), поддержка `code` и `password` для обратной совместимости, удаление `password_hash` из регистрации.
- `server/api/system.py`: добавлена функция `is_raspberry_pi()`, возвращающая `is_real_rpi: bool` в `GET /api/system/status`.

### Web SPA (`clients/web/`):
- `clients/web/index.html`: упрощенная форма авторизации (Telegram + пароль разработчика или код инвайта биолога), добавлены `#server-offline-banner` и `#modal-confirm`.
- `clients/web/style.css`: цвет `#161e29` для превью карточек, галереи и тестового снимка, стили для `#server-offline-banner`, `#modal-confirm`, полная CSS-анимация роста растения (`@keyframes plant-*-cycle`).
- `clients/web/js/api.js`: исправлен URL кадров на `/api/timelapse/${id}/frames`, перехват сетевых ошибок с показом `#server-offline-banner`.
- `clients/web/js/app.js`: подтверждение выхода через стилизованную модалку `showConfirm`.
- `clients/web/js/auth.js`: передача кода доступа в `api.login`.
- `clients/web/js/dialogs.js`: модалка подтверждения `showConfirm(message, title, confirmText, cancelText)`.
- `clients/web/js/gallery.js`: отображение кадров, SVG-анимация жизненного цикла растения во время загрузки, заглушка при отсутствии кадров.
- `clients/web/js/test_snap.js`: чтение снимка из `res.captures[0]`, SVG-анимация жизненного цикла растения при съёмке, медиа-заглушка с юмором при ошибке камеры.
- `clients/web/js/units.js`: стилизованные медиа-заглушки для превью установок.
- `clients/web/js/rpi_setup.js`: блокировка выбора боевого режима `rpi` при отсутствии реального оборудования (`is_real_rpi === false`).

### Telegram-бот (`clients/telegram_bot/`):
- `clients/telegram_bot/handlers/installations.py`: кнопка «Отмена» во всех состояниях создания и редактирования установки (`inst:cancel` и `inst:cancel_edit:{inst_id}`), сохранение шага FSM при невалидном вводе и ошибках API.
- `clients/telegram_bot/handlers/timelapse.py`: расчет частоты строго под 30 FPS (`round(total / 30.0, 1)`) без пресетов 5/10/15/30с, подпись при равномерном прореживании: «Собрано видео с частотой 30 FPS (прореживание 1 из {step} для соблюдения лимита Telegram)».

### Edge-агент и скрипты:
- `edge_agent/config.py`: дефолт `HARDWARE_MODE="auto"`, автоопределение Raspberry Pi с фолбэком на `mock`.
- `scripts/install_server.sh`: настройка `/etc/sudoers.d/growvision` для беспарольного перезапуска служб через sudo.

### Документация:
- `docs/API_CONTRACT.md`
- `docs/BOT_SPEC.md`
- `docs/DB_SCHEMA.md`
- `docs/USER_GUIDE.md`
- `docs/WEB_SPEC.md`

### Тесты:
- `tests/e2e/test_stage8_5_ux_and_mock_e2e.py` (3 теста: унификация входа, RPi/Mock блокировка, mock-изоляция БД).
- `clients/telegram_bot/tests/test_stage8_5_bot_fsm_and_timelapse.py` (3 теста: сброс FSM по отмене, сохранение шага при валидации, 30 FPS таймлапс).
- `tests/e2e/fake_telegram.py`: обновление полей `BotFullInfo` в mock Telegram Bot API (`getMe`).
- `tests/e2e/test_stage8_web_e2e.py`: адаптация теста повторного входа под вход по коду приглашения без личного пароля.
- `edge_agent/tests/test_config.py`, `server/tests/test_stage8_web_auth.py`, `tests/e2e/test_stage8_rpi_setup_e2e.py`, `clients/telegram_bot/tests/test_stage7_3_bot.py`.

---

## 3. Результаты проверок и тестов

1. **Edge-агент**:
   - `edge_agent/.venv/bin/pytest edge_agent/tests/`: **55 passed**.
2. **CLI**:
   - `clients/cli/.venv/bin/pytest clients/cli/tests/`: **51 passed**.
3. **Сервер**:
   - `server/.venv/bin/pytest server/tests/`: **74 passed**.
4. **Telegram-бот**:
   - `clients/telegram_bot/.venv/bin/pytest clients/telegram_bot/tests/`: **66 passed**.
5. **E2E-тесты**:
   - `server/.venv/bin/pytest tests/e2e/`: **73 passed**.
6. **Линтинг и форматирование**:
   - `server/.venv/bin/ruff check .`: **All checks passed!**
   - `server/.venv/bin/ruff format --check .`: **243 files already formatted**.
7. **Инварианты Web SPA**:
   - 0 эмодзи: **PASSED**.
   - 0 alert/confirm/prompt: **PASSED**.
   - 0 X-Client-Key: **PASSED**.
   - 0 CDN / внешних библиотек: **PASSED**.
   - Статическая валидация ES-модулей: **PASSED**.
