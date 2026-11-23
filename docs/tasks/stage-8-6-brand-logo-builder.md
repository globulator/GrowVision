# Задача: Этап 8.6 / Интеграция квадратного 1:1 SVG-логотипа GrowVision в Web SPA и favicon
Режим: FAST
Ветка: stage-8/brand-logo
Цель: Интегрировать обновлённый пользователем квадратный 1:1 SVG-логотип из clients/web/assets/ в шапку Web SPA и в favicon (Data URI) со строгими пропорциями 1:1.

Прочитать перед началом:
- `docs/PROJECT.md` (раздел 9: правила скорости и тестов)
- `docs/WEB_SPEC.md`
- `docs/team_roles/builder.md`

Allowed Paths (можно создавать и менять только это):
- `clients/web/index.html`
- `clients/web/style.css`
- `clients/web/js/` (если необходимо)
- `docs/`
- `tests/e2e/`
- `VERSION`

Только чтение:
- `clients/web/assets/` (СТРОГО: НЕ перерисовывать, НЕ менять геометрию/viewBox и НЕ перезаписывать файлы logo-green.svg, logo-white.svg, logo-black.svg!)
- `server/`
- `edge_agent/`
- `clients/cli/`
- `clients/telegram_bot/`
- `shared/`

Критерии приёмки:
1. Сохранение пользовательских 1:1 SVG-ассетов (`clients/web/assets/`):
   - НЕ перерисовывать, НЕ изменять геометрию/viewBox и НЕ перезаписывать подготовленные пользователем файлы `clients/web/assets/logo-green.svg`, `clients/web/assets/logo-white.svg` и `clients/web/assets/logo-black.svg` (соотношение сторон `1:1`).
2. Интеграция в Web SPA (`clients/web/index.html`, `clients/web/style.css`):
   - Подключить знак логотипа из `clients/web/assets/logo-green.svg` (или `logo-white.svg`) в левую часть верхней шапки рядом с названием `GrowVision` строго в квадратных пропорциях `1:1` (`aspect-ratio: 1 / 1`, одинаковые `width` и `height`, например `28px × 28px` или `32px × 32px`), чтобы логотип не искажался на десктопе и мобильных экранах от `360 px`.
   - Сгенерировать Data URI для `<link rel="icon" type="image/svg+xml" ...>` в `clients/web/index.html` напрямую из актуального файла `clients/web/assets/logo-green.svg` на диске.
   - Соблюсти все ограничения `docs/WEB_SPEC.md` (0 эмодзи, 0 внешних ссылок и CDN).
3. Проверка и качество:
   - Проверить валидность XML/SVG всех трёх файлов в `clients/web/assets/`.
   - Обновить автотест `tests/e2e/test_stage8_web_e2e.py` под квадратные пропорции 1:1 и актуальный Data URI favicon.
   - `ruff check .` и `ruff format --check .` чисты.

Команды проверки:
- `server/.venv/bin/pytest tests/e2e/test_stage8_web_e2e.py -q`
- `server/.venv/bin/ruff check .`
- `server/.venv/bin/ruff format --check .`

Файл заметок (handoff): `docs/handoff/stage-8-6-brand-logo.md`
