# Задача: Этап 7.2 — Комплексные исправления Telegram-бота, сервера, CLI и Edge-агента (Раунд 4)

- **Компонент**: `telegram_bot`, `server`, `cli`, `edge_agent`, `shared`
- **Ветка**: `stage-7/bot-fixes-4`
- **Режим**: FULL
- **Цель**: Устранить дефекты живого прогона бота, расширить функционал работы с камерами, архивом, задержкой съёмки `capture_delay_sec`, удалением установок с кадрами, синхронизацией команд CLI и бота, блокировками ручной съёмки и реалистичной динамикой роста растений в эмуляторе.
- **Прочитать перед началом**:
  - `docs/PROJECT.md` (раздел 9: правила скорости и тестов — обязателен!);
  - `docs/API_CONTRACT.md`, `docs/BOT_SPEC.md`, `docs/CLI_SPEC.md`;
  - `server/api/installations.py`, `server/api/cameras.py`, `server/api/system.py`, `server/models/`;
  - `clients/telegram_bot/`, `clients/cli/`, `edge_agent/`.
- **Allowed Paths**:
  - `clients/telegram_bot/`
  - `clients/cli/`
  - `server/`
  - `edge_agent/`
  - `shared/`
  - `tests/e2e/`
  - `docs/USER_GUIDE.md`
  - `docs/handoff/`
  - `docs/tasks/`
  - `docs/STATE.md`
- **Файл заметок (handoff)**: `docs/handoff/bot.md`

---

## Критерии приёмки

1. **Кнопка «📷 Камеры установки» и служебные callback-заглушки в боте**:
   - Для инлайн-кнопки `inst:cameras:{inst_id}` (`keyboards/inline.py:88`) реализовать обработчик: вывод списка всех привязанных к данной установке камер (включая оффлайн/недоступные камеры с наглядной пометкой статуса), кнопок управления/привязки и кнопки возврата к карточке (`inst:view:{inst_id}`).
   - Добавить явные обработчики-заглушки (`await callback.answer()`) для `frm:close`, `frm:noop`, `pag:noop`, чтобы нажатие на них не приводило к вечной анимации загрузки Telegram.

2. **Галерея плановых кадров и ролевой доступ**:
   - В `clients/telegram_bot/api_client.py:get_timelapse_frames` и `handlers/timelapse.py`: сервер возвращает структуру `{"installation_id": ..., "frames": [...]}`, парсить список как `res.get("frames", [])` с fallback на случай `list`.
   - Разграничение по ролям: пользователю с ролью `guest` показывать только самый последний плановый кадр (без листания всей галереи); пользователям с ролями `biologist` и `developer` — полную галерею с пагинацией.

3. **Блок `🐞 DEBUG`**:
   - В `handlers/cameras.py`, `handlers/control.py` и везде, где вызывается `format_debug_block`: читать строку лога из `resp["debug_info"]["log_line"]` (ранее искалось `resp.get("debug_log_line")`), чтобы корректно отображались и «Команда:», и «Лог:».

4. **Валидация GPIO-пинов в мастере создания установки**:
   - На шагах `waiting_light_pin` и `waiting_flash_pin` проверять диапазон сервера `0 <= pin <= 53`.
   - При выходе за диапазон выводить понятную ошибку и оставаться в текущем FSM-состоянии без сброса мастера.

5. **Фильтрация архивных установок**:
   - В `handlers/installations.py` при `show_archived=True` фильтровать список от `GET /api/installations?include_archived=true` строго по `inst.get("is_archived") is True`, исключая дублирование активных установок.

6. **Ручной снимок для роли Биолог**:
   - При ручной съёмке (`handlers/control.py`) пользователю с ролью `biologist` отправлять только `raw`-снимок через `send_photo` (без `annotated`-снимка с контурами).
   - Для роли `developer` сохранять отправку полного альбома из двух снимков (`raw` + `annotated`).

7. **Версия сервера и примеры в диалогах**:
   - В `handlers/start_auth.py` из ответа `GET /api/version` извлекать `ver_info.get("server_version") or ver_info.get("version", "неизвестно")`, исключив отображение `vнеизвестно`.
   - В строке `start_auth.py:180` заменить пример `Иван Петров` на `Андрей Бармин`.

8. **HTML-экранирование, зависимости и фатальный выход при невалидном токене**:
   - Добавить `html.escape` в `formatters.py:191,193` (`command`, `log_line`), `handlers/timelapse.py:133,472` (`inst_name`), `handlers/start_auth.py:45,51,56` (`username`).
   - Добавить `aiohttp-socks` в `clients/telegram_bot/requirements.txt` (при пустом `TELEGRAM_PROXY` бот работает напрямую).
   - В `clients/telegram_bot/main.py:wait_for_telegram_connection` при ошибке `TelegramUnauthorizedError` логировать критическую ошибку и немедленно завершать процесс.

9. **Удаление архивных установок с кадрами на сервере (`server/api/installations.py`)**:
   - Если `inst.is_archived is True`, разрешить вызов `DELETE /api/installations/{id}` даже при наличии кадров (`captures_count > 0`): отвязать камеры, каскадно удалить связанные записи в БД и физически удалить файлы/каталог кадров установки из директории медиа (`server/media/...`).
   - Если `inst.is_archived is False`, при наличии кадров по-прежнему возвращать `409 Conflict`.

10. **Сканирование камер (`server/api/cameras.py:scan_cameras`)**:
    - В ответе `POST /api/cameras/scan` возвращать только камеры, реально обнаруженные агентом в момент сканирования.
    - Недоступные/отключенные камеры не выводятся в результатах сканирования, но остаются видимыми в карточке своей установки с пометкой оффлайн.

11. **Настройка «Задержка съёмки» (`capture_delay_sec`) и паритет CLI ↔ Бот**:
    - Сервер и БД: добавить глобальную настройку `capture_delay_sec` в `system_settings` (по умолчанию `1.5` с, диапазон `0.0..30.0`) и поле `Installation.capture_delay_sec: float | None = None` (если `None` — используется глобальная). Передать значение в `edge_agent` для выдержки паузы после света/вспышки перед съёмкой.
    - Бот: в меню «💡 Свет и реле» и меню установки добавить кнопку «⏱ Задержка съёмки» с текущим значением, пресетами (`0.5 с`, `1.5 с`, `3.0 с`, `5.0 с`; для установки — «Использовать общую») и вводом числа `0.0..30.0` сообщением без сброса FSM.
    - CLI: добавить команды настройки `capture_delay_sec` (глобально и на установку) и обеспечить полный функциональный паритет разделов (`auth`, `light`, `snap`, `system`, `unit`, `camera`).

12. **Edge-агент: блокировки и реалистичный рост в эмуляторе**:
    - В `edge_agent/orchestrator.py` ручные команды выполняют `self.hardware.acquire_lock(blocking=True, timeout=10.0)`, дожидаясь завершения короткой плановой съёмки без мгновенной ошибки «Камера недоступна».
    - В `orchestrator.py` и `synthetic.py` вести отдельный `frame_index` для каждой пары `(installation_id, camera_id)` с `0`.
    - Заменить расчет радиуса на ступенчатую функцию:
      ```python
      def compute_plant_radius(frame_index: int, max_radius: int) -> int:
          i = max(0, int(frame_index))
          if i < 10:
              r = 24 + i * 4
          elif i < 20:
              r = 64 + (i - 10) * 3
          elif i < 35:
              r = 94 + (i - 20) * 2
          else:
              r = 124 + (i - 35) * 1
          return min(r, max_radius)
      ```
      где `max_radius = min(width, height) // 2 - 30`, и добавить поворот листьев `angle_offset = (frame_index * 7) % 360`.

13. **Тестирование и чистота**:
    - Покрыть все пункты 1–12 тестами (unit и e2e).
    - Раздел 9 `PROJECT.md`: быстрые тесты (<3 с на тест, общий сьют <60 с, без sleep), чистый `git status`, линтер `ruff check .` и `ruff format --check .` чистые.
    - `VERSION` не менять (`0.3.1`), теги не ставить.
