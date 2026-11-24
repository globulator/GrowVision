# Handoff: Этап 8.9 (Fix Gallery, FPS & SNAP)

- **Ветка**: `stage-8/fix-gallery-fps-snap`
- **Режим**: FAST
- **Цель**: Исправить отображение кадров в Web-галерее (парсинг `raw_url` / `annotated_url`), реализовать динамический расчет длительности и FPS таймлапса в Telegram-боте без хардкода 30 FPS, и ускорить реакцию/выполнение тестового снимка SNAP в Edge-агенте (`command_poll_interval_sec = 0.5` с, исключение искусственных sleep/warmup в mock-режиме, диагностическое логирование).

---

## 1. Выполненные изменения

### 1. Web-галерея: безопасный fallback полей URL кадра
- **`clients/web/js/gallery.js`**:
  - В функции `loadOne` чтение URL кадра из ответа API `/api/timelapse/{id}/frames` переведено на безопасный fallback:
    - Оригинал: `frame.raw_url || frame.raw_image_url || ''`
    - Разметка: `frame.annotated_url || frame.annotated_image_url || ''`
    - Выбор слоя: `currentLayer === 'ann' && annUrl ? annUrl : (rawUrl || annUrl)`
  - Кадры гарантированно загружаются и отрисовываются в основном элементе `<img>` галереи.
  - Сохранены инварианты Web SPA: 0 эмодзи, 0 alert/confirm/prompt.

### 2. Telegram-бот: динамический расчёт FPS и длительности таймлапса
- **`clients/telegram_bot/handlers/timelapse.py`**:
  - Полностью исключён хардкод 30 FPS при подтверждении сборки видео и в подписи к нему.
  - Расчёт длительности и частоты кадров согласован с логикой Web SPA:
    - `calc_duration = max(1, round(total_frames / 15))` (целевые ~15 FPS, минимум 1 с);
    - `actual_fps = min(30, max(1, round(total_frames / calc_duration)))` (для 4 кадров за 1 с выводится 4 FPS, для 90 кадров за 6 с выводится 15 FPS; потолок 30 FPS включается при превышении 30).
  - В кнопке подтверждения: `▶️ Собрать видео ({actual_fps} FPS)`, в `callback_data`: `vid_confirm:{inst_id}:{cam_part}:{calc_duration}:{actual_fps}`.
  - При `total_frames / calc_duration > 30` выводится предупреждение: `⚠️ Частота ограничена до 30 FPS (с автоматическим прореживанием)`.
  - В подписи к готовому видео при прореживании указывается фактическая частота (`actual_fps_val FPS`).
- **Тесты Telegram-бота**:
  - В `test_stage7_3_bot.py` и `test_stage8_5_bot_fsm_and_timelapse.py` обновлены проверки под динамический FPS.
  - Добавлен тест `test_stage8_9_bot_fps_4_frames` (проверка 4 кадров -> 1 с, 4 FPS, callback_data).

### 3. Edge-агент: быстрый отклик и мгновенный SNAP в mock-режиме
- **`edge_agent/config.py`**:
  - Значение `command_poll_interval_sec` по умолчанию снижено с `2.0` до `0.5` с.
- **`edge_agent/hardware.py`**:
  - В `capture_frame_with_sequence`: для `self.mode == "mock"` исключены вызовы `time.sleep` стабилизации реле/задержки съемки, а `warmup_frames` установлен в 0 (прогрев применяется только для физических USB/CSI-камер на RPi).
- **`edge_agent/orchestrator.py`**:
  - В `_handle_snap`: замеряется точное время выполнения `elapsed_sec = time.perf_counter() - snap_start`, выводится диагностический лог уровня INFO (`[SNAP] Команда #{cmd.id} выполнена за {elapsed_sec:.2f} с`), а в `debug_log_line` добавляется метка `[SNAP] elapsed={elapsed_sec:.2f}s`.
- **Тесты Edge-агента**:
  - В `test_config.py` проверено `command_poll_interval_sec == 0.5`.
  - В `test_orchestrator.py` добавлен тест `test_stage8_9_snap_fast_mock_execution_and_poll_interval` (проверка выполнения SNAP в mock-режиме быстрее 1 с и наличия диагностического лога).

### 4. E2E Тесты
- В `tests/e2e/test_stage8_web_e2e.py` добавлен сквозной тест `test_stage8_9_fix_gallery_fps_snap_e2e`:
  - Проверка исходного кода `gallery.js` на безопасный fallback полей URL.
  - Загрузка кадра через `/api/edge/captures` и проверка ответа `/api/timelapse/{id}/frames` (`raw_url`, `annotated_url` со ссылками `/media/`).
  - Проверка доступности изображения по URL.
