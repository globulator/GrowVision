# STATE.md — состояние проекта

Файл ведёт только Coordinator. Остальные агенты его читают и не меняют.
Обновляется после каждого события. История — не более 15 последних строк.

## Сейчас
- Текущий этап: 7.4
- Название этапа: Общие GPIO-пины с программной синхронизацией и переименование пользователей
- Режим: FULL
- Статус: STAGE_PASS
- Итерация QA (кругов FAIL): 0 из 3
- Ждём от пользователя: ничего (идёт финализация документации Scribe)

## Активные агенты

| Агент | Компонент | Ветка | Карточка | Последний отчёт |
|---|---|---|---|---|
| scribe | docs | main | — | — |

## Коммиты текущего этапа
- Ветка stage-7/shared-pins-and-rename создана от main (7ed7501)
- f69b2fe docs(repo): prepare stage 7.4 task and update state to building
- edce6b3 feat(repo): implement stage 7.4 shared gpio pins, relay sync, and user rename
- 343065c docs(repo): update state to testing and include v2 roadmap
- 3736475 docs(qa): update qa_log with stage 7.4 verification
- a4a9cf4 chore(repo): merge stage-7/shared-pins-and-rename into main
- 5410dd4 chore(repo): bump version to 0.3.3

## Последний вердикт QA
- Вердикт: PASS (по этапу 7.4)
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

## История (последние 15 событий)
- 2026-11-22: Подготовлена карточка docs/tasks/stage-7-auth-and-polish-builder.md, статус BUILDING, задача передана Builder-у
- 2026-11-22: Builder реализовал этап 7.3 (279 тестов passed), статус READY_FOR_QA
- 2026-11-22: Подготовлена карточка docs/tasks/stage-7-auth-and-polish-qa.md, статус изменён на TESTING, задача передана QA
- 2026-11-22: QA проверил этап 7.3, вердикт PASS (279 тестов, 0 дефектов)
- 2026-11-22: Ветка stage-7/auth-and-polish слита в main (версия 0.3.1 сохранена без изменений)
- 2026-11-22: Статус этапа 7.3 изменён на STAGE_PASS, задача передана Scribe
- 2026-11-22: Scribe создал docs/stage_reports/stage-7.3.md, обновил docs/stage_reports/stage-7.md и README.md
- 2026-11-22: Этап 7.3 окончательно закрыт (DONE)
- 2026-11-22: Пользователь запустил этап 7.4 (общие GPIO-пины и переименование) в режиме FULL
- 2026-11-22: Создана ветка stage-7/shared-pins-and-rename от main
- 2026-11-22: Подготовлена карточка docs/tasks/stage-7-shared-pins-and-rename-builder.md, статус BUILDING, задача передана Builder-у
- 2026-11-23: Builder реализовал этап 7.4 (290 тестов passed), статус READY_FOR_QA
- 2026-11-23: Статус изменён на TESTING, задача передана QA
- 2026-11-23: QA проверил этап 7.4, вердикт PASS (290 тестов, 0 дефектов)
- 2026-11-23: Ветка stage-7/shared-pins-and-rename слита в main, поднята версия 0.3.3 (тег v0.3.3), статус STAGE_PASS
