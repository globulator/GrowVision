# Этап 8.8: Исправление веб-авторизации через Telegram
**Результат:** принят (PASS)
**Что появилось:**
- Полное упразднение личных паролей веб-клиента: сняты требования к паролю в POST /api/auth/web/register/complete и схеме WebRegisterCompleteRequest; доступ управляется связкой Telegram + инвайт-код/мастер-пароль.
- Реальный bot_username: функция get_telegram_bot_username() запрашивает Telegram API getMe по TELEGRAM_BOT_TOKEN с кешированием в памяти и безопасным fallback; эндпоинты /api/version и /api/auth/web/me отдают актуальное имя бота.
- Бесшовный вход и регистрация Web SPA: закрыт тег .password-group в index.html; настроены правила видимости .reg-step в style.css; при 403 telegram_verification_required форма входа автоматически вызывает registerStart, сохраняет accessCode и переходит сразу на Шаг 2 (ввод кода), а на Шаге 3 предзаполняет recommended_name и accessCode.
- Отслеживание доставки кодов и валидация /start: колонка delivery_error в таблице web_verification_codes; отчёт об ошибках доставки ботом (POST /fail); эндпоинт GET /register/code-status с опросом из auth.js; возврат HTTP 400 с требованием нажать /start для неизвестных числовых Telegram ID.
- Метрики качества: 271 автотест (Server 74, Edge 55, Bot 66, E2E 76) без замечаний, ruff clean, 0 эмодзи, 0 alerts. Подробный handoff: [docs/handoff/stage-8-8-fix-web-tg-auth.md](file:///home/mcperchik/study/GrowVision/docs/handoff/stage-8-8-fix-web-tg-auth.md).
**Как попробовать:** в Web SPA `http://127.0.0.1:8000/` ввести Telegram-юзернейм и код инвайта: форма бесшовно запросит код подтверждения из бота
**Исправленные дефекты:** отмена личных паролей в register/complete, получение bot_username через getMe, вёрстка шагов регистрации, автопереход на Шаг 2, отслеживание доставки кодов
**Отложенные замечания (DEFERRED):** нет
**Что нужно от вас:** ничего
