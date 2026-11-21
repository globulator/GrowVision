# STATE.md — состояние проекта

Файл ведёт только Coordinator. Остальные агенты его читают и не меняют.
Обновляется после каждого события. История — не более 15 последних строк.

## Сейчас
- Текущий этап: 7 (Telegram-бот)
- Название этапа: Telegram-бот
- Режим: FULL
- Статус: BUILDING
- Итерация QA (кругов FAIL): 1 из 3
- Ждём от пользователя: ничего (идёт исправление дефектов Builder-ом)

## Активные агенты

| Агент | Компонент | Ветка | Карточка | Последний отчёт |
|---|---|---|---|---|
| Builder | bot | stage-7/bot | docs/tasks/stage-7-bot-fixes.md | docs/handoff/bot.md |

## Коммиты текущего этапа
- Ветка stage-7/bot создана от main (669b9f3)
- 869fbc4 feat(bot): implement telegram bot client for GrowVision
- 95f5575 test(bot): add unit tests for telegram bot handlers, isolation and video
- a155d98 docs(bot): update user guide, stage task and handoff report
- bad8e5b docs(repo): prepare stage 7 qa task and update state to testing
- cc689ae test(bot): add e2e tests and log open defects for telegram bot

## Последний вердикт QA
- Вердикт: FAIL (по этапу 7)
- Открытые дефекты (ID): BUG-001, BUG-002
- Отложенные (DEFERRED, ID): —

## Закрытые этапы
- Этап 0: подготовка проекта (PROJECT.md согласован, план принят)
- Этап 1: каркас репозитория и модели БД + доработки LIGHT (принят QA, слит в main, отчёт docs/stage_reports/stage-1.md)
- Этап 2: Edge API сервера + доработки LIGHT (принят QA, слит в main, версия 0.1.0, отчёт docs/stage_reports/stage-2.md)
- Этап 3: Client API, часть 1 + доработки LIGHT (принят QA, слит в main, отчёт docs/stage_reports/stage-3.md)
- Этап 4: Client API, часть 2 + доработки fixes (принят QA, слит в main, отчёт docs/stage_reports/stage-4.md)
- Этап 5: CLI-клиент + доработки fixes (принят QA, слит в main, версия 0.2.0, отчёт docs/stage_reports/stage-5.md)
- Этап 6: Edge-агент для Raspberry Pi 5 + доработки fixes + fixes-2 (принят QA, слит в main, версия 0.3.0, отчёт docs/stage_reports/stage-6.md)

## История (последние 15 событий)
- 2026-11-21: Подготовлена карточка docs/tasks/stage-6-fixes-2-qa.md, статус изменён на TESTING, задача передана QA
- 2026-11-21: QA проверил доработки fixes-2, вердикт PASS (184 теста, 0 дефектов)
- 2026-11-21: Ветка stage-6/fixes-2 слита в main (версия 0.2.1 сохранена, теги не ставились)
- 2026-11-21: Статус этапа 6 изменён на STAGE_PASS, задача передана Scribe
- 2026-11-21: Scribe обновил docs/stage_reports/stage-6.md и docs/user_tasks.md (T-001) в соответствии с DEPLOYMENT_RPI.md (DOCS_UPDATED)
- 2026-11-21: Этап 6 окончательно закрыт (DONE)
- 2026-11-21: Пользователь принял этап 6 по коду, выставил версию 0.3.0 (тег v0.3.0), отложил задачу T-001 до релиза v1.0.0
- 2026-11-21: Scribe перенёс T-001 в раздел «Отложенные до релиза» в docs/user_tasks.md (DOCS_UPDATED)
- 2026-11-21: Пользователь дал команду запустить этап 7 (Telegram-бот) в режиме FULL
- 2026-11-21: Создана ветка stage-7/bot, подготовлена карточка docs/tasks/stage-7-bot.md
- 2026-11-21: Этап 7 переведён в статус BUILDING, задача передана Builder-у
- 2026-11-21: Builder реализовал Telegram-бота (30 unit-тестов, полная изоляция), статус READY_FOR_QA
- 2026-11-21: Подготовлена карточка docs/tasks/stage-7-qa.md, статус изменён на TESTING, задача передана QA
- 2026-11-21: QA выполнил E2E тесты (tests/e2e/test_bot_e2e.py), вердикт FAIL (BUG-001, BUG-002)
- 2026-11-21: Подготовлена карточка docs/tasks/stage-7-bot-fixes.md, статус изменён на BUILDING (итерация 1/3), задача возвращена Builder-у
