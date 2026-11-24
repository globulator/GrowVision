# STATE.md — состояние проекта

Файл ведёт только Coordinator. Остальные агенты его читают и не меняют.
Обновляется после каждого события. История — не более 15 последних строк.

## Сейчас
- Текущий этап: 8.8
- Название этапа: Исправление сквозной авторизации и регистрации Web ↔ Telegram
- Режим: FAST
- Статус: DONE
- Итерация QA (кругов FAIL): 0 из 3
- Ждём от пользователя: задачи следующего этапа

## Активные агенты

| Агент | Компонент | Ветка | Карточка | Последний отчёт |
|---|---|---|---|---|
| — | — | — | — | — |

## Коммиты текущего этапа
- Ветка stage-8/fix-web-tg-auth создана от main (6b77089)
- ff9b3c6 feat(stage-8.8): fix web tg auth, remove password check, seamless verification flow, bot username and delivery status
- d19499c docs(repo): prepare task card and update state for stage 8.8
- 46719a8 docs(qa): update qa_log with stage 8.8 verification
- bfc908d chore(repo): merge stage-8/fix-web-tg-auth into main
- 7d36534 chore(repo): bump version to 0.6.3 (tag v0.6.3)
- 05b7f3a docs(repo): document stage 8.8 fix web tg auth

## Последний вердикт QA
- Вердикт: PASS (по этапу 8.8)
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

## История (последние 15 событий)
- 2026-11-24: Подготовлена карточка docs/tasks/stage-8-7-ux-polish-and-realtime-builder.md, статус BUILDING
- 2026-11-24: Builder реализовал этап 8.7 (коммит 2a7df59), статус READY_FOR_QA
- 2026-11-24: Подготовлена карточка docs/tasks/stage-8-7-ux-polish-and-realtime-qa.md, статус TESTING, задача передана QA
- 2026-11-24: QA проверил этап 8.7, вердикт PASS (321 тест, 0 дефектов)
- 2026-11-24: Ветка stage-8/ux-polish-and-realtime слита в main
- 2026-11-24: Поднята версия 0.6.2 (тег v0.6.2)
- 2026-11-24: Scribe оформил docs/stage_reports/stage-8.7.md, обновил stage-8.md и README.md (коммит 0afa737)
- 2026-11-24: Этап 8.7 закрыт (DONE)
- 2026-11-24: Запущен этап 8.8 (исправление сквозной авторизации Web ↔ Telegram, режим FAST)
- 2026-11-24: Создана ветка stage-8/fix-web-tg-auth от main
- 2026-11-24: Builder реализовал этап 8.8 (коммит ff9b3c6), статус READY_FOR_QA
- 2026-11-24: Подготовлена карточка docs/tasks/stage-8-8-fix-web-tg-auth-qa.md, статус TESTING, задача передана QA
- 2026-11-24: QA проверил этап 8.8, вердикт PASS (271 тест, 0 дефектов)
- 2026-11-24: Ветка stage-8/fix-web-tg-auth слита в main, поднята версия 0.6.3 (тег v0.6.3)
- 2026-11-24: Scribe оформил docs/stage_reports/stage-8.8.md, обновил stage-8.md и README.md (коммит 05b7f3a)
- 2026-11-24: Этап 8.8 закрыт (DONE)
