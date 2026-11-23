# STATE.md — состояние проекта

Файл ведёт только Coordinator. Остальные агенты его читают и не меняют.
Обновляется после каждого события. История — не более 15 последних строк.

## Сейчас
- Текущий этап: 8.3
- Название этапа: Полная пересборка Web SPA с нуля по новому WEB_SPEC.md (Telegram-авторизация, плеер с клиентским GIF-экспортом, бейдж роли, только SVG)
- Режим: FULL
- Статус: TESTING
- Итерация QA (кругов FAIL): 0 из 3
- Ждём от пользователя: ничего (идёт тестирование QA)

## Активные агенты

| Агент | Компонент | Ветка | Карточка | Последний отчёт |
|---|---|---|---|---|
| qa | full-stack | stage-8/web-rebuild | docs/tasks/stage-8-web-rebuild-qa.md | — |

## Коммиты текущего этапа
- Ветка stage-8/web-rebuild создана от main (cb13144)
- a40fdc7 docs(repo): prepare stage 8.3 task and update state to building
- 8742557 feat(web,server,bot): rebuild web spa from scratch, telegram auth, clean player, gif export

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
- Этап 8.2: Фиксы Web SPA, разархивация, clean UI, прокси и логирование (принят QA, слит в main, версия 0.4.2, отчёт docs/stage_reports/stage-8.2.md)

## История (последние 15 событий)
- 2026-11-23: Builder реализовал этап 8.1 (295 тестов passed), QA проверил этап 8.1 (PASS)
- 2026-11-23: Ветка stage-8/ux-polish-and-fixes слита в main, поднята версия 0.4.1 (тег v0.4.1)
- 2026-11-23: Scribe обновил отчёты и документацию, этап 8.1 закрыт (DONE)
- 2026-11-23: Пользователь запустил этап 8.2 (web/bot фиксы, разархивация, clean UI, прокси, логгер)
- 2026-11-23: Создана ветка stage-8/web-bot-fixes от main
- 2026-11-23: Подготовлена карточка docs/tasks/stage-8-web-bot-fixes-builder.md, статус BUILDING, задача передана Builder-у
- 2026-11-23: Builder реализовал этап 8.2 (303 теста passed), статус READY_FOR_QA
- 2026-11-23: Подготовлена карточка docs/tasks/stage-8-web-bot-fixes-qa.md, статус изменён на TESTING, задача передана QA
- 2026-11-23: QA проверил этап 8.2, вердикт PASS (303 теста, 0 дефектов)
- 2026-11-23: Ветка stage-8/web-bot-fixes слита в main, поднята версия 0.4.2 (тег v0.4.2)
- 2026-11-23: Scribe создал docs/stage_reports/stage-8.2.md, обновил docs/stage_reports/stage-8.md, README.md
- 2026-11-23: Этап 8.2 окончательно закрыт (DONE)
- 2026-11-23: Пользователь запустил этап 8.3 (полная пересборка Web SPA с нуля по новому WEB_SPEC.md)
- 2026-11-23: Создана ветка stage-8/web-rebuild от main
- 2026-11-23: Builder реализовал этап 8.3 (305 тестов passed), статус READY_FOR_QA
