# Задача: Этап 4 / Client API, часть 2 (управление, таймлапс, видео, раздача веба)
Режим: FULL
Ветка: stage-4/client-api-part2
Цель: Реализовать управляющие эндпоинты /api/control/* и /api/cameras/scan с ожиданием ответа RPi, фоновую очистку таймаутов, эндпоинты таймлапса и синхронную сборку видео через ffmpeg, а также раздачу статики (/media и clients/web).
Прочитать перед началом: docs/team_roles/builder.md, docs/PROJECT.md (разд. 2, 3, 5 этап 4), docs/API_CONTRACT.md (разд. 1.3, 1.4, 4.3, 4.4, 4.5), docs/REPO_AND_VERSIONING.md (разд. 4.2).

Allowed Paths (можно создавать и менять только это):
- server/main.py
- server/api/control.py
- server/api/timelapse.py
- server/api/cameras.py
- server/core/
- server/tests/
- docs/handoff/client_api_part2.md

Только чтение:
- docs/PROJECT.md
- docs/DB_SCHEMA.md
- docs/API_CONTRACT.md
- docs/REPO_AND_VERSIONING.md
- docs/team_roles/
- docs/STATE.md
- shared/

Критерии приёмки:
1. Управление в server/api/control.py (разд. 4.4):
   - POST /api/control/snap (Biologist, Developer), POST /api/control/light/main (Developer), POST /api/control/light/flash (Developer), POST /api/control/relay-test (Developer);
   - Проверка офлайна RPi (heartbeat старше 90 с) -> 503 {"detail": "Raspberry Pi офлайн"};
   - target: целое число (installation_id) или "all". target="all" без активных установок -> 409 {"detail": "Нет активных установок"}; архивная установка -> 409; не найдена -> 404;
   - Ожидание ответа RPi: 15 с для SNAP одной установки, 15 + 10*N с для SNAP с target="all", 10 с для остальных команд. При истечении времени ожидания -> 504 {"detail": "..."};
   - При ошибке RPi (status="error") клиент получает 200 {"status": "error", "result_message": ...};
   - Ручные кадры сохраняются с is_manual=True (в таймлапс не попадают);
   - debug_info выдаётся только Developer при debug_mode="true" (Biologist его не видит).
2. Сканирование камер в server/api/cameras.py (разд. 4.3):
   - POST /api/cameras/scan (Developer): ставит SCAN_CAMERAS (is_global=True), ожидает ответ RPi до 10 с (503 если RPi офлайн, 504 если таймаут);
   - Ответ 200 {"command_id": ..., "status": "done", "cameras": [...]}, где перечислены все обнаруженные камеры (свободные и привязанные) в формате GET /api/cameras;
   - debug_info выдаётся только Developer при debug_mode="true".
3. Фоновая очистка таймаутов (раз в 5 с в lifespan server/main.py):
   - Единая логика таймаутов в server/core/ (используется и в claim, и в фоновой задаче):
     * pending старше 30 с -> timeout;
     * running одиночные старше 60 с -> timeout;
     * running глобальный SNAP старше (15 + 10*N) + 15 с -> timeout;
   - Фоновая задача запускается в lifespan FastAPI и корректно отменяется при завершении.
4. Очистка app_state:
   - Записи в памяти (связь command_id -> capture_id, результаты сканирования) удаляются после выдачи клиенту и не накапливаются.
5. Таймлапс-кадры в server/api/timelapse.py (разд. 4.5):
   - GET /api/timelapse/{installation_id}/frames (?camera_id=<id>): доступен Гостю;
   - Отдаёт только плановые кадры (is_manual=False) в порядке captured_at ASC без пагинации;
   - area_display рассчитывается для plant_index=0;
   - При отсутствии кадров -> 200 {"frames": []}.
6. Сборка видео в server/api/timelapse.py (разд. 4.5):
   - POST /api/timelapse/{installation_id}/video (Biologist, Developer): синхронный эндпоинт (def, не async def);
   - 0 кадров -> 404; 1 кадр -> 200 Content-Type: image/jpeg (raw файл); >=2 кадра -> 200 Content-Type: video/mp4 (attachment с Content-Length);
   - Сборка через ffmpeg: libx264, preset veryfast, crf 28, yuv420p, movflags +faststart, scale=-2:min(720\,ih), без звука;
   - Расчёт framerate по числу кадров n: n >= F * D (F=10, D=2) -> 10 fps; иначе n / D (видео ровно D=2 с);
   - Несколько камер без camera_id при одновременной съёмке (<= 10 мин) -> 422 с перечислением камер;
   - Фоновые запросы (heartbeat, claim) не блокируются во время сборки видео.
7. Раздача статики в server/main.py:
   - StaticFiles для /media (server/media/) и / (clients/web/, html=True) монтируются строго после всех API-роутеров.
8. Тесты в server/tests/ изолированы через tmp_path / monkeypatch и не пишут в рабочие server/media и growvision.db.
9. pytest server/tests проходит; ruff check . и ruff format --check . проходят без ошибок.
10. Формат коммитов: Conventional Commits — тип(scope): описание (scope: server). Версию VERSION не поднимать.

Команды проверки:
- ./server/.venv/bin/pytest server/tests -q
- ./server/.venv/bin/ruff check .
- ./server/.venv/bin/ruff format --check .

Файл заметок (handoff): docs/handoff/client_api_part2.md
