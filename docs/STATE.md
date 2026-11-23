# STATE.md — состояние проекта

Файл ведёт только Coordinator. Остальные агенты его читают и не меняют.
Обновляется после каждого события. История — не более 15 последних строк.

## Сейчас
- Текущий этап: 8.2
- Название этапа: Фиксы Web SPA по итогам живого теста, разархивация, отказ от alert/confirm и эмодзи, фикс прокси и логирования
- Режим: FULL
- Статус: STAGE_PASS
- Итерация QA (кругов FAIL): 0 из 3
- Ждём от пользователя: ничего (идёт финализация документации Scribe)

## Активные агенты

| Агент | Компонент | Ветка | Карточка | Последний отчёт |
|---|---|---|---|---|
| scribe | docs | main | — | — |

## Коммиты текущего этапа
- Ветка stage-8/web-bot-fixes создана от main (6ed2194)
- b42c649 docs(repo): prepare stage 8.2 task and update state to building
- 602e697 feat(web,bot,server,edge): live test fixes, unarchive, no emojis and alert, proxy fix
- 288d001 docs(repo): update state to testing for stage 8.2
- fa0c01b docs(qa): update qa_log with stage 8.2 verification
- 849a647 chore(repo): merge stage-8/web-bot-fixes into main
- ec71209 chore(repo): bump version to 0.4.2

## Последний вердикт QA
- Вердикт: PASS (по этапу 8.2)
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

## История (последние 15 событий)
- 2026-11-23: Ветка stage-8/web-spa слита в main, поднята версия 0.4.0 (тег v0.4.0)
- 2026-11-23: Scribe создал docs/stage_reports/stage-8.md, обновил docs/USER_GUIDE.md и README.md
- 2026-11-23: Этап 8 окончательно закрыт (DONE)
- 2026-11-23: Пользователь запустил этап 8.1 (UX-полировка, объединение таймлапса/галереи, пины, фиксы) в режиме FULL
- 2026-11-23: Создана ветка stage-8/ux-polish-and-fixes от main
- 2026-11-23: Builder реализовал этап 8.1 (295 тестов passed), QA проверил этап 8.1 (PASS)
- 2026-11-23: Ветка stage-8/ux-polish-and-fixes слита в main, поднята версия 0.4.1 (тег v0.4.1)
- 2026-11-23: Scribe обновил отчёты и документацию, этап 8.1 закрыт (DONE)
- 2026-11-23: Пользователь запустил этап 8.2 (web/bot фиксы, разархивация, clean UI, прокси, логгер)
- 2026-11-23: Создана ветка stage-8/web-bot-fixes от main
- 2026-11-23: Подготовлена карточка docs/tasks/stage-8-web-bot-fixes-builder.md, статус BUILDING, задача передана Builder-у
- 2026-11-23: Builder реализовал этап 8.2 (303 теста passed), статус READY_FOR_QA
- 2026-11-23: Подготовлена карточка docs/tasks/stage-8-web-bot-fixes-qa.md, статус изменён на TESTING, задача передана QA
- 2026-11-23: QA проверил этап 8.2, вердикт PASS (303 теста, 0 дефектов)
- 2026-11-23: Ветка stage-8/web-bot-fixes слита в main, поднята версия 0.4.2 (тег v0.4.2), статус STAGE_PASS
