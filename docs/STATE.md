# STATE.md — состояние проекта

Файл ведёт только Coordinator. Остальные агенты его читают и не меняют.
Обновляется после каждого события. История — не более 15 последних строк.

## Сейчас
- Текущий этап: 8.4
- Название этапа: Помощник подключения Raspberry Pi в Web SPA для разработчика, одноразовый bootstrap-скрипт на сервере, универсальные скрипты установки сервера и обновления gv-update
- Режим: FULL
- Статус: TESTING
- Итерация QA (кругов FAIL): 0 из 3
- Ждём от пользователя: ничего (идёт тестирование QA)

## Активные агенты

| Агент | Компонент | Ветка | Карточка | Последний отчёт |
|---|---|---|---|---|
| qa | full-stack | stage-8/rpi-setup-and-deploy | docs/tasks/stage-8-rpi-setup-and-deploy-qa.md | — |

## Коммиты текущего этапа
- Ветка stage-8/rpi-setup-and-deploy создана от main (3b0dfcb)
- 0ec13b1 feat(edge,web,scripts): rpi setup assistant in web spa, edge bootstrap endpoints, deploy scripts

## Последний вердикт QA
- Вердикт: PASS (по этапу 8.3)
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

## История (последние 15 событий)
- 2026-11-23: Пользователь запустил этап 8.3 (полная пересборка Web SPA с нуля по новому WEB_SPEC.md)
- 2026-11-23: Создана ветка stage-8/web-rebuild от main
- 2026-11-23: Builder реализовал этап 8.3 (305 тестов passed), статус READY_FOR_QA
- 2026-11-23: QA проверил этап 8.3, вердикт PASS (305 тестов, 0 дефектов)
- 2026-11-23: Ветка stage-8/web-rebuild слита в main, поднята версия 0.5.0 (тег v0.5.0)
- 2026-11-23: Scribe создал docs/stage_reports/stage-8.3.md, обновил README.md и docs/stage_reports/stage-8.md
- 2026-11-23: Этап 8.3 окончательно закрыт (DONE)
- 2026-11-23: Пользователь запустил этап 8.4 (помощник подключения RPi, bootstrap-скрипт, gv-update)
- 2026-11-23: Создана ветка stage-8/rpi-setup-and-deploy от main
- 2026-11-23: Подготовлена карточка docs/tasks/stage-8-rpi-setup-and-deploy-builder.md, статус BUILDING
- 2026-11-23: Builder реализовал этап 8.4 (коммит 0ec13b1, 310 тестов passed), статус READY_FOR_QA
- 2026-11-23: Подготовлена карточка docs/tasks/stage-8-rpi-setup-and-deploy-qa.md, статус TESTING
