# Задача: Этап 5 — CLI-клиент

- **Этап**: 5 (CLI-клиент)
- **Компонент**: `cli`
- **Режим**: FULL
- **Ветка**: `stage-5/cli`
- **Версия**: не поднимать (0.2.0 выставит пользователь после приёмки)
- **Allowed Paths**:
  - `clients/cli/`
  - `docs/USER_GUIDE.md` (раздел по CLI)
  - `docs/handoff/cli.md`

## Прочитать перед началом
1. `docs/PROJECT.md`, включая **раздел 9 («Правила скорости и тестов»)** — обязателен для Builder и QA!
2. `docs/CLI_SPEC.md` — детальная спецификация всех команд, аргументов, флагов, вывода, ролей и кодов ошибок.
3. `docs/API_CONTRACT.md` — контракты эндпоинтов Client API.
4. `docs/REPO_AND_VERSIONING.md` — структура репозитория, правила версий и коммитов.

## Критерии приёмки
1. Реализован CLI-клиент в `clients/cli/` (Python, Typer, httpx, rich, python-dotenv).
2. Поддерживаются оба режима:
   - Отдельные команды: `clients/cli/.venv/bin/python -m clients.cli.main <команда>` (или `help`, `version`).
   - Интерактивный shell: `clients/cli/.venv/bin/python -m clients.cli.main shell` с приглашением `growvision> ` и выполнением команд без префикса `growvision`.
3. Все 6 разделов и глобальные команды строго по `CLI_SPEC.md`:
   - `auth`: `info`, `login`, `logout`, `status`. Пароль запрашивается скрыто (`hide_input`).
   - `unit`: `info`, `list`, `show`, `add`, `edit`, `archive`, `delete`.
   - `camera`: `info`, `list`, `scan`, `bind`, `unbind`.
   - `light`: `info`, `main`, `flash`, `test`.
   - `snap`: `info`, `take` (с опциональным `--download`), `video` (с прогрессом, скачиванием во временный `.part` и обработкой одного кадра как JPEG).
   - `system`: `info`, `status`, `schedule`, `debug`.
   - Глобальные: `help`, `version`, `shell`, флаг `--verbose`.
4. Права ролей (Гость, Biologist, Developer) соблюдаются: адаптивный вывод таблиц под поля роли, Developer видит `debug_info`.
5. Обработка ошибок по `CLI_SPEC.md` разд. 4: понятный текст, дословный `detail` сервера, отсутствие трейсбеков в штатных ситуациях, код возврата 1 при ошибках.
6. Конфигурация и сессия: `clients/cli/.env` (`SERVER_URL`, `CLIENT_API_KEY`, опциональный `CLI_DEBUG`), сессия в `~/.growvision_cli.json` (с `external_id: "cli_<uuid4>"`).
7. Заголовки на каждый запрос: `X-Client-Key`, `X-Client-Type: cli`, `X-External-Id`.
8. Полная изоляция от бэкенда:
   - CLI ходит к серверу исключительно по HTTP (httpx).
   - **Строгий запрет импортов**: `grep -rn "from server\|from shared\|from edge_agent\|import server\|import shared" clients/cli` должен возвращать пустоту.
9. Тесты компонента:
   - Unit-тесты в `clients/cli/tests/` с изоляцией сетевых вызовов через `httpx.MockTransport` и временным файлом сессии.
   - Тесты быстрые (по разделу 9 `PROJECT.md`), без зависаний, с `timeout 300`.
10. Документация: черновик раздела CLI в `docs/USER_GUIDE.md` и заметка `docs/handoff/cli.md`.
11. Качество кода: `ruff check .` и `ruff format --check .` проходят без замечаний.
12. Формат коммитов: Conventional Commits (`feat(cli): ...`, `test(cli): ...`, `docs(cli): ...`). Форматирование ruff включается в тот же коммит, что и код.
