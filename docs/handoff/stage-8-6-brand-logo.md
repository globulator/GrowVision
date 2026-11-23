# Handoff: Этап 8.6 — Интеграция фирменного SVG-логотипа GrowVision в Web SPA и favicon

## 1. Цели и задачи этапа
Реализация требований Этапа 8.6 (режим FAST):
1. **Использование готовых ассетов**:
   - Файлы `clients/web/assets/logo-green.svg`, `clients/web/assets/logo-white.svg` и `clients/web/assets/logo-black.svg` сохранены без изменений (`viewBox="0 0 80 60"`). Валидность их структуры подтверждена.
2. **Интеграция в Web SPA**:
   - В `clients/web/index.html` в левой части верхней шапки рядом с заголовком `GrowVision` размещен знак фирменного логотипа (`viewBox="0 0 80 60"`, пропорции 4:3 — 36×27 px на десктопе, 32×24 px на мобильных экранах от 360 px).
   - В `clients/web/index.html` обновлен Data URI `<link rel="icon" ...>` на встроенный SVG, точно воспроизводящий геометрию и цвета `logo-green.svg` (`viewBox="0 0 80 60"`).
   - В `clients/web/style.css` добавлены правила для `.brand-logo` с сохранением пропорций 4:3 и адаптацией для мобильных устройств в медиа-запросе `@media (max-width: 600px)`.
   - Строгие инварианты `docs/WEB_SPEC.md` соблюдены: 0 Unicode-эмодзи, 0 внешних ссылок и CDN.
3. **Тестирование и валидация**:
   - Добавлен тест `test_stage8_brand_logo_and_favicon_validation` в `tests/e2e/test_stage8_web_e2e.py`.
   - Проверена раздача файлов логотипов по `/assets/logo-*.svg`.

---

## 2. Изменённые файлы
- `clients/web/index.html`: обновление Data URI favicon (80×60) и добавление знака логотипа в `.header-logo`.
- `clients/web/style.css`: класс `.brand-logo` (36×27 px) и мобильная адаптивность (32×24 px, gap 8 px).
- `tests/e2e/test_stage8_web_e2e.py`: проверка раздачи статических ассетов логотипов и сквозной тест валидации структуры логотипа, стилей и favicon.
- `docs/handoff/stage-8-6-brand-logo.md`: настоящий handoff-документ.

---

## 3. Результаты проверок
- `server/.venv/bin/pytest tests/e2e/test_stage8_web_e2e.py`: **4 passed** за 1.00 с.
- `server/.venv/bin/ruff check .`: **All checks passed!**
- `server/.venv/bin/ruff format --check .`: **247 files already formatted**.
- `git status`: только целевые файлы.
