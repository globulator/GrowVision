# Этап 8.9: Исправления Web-галереи, расчет FPS в боте и ускорение SNAP
**Результат:** принят (PASS)
**Что появилось:**
- Web-галерея (clients/web/js/gallery.js): безопасный fallback полей URL кадра (raw_url || raw_image_url и annotated_url || annotated_image_url) в loadOne; гарантированное отображение и рендеринг кадров в <img> галереи; 0 эмодзи.
- Telegram-бот (clients/telegram_bot/handlers/timelapse.py): устранён хардкод 30 FPS; динамический расчет длительности и частоты кадров согласован с Web SPA: calc_duration = max(1, round(N/15)) и actual_fps = min(30, max(1, round(N/calc_duration))); вывод actual_fps в тексте, на кнопке «Собрать видео ({actual_fps} FPS)», в callback_data и в подписи; предупреждение об автопрореживании при превышении 30 FPS.
- Edge-агент: command_poll_interval_sec снижен с 2.0 до 0.5 с по умолчанию; исключены искусственные задержки time.sleep и warmup_frames в mock-режиме; ускорение выполнения тестового снимка SNAP (< 1 с) с диагностической меткой [SNAP] elapsed={elapsed_sec:.2f}s.
- Метрики качества: 274 автотеста (Server 74, Bot 67, Edge 56, E2E 77) без замечаний, ruff clean. Подробный handoff: [docs/handoff/stage-8-9-fix-gallery-fps-snap.md](file:///home/mcperchik/study/GrowVision/docs/handoff/stage-8-9-fix-gallery-fps-snap.md).
**Как попробовать:** открыть галерею в Web SPA `http://127.0.0.1:8000/`; запросить таймлапс в Telegram-боте (проверить FPS для малого числа кадров); сделать SNAP
**Исправленные дефекты:** fallback URL в web-галерее, хардкод 30 FPS в боте, задержка опроса команд и прогрева камер в mock-режиме
**Отложенные замечания (DEFERRED):** нет
**Что нужно от вас:** ничего
