# Задача: Этап 8.6 / Интеграция фирменного SVG-логотипа GrowVision в Web SPA и favicon
Режим: FAST
Ветка: stage-8/brand-logo
Цель: Интегрировать готовый фирменный SVG-логотип из clients/web/assets/ в шапку Web SPA и в favicon (Data URI).

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
- `clients/web/assets/` (СТРОГО: НЕ перерисовывать и НЕ перезаписывать logo-green.svg, logo-white.svg, logo-black.svg!)
- `server/`
- `edge_agent/`
- `clients/cli/`
- `clients/telegram_bot/`
- `shared/`

Критерии приёмки:
1. Использование готовых ассетов (`clients/web/assets/`):
   - НЕ перерисовывать и НЕ перезаписывать уже подготовленные файлы `clients/web/assets/logo-green.svg`, `clients/web/assets/logo-white.svg` и `clients/web/assets/logo-black.svg` (`viewBox="0 0 80 60"`).
2. Интеграция в Web SPA (`clients/web/index.html`, `clients/web/style.css`):
   - Подключить знак логотипа (из `logo-green.svg` / `logo-white.svg`) в левую часть верхней шапки рядом с названием `GrowVision` с корректными пропорциями `4:3` (например, 32×24 px или 40×30 px) и адаптацией под мобильные экраны от `360 px`.
   - Обновить `<link rel="icon" type="image/svg+xml" ...>` в `clients/web/index.html` на встроенный Data URI, точно повторяющий `clients/web/assets/logo-green.svg` (`viewBox="0 0 80 60"`).
   - Соблюсти все ограничения `docs/WEB_SPEC.md` (0 эмодзи, 0 внешних ссылок/CDN).
3. Проверка и качество:
   - Проверить валидность XML/SVG всех трёх файлов в `clients/web/assets/`.
   - Прогнать тесты Web SPA в `tests/e2e/` и обновить автотест валидации favicon/HTML при необходимости.
   - `ruff check .` и `ruff format --check .` чисты.

Команды проверки:
- `server/.venv/bin/pytest tests/e2e/test_stage8_web_e2e.py -q`
- `server/.venv/bin/ruff check .`
- `server/.venv/bin/ruff format --check .`

Файл заметок (handoff): `docs/handoff/stage-8-6-brand-logo.md`
