# Карточка задачи: Исправление дефектов Этапа 7 — Telegram-бот (Итерация 1)

- **Компонент**: `telegram_bot`
- **Ветка**: `stage-7/bot`
- **Режим**: FULL
- **Спецификация**: `docs/BOT_SPEC.md`
- **Контракт API**: `docs/API_CONTRACT.md`
- **Журнал QA**: `docs/qa_log.md`

---

## Цель
Устранить дефекты BUG-001 и BUG-002, выявленные QA в ходе сквозного тестирования Telegram-бота против реального сервера Client API, обновить unit-тесты бота и обеспечить успешное прохождение всех тестов, включая `tests/e2e/test_bot_e2e.py`.

---

## Дефекты для устранения

### 1. [BUG-001] Неверное имя поля в запросе авторизации `POST /api/auth/login`
- **Где**: `clients/telegram_bot/api_client.py` (`GrowVisionApiClient.login`)
- **Проблема**: В теле запроса к Client API передаётся ключ `"username"` вместо `"login"`.
- **Требование контракта (`API_CONTRACT.md` п. 4.1, `BOT_SPEC.md` п. 5.1)**:
  Тело запроса должно содержать:
  ```json
  {
    "login": "<username>",
    "password": "<password>",
    "client_type": "telegram",
    "external_id": "<str(user_id)>"
  }
  ```
- **Ожидаемый результат**: Сервер принимает тело запроса и возвращает HTTP 200 с ролью пользователя (или 401 при неверном пароле).

### 2. [BUG-002] Неверное имя поля в запросах управления оборудованием и ручной съёмки
- **Где**: `clients/telegram_bot/api_client.py` (`control_snap`, `control_light_main`, `control_light_flash`, `control_relay_test`)
- **Проблема**: В теле запросов `POST /api/control/*` передаётся `"installation_id"` (или `None`), а сервер ожидает обязательное поле `"target"`.
- **Требование контракта (`API_CONTRACT.md` п. 4.4)**:
  Тело запросов управления:
  - `POST /api/control/snap`: `{"target": <installation_id | "all">}`
  - `POST /api/control/light/main`: `{"target": <installation_id | "all">, "state": bool}`
  - `POST /api/control/light/flash`: `{"target": <installation_id | "all">, "state": bool}`
  - `POST /api/control/relay-test`: `{"target": <installation_id | "all">, "duration_s": 3}` (или default)
- **Ожидаемый результат**: Сервер валидирует поле `"target"` без ошибки 422 и передаёт команду в очередь RPi.

---

## Критерии приёмки
1. BUG-001 и BUG-002 полностью устранены в `clients/telegram_bot/api_client.py`.
2. Unit-тесты `clients/telegram_bot/tests/` обновлены в соответствии с реальными телами запросов (проверяют `"login"` и `"target"`).
3. Все 30 unit-тестов бота проходят: `clients/telegram_bot/.venv/bin/pytest clients/telegram_bot/tests -v`.
4. Все E2E-тесты бота проходят: `server/.venv/bin/pytest tests/e2e/test_bot_e2e.py -v`.
5. Общий регрессионный прогон репозитория зелёный (222+ тестов проходят, <60 с, без `growvision.db*` в корне).
6. Форматирование и линтер чистые: `ruff check .` и `ruff format --check .`.
7. Строгая изоляция сохранена: запрещённые импорты отсутствуют.
