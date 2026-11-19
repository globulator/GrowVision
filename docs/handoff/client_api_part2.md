# План и отчёт реализации: Этап 4 / Client API, часть 2 (управление, таймлапс, видео, раздача веба)

## Реализованный функционал
1. `server/core/state.py`:
   - Реализованы методы `pop_capture_by_command(command_id)` и `pop_captures_by_command(command_id)` для безопасного извлечения и очистки связей снимков.
   - Реализован метод `pop_scanned_cameras(command_id)` для извлечения и очистки результатов сканирования камер.
2. `server/core/timeouts.py`:
   - Реализована функция `expire_stale_commands(db, now)`:
     - pending старше 30 с -> timeout;
     - running одиночные старше 60 с -> timeout;
     - running глобальный SNAP старше `(15 + 10 * N) + 15` с -> timeout.
3. `server/main.py`:
   - Фоновая задача `cleanup_timeouts_task` раз в 5 секунд в `lifespan` FastAPI (с корректной отменой при остановке приложения).
   - Подключение роутеров `control_router` и `timelapse_router`.
   - Монтирование статики через `StaticFiles`: `/media` -> `server/media/` и `/` -> `clients/web/` (`html=True`) строго после всех API-роутеров.
4. `server/api/control.py`:
   - Проверка офлайна RPi (heartbeat > 90 с -> 503).
   - Валидация target (`target="all"` без активных установок -> 409, в архиве -> 409, не найдена -> 404).
   - Ожидание результата RPi (15 с для одиночной SNAP, `15 + 10 * N` с для SNAP target="all", 10 с для остальных; таймаут -> 504 с переводом pending в timeout).
   - Ручные снимки сохраняются с `is_manual=True` (в таймлапс не попадают).
   - `debug_info` отдается только роли Developer при включенном `debug_mode="true"`.
5. `server/api/cameras.py`:
   - Реализован `POST /api/cameras/scan` (Developer): запуск SCAN_CAMERAS (`is_global=True`), ожидание до 10 с (503 при офлайне RPi, 504 при таймауте).
   - Возврат списка всех обнаруженных камер с автоматической регистрацией новых камер и обновлением `last_seen_at`.
   - Очистка `app_state` после выдачи клиенту.
6. `server/api/timelapse.py`:
   - `GET /api/timelapse/{installation_id}/frames`: доступен Гостю; только плановые кадры (`is_manual=False`) в порядке `captured_at ASC`; `area_display` для `plant_index=0`.
   - `POST /api/timelapse/{installation_id}/video` (Biologist, Developer): синхронный эндпоинт (`def`); 0 кадров -> 404; 1 кадр -> 200 `image/jpeg`; >=2 кадров -> сборка `video/mp4` через ffmpeg с параметрами `libx264`, `preset veryfast`, `crf 28`, `scale=-2:min(720\,ih)`, без звука, расчет framerate `n / D` или `F`. Проверка одновременной съемки разными камерами (<=10 мин без `camera_id` -> 422).
7. `server/tests/test_client_api_part2.py`: 9 комплексных тестов с изоляцией БД, лимитеров, app_state и медиа.

## Результаты верификации
- `pytest server/tests`: 50 passed, 0 failed.
- `ruff check .`: All checks passed.
- `ruff format --check .`: 69 files already formatted.
- Статус: **READY_FOR_QA**
