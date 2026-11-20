# Задача: Этап 6 — Edge-агент для Raspberry Pi 5

- **Этап**: 6 (Edge-агент для Raspberry Pi 5)
- **Компонент**: `edge`
- **Режим**: FULL
- **Ветка**: `stage-6/edge-agent`
- **Версия**: не поднимать (0.3.0 выставит пользователь после приёмки этапа; PATCH поднимается отдельным коммитом при слиянии в main)
- **Allowed Paths**:
  - `edge_agent/`
  - `docs/DEPLOYMENT_RPI.md`
  - `docs/HARDWARE_WIRING.md`
  - `docs/handoff/edge.md`

## Прочитать перед началом
1. `docs/PROJECT.md`, включая **раздел 9 («Правила скорости и тестов»)** — обязателен для Builder и QA!
   - Быстрые тесты (<3 с на тест, <60 с весь набор).
   - Подмена длительных пауз и таймаутов через monkeypatch.
   - Запуск через `timeout 300 ... pytest`.
   - При 2 неудачах подряд — остановка и статус `BLOCKED`.
2. `docs/REPO_AND_VERSIONING.md`, раздел 6 — версионирование и формат коммитов (`feat(edge): ...`, `test(edge): ...`, `docs(edge): ...`). В обычных коммитах файл `VERSION` не трогать!
3. `docs/API_CONTRACT.md`, раздел 3 («Edge API: схемы») и раздел 1.4 («Жизненный цикл команд»).
4. `shared/schemas.py` и `shared/version.py`.

## Функциональность и критерии приёмки
1. **Архитектура и изоляция**:
   - Реализовать агент в `edge_agent/` с собственным виртуальным окружением `.venv` и зависимостями в `requirements.txt`.
   - Запуск: `python -m edge_agent.main`.
   - Строгая изоляция: взаимодействие с сервером исключительно через HTTP REST API по `API_CONTRACT.md` с заголовком `X-Edge-Key`.
   - **Запрет импортов**: `grep -rn --include="*.py" "from server\|from clients\|import server\|import clients" edge_agent` должен возвращать пустой вывод.
   - Разрешено использовать `shared/` (`shared.schemas`, `shared.version`, `shared.logger`).
   - Секреты и временные файлы изолированы (`.env`, `offline_queue/`).

2. **Работа с оборудованием (GPIO и камеры)**:
   - Поддержка двух режимов через переменную окружения `HARDWARE_MODE`:
     - `HARDWARE_MODE=rpi` (по умолчанию на RPi): управление пинами через `gpiozero` / `lgpio`, захват с камер через `opencv`.
     - `HARDWARE_MODE=mock`: безопасный запуск на ПК и в тестах. Симуляция пинов реле и вспышки, генерация тестовых кадров (или использование тестовых изображений) без реального железа.
   - Единый аппаратный мьютекс: плановая съёмка имеет наивысший приоритет. При занятом оборудовании ручные команды завершаются ошибкой `"Камера недоступна, попробуйте позже"`.

3. **Связь с сервером по Edge API (`API_CONTRACT.md`)**:
   - **Heartbeat (`POST /api/edge/heartbeat`)**:
     - Периодичность 30 с.
     - Передача полной структуры: `software` (`edge_version`, `python_version`, `model_name`), `hardware` (`board_model`, `os_info`, `uptime_sec`, `cpu_temp_c`, `cpu_usage_percent`, `ram_used_mb`, `ram_total_mb`, `disk_free_gb`, `gpio_mode`), `offline_queue_count`, `detected_cameras`.
     - Применение полученной конфигурации: `capture_interval_hours`, `debug_mode`, `active_installations`.
   - **Опрос и выполнение команд (`POST /api/edge/commands/claim` и `.../result`)**:
     - Периодичность опроса 2–3 с.
     - Команды:
       - `LIGHT_MAIN`: включение/выключение основного света на указанной установке или на всех (`target = all`).
       - `LIGHT_FLASH`: включение/выключение вспышки.
       - `RELAY_TEST`: кратковременное (3 с) включение света и вспышки для проверки реле.
       - `SCAN_CAMERAS`: сканирование подключенных USB/CSI-камер и возврат списка `scanned_cameras` в отчёте.
       - `SNAP`: захват кадра с камеры, детекция ArUco, сегментация ONNX, загрузка кадра на сервер (`POST /api/edge/captures`) с `command_id`, затем отправка отчёта `POST .../result`.
   - **Загрузка кадров (`POST /api/edge/captures`)**:
     - `multipart/form-data`: `metadata` (JSON CaptureMeta), `raw_image` (.jpg), `annotated_image` (.jpg).

4. **Компьютерное зрение и нейросеть**:
   - Детекция ArUco-маркера и расчёт его площади `aruco_area_px`.
   - Сегментация листьев через ONNX-модель: вычисление маски, расчёт площади `area_px` контура растения, отрисовка маски на `annotated_image`.
   - В mock-режиме (и при отсутствии файла модели): детерминированная симуляция маски и замеров площади.

5. **Автосъёмка и офлайн-очередь**:
   - Фоновый планировщик плановой съёмки по интервалу `capture_interval_hours`.
   - Буферизация в `edge_agent/offline_queue/`: при сетевых сбоях или недоступности сервера кадры сохраняются локально. При восстановлении связи выгружаются в `/api/edge/captures`.
   - Обработка ответов сервера:
     - 200/201: кадр успешно удаляется из очереди.
     - 409 (камера не зарегистрирована): остаётся в очереди для повторной попытки.
     - 422/404: перемещается в `edge_agent/offline_queue/failed/`, чтобы не блокировать очередь.

6. **Документация**:
   - Подготовить черновики `docs/DEPLOYMENT_RPI.md` (инструкция развёртывания на Raspberry Pi 5, настройка venv, systemd unit) и `docs/HARDWARE_WIRING.md` (схема подключения реле, питания и камер к GPIO RPi 5).
   - Заполнить `docs/handoff/edge.md`.

7. **Тесты и качество**:
   - Набор unit-тестов в `edge_agent/tests/`: мок железа, мок HTTP, проверка жизненного цикла команд, офлайн-очереди, сборки heartbeat.
   - `ruff check .` и `ruff format --check .` проходят без замечаний.
   - По завершении сообщить `READY_FOR_QA`.
