# STATE.md — состояние проекта

Файл ведёт только Coordinator. Остальные агенты его читают и не меняют.
Обновляется после каждого события. История — не более 15 последних строк.

## Сейчас
- Текущий этап: 9.2
- Название этапа: Live-стрим (ustreamer), реальные V4L2-контролы, фиксы по итогам теста на реальной RPi 5 (Logitech C270) и новый Live UI
- Режим: FAST (coordinator2 + builder2)
- Статус: DONE
- Итераций ревью: 1 из 3
- Ждём от пользователя: задачи следующего этапа

## Активные агенты

| Агент | Компонент | Ветка | Карточка | Последний отчёт |
|---|---|---|---|---|
| — | — | — | — | — |

## Коммиты текущего этапа
- d206095 feat(live): implement ustreamer live stream, camera controls, low-latency proxy, and aligned schedule
- 3f363bc fix(live): fix RPi ustreamer proxy, real V4L2 controls, camera card and live UI
- f0a5654 fix(live): guard proxied fps from push doubling, close stream on pause, normalize START_LIVE url
- 054d66f docs(repo): add coordinator2 and builder2 role guides
- Слияние stage-9/live-stream-camera-settings-and-schedule в main, версия 0.7.1 (тег v0.7.1)

## Последний вердикт ревью
- Вердикт: PASS (по этапу 9.2, итераций ревью: 1/3)
- Открытые дефекты: —

## Закрытые этапы
- Этап 0: подготовка проекта (PROJECT.md согласован, план принят)
- Этап 1: каркас репозитория и модели БД + доработки LIGHT (принят QA, слит в main, отчёт docs/stage_reports/stage-1.md)
- Этап 2: Edge API сервера + доработки LIGHT (принят QA, слит в main, версия 0.1.0, отчёт docs/stage_reports/stage-2.md)
- Этап 3: Client API, часть 1 + доработки LIGHT (принят QA, слит в main, отчёт docs/stage_reports/stage-3.md)
- Этап 4: Client API, часть 2 + доработки fixes (принят QA, слит в main, отчёт docs/stage_reports/stage-4.md)
- Этап 5: CLI-клиент + доработки fixes (принят QA, слит в main, версия 0.2.0, отчёт docs/stage_reports/stage-5.md)
- Этап 6: Edge-агент для Raspberry Pi 5 + доработки fixes + fixes-2 (принят QA, слит в main, версия 0.3.0, отчёт docs/stage_reports/stage-6.md)
- Этап 6.1: Тестовые инструменты агента (принят QA, слит в main, версия 0.3.1, отчёт docs/stage_reports/stage-6.1.md)
- Этап 7: Telegram-бот + fixes + fixes-2 + fixes-3 + fixes-4 (принят QA, слит в main, версия 0.3.1, отчёт docs/stage_reports/stage-7.md, docs/stage_reports/stage-7.2.md)
- Этап 7.2: Комплексные исправления раунда 4 (принят QA, слит в main, версия 0.3.1, отчёт docs/stage_reports/stage-7.2.md)
- Этап 7.3: Авторизация по коду, управление сессиями, CV-разметка, UX-полировка и фиксы раунда 5 (принят QA, слит в main, версия 0.3.1, отчёт docs/stage_reports/stage-7.3.md)
- Этап 7.4: Общие GPIO-пины с программной синхронизацией и переименование пользователей (принят QA, слит в main, версия 0.3.3, отчёт docs/stage_reports/stage-7.4.md)
- Этап 8: Полноценный Web SPA-клиент (принят QA, слит в main, версия 0.4.0, отчёт docs/stage_reports/stage-8.md)
- Этап 8.1: Компактный UX, объединение галереи/таймлапса, совпадающие пины, свет ON (принят QA, слит в main, версия 0.4.1, отчёт docs/stage_reports/stage-8.1.md)
- Этап 8.2: Фиксы Web SPA, разархивация, clean UI, прокси и логирование (принят QA, слит в main, версия 0.4.2, отчёт docs/stage_reports/stage-8.2.md)
- Этап 8.3: Полная пересборка Web SPA с нуля по новому WEB_SPEC.md (принят QA, слит в main, версия 0.5.0, отчёт docs/stage_reports/stage-8.3.md)
- Этап 8.4: Помощник подключения Raspberry Pi в Web SPA, bootstrap-скрипт, gv-update и install_server (принят QA, слит в main, версия 0.5.1, отчёт docs/stage_reports/stage-8.4.md)
- Этап 8.4.1: Хотфикс багов живого теста (ES-импорт в Web SPA, коллбэк таймлапса в боте, логи httpx, favicon) (принят QA, слит в main, версия 0.5.2, отчёт docs/stage_reports/stage-8.4.1.md)
- Этап 8.5: UX-полировка, единый вход без пароля, 30 FPS в боте, SVG-заглушки и изоляция mock-БД (принят QA, слит в main, версия 0.6.0, отчёт docs/stage_reports/stage-8.5.md)
- Этап 8.6: Интеграция квадратного 1:1 SVG-логотипа GrowVision в шапку Web SPA и favicon (принят QA, слит в main, версия 0.6.1, отчёт docs/stage_reports/stage-8.6.md)
- Этап 8.7: Исправление багов живого теста v0.6.1 (Telegram Web Auth, галерея, мягкая обработка ошибок, RPi mock auto, баннер, футер, поллинг) (принят QA, слит в main, версия 0.6.2, отчёт docs/stage_reports/stage-8.7.md)
- Этап 8.8: Исправление сквозной авторизации и регистрации Web ↔ Telegram (принят QA, слит в main, версия 0.6.3, отчёт docs/stage_reports/stage-8.8.md)
- Этап 8.9: Исправление отображения кадров в Web-галерее, расчёт FPS в боте и ускорение SNAP в Edge-агенте (принят QA, слит в main, версия 0.6.4, отчёт docs/stage_reports/stage-8.9.md)
- Этап 8.10: Стабилизация съёмки RPi (автоподбор качества, умный прогрев, HSV-сегментация без ONNX, автокалибровка камеры в боте) и инфо-панель RPi в Web (принят QA, слит в main, версия 0.6.5, отчёт docs/stage_reports/stage-8.10.md)
- Этап 9.1: Исправление кириллицы на CV-разметке, фильтрация шума/серого фона в HSV-сегментации и 50 Гц для V4L2-камер (принят QA, слит в main, версия 0.7.0, отчёт docs/stage_reports/stage-9.1.md)
- Этап 9.2: Live-стрим, реальные V4L2-контролы, фиксы по тесту на реальной RPi 5 (Logitech C270) и новый Live UI (принят после ревью, слит в main, версия 0.7.1)

## История (последние 10 событий)
- 2026-11-24: Поднята версия 0.7.0 (тег v0.7.0), этап 9.1 закрыт (DONE)
- 2026-11-24: В ветке stage-9/live-stream-camera-settings-and-schedule реализована базовая часть этапа 9.2 (d206095)
- 2026-11-25: Запущены исправления этапа 9.2 по итогам ручного теста на RPi 5 (Logitech C270) в режиме coordinator2 + builder2
- 2026-11-25: Builder v2 реализовал правки по карточке камеры, ustreamer, V4L2-контролам, FPS/задержке, заглушкам и новой панели настроек (3f363bc)
- 2026-11-25: По итогам ревью (1/3) исправлены двойной подсчёт FPS, буфер паузы прокси и нормализация START_LIVE URL (f0a5654)
- 2026-11-25: Пройдены проверки pytest (edge_agent 78, server 86, bot 72, cli 51, целевые e2e 15) и ruff
- 2026-11-25: Ветка stage-9/live-stream-camera-settings-and-schedule слита в main
- 2026-11-25: Поднята версия 0.7.1, поставлен тег v0.7.1, этап 9.2 закрыт (DONE)
