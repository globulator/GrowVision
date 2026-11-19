# План и отчет реализации: Этап 3 / Client API, часть 1 (auth, installations, cameras, system)

## План реализации
1. `server/core/area.py`: расчет `last_area_display` по DB_SCHEMA.md п. 4.5 и API_CONTRACT.md п. 4.2.
2. `server/core/rate_limit.py`: rate limiter для `POST /api/auth/login` (макс 5 попыток в минуту на пару client_type + external_id, 429 Retry-After).
3. `server/api/deps.py`: проверка `X-Client-Key`, аутентификация сессий, проверка ролей (Гость, Biologist, Developer), обновление `last_active_at` (API_CONTRACT.md п. 1.1).
4. `server/api/auth.py`: `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me` (API_CONTRACT.md п. 4.1).
5. `server/api/installations.py`: `GET /api/installations` (фильтрация, проекции ролей, `last_area_display`), `GET /{id}`, `POST` (валидация пинов и имен 409), `PATCH` (права ролей 403), `POST /{id}/archive`, `DELETE /{id}` (проверка снимков 409) (API_CONTRACT.md п. 4.2).
6. `server/api/cameras.py`: `GET /api/cameras`, `POST /api/cameras/{id}/bind` (404/409 при архиве) (API_CONTRACT.md п. 4.3).
7. `server/api/system.py`: `GET /api/version` (доступен Гостю), `GET /api/system/status` (статус RPi 90 с), `PATCH /api/system/settings` (валидация 422, warning) (API_CONTRACT.md п. 4.6).
8. `server/main.py`: подключение роутеров auth, installations, cameras, system.
9. `server/tests/`: покрытие тестами всех эндпоинтов, прав ролей, rate limit, валидаций и граничных случаев с изоляцией медиа и БД.
10. Проверка качества: `pytest server/tests`, `ruff check .`, `ruff format --check .`.

## Реализованные эндпоинты
- `POST /api/auth/login` (rate limit, timing-safe compare, обновление session_token, last_active_at, client_type, external_id)
- `POST /api/auth/logout` (очистка сессии, сброс external_id/client_type)
- `GET /api/auth/me` (роль и id текущего пользователя)
- `GET /api/installations` (query `include_archived=false`, проекции по ролям Гость/Biologist/Developer, `last_area_display`)
- `GET /api/installations/{id}` (404 при отсутствии, проекции ролей)
- `POST /api/installations` (201 Developer only, валидация уникальности имени и непересечения пинов `light_pin`/`flash_pin` среди активных)
- `PATCH /api/installations/{id}` (права: Biologist только `name` и `culture`, Developer все поля; 403/404/409)
- `POST /api/installations/{id}/archive` (Developer only, архивация установки и автоматическая отвязка привязанных камер)
- `DELETE /api/installations/{id}` (Developer only: 204 если снимков нет, 409 если снимки есть)
- `GET /api/cameras` (доступен Biologist и Developer, скрытие `hardware_id` для Biologist)
- `POST /api/cameras/{id}/bind` (Developer only: привязка/отвязка `installation_id`, 404/409)
- `GET /api/version` (доступен Гостю, формат `{ "version": "..." }`)
- `GET /api/system/status` (онлайн статус RPi по порогу `last_heartbeat_at <= 90` с, `active_commands_count`)
- `PATCH /api/system/settings` (Developer only, валидация strict int >= 1 для интервала кадров, возвращает warning)

## Результаты верификации
- `pytest server/tests`: 36 passed, 0 failed.
- `ruff check .`: All checks passed.
- `ruff format --check .`: 58 files already formatted.
- Статус: **READY_FOR_QA**
