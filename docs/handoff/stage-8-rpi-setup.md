# Handoff: Этап 8.4 — Помощник подключения Raspberry Pi в Web SPA, bootstrap-скрипт и скрипты деплоя

## 1. Общие сведения
- **Этап:** 8.4
- **Режим:** FULL
- **Ветка:** `stage-8/rpi-setup-and-deploy`
- **Роль исполнителя:** Builder

## 2. Реализованный функционал

### 2.1. Конфигурация сервера и безопасность
- В `server/config.py` добавлены поля `repo_url` (`REPO_URL`) и `edge_server_url` (`EDGE_SERVER_URL`).
- Обновлен `server/.env.example` с безопасными нейтральными плейсхолдерами (`https://github.com/<owner>/<repo>.git`, `http://<server-host>:8000`).
- Нигде в коде, тестах, скриптах и подсказках не используются реальные личные IP, пароли или токены.

### 2.2. Серверные эндпоинты первичной настройки Edge (`server/api/edge_setup.py`)
- `GET /api/edge/setup/defaults` (доступ только роли `developer`): возвращает `default_server_url` и `repo_url` (с автоопределением из git).
- `POST /api/edge/setup/token` (доступ только роли `developer`): принимает адрес сервера, необязательный `netbird_setup_key` и `hardware_mode` (`rpi`/`mock`). Генерирует криптостойкий токен (`secrets.token_urlsafe(24)`), сохраняет параметры строго в памяти (`_setup_tokens`, TTL 600 с, без записи в SQLite и без логирования секретов). Возвращает токен, время жизни и путь к скрипту.
- `GET /api/edge/setup/{token}` (публичный для curl на Raspberry Pi): проверяет токен и TTL, немедленно сжигает токен (удаляет из памяти) и возвращает готовый bash-скрипт (`text/x-shellscript`) с экранированием всех подставляемых параметров через `shlex.quote`. Повторный вызов возвращает `404 Not Found`.

### 2.3. Универсальные скрипты деплоя и обновления (`scripts/`)
- `scripts/gv-update.sh` (chmod +x): единая команда обновления `gv-update` для сервера и RPi. Делает `git pull --ff-only`, определяет установленные окружения Python (`server`, `telegram_bot`, `cli`, `edge_agent`), обновляет зависимости и перезапускает активные systemd-службы (`growvision-server`, `growvision-bot`, `growvision-edge`).
- `scripts/install_server.sh` (chmod +x): скрипт развёртывания сервера с нуля. Настраивает пакеты, создает `.venv`, генерирует `server/.env` из шаблона с правами `chmod 600`, регистрирует `growvision-server.service` и `growvision-bot.service`, создает симлинк `/usr/local/bin/gv-update`.
- Проверена чистота синтаксиса: `bash -n scripts/gv-update.sh scripts/install_server.sh`.

### 2.4. Помощник подключения Raspberry Pi в Web SPA (`clients/web/`)
- **Слот в шапке:** Кнопка `#btn-rpi-setup` с SVG-иконкой платы и индикатором статуса Edge-агента (`#rpi-status-dot`, зеленый/серый). Видна только для роли `developer`.
- **Модальное окно `#modal-rpi-setup`:**
  - Живой статус подключения (Онлайн/Офлайн), режим (`rpi`/`mock`), локальное время последнего отклика с автообновлением каждые 3 секунды (останавливается при закрытии окна).
  - Форма: предзаполнение адреса сервера, парольное поле NetBird Setup Key с SVG-кнопкой переключения видимости, переключатель режима оборудования, кнопка «Получить команду» с анимацией ожидания.
  - Блок результата: однострочная команда `curl --noproxy '*' -fsSL ... | bash` с SVG-кнопкой копирования в буфер обмена (с подтверждением «Скопировано!» и fallback на `execCommand`) и предупреждением об одноразовости (10 минут).
  - Сворачиваемая нейтральная памятка по записи microSD, SSH и утилитам `gv-update` / `sudo nmtui`.
- **Соблюдение строгих правил:**
  - 0 Unicode-эмодзи (только векторные SVG).
  - 0 вызовов `alert/confirm/prompt`.
  - 0 утечек `X-Client-Key`.
  - 0 внешних CDN.

### 2.5. Документация и тесты
- Актуализированы `docs/API_CONTRACT.md` и `docs/USER_GUIDE.md`.
- Написаны unit-тесты `server/tests/test_stage8_rpi_setup.py` (4 passed).
- Написан e2e тест `tests/e2e/test_stage8_rpi_setup_e2e.py` (1 passed).

## 3. Результаты тестов
- Server unit: 74 passed
- Edge Agent: 54 passed
- CLI: 51 passed
- Telegram Bot: 62 passed
- E2E: 69 passed
- Ruff check & format: PASSED
- Bash syntax check: PASSED
