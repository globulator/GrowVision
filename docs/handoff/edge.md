# Отчёт о реализации: Этап 6.1 — Тестовые инструменты Edge-агента

- **Статус**: READY_FOR_QA
- **Ветка**: `stage-6/test-tools`
- **Компонент**: `edge`
- **Карточка**: `docs/tasks/stage-6-test-tools-builder.md`
- **Спецификация API**: `docs/API_CONTRACT.md` (раздел 3: Edge API)

---

## 1. Выполненные задачи и архитектурные решения

### 1.1. Тестовый интервал плановой съёмки `TEST_CAPTURE_INTERVAL_SEC`
- В `edge_agent/config.py` добавлена переменная окружения `TEST_CAPTURE_INTERVAL_SEC` (целое число секунд, валидатор строго требует `>= 10` с; при значении `< 10` выбрасывается ошибка валидации при старте).
- Если переменная задана: в `EdgeOrchestrator` плановая автосъёмка запускается циклически каждые N секунд от момента старта агента (игнорируя часовую сетку UTC и серверный интервал часов).
- Работает одинаково как в `HARDWARE_MODE=rpi`, так и в `HARDWARE_MODE=mock`.
- При старте оркестратора логируется явный `WARNING: ТЕСТОВЫЙ ИНТЕРВАЛ СЪЁМКИ: N с`.
- Факт использования тестового интервала фиксируется в `debug_log_line` каждой плановой съёмки: `[TEST_INTERVAL=N s] ...`, а также сохраняется в атрибуте `last_scheduled_debug_log_line`.
- Если переменная не задана (или пустая): строго сохраняется прежнее поведение астрономической сетки UTC (`HH % n_hours == 0 and MM == 0`).

### 1.2. Команда симуляции и посева истории кадров `python -m edge_agent.simulator seed`
- В модуле `edge_agent/simulator.py` реализована подкоманда CLI:
  ```bash
  python -m edge_agent.simulator seed --installation <id> [--frames 48] [--step-hours 1] [--start <UTC>] [--cameras all|<hardware_id,...>] [--from-dir <папка>]
  ```
- Загружает серию кадров через настоящий Edge API (`POST /api/edge/captures`, с заголовком `X-Edge-Key` и адресом `SERVER_URL` из `edge_agent/.env` или окружения) с историческими временными метками `captured_at`.
- **Без `--from-dir`**: кадры синтетические разрешением 1280x720 средствами OpenCV (нейтральный темный фон, зеленое растение с монотонно растущей площадью от номера кадра, маркер ArUco `DICT_4X4_50` id 0 с белой контрастной каймой, детерминированные смещения ракурсов по камерам).
- **С `--from-dir`**: берутся реальные JPEG-снимки из папки циклически по порядку имён файлов.
- При посеве `annotated_image` равен `raw_image`.
- `area_px` рассчитывается цветовым порогом HSV (`compute_green_area_px`).
- `aruco_area_px` рассчитывается детекцией `cv2.aruco` (`detect_aruco_area_px`).
- **Идемпотентность**: повторный запуск с фиксированным `--start` безопасен (сервер возвращает HTTP 200 на существующие дубликаты по `(installation_id, camera_id, captured_at)`).
- **Валидация оборудования**: проверяется наличие активной установки и привязанных к ней камер; если камера не привязана к установке, выводится понятная ошибка с подсказкой.

### 1.3. Синтетический генератор кадров и отказ от констант в `VisionPipeline`
- В `edge_agent/synthetic.py` реализован генератор `generate_synthetic_frame`, формирующий кадры 1280x720 с маркером ArUco и растущим растением.
- При отсутствии OpenCV возвращается запасной 1x1 JPEG с обязательным предупреждением `WARNING` в лог.
- В `VisionPipeline` константы `1200` и `5400` в mock-режиме полностью заменены на реальную детекцию ArUco (`cv2.aruco`) и цветовую сегментацию по порогу HSV.
- **Строгий запрет синтетики в режиме RPi**: в `HardwareManager.capture_frame` при `mode == "rpi"` синтетический генератор заблокирован; при отсутствии или сбое камеры гарантированно выбрасывается `RuntimeError` (проверено unit-тестом).

### 1.4. Документация в `docs/USER_GUIDE.md`
- В [docs/USER_GUIDE.md](file:///home/mcperchik/study/GrowVision/docs/USER_GUIDE.md) добавлен раздел **«7. Тестирование без ожидания»**:
  - 7.1. Запуск без реального оборудования (локальный стенд: сервер, агент в mock-режиме, CLI/бот);
  - 7.2. Ускоренный тестовый интервал съёмки (`TEST_CAPTURE_INTERVAL_SEC`) для тестирования с камерами и реле на Raspberry Pi;
  - 7.3. Посев истории кадров симулятором (`edge_agent.simulator seed`) с описанием параметров, синтетического режима и посева из каталога.

---

## 2. Результаты проверок и метрики

1. **Юнит-тесты Edge-агента (`edge_agent/tests/`)**:
   - `45 passed in 3.16s` (`edge_agent/.venv/bin/pytest edge_agent -c edge_agent/pytest.ini -v`).
   - Покрыты: генератор ArUco и монотонный рост площади, HSV-сегментация, циклическое чтение `--from-dir`, валидация `TEST_CAPTURE_INTERVAL_SEC < 10`, строгая блокировка синтетики в `rpi`, факт тестового интервала в логах и `debug_log_line`.
2. **Юнит-тесты CLI (`clients/cli/tests/`)**:
   - `42 passed in 0.45s` (`clients/cli/.venv/bin/pytest clients/cli/tests -q`).
3. **Юнит-тесты Telegram-бота (`clients/telegram_bot/tests/`)**:
   - `30 passed in 0.33s` (`clients/telegram_bot/.venv/bin/pytest clients/telegram_bot/tests -c clients/telegram_bot/pytest.ini -q`).
4. **Серверные и e2e тесты (`server/tests/`, `tests/e2e/`)**:
   - `113 passed in 41.67s` (`server/.venv/bin/pytest server/tests tests/e2e -q`).
   - Сквозные тесты симулятора `test_simulator_seed_e2e` (12 кадров на 2 камеры, `area_display` в см², сборка видео MP4, дубликаты 200) и агента `test_edge_agent_test_capture_interval_e2e` (`TEST_CAPTURE_INTERVAL_SEC=10`, `[TEST_INTERVAL=10s]`) пройдены за 2.94 с.
5. **Общий регрессионный набор**:
   - **230 тестов** успешно пройдено суммарно за < 50 с (< 60 с по разделу 9 `PROJECT.md`).
6. **Статический анализ и линтинг**:
   - `server/.venv/bin/ruff check .`: All checks passed!
   - `server/.venv/bin/ruff format --check .`: 174 files already formatted.
7. **Состояние репозитория**:
   - `growvision.db*` в корне отсутствуют, строгая изоляция компонентов подтверждена тестами.
   - Защищенные файлы спецификаций и `VERSION` сохранены без изменений (`0.3.1`).
