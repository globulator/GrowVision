# Handoff: Этап 8.6 — Интеграция готового 1:1 SVG-логотипа GrowVision в Web SPA и favicon

## 1. Цели и задачи этапа
Реализация требований Этапа 8.6 (режим FAST) с обновленными 1:1 ассетами пользователя:
1. **Сохранение пользовательских ассетов**:
   - Файлы `clients/web/assets/logo-green.svg`, `clients/web/assets/logo-white.svg` и `clients/web/assets/logo-black.svg` сохранены без изменений (`viewBox="0 0 120 90"`). Валидность их структуры подтверждена.
2. **Интеграция в Web SPA**:
   - В `clients/web/index.html` в левой части верхней шапки `.header-logo` рядом с текстом `GrowVision` размещен знак фирменного логотипа в строго квадратных пропорциях 1:1 (`aspect-ratio: 1 / 1`, 32×32 px на десктопе, 28×28 px на мобильных экранах от 360 px) с предотвращением искажения геометрии.
   - В `clients/web/index.html` сгенерирован Data URI favicon напрямую из актуального файла `clients/web/assets/logo-green.svg` (`viewBox="0 0 120 90"`).
   - В `clients/web/style.css` настроены стили `.brand-logo` (`aspect-ratio: 1 / 1; width: 32px; height: 32px;`) и мобильная адаптивность (`width: 28px; height: 28px;` в `@media (max-width: 600px)`).
   - Строгие инварианты `docs/WEB_SPEC.md` соблюдены: 0 Unicode-эмодзи, 0 внешних ссылок и CDN.
3. **Тестирование и валидация**:
   - Обновлен тест `test_stage8_brand_logo_and_favicon_validation` в `tests/e2e/test_stage8_web_e2e.py` под квадратные пропорции 1:1, `viewBox="0 0 120 90"` и актуальный Data URI favicon.
   - Проверена раздача файлов логотипов по `/assets/logo-*.svg`.

---

## 2. Изменённые файлы
- `clients/web/index.html`: Data URI favicon из `logo-green.svg` и интеграция знака логотипа 1:1 в `.header-logo`.
- `clients/web/style.css`: класс `.brand-logo` со строгими пропорциями 1:1 (`aspect-ratio: 1 / 1`, 32×32 px / 28×28 px).
- `tests/e2e/test_stage8_web_e2e.py`: автотесты раздачи и валидации структуры 1:1 логотипа, стилей и favicon.
- `docs/handoff/stage-8-6-brand-logo.md`: настоящий handoff-документ.

---

## 3. Результаты проверок
- `server/.venv/bin/pytest tests/e2e/test_stage8_web_e2e.py`: **4 passed** за 1.02 с.
- `server/.venv/bin/ruff check .`: **All checks passed!**
- `server/.venv/bin/ruff format --check .`: **249 files already formatted**.
- `git status clients/web/assets/`: без изменений (read-only).
