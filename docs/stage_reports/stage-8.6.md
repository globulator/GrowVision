# Этап 8.6: Интеграция фирменного SVG-логотипа GrowVision
**Результат:** принят (PASS)
**Что появилось:**
- Фирменный логотип в шапке Web SPA: в .header-logo перед текстом GrowVision интегрирован векторный знак с пропорциями 4:3 (viewBox="0 0 80 60", 36×27 px на десктопе, 32×24 px на мобильных экранах от 360 px).
- Обновление Favicon: в clients/web/index.html встроен Data URI SVG favicon, точно воспроизводящий геометрию и цветовую палитру logo-green.svg (viewBox="0 0 80 60").
- Сохранность ассетов: файлы clients/web/assets/logo-green.svg, logo-white.svg и logo-black.svg сохранены в исходном виде, валидность XML/SVG подтверждена, доступна раздача через статику.
- Соблюдение инвариантов: 0 эмодзи, 0 внешних ссылок, 0 CDN.
- Метрики качества: 320 автотестов (Server & E2E 148, Edge 55, Bot 66, CLI 51) без замечаний, ruff clean. Подробный handoff: [docs/handoff/stage-8-6-brand-logo.md](file:///home/mcperchik/study/GrowVision/docs/handoff/stage-8-6-brand-logo.md).
**Как попробовать:** открыть Web SPA `http://127.0.0.1:8000/` и проверить отображение фирменного логотипа в шапке и на вкладке браузера
**Исправленные дефекты:** интеграция официального логотипа GrowVision в Web SPA и favicon с адаптивностью от 360 px
**Отложенные замечания (DEFERRED):** нет
**Что нужно от вас:** ничего
