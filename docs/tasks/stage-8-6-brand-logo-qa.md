# Задача: QA-приёмка / Этап 8.6 (Интеграция квадратного 1:1 SVG-логотипа GrowVision в Web SPA и favicon)
Режим: FAST
Ветка: stage-8/brand-logo
Цель: Проверить корректность интеграции обновлённого пользователем квадратного 1:1 SVG-логотипа в шапку Web SPA и favicon без перезаписи ассетов.

Прочитать перед началом:
- `docs/PROJECT.md` (раздел 9: правила скорости и тестов)
- `docs/WEB_SPEC.md`
- `docs/team_roles/qa_tester.md`
- `docs/handoff/stage-8-6-brand-logo.md` (после готовности Builder)

Критерии проверки:
1. Сохранность пользовательских ассетов:
   - Файлы `clients/web/assets/logo-*.svg` не были изменены или повреждены (сохранены пользовательские изменения из коммита `edd8560`).
   - Валидный XML/SVG во всех трёх файлах.
2. Web SPA:
   - В шапке `clients/web/index.html` перед текстом GrowVision отображается фирменный SVG-логотип с квадратными пропорциями 1:1 (`aspect-ratio: 1 / 1`, одинаковые `width` и `height`).
   - `<link rel="icon" type="image/svg+xml" ...>` содержит Data URI, сгенерированный напрямую из `logo-green.svg`.
   - Инварианты сохранены: 0 эмодзи, 0 alert/confirm/prompt, 0 внешних ссылок и CDN.
   - Корректное отображение на мобильных экранах (от 360 px).
3. Тесты и линтер:
   - `tests/e2e/test_stage8_web_e2e.py` проходит успешно.
   - `ruff check .` и `ruff format --check .` чисты.
   - Чистый `git status`.
