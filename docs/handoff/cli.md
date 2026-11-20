# Отчёт о реализации: Этап 5 — CLI-клиент

- **Статус**: READY_FOR_QA
- **Ветка**: `stage-5/cli`
- **Компонент**: `cli`
- **Карточка**: `docs/tasks/stage-5-cli.md`
- **Спецификация**: `docs/CLI_SPEC.md`
- **Контракт API**: `docs/API_CONTRACT.md`

## 1. Выполненные задачи и архитектурные решения

### 1.1. Структура компонента и строгая изоляция
- CLI реализован в директории `clients/cli/` с собственным виртуальным окружением `.venv` и зависимостями в `requirements.txt` (`typer`, `httpx`, `rich`, `python-dotenv`, `pytest`, `pytest-asyncio`).
- Обеспечена строгая изоляция от бэкенда: CLI не импортирует модули из `server/`, `shared/`, `edge_agent/` и взаимодействует исключительно через HTTP (`httpx.Client`). Проверка `grep -rn "from server|from shared|from edge_agent|import server|import shared" clients/cli` возвращает пустой вывод.
- Конфигурация загружается из `clients/cli/.env` (`SERVER_URL`, `CLIENT_API_KEY`, `CLI_DEBUG`) с валидацией наличия и формата URL.
- Сессия клиента сохраняется в `~/.growvision_cli.json` с автогенерацией `cli_<uuid4>`. В каждый HTTP-запрос передаются заголовки `X-Client-Key`, `X-Client-Type: cli`, `X-External-Id`.

### 1.2. Два режима запуска
1. **Интерактивная оболочка (`shell`)**: запуск без аргументов (`python -m clients.cli.main`) или через явную команду `shell`. Отображает баннер `GrowVision <server_version>`, приглашение `growvision> `, поддерживает выполнение команд без префикса, команды `exit`/`quit`/`q`, перехват исключений без аварийного выхода из оболочки.
2. **Одиночные команды**: `python -m clients.cli.main <команда_или_раздел> [аргументы]`.

### 1.3. Полное дерево команд по `CLI_SPEC.md`
- **Глобальные команды**: `help` (общая справка, описание ролей, примеры), `version` (версия сервера через `GET /api/version`), флаг `--verbose` / `-v` (отладка HTTP).
- **Раздел `auth`**: `auth info`, `auth login` (2-шаговый безопасный ввод с `hide_input=True`), `auth status` (`GET /api/auth/me`), `auth logout`.
- **Раздел `unit`**: `unit info`, `unit list` (таблица с адаптацией колонок под роль), `unit show` (карточка установки, поддержка `all` для всех активных), `unit add` (интерактивный ввод или флаги), `unit edit` (включая `--clear-aruco` -> `null`), `unit archive` (подтверждение или `-y`), `unit delete` (удаление при 204, обработка 409).
- **Раздел `camera`**: `camera info`, `camera list` (таблица со статусами и привязками), `camera scan` (сканирование USB/CSI-камер RPi, вывод найденных камер, блок `debug_info`), `camera bind` (привязка по ID/hardware_id), `camera unbind`.
- **Раздел `light`**: `light info`, `light main <target> <on|off>`, `light flash <target> <on|off>`, `light test <target>` (3-секундный тест реле).
- **Раздел `snap`**: `snap info`, `snap take <target> [--download <папка>]` (таблица снимков, скачивание файлов raw и ann), `snap video <target>` (сборка MP4 с прогрессом, скачивание во временный `.part`, обработка 1 кадра как JPEG с расширением `.jpg`, прореживание `--step N`).
- **Раздел `system`**: `system info`, `system status` (сервер и сводка телеметрии железа Raspberry Pi 5), `system schedule <hours>` (с выводом warning сервера), `system debug <on|off>`.

### 1.4. Резолвер целей (`clients/cli/resolver.py`)
- Единая обработка аргумента `<target>`: числовой ID, точное имя установки (с учётом `include_archived`), ключевое слово `all` (с запретом в деструктивных командах).
- Резолвер камер по ID или Hardware ID.

### 1.5. Обработка ошибок
- Реализована по разделу 4 `CLI_SPEC.md` без трейсбеков в штатных ситуациях:
  `Ошибка (<контекст действия>): <дословный detail от сервера>\nРекомендация: <конкретное действие>`
- Код возврата 1 при ошибках.

### 1.6. Документация
- Создано руководство пользователя `docs/USER_GUIDE.md` с подробным описанием установки, режимов запуска, команд и примеров.

## 2. Результаты проверок

- **Unit-тесты**: `timeout 300 ./clients/cli/.venv/bin/pytest clients/cli/tests -q` — **39 passed in 0.50s**.
- **Линтер**: `./server/.venv/bin/ruff check .` — **All checks passed!**
- **Форматирование**: `./server/.venv/bin/ruff format --check .` — **99 files already formatted.**
- **Изоляция импортов**: `grep -rn "from server|from shared|from edge_agent|import server|import shared" clients/cli` — **пусто (0 совпадений)**.
