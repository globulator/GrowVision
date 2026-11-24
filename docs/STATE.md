# STATE.md — состояние проекта

Файл ведёт только Coordinator. Остальные агенты его читают и не меняют.
Обновляется после каждого события. История — не более 15 последних строк.

## Сейчас
- Текущий этап: 9.1
- Название этапа: Исправление кириллицы на CV-разметке, фильтрация шума/серого фона в HSV-сегментации и 50 Гц для V4L2-камер
- Режим: FAST
- Статус: DONE
- Итерация QA (кругов FAIL): 0 из 3
- Ждём от пользователя: задачи следующего этапа

## Активные агенты

| Агент | Компонент | Ветка | Карточка | Последний отчёт |
|---|---|---|---|---|
| — | — | — | — | — |

## Коммиты текущего этапа
- Ветка stage-9/fix-vision-cyrillic-and-50hz создана от main (2adc3e0)
- 59b6547 feat(edge): fix vision cyrillic annotations, improve hsv segmentation, and set 50hz frequency
- 0410996 docs(repo): prepare QA task card and update state for stage 9.1
- 0a53a60 docs(qa): update qa_log with stage 9.1 verification
- 1b71f6b chore(repo): merge stage-9/fix-vision-cyrillic-and-50hz into main
- e5e1bad chore(repo): bump version to 0.7.0 (tag v0.7.0)
- 9bdb38b docs(repo): document stage 9.1 fix vision cyrillic and 50hz

## Последний вердикт QA
- Вердикт: PASS (по этапу 9.1)
- Открытые дефекты (ID): —
- Отложенные (DEFERRED, ID): —

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

## История (последние 15 событий)
- 2026-11-24: QA проверил этап 8.10, вердикт PASS (285 тестов, 0 дефектов)
- 2026-11-24: Ветка stage-8/rpi-camera-vision-and-ui слита в main
- 2026-11-24: Поднята версия 0.6.5 (тег v0.6.5)
- 2026-11-24: Scribe оформил docs/stage_reports/stage-8.10.md, обновил stage-8.md и README.md (коммит 6ba33d5)
- 2026-11-24: Этап 8.10 закрыт (DONE)
- 2026-11-24: Запущен этап 9.1 (кириллица в CV через Pillow, HSV фильтрация шума/фона, 50 Гц для V4L2-камер, режим FAST)
- 2026-11-24: Создана ветка stage-9/fix-vision-cyrillic-and-50hz от main
- 2026-11-24: Подготовлена карточка docs/tasks/stage-9-1-fix-vision-cyrillic-and-50hz-builder.md, статус BUILDING
- 2026-11-24: Builder реализовал этап 9.1 (коммит 59b6547), статус READY_FOR_QA
- 2026-11-24: Подготовлена карточка docs/tasks/stage-9-1-fix-vision-cyrillic-and-50hz-qa.md, статус TESTING, задача передана QA
- 2026-11-24: QA проверил этап 9.1, вердикт PASS (293 теста, 0 дефектов)
- 2026-11-24: Ветка stage-9/fix-vision-cyrillic-and-50hz слита в main
- 2026-11-24: Поднята версия 0.7.0 (тег v0.7.0)
- 2026-11-24: Scribe оформил docs/stage_reports/stage-9.1.md и обновил README.md (коммит 9bdb38b)
- 2026-11-24: Этап 9.1 закрыт (DONE)
