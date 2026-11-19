# Этап 3: Client API (часть 1: auth, installations, cameras, system)
**Результат:** принят (PASS)
**Что появилось:**
- Эндпоинты авторизации: POST /api/auth/login (с ограничением 5 попыток/мин и заголовком Retry-After), POST /api/auth/logout, GET /api/auth/me с разграничением прав ролей (Гость, Biologist, Developer).
- Управление установками: GET /api/installations (с фильтром архива, проекциями ролей и расчетом last_area_display), GET/POST/PATCH/DELETE и архивация POST /{id}/archive.
- Управление камерами: GET /api/cameras (сокрытие hardware_id для биологов) и привязка/отвязка POST /api/cameras/{id}/bind с проверкой архивных установок.
- Системные эндпоинты: GET /api/version, GET /api/system/status (онлайн-статус RPi по порогу 90 с), PATCH /api/system/settings с валидацией интервала кадров.
- 60 автотестов (юнит и E2E), проверяющих ролевой доступ, rate limit и бизнес-правила.
**Как попробовать:** `server/.venv/bin/python -m server.main` (эндпоинты в `http://127.0.0.1:8000/docs`)
**Исправленные дефекты:** нет
**Отложенные замечания (DEFERRED):** нет
**Что нужно от вас:** ничего
