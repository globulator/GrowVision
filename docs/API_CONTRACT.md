# GrowVision — API-контракт (MVP)

Контракт REST API и протокола обмена Server ↔ Edge Agent (Raspberry Pi 5) ↔ Клиенты (CLI, Telegram-бот, Web-плеер). Построен на схеме БД из `DB_SCHEMA.md`.

Два контура:

- **Edge API** (`/api/edge/*`) — Raspberry Pi. Pull-модель: RPi сама шлёт heartbeat, забирает команды и загружает кадры.
- **Client API** (`/api/*`) — единый API для тонких клиентов. Бизнес-логика, проверка ролей и расчёт площади живут только на сервере.

---

## 1. Общие правила

| Правило | Значение |
|---|---|
| Формат | `application/json`; загрузка кадров — `multipart/form-data`; видео — `video/mp4`. |
| Время | ISO 8601, UTC, с суффиксом `Z` (`2026-11-18T19:30:00Z`). |
| Авторизация | Edge: `X-Edge-Key`. Клиенты: `X-Client-Key` + `X-Client-Type` + `X-External-Id`. Публичные эндпоинты (роль Гость) — без заголовков или с верным `X-Client-Key` без сессии (см. 1.1). |
| Ошибки | Единый формат `{"detail": "..."}`; коды: 401, 403, 404, 409, 422, 429, 503, 504. |

### 1.1. Авторизация

| Заголовок | Кто | Значение |
|---|---|---|
| `X-Edge-Key` | RPi | Секрет `EDGE_API_KEY` из `.env`. Неверный или отсутствует → 401. |
| `X-Client-Key` | CLI, бот | Общий секрет клиентов `CLIENT_API_KEY` из `server/.env`. Неверный → 401; отсутствует → 401 (кроме публичных эндпоинтов, где заголовок необязателен). |
| `X-Client-Type` | CLI, бот | `telegram` или `cli`. |
| `X-External-Id` | CLI, бот | `telegram_id` или идентификатор профиля CLI. |

**Защищённые эндпоинты** (все клиентские, кроме `/api/auth/login` и публичных ниже): сервер находит сессию в `users` по паре `(client_type, external_id)`, обновляет `last_active_at` и проверяет роль:

- нет сессии → 401;
- роли не хватает → 403.

**Публичные эндпоинты** (`GET /api/version`, `GET /api/installations`, `GET /api/installations/{id}`, `GET /api/timelapse/{installation_id}/frames`) доступны Гостю:

- без заголовков (Web-плеер) → роль Гость;
- с верным `X-Client-Key`, но без сессии в `users` для `(client_type, external_id)` (неавторизованный пользователь бота или CLI) → роль Гость;
- с верным `X-Client-Key` и существующей сессией → ответ строится по роли пользователя, `last_active_at` обновляется.

На публичных эндпоинтах 401 возвращается **только при неверном `X-Client-Key`**.

**Лимит входа:** не более 5 попыток `POST /api/auth/login` в минуту на пару `(client_type, external_id)`, включая неудачные. При превышении — 429 с заголовком `Retry-After`. Пароль сравнивается через `secrets.compare_digest`.

### 1.2. Коды ошибок

| Код | Когда |
|---|---|
| 401 | Нет или неверный ключ; нет сессии на защищённом эндпоинте; неверный пароль. На публичных эндпоинтах — только неверный `X-Client-Key`. |
| 403 | Недостаточно прав роли. |
| 404 | Установка, камера, команда или кадр не найдены. |
| 409 | Конфликт состояния (уникальность имени, занятый пин, удаление установки с кадрами, установка в архиве, `target = "all"` при отсутствии активных установок, неизвестная камера при загрузке кадра, результат для не-`running` команды). |
| 422 | Невалидные данные (в т.ч. `step` не целое ≥ 1, несколько камер без `camera_id` при сборке видео, `capture_interval_hours` не целое число ≥ 1). |
| 429 | Превышен лимит попыток входа. |
| 503 | Raspberry Pi офлайн (последний heartbeat старше 90 с): команда не ставится в очередь. |
| 504 | Raspberry Pi не ответила за время ожидания. |

Статус команды `error` (RPi выполнила команду, но с ошибкой) — **не ошибка HTTP**: клиентский эндпоинт возвращает 200 с `"status": "error"` и текстом в `result_message`.

### 1.3. Режим отладки

Если `system_settings.debug_mode = "true"`, ответы на управляющие действия (`/api/control/*`, `/api/cameras/scan`) содержат `debug_info`. Поле видит **только Developer**; Biologist его не получает. `log_line` берётся из `edge_commands.debug_log`, а `result_message` в ответе остаётся обычным текстом для всех ролей.

```json
"debug_info": {
  "command": {"id": 42, "command_type": "SNAP", "target": 1, "payload": null},
  "log_line": "[2026-11-18T19:30:02Z] [INFO] [GPIO] Inst#1: light=0, flash=1 -> captured 1 frames"
}
```

### 1.4. Жизненный цикл команд (`edge_commands`)

```
pending --claim--> running --result--> done | error
   |                  |
   +--- timeout ------+
```

| Правило | Значение |
|---|---|
| Срок жизни `pending` | 30 с. Старше → `timeout`, чтобы свет и камера не сработали внезапно позже. |
| Срок `running` | От `started_at`: `max(60 с, ожидание клиента + 15 с)`. Для одиночных команд это 60 с; для SNAP с `is_global = TRUE` — `(15 + 10 × N) + 15` с, где N — число активных установок (например, 80 с при N = 5). Старше → `timeout` (защита от потери питания RPi). |
| Когда проверяется | Перед каждым `claim` и фоновой задачей раз в 5 с. |
| Выдача команд | `claim` выдаёт не более одной команды за запрос. Агент выполняет команды строго по очереди и забирает следующую после отчёта о предыдущей, поэтому время на `running` не расходуется на ожидание. |
| Занятость оборудования | На RPi единый аппаратный мьютекс на GPIO и камеры. Если во время плановой съёмки приходит ручная команда, плановая съёмка выполняется первой (с приоритетом), а ручная команда не ждёт в очереди: агент сразу отвечает `status = "error"`, `result_message = "Камера недоступна, попробуйте позже"`. |
| Ожидание клиента | SNAP — 15 с для одной установки; для `target = "all"` — 15 с + 10 с × N, где N — число активных установок. Остальные управляющие команды и SCAN_CAMERAS — 10 с. |
| По истечении ожидания | Если команда ещё `pending` — сервер сразу переводит её в `timeout` (RPi её не выполнит) и отвечает 504. Если уже `running` — 504 с пометкой, что результат неизвестен; команда уйдёт в `timeout` по сроку `running`. |
| Повторы | Для ручных команд повторных попыток нет. `attempts` ведётся для телеметрии и на будущее (всегда 1). |
| Целевая установка | `target = <id>` → `installation_id = id`, `is_global = FALSE`. `target = "all"` → `installation_id = NULL`, `is_global = TRUE`. Для `all` берутся только активные установки; если их нет, команда не создаётся, ответ `409` `{"detail": "Нет активных установок"}`. |

---

## 2. Сводка эндпоинтов

### 2.1. Edge API

Доступ: `X-Edge-Key`.

| Метод | URL | Назначение |
|---|---|---|
| POST | `/api/edge/heartbeat` | Телеметрия, статус известных камер; в ответе актуальный конфиг. Интервал 30 с (`HEARTBEAT_INTERVAL_SEC`). |
| POST | `/api/edge/commands/claim` | Забрать одну команду из очереди (`pending → running`). Интервал 2–3 с. |
| POST | `/api/edge/commands/{command_id}/result` | Отчёт о выполнении команды. |
| POST | `/api/edge/captures` | Загрузка кадра и замеров (плановые, ручные, выгрузка из `offline_queue`). |

### 2.2. Client API

| Метод | URL | Доступ | Назначение |
|---|---|---|---|
| POST | `/api/auth/login` | Все (с `X-Client-Key`) | Вход по логину и паролю/коду, создание/активация сессии. |
| POST | `/api/auth/logout` | Biologist, Developer | Выход, перевод сессии в `is_active = False`. |
| GET | `/api/auth/me` | Biologist, Developer | Роль, логин, время активности. |
| POST | `/api/auth/invites` | Developer | Создать одноразовый код приглашения (TTL 10 мин). |
| GET | `/api/auth/invites` | Developer | Список активных одноразовых кодов. |
| DELETE | `/api/auth/invites/{invite_id}` | Developer | Отозвать неиспользованный одноразовый код. |
| GET | `/api/auth/sessions` | Developer | Список всех сессий (активных и завершённых). |
| POST | `/api/auth/sessions/{session_id}/terminate` | Developer | Принудительное завершение активной сессии. |
| GET | `/api/version` | Гость, Biologist, Developer | Версия сервера. |
| GET | `/api/installations` | Гость, Biologist, Developer | Список установок (проекция зависит от роли). |
| GET | `/api/installations/{id}` | Гость, Biologist, Developer | Карточка установки. |
| POST | `/api/installations` | Developer | Создать установку. |
| PATCH | `/api/installations/{id}` | Biologist (name, culture_name), Developer (все поля) | Изменить установку. |
| POST | `/api/installations/{id}/archive` | Developer | Архивировать установку, отвязать камеры. |
| DELETE | `/api/installations/{id}` | Developer | Удалить установку (только если кадров 0). |
| GET | `/api/cameras` | Developer | Список камер. |
| POST | `/api/cameras/scan` | Developer | Сканирование камер на RPi (SCAN_CAMERAS). |
| POST | `/api/cameras/{camera_id}/bind` | Developer | Привязать или отвязать камеру. |
| POST | `/api/control/snap` | Biologist, Developer | Ручной снимок (SNAP). |
| GET | `/api/control/captures/{capture_id}` | Biologist, Developer | Получение ссылок на кадр (`raw_url`, `annotated_url`). |
| POST | `/api/control/light/main` | Developer | Основной свет (LIGHT_MAIN). |
| POST | `/api/control/light/flash` | Developer | Вспышка (LIGHT_FLASH). |
| POST | `/api/control/relay-test` | Developer | Тест реле (RELAY_TEST), 3 с. |
| GET | `/api/timelapse/{installation_id}/frames` | Гость, Biologist, Developer | Плановые кадры таймлапса. |
| POST | `/api/timelapse/{installation_id}/video` | Biologist, Developer | MP4 из всех плановых кадров (с прореживанием `step` или целевой длительностью `duration_sec`); при одном плановом кадре — JPEG. |
| GET | `/api/system/status` | Developer | Версия сервера, телеметрия RPi, настройки, связь. |
| PATCH | `/api/system/settings` | Developer | Изменить глобальные настройки. |

---

## 3. Edge API: схемы

### 3.1. `POST /api/edge/heartbeat`

Запрос:

```json
{
  "software": {
    "edge_version": "0.2.0-a3f9c12",
    "python_version": "3.11.2",
    "model_name": "lettuce_seg_v1.onnx"
  },
  "hardware": {
    "board_model": "Raspberry Pi 5 Model B Rev 1.0",
    "os_info": "Debian GNU/Linux 12 (bookworm)",
    "uptime_sec": 84210,
    "cpu_temp_c": 48.5,
    "cpu_usage_percent": 14.2,
    "ram_used_mb": 612,
    "ram_total_mb": 8062,
    "disk_free_gb": 18.4,
    "gpio_mode": "rpi"
  },
  "offline_queue_count": 0,
  "detected_cameras": [
    {"hardware_id": "usb-Digma_DiCam_300_SN01-video-index0", "auto_name": "Digma DiCam 300"}
  ]
}
```

Действия сервера:

- Сохраняет тело в `system_settings.rpi_last_telemetry` (время приёма — `updated_at`).
- **Камеры не создаёт.** Для известных `hardware_id`, присутствующих в `detected_cameras`: `is_online = TRUE`, `last_seen_at = now`.
- Известные камеры, которых нет в списке: `is_online = FALSE` (`last_seen_at` не меняется).
- Неизвестные `hardware_id` игнорируются. Новые камеры появляются только через SCAN_CAMERAS.

Ответ `200 OK` (RPi применяет конфиг без перезагрузки):

```json
{
  "capture_interval_hours": 1,
  "debug_mode": false,
  "active_installations": [
    {
      "id": 1,
      "light_gpio_pin": 17,
      "flash_gpio_pin": 27,
      "camera_hardware_ids": ["usb-Digma_DiCam_300_SN01-video-index0"]
    }
  ]
}
```

### 3.2. `POST /api/edge/commands/claim`

Тело запроса пустое. Сервер:

1. Переводит просроченные команды в `timeout` (см. 1.4).
2. Берёт **одну** самую старую команду `pending` (`created_at ASC`, `LIMIT 1`), переводит в `running`, ставит `started_at = now`, `attempts += 1`.

Метод `POST`, потому что запрос меняет состояние. Если ответ потерялся, команда уйдёт в `timeout` по сроку `running` (см. 1.4) и повторно не выдаётся. За один запрос выдаётся не более одной команды (см. 1.4).

Ответ `200 OK` (массив из 0 или 1 элемента; пустой, если команд нет):

```json
[
  {
    "id": 42,
    "command_type": "SNAP",
    "is_global": false,
    "installation": {
      "id": 1,
      "light_gpio_pin": 17,
      "flash_gpio_pin": 27,
      "camera_hardware_ids": ["usb-Digma_DiCam_300_SN01-video-index0"]
    },
    "installations": null,
    "payload": null
  }
]
```

- `command_type`: `SNAP`, `LIGHT_MAIN`, `LIGHT_FLASH`, `RELAY_TEST`, `SCAN_CAMERAS`.
- Для `is_global: true` заполнено `installations` (массив всех активных установок в том же формате), а `installation` равно `null`.

### 3.3. `POST /api/edge/commands/{command_id}/result`

```json
{
  "status": "done",
  "result_message": "Снимок выполнен успешно",
  "debug_log_line": "[2026-11-18T19:30:02Z] [INFO] [GPIO] Inst#1: light=0, flash=1 -> captured 1 frames",
  "scanned_cameras": [
    {"hardware_id": "usb-Digma_DiCam_300_SN01-video-index0", "auto_name": "Digma DiCam 300"}
  ]
}
```

- `status`: `done` | `error`. При ошибке камеры: `"status": "error"`, `"result_message": "Камера недоступна, попробуйте позже"`.
- `scanned_cameras` — только для SCAN_CAMERAS. Сервер создаёт записи для новых `hardware_id` (`is_online = TRUE`), для известных обновляет `auto_name`, `is_online`, `last_seen_at`. Список `hardware_id` из `scanned_cameras` сервер хранит в памяти процесса только на время ожидания клиента (в БД не пишется) и по нему формирует ответ `POST /api/cameras/scan`.
- `result_message` сохраняется в `edge_commands.result_message`, `debug_log_line` (необязательное поле) — в `edge_commands.debug_log`.
- Ответ: `200 {"ok": true}`. Команда не в статусе `running` (например, уже `timeout`) → `409`; агент отчёт отбрасывает.

### 3.4. `POST /api/edge/captures`

`multipart/form-data`:

| Часть | Тип | Описание |
|---|---|---|
| `metadata` | JSON-строка | См. ниже. |
| `raw_image` | файл `.jpg` | Оригинал. |
| `annotated_image` | файл `.jpg` | С наложенной маской нейросети. |

`metadata`:

```json
{
  "installation_id": 1,
  "camera_hardware_id": "usb-Digma_DiCam_300_SN01-video-index0",
  "captured_at": "2026-11-18T19:00:05Z",
  "is_manual": false,
  "aruco_area_px": 4120.5,
  "command_id": null,
  "measurements": [
    {"plant_index": 0, "area_px": 15430.0}
  ]
}
```

| Поле | Правило |
|---|---|
| `command_id` | Необязательный. Заполняется для кадров, снятых по команде (SNAP, `is_manual = true`). Для плановых кадров и выгрузки из `offline_queue` — `null`. |
| `aruco_area_px` | `null`, если маркер не найден. |
| `measurements` | Может быть пустым (растение не обнаружено). |

Порядок для SNAP: **сначала** загрузка кадра, **затем** `POST .../result`. По `command_id` сервер связывает кадр с ожидающим клиентским запросом. Связь `command_id → capture_id` хранится в памяти процесса только на время ожидания клиента и в БД не пишется. Если команда не найдена или уже не `running`, кадр всё равно сохраняется, а связь игнорируется.

Действия сервера:

- Нормализует `captured_at` до секунд (`YYYY-MM-DDTHH:MM:SSZ`) перед проверкой уникальности.
- Находит камеру по `camera_hardware_id`. Неизвестная камера → `409` с текстом «Камера не зарегистрирована, выполните сканирование камер» (кадр остаётся в `offline_queue` RPi до регистрации камеры через SCAN_CAMERAS). Код отличается от `422`, чтобы агент не путал этот случай с битым пакетом.
- Сохраняет файлы в `server/media/{installation_id}/` (`raw_{capture_id}.jpg`, `ann_{capture_id}.jpg`), создаёт записи в `captures` и `plant_measurements`.
- Кадры архивных установок принимаются.

Ответы:

| Код | Когда | Тело |
|---|---|---|
| `201 Created` | Новый кадр | `{"capture_id": 105}` |
| `200 OK` | Дубль по `UNIQUE(installation_id, camera_id, captured_at)` | `{"capture_id": 105}` (существующий; замеры и файлы не перезаписываются) |
| `404` | Установка не найдена | `{"detail": "..."}` |
| `409` | Камера с таким `camera_hardware_id` не зарегистрирована | `{"detail": "Камера не зарегистрирована, выполните сканирование камер"}` |
| `422` | Невалидные данные | `{"detail": "..."}` |

Поведение агента:

- `200` или `201` — RPi удаляет локальный файл из `offline_queue/`.
- `409` — пакет остаётся в `offline_queue/` и отправляется повторно после регистрации камеры через SCAN_CAMERAS.
- `422` (а также `404`) — агент переносит пакет в `offline_queue/failed/` и продолжает выгрузку остальной очереди, чтобы битый пакет её не блокировал.

---

## 4. Client API: схемы

### 4.1. Авторизация и управление сессиями

`POST /api/auth/login`:

Тело запроса:
```json
{
  "client_type": "telegram",
  "external_id": "123456789",
  "password": "secret_or_invite_code",
  "login": "Андрей Бармин"
}
```

Правила обработки:
- Поле `login` является опциональным (`login: str | None = None`).
- Для `client_type == "cli"` принимается **только** постоянный пароль разработчика (`settings.developer_password`), поле `login` обязательно; при успехе возвращается роль `developer`.
- Для `client_type == "telegram"`:
  - Если передан `settings.developer_password` — назначается роль `developer`.
  - Если передан активный одноразовый код из `invite_codes` (`used_at IS NULL`, `is_revoked == False`, `expires_at > now`) — назначается роль `biologist`.
- Если запись `(client_type, external_id)` уже существует в `users` и `login` не передан — сервер автоматически подтягивает сохранённый `user.login`.
- Если записи в `users` нет и `login` не передан (первичный вход) — код **не сжигается**, сервер возвращает ответ:
  ```json
  {"status": "needs_login", "role": "biologist"}
  ```
  чтобы клиент запросил у пользователя Имя и Фамилию и повторил запрос с `login`.
- При успешном завершении входа:
  - Одноразовый код помечается использованным (`used_at = now`, `used_by_user_id = user.id`).
  - Сессия активируется (`is_active = True`, `logged_out_at = None`, обновляются `authorized_at` и `last_active_at`).
  - Ответ `200`: `{"role": "biologist", "login": "Андрей Бармин"}`.

Ошибки:
- Неверный пароль или недействительный/просроченный код → `401`.
- Превышен лимит попыток (более 5 в минуту) → `429` с заголовком `Retry-After`.

`POST /api/auth/logout`:
Завершение текущей сессии. Запись из таблицы `users` **не удаляется**, а переводится в `is_active = False` с фиксацией времени `logged_out_at = now`. Ответ: `200 {"ok": true}`.

`GET /api/auth/me`:
Возвращает профиль текущей активной сессии:
```json
{
  "login": "Андрей Бармин",
  "role": "biologist",
  "authorized_at": "2026-11-18T18:00:00Z",
  "last_active_at": "2026-11-18T19:25:10Z"
}
```
Если сессия не найдена или `is_active == False` → `401`.

#### Эндпоинты управления кодами приглашений (только Developer)

- `POST /api/auth/invites`:
  Генерация одноразового кода для биолога (TTL 10 минут). Тело запроса (опционально): `{"role": "biologist"}`.
  Ответ `200`:
  ```json
  {
    "id": 1,
    "code": "A7K9PX",
    "role": "biologist",
    "created_at": "2026-11-22T19:00:00Z",
    "expires_at": "2026-11-22T19:10:00Z"
  }
  ```
- `GET /api/auth/invites`:
  Список всех активных неиспользованных кодов (`used_at IS NULL`, `is_revoked == False`, `expires_at > now`).
  Ответ `200`: `[ { "id": 1, "code": "A7K9PX", "role": "biologist", ... } ]`.
- `DELETE /api/auth/invites/{invite_id}`:
  Досрочный отзыв неиспользованного кода (`is_revoked = True`).
  Ответ `200 {"ok": true}`.

#### Эндпоинты управления сессиями (только Developer)

- `GET /api/auth/sessions`:
  Список всех сессий пользователей (активных и завершённых).
  Ответ `200`:
  ```json
  [
    {
      "id": 1,
      "client_type": "telegram",
      "external_id": "123456789",
      "login": "Андрей Бармин",
      "role": "biologist",
      "is_active": true,
      "authorized_at": "2026-11-22T18:00:00Z",
      "last_active_at": "2026-11-22T18:30:00Z",
      "logged_out_at": null
    }
  ]
  ```
- `POST /api/auth/sessions/{session_id}/terminate`:
  Принудительное завершение активной сессии (перевод в `is_active = False`, фиксация `logged_out_at = now`). Позволяет завершить сессию любого биолога или другого разработчика.
  Ответ `200 {"ok": true}`.

### 4.2. Установки

Проекции ответа `GET /api/installations` и `GET /api/installations/{id}`:

| Роль | Поля |
|---|---|
| Гость | `id`, `name`, `culture_name`, `is_archived`, `last_area_display`. Без пинов и `hardware_id`. |
| Biologist | Поля Гостя + `aruco_size_mm`, `created_at`, `cameras[]` (`id`, `auto_name`, `is_online`). |
| Developer | Поля Biologist + `light_gpio_pin`, `flash_gpio_pin`, `cameras[].hardware_id`. |

`last_area_display` считается сервером по правилу из `DB_SCHEMA.md` для последнего планового кадра (`is_manual = FALSE`), `plant_index = 0`. Значения: `"48.3 см²"` при наличии маркера и `aruco_size_mm`, иначе `"15430 px"`, `null`, если кадров или замеров нет.

Пример для Гостя (`GET /api/installations?include_archived=false`):

```json
[
  {"id": 1, "name": "Установка 1 — Фитолампа", "culture_name": "Салат Афицион", "is_archived": false, "last_area_display": "48.3 см²"}
]
```

Пример для Developer (одна запись):

```json
{
  "id": 1,
  "name": "Установка 1 — Фитолампа",
  "culture_name": "Салат Афицион",
  "is_archived": false,
  "last_area_display": "48.3 см²",
  "aruco_size_mm": 50.0,
  "created_at": "2026-11-10T09:00:00Z",
  "light_gpio_pin": 17,
  "flash_gpio_pin": 27,
  "cameras": [
    {"id": 1, "auto_name": "Digma DiCam 300", "is_online": true, "hardware_id": "usb-Digma_DiCam_300_SN01-video-index0"}
  ]
}
```

| Метод и URL | Тело / параметры | Результат и ошибки |
|---|---|---|
| `GET /api/installations` | `?include_archived=false` (по умолчанию) | `200` список. |
| `POST /api/installations` | `{"name", "culture_name", "light_gpio_pin", "flash_gpio_pin", "aruco_size_mm"}` (`aruco_size_mm` необязательный) | `201` карточка. `409`: имя занято, пин занят другой активной установкой (в т.ч. пин света = пин вспышки другой); `422`: невалидные данные. |
| `PATCH /api/installations/{id}` | Любое подмножество полей; Biologist — только `name`, `culture_name` | `200` карточка. Biologist с другими полями → `403`; `409` как при создании. |
| `POST /api/installations/{id}/archive` | — | `200`: `is_archived = TRUE`, у всех камер установки `installation_id = NULL`. |
| `DELETE /api/installations/{id}` | — | `204`, если в `captures` 0 кадров; иначе `409` `{"detail": "У установки есть снимки, используйте архивацию"}`. |

### 4.3. Камеры

`GET /api/cameras` → `200`:

```json
[
  {"id": 1, "hardware_id": "usb-Digma_DiCam_300_SN01-video-index0", "auto_name": "Digma DiCam 300", "is_online": true, "last_seen_at": "2026-11-18T19:29:40Z", "installation_id": 1, "installation_name": "Установка 1 — Фитолампа"}
]
```

`installation_id` и `installation_name` равны `null`, если камера свободна.

- `POST /api/cameras/scan` — ставит SCAN_CAMERAS (`is_global = TRUE`), ждёт до 10 с. Ответ `200`: `{"command_id": 43, "status": "done", "cameras": [ ... ]}`. RPi офлайн → `503`; нет ответа → `504`.
  - `cameras` содержит **все камеры, обнаруженные при сканировании**: и ранее известные, и найденные впервые (новые регистрируются автоматически как свободные). Камеры, не найденные при сканировании, в ответ не входят. Признака «новая» нет.
  - Формат элементов как в `GET /api/cameras`: у свободной камеры `installation_id` и `installation_name` равны `null`, у привязанной — заполнены. По ним клиент отличает камеры, которые можно привязать, от уже привязанных к установке.
  - При `status = "error"` (RPi выполнила команду с ошибкой) `cameras` — пустой массив, а текст ошибки в `result_message`.
- `POST /api/cameras/{camera_id}/bind` — `{"installation_id": 1}` или `{"installation_id": null}`. `200` с обновлённой камерой; `404`: камера или установка не найдены; `409`: установка в архиве.

### 4.4. Управление

Все запросы принимают `target`: число (`installation_id`) или `"all"`. Установка не найдена → `404`, установка в архиве → `409`, `"all"` без активных установок → `409` `{"detail": "Нет активных установок"}`, RPi офлайн → `503`, нет ответа → `504`. Время ожидания — по 1.4 (для SNAP с `"all"`: 15 с + 10 с × N).

`POST /api/control/snap` — `{"target": 1}`. Ответ `200`:

```json
{
  "command_id": 42,
  "status": "done",
  "result_message": "Снимок выполнен успешно",
  "captures": [
    {
      "capture_id": 105,
      "installation_id": 1,
      "camera_id": 1,
      "captured_at": "2026-11-18T19:30:01Z",
      "raw_url": "/media/1/raw_105.jpg",
      "annotated_url": "/media/1/ann_105.jpg",
      "area_display": "48.3 см²"
    }
  ]
}
```

- Для `"all"` в `captures` по одному элементу на каждый полученный кадр.
- Ошибка RPi: `{"command_id": 42, "status": "error", "result_message": "Камера недоступна, попробуйте позже", "captures": []}`.
- Если RPi занята плановой съёмкой, ответ тот же, что и при ошибке RPi: `200` с `"status": "error"` и `"result_message": "Камера недоступна, попробуйте позже"` (см. 1.4).
- Ручные кадры сохраняются с `is_manual = TRUE` и в таймлапс не попадают.

`GET /api/control/captures/{capture_id}` (Biologist, Developer):
Возвращает метаданные и прямые ссылки на сырой и размеченный кадр по `capture_id`:
```json
{
  "capture_id": 105,
  "installation_id": 1,
  "camera_id": 1,
  "captured_at": "2026-11-18T19:30:01Z",
  "raw_url": "/media/1/raw_105.jpg",
  "annotated_url": "/media/1/ann_105.jpg",
  "area_display": "48.3 см²"
}
```
Используется Telegram-ботом при переключении слоёв «Исходник / Разметка» в карточке ручного снимка. Если кадр не найден → `404`.

`POST /api/control/light/main` и `POST /api/control/light/flash` (Developer): `{"target": 1, "state": true}` (`target` может быть `"all"`). `POST /api/control/relay-test` (Developer): `{"target": 1}` (свет и вспышка одновременно на 3 с). Ответ `200`:

```json
{"command_id": 44, "status": "done", "result_message": "OK"}
```

### 4.5. Таймлапс

`GET /api/timelapse/{installation_id}/frames?camera_id=<id>` (доступно Гостю):

- возвращаются только плановые кадры (`is_manual = FALSE`), порядок `captured_at ASC`;
- `camera_id` необязателен: без него отдаются кадры всех камер установки (в каждом кадре есть `camera_id`; если у установки несколько камер, клиент группирует кадры по нему);
- `area_display` берётся по `plant_index = 0` (формат — как у `last_area_display`; `null`, если замеров нет); суммирование площадей нескольких кустов оставлено на будущее;
- пагинации в MVP нет;
- если плановых кадров нет, ответ `200` с пустым массивом `frames`.

```json
{
  "installation_id": 1,
  "installation_name": "Установка 1 — Фитолампа",
  "culture_name": "Салат Афицион",
  "frames": [
    {
      "capture_id": 12,
      "camera_id": 1,
      "captured_at": "2026-11-18T10:00:05Z",
      "raw_url": "/media/1/raw_12.jpg",
      "annotated_url": "/media/1/ann_12.jpg",
      "area_display": "42.1 см²"
    }
  ]
}
```

`POST /api/timelapse/{installation_id}/video` (Biologist, Developer):

```json
{"camera_id": null, "step": 1, "duration_sec": 10.0}
```

- `camera_id` необязателен.
- `step` необязателен: целое ≥ 1, по умолчанию `1` (прореживание выключено, берутся все плановые кадры). `step = N` — берётся каждый N-й плановый кадр, начиная с первого; последний кадр включается всегда, поэтому при двух и более плановых кадрах в видео всегда есть минимум 2 кадра.
- `duration_sec` необязателен: целевая длительность результирующего видео в секундах (> 0). Если параметр передан, сервер рассчитывает FPS для получения заданной длительности (`fps = clamp(total_frames / duration_sec, 1.0, 60.0)`). По умолчанию используется базовый FPS (10.0).
- Видео собирается из плановых кадров установки за всю историю (включая архивные установки и кадры сменённых камер) в порядке `captured_at ASC`. Число кадров сервер не ограничивает. `step` по умолчанию выключен; прореживание запрашивают CLI (`--step`) и Telegram-бот (при превышении лимита 50 МБ). Веб видео не запрашивает: он загружает кадры через `GET .../frames` и проигрывает их сам.
- Ответ зависит от числа плановых кадров N (с учётом `camera_id`, до прореживания):

| N | Ответ |
|---|---|
| 0 | `404` `{"detail": "Плановые снимки не найдены"}` |
| 1 | `200`, `Content-Type: image/jpeg`: оригинал единственного кадра (`raw`) вместо видео; `step` игнорируется |
| ≥ 2 | `200`, `Content-Type: video/mp4` |

- Если `camera_id` не указан и у установки есть плановые кадры разных камер, снятые с интервалом не более 10 минут (камеры снимали одновременно), → `422`; в `detail` перечислены камеры: `{"detail": "У установки несколько камер, укажите camera_id: 1 (Digma DiCam 300), 2 (Logitech C270)"}`. Кадры сменённых камер, не пересекающиеся по времени, такой ошибки не вызывают: при замене камеры таймлапс не рвётся.
- Кодирование через `ffmpeg`: H.264 (`libx264`, `-preset veryfast -crf 28 -pix_fmt yuv420p -movflags +faststart`), без звука. Исходники — оригиналы кадров (`raw`); кадр уменьшается до высоты не более 720 px с сохранением пропорций и без увеличения: `-vf "scale=-2:min(720\,ih)"` (ширина получается чётной, `pad` не нужен).
- Частота кадров вычисляется по числу кадров n в видео (после прореживания) без округления, сравнение целочисленное. Пусть `F = VIDEO_MAX_FPS`, `D = VIDEO_MIN_DURATION_SEC`:
  - если `n ≥ F × D` (граничное равенство включено), частота равна `F`, длительность не меньше `D`;
  - иначе частота равна дроби `n / D`, и видео длится ровно `D` секунд.
  
  Дробь передаётся в `ffmpeg` как есть (например, `-framerate 3/2`). По умолчанию `F = 10`, `D = 2` (целое ≥ 1). Примеры: 2 кадра → 1 fps, ровно 2 с; 10 кадров → 5 fps, 2 с; 20 кадров → 10 fps, 2 с (граница); 100 кадров → 10 fps, 10 с.
- Эндпоинт синхронный (`def`, не `async def`): сборка идёт в пуле потоков, чтобы `ffmpeg` не блокировал event loop единственного воркера (heartbeat и claim команд обрабатываются во время сборки).
- Ответ `200` отдаётся как `Content-Disposition: attachment` с `Content-Length`. Размер файла сервер не ограничивает.
- Ошибки: `404` — установка или камера не найдены, плановых кадров нет; `422` — `step` не целое ≥ 1, несколько камер без `camera_id`.

### 4.6. Версия и система

`GET /api/version` (доступен Гостю, см. 1.1) → `200`:

```json
{"server_version": "0.2.0-b71c4de"}
```

Единый источник версии для CLI, Telegram-бота и Web-плеера: клиенты не импортируют `shared/version.py`. Версия ПО RPi доступна Developer в `GET /api/system/status` (`rpi_telemetry.software.edge_version`).

`GET /api/system/status` (Developer) → `200`:

```json
{
  "server_version": "0.2.0-b71c4de",
  "rpi_connection": "online",
  "rpi_last_heartbeat_at": "2026-11-18T19:29:40Z",
  "settings": {"capture_interval_hours": 1, "debug_mode": false},
  "rpi_telemetry": {
    "software": {"edge_version": "0.2.0-a3f9c12", "python_version": "3.11.2", "model_name": "lettuce_seg_v1.onnx"},
    "hardware": {"board_model": "Raspberry Pi 5 Model B Rev 1.0", "cpu_temp_c": 48.5, "disk_free_gb": 18.4},
    "offline_queue_count": 0
  }
}
```

`rpi_connection` равен `"offline"`, если последний heartbeat был больше 90 с назад (или heartbeat не поступал). `rpi_telemetry` — последнее тело heartbeat целиком (в примере сокращено).

`PATCH /api/system/settings` (Developer): `{"capture_interval_hours": 2}` и/или `{"debug_mode": true}`. Ответ `200` содержит актуальные настройки. `capture_interval_hours` — строго положительное целое число (≥ 1, дробные значения вроде 0.5 не допускаются). Невалидное значение (не целое ≥ 1, `debug_mode` не булево) → `422`. Изменения доходят до RPi с ближайшим heartbeat (до 30 с).

При смене `capture_interval_hours` в ответ добавляется предупреждение о неравномерном таймлапсе (изменение применяется без дополнительных подтверждений):

```json
{
  "capture_interval_hours": 2,
  "debug_mode": false,
  "warning": "Интервал съёмки изменён с 1 на 2 ч: шаг таймлапса станет неравномерным."
}
```

### 4.7. Формат ошибки

```json
{"detail": "Камера недоступна, попробуйте позже"}
```
