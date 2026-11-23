# Задача: QA-приёмка / Этап 8.6 (Интеграция фирменного SVG-логотипа GrowVision в Web SPA и favicon)
Режим: FAST
Ветка: stage-8/brand-logo
Цель: Проверить корректность интеграции готового фирменного SVG-логотипа в шапку Web SPA и favicon без перезаписи ассетов.

Прочитать перед началом:
- `docs/PROJECT.md` (раздел 9: правила скорости и тестов)
- `docs/WEB_SPEC.md`
- `docs/team_roles/qa_tester.md`
- `docs/handoff/stage-8-6-brand-logo.md` (после готовности Builder)

Критерии проверки:
1. Сохранность ассетов:
   - Файлы `clients/web/assets/logo-*.svg` не были изменены или повреждены.
   - Валидный XML/SVG во всех трёх файлах.
2. Web SPA:
   - В шапке `clients/web/index.html` перед названием GrowVision отображается фирменный SVG-логотип с пропорциями 4:3.
   - `<link rel="icon" type="image/svg+xml" ...>` содержит Data URI, соответствующий `logo-green.svg` (`viewBox="0 0 80 60"`).
   - Инварианты сохранены: 0 эмодзи, 0 alert/confirm/prompt, 0 внешних ссылок и CDN.
   - Корректное отображение на мобильных экранах (от 360 px).
3. Тесты и линтер:
   - `tests/e2e/test_stage8_web_e2e.py` проходит успешно.
   - `ruff check .` и `ruff format --check .` чисты.
   - Чистый `git status`.
