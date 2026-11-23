# STATE.md — состояние проекта

Файл ведёт только Coordinator. Остальные агенты его читают и не меняют.
Обновляется после каждого события. История — не более 15 последних строк.

## Сейчас
- Текущий этап: 8.5
- Название этапа: UX-полировка, единый вход без пароля, 30 FPS в боте, SVG-заглушки и изоляция mock-БД
- Режим: FULL
- Статус: DONE
- Итерация QA (кругов FAIL): 0 из 3
- Ждём от пользователя: задачи следующего этапа

## Активные агенты

| Агент | Компонент | Ветка | Карточка | Последний отчёт |
|---|---|---|---|---|
| — | — | — | — | — |

## Коммиты текущего этапа
- Ветка stage-8/ux-and-mock-isolation создана от main (b61b3e2)
- e24c69a feat(stage-8.5): unified web login, bot 30fps and cancel fsm, web gallery and snap fixes, mock isolation
- 079333b docs(repo): update state to testing for stage 8.5
- d8a98b9 docs(qa): update qa_log with stage 8.5 verification
- eea5b41 chore(repo): merge stage-8/ux-and-mock-isolation into main
- ba5efba chore(repo): bump version to 0.6.0 (tag v0.6.0)
- d8df006 docs(repo): document stage 8.5 ux and mock isolation

## Последний вердикт QA
- Вердикт: PASS (по этапу 8.5)
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

## История (последние 15 событий)
- 2026-11-23: QA проверил этап 8.4.1 в режиме VERIFY, вердикт PASS (BUG-003 закрыт, 312 тестов passed)
- 2026-11-23: Ветка fix/stage-8-web-and-bot слита в main, поднята версия 0.5.2 (тег v0.5.2)
- 2026-11-23: Scribe создал docs/stage_reports/stage-8.4.1.md, обновил README.md и docs/stage_reports/stage-8.md
- 2026-11-23: Этап 8.4.1 окончательно закрыт (DONE)
- 2026-11-23: Пользователь запустил этап 8.5 (UX-полировка, единый вход без пароля, 30 FPS, SVG-заглушки, mock-БД)
- 2026-11-23: Создана ветка stage-8/ux-and-mock-isolation от main
- 2026-11-23: Builder реализовал этап 8.5 (коммит e24c69a, 321 тест passed), статус READY_FOR_QA
- 2026-11-23: Подготовлена карточка docs/tasks/stage-8-5-ux-and-mock-isolation-qa.md, статус TESTING, задача передана QA
- 2026-11-23: QA проверил этап 8.5, вердикт PASS (319 тестов, 0 дефектов)
- 2026-11-23: Ветка stage-8/ux-and-mock-isolation слита в main, поднята версия 0.6.0 (тег v0.6.0)
- 2026-11-23: Scribe создал docs/stage_reports/stage-8.5.md, обновил README.md и docs/stage_reports/stage-8.md
- 2026-11-23: Этап 8.5 окончательно закрыт (DONE)
- 2026-11-23: QA проверил этап 8.5, вердикт PASS (319 тестов, 0 дефектов)
- 2026-11-23: Ветка stage-8/ux-and-mock-isolation слита в main, поднята версия 0.6.0 (тег v0.6.0)
- 2026-11-23: Scribe назначен для подготовки отчёта docs/stage_reports/stage-8.5.md и обновления документации
