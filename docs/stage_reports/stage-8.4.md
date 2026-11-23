# Этап 8.4: Помощник подключения Raspberry Pi и скрипты деплоя
**Результат:** принят (PASS)
**Что появилось:**
- Помощник подключения Raspberry Pi в Web SPA (только Developer): SVG-слот в шапке со статусом платы (Онлайн/Офлайн, автообновление раз в 3 с); модальное окно с формой генерации одноразовой команды, скрытым полем NetBird Setup Key с переключателем видимости, копированием в буфер обмена (с fallback) и сворачиваемой памяткой по microSD/SSH/nmtui.
- Серверные эндпоинты Edge Setup (/api/edge/setup/*): in-memory хранилище токенов (_setup_tokens, TTL 600 с, без записи в SQLite и без логирования ключей); эндпоинт GET /{token} сжигает токен при первом обращении (повтор -> 404) и отдает bash-скрипт со строгим экранированием shlex.quote; безопасные нейтральные плейсхолдеры в server/config.py и .env.example.
- Универсальные bash-скрипты автоматизации в scripts/ (chmod +x, проверка bash -n):
  - scripts/gv-update.sh — единая утилита обновления gv-update для сервера и RPi (git pull --ff-only, селективное обновление .venv и перезапуск active служб);
  - scripts/install_server.sh — автоматизированное развертывание сервера с нуля (пакеты, .venv, server/.env chmod 600, службы systemd, симлинк gv-update).
- Метрики качества: 310 автотестов (Server & E2E 143, Edge 54, Bot 62, CLI 51) без замечаний, ruff clean, 0 эмодзи, 0 alerts, 0 CDNs. Подробный handoff: [docs/handoff/stage-8-rpi-setup.md](file:///home/mcperchik/study/GrowVision/docs/handoff/stage-8-rpi-setup.md).
**Как попробовать:** в Web SPA `http://127.0.0.1:8000/` под developer нажать кнопку платы в шапке; запустить `bash -n scripts/*.sh`
**Исправленные дефекты:** помощник RPi в Web SPA, безопасный одноразовый bootstrap без сохранения ключей, скрипты install_server.sh и gv-update.sh
**Отложенные замечания (DEFERRED):** нет
**Что нужно от вас:** ничего
