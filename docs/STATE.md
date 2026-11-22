# STATE.md — состояние проекта

Файл ведёт только Coordinator. Остальные агенты его читают и не меняют.
Обновляется после каждого события. История — не более 15 последних строк.

## Сейчас
- Текущий этап: 6.1 (тестовые инструменты агента)
- Название этапа: Тестовые инструменты агента (TEST_CAPTURE_INTERVAL_SEC, simulator seed, mock синтетика)
- Режим: FULL
- Статус: TESTING
- Итерация QA (кругов FAIL): 0 из 3
- Ждём от пользователя: ничего (идёт тестирование QA)

## Активные агенты

| Агент | Компонент | Ветка | Карточка | Последний отчёт |
|---|---|---|---|---|
| QA | edge | stage-6/test-tools | docs/tasks/stage-6-test-tools-qa.md | — |

## Коммиты текущего этапа
- Ветка stage-6/test-tools создана от main (3377084)
- 9ea7a73 docs(repo): prepare stage 6.1 task and update state to building
- e901753 feat(edge): add test capture interval, synthetic generator and simulator seed
- a551d99 test(edge): add unit and e2e tests for test tools and interval
- a794dc5 docs(edge): add testing without waiting section and handoff report

## Последний вердикт QA
- Вердикт: PASS (по этапу 7)
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
- Этап 7: Telegram-бот (принят QA, слит в main, версия 0.3.1, отчёт docs/stage_reports/stage-7.md)

## История (последние 15 событий)
- 2026-11-21: Ветка stage-7/bot слита в main
- 2026-11-21: Централизованно поднята PATCH-версия до 0.3.1 (коммит cf5b49d)
- 2026-11-21: Статус этапа 7 изменён на STAGE_PASS, задача передана Scribe
- 2026-11-21: Scribe обновил docs/stage_reports/stage-7.md и README.md (DOCS_UPDATED, коммит fe96be8)
- 2026-11-21: Этап 7 окончательно закрыт (DONE)
- 2026-11-22: Пользователь дал команду запустить этап 6.1 (тестовые инструменты агента) в режиме FULL
- 2026-11-22: Создана ветка stage-6/test-tools от main
- 2026-11-22: Подготовлена карточка docs/tasks/stage-6-test-tools-builder.md
- 2026-11-22: Этап 6.1 переведён в статус BUILDING, задача передана Builder-у
- 2026-11-22: Builder реализовал TEST_CAPTURE_INTERVAL_SEC, simulator seed, mock-генератор и тесты, статус READY_FOR_QA
- 2026-11-22: Подготовлена карточка docs/tasks/stage-6-test-tools-qa.md, статус изменён на TESTING, задача передана QA
