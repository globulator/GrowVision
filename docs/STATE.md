# STATE.md — состояние проекта

Файл ведёт только Coordinator. Остальные агенты его читают и не меняют.
Обновляется после каждого события. История — не более 15 последних строк.

## Сейчас
- Текущий этап: 8.1
- Название этапа: Компактный UX и визуал Web SPA, объединение галереи и таймлапса в Web и боте, совпадение пинов, свет по умолчанию ON, фикс прокси
- Режим: FULL
- Статус: STAGE_PASS
- Итерация QA (кругов FAIL): 0 из 3
- Ждём от пользователя: ничего (идёт финализация документации Scribe)

## Активные агенты

| Агент | Компонент | Ветка | Карточка | Последний отчёт |
|---|---|---|---|---|
| scribe | docs | main | — | — |

## Коммиты текущего этапа
- Ветка stage-8/ux-polish-and-fixes создана от main (8a08d66)
- a825562 docs(repo): prepare stage 8.1 task and update state to building
- 3ae8ee5 feat(web,bot,edge): compact ux, gallery timelapse merge, shared pins and default light on (stage 8.1)
- e20f250 docs(repo): update state to testing for stage 8.1
- 42efa60 docs(qa): update qa_log with stage 8.1 verification
- 55a4e5d chore(repo): merge stage-8/ux-polish-and-fixes into main
- a71547d chore(repo): bump version to 0.4.1

## Последний вердикт QA
- Вердикт: PASS (по этапу 8.1)
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

## История (последние 15 событий)
- 2026-11-23: Builder реализовал этап 8 (294 теста passed), статус READY_FOR_QA
- 2026-11-23: Подготовлена карточка docs/tasks/stage-8-web-spa-qa.md, статус изменён на TESTING, задача передана QA
- 2026-11-23: QA проверил этап 8, вердикт PASS (294 теста, 0 дефектов)
- 2026-11-23: Ветка stage-8/web-spa слита в main, поднята версия 0.4.0 (тег v0.4.0)
- 2026-11-23: Scribe создал docs/stage_reports/stage-8.md, обновил docs/USER_GUIDE.md и README.md
- 2026-11-23: Этап 8 окончательно закрыт (DONE)
- 2026-11-23: Пользователь запустил этап 8.1 (UX-полировка, объединение таймлапса/галереи, пины, фиксы) в режиме FULL
- 2026-11-23: Создана ветка stage-8/ux-polish-and-fixes от main
- 2026-11-23: Подготовлена карточка docs/tasks/stage-8-ux-polish-and-fixes-builder.md, статус BUILDING, задача передана Builder-у
- 2026-11-23: Builder реализовал этап 8.1 (295 тестов passed), статус READY_FOR_QA
- 2026-11-23: Подготовлена карточка docs/tasks/stage-8-ux-polish-and-fixes-qa.md, статус изменён на TESTING, задача передана QA
- 2026-11-23: QA проверил этап 8.1, вердикт PASS (295 тестов, 0 дефектов)
- 2026-11-23: Ветка stage-8/ux-polish-and-fixes слита в main, поднята версия 0.4.1 (тег v0.4.1), статус STAGE_PASS
