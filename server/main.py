import asyncio
import json
import signal
import threading
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError

from server.api.auth import router as auth_router
from server.api.cameras import router as cameras_router
from server.api.climate import climate_router, esp_router
from server.api.control import router as control_router
from server.api.edge import router as edge_router
from server.api.edge_setup import router as edge_setup_router
from server.api.installations import router as installations_router
from server.api.system import router as system_router
from server.api.timelapse import router as timelapse_router
from server.config import get_settings
from server.core.climate import (
    confirm_esp_command,
    esp_queue,
    record_sensor_reading,
    run_climate_scheduler_step,
    touch_esp_seen,
)
from server.core.light_state import (
    compute_active_pin_states,
    sync_app_state_pins,
    sync_day_night_phases_step,
)
from server.core.live_manager import live_manager
from server.core.timeouts import expire_stale_commands
from server.db.database import SessionLocal, init_db
from shared.esp_bridge import EspTcpBridge
from shared.logger import get_logger
from shared.version import get_app_version

logger = get_logger("server.main")


async def cleanup_timeouts_task() -> None:
    """Фоновая периодическая очистка таймаутов команд раз в 5 секунд."""
    while True:
        try:
            await asyncio.sleep(5)
            db = SessionLocal()
            try:
                expire_stale_commands(db)
            finally:
                db.close()
        except asyncio.CancelledError:
            break
        except Exception as e:  # noqa: BLE001
            logger.warning("Ошибка фоновой очистки таймаутов: %s", e)


async def day_night_sync_task() -> None:
    """Фоновая синхронизация фаз цикла День/Ночь раз в 20 секунд."""
    while True:
        try:
            await asyncio.sleep(20)
            db = SessionLocal()
            try:
                sync_day_night_phases_step(db)
            finally:
                db.close()
        except asyncio.CancelledError:
            break
        except Exception as e:  # noqa: BLE001
            logger.warning("Ошибка фоновой синхронизации цикла День/Ночь: %s", e)


async def climate_scheduler_task() -> None:
    """Фоновый планировщик полива и вентиляции ESP раз в 5 секунд."""
    while True:
        try:
            await asyncio.sleep(5)
            db = SessionLocal()
            try:
                run_climate_scheduler_step(db)
            finally:
                db.close()
        except asyncio.CancelledError:
            break
        except Exception as e:  # noqa: BLE001
            logger.warning("Ошибка фонового планировщика климата: %s", e)


def _handle_esp_adapter_request_sync(
    method: str, path: str, body: bytes
) -> tuple[int, bytes]:
    """Обрабатывает запрос от встроенного ESP TCP-адаптера напрямую через БД."""
    db = SessionLocal()
    try:
        if method == "GET" and path == "/ping":
            touch_esp_seen(db)
            db.commit()
            return 200, b""

        if method == "GET" and path == "/api/esp/command":
            touch_esp_seen(db)
            db.commit()
            payload = json.dumps(esp_queue.peek_payload()).encode("utf-8")
            return 200, payload

        if method == "POST" and path == "/api/esp/sensors":
            data = json.loads(body.decode("utf-8"))
            record_sensor_reading(
                db, float(data["temperature"]), float(data["humidity"])
            )
            return 200, b""

        if method == "POST" and path == "/api/esp/command":
            data = json.loads(body.decode("utf-8"))
            cmd = confirm_esp_command(db, int(data["id"]))
            return (200, b"") if cmd is not None else (409, b"")

        return 404, b""
    except Exception:  # noqa: BLE001
        return 400, b""
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Жизненный цикл приложения: инициализация БД, пинов, сигналов и фоновых задач."""
    init_db()
    live_manager.reset_shutdown()
    live_manager.bind_loop(asyncio.get_running_loop())

    # Перехватываем SIGINT/SIGTERM (поверх uvicorn.capture_signals), чтобы мгновенно
    # завершать открытые MJPEG-стримы до ожидания закрытия HTTP-соединений в uvicorn.
    orig_handlers: dict[int, Any] = {}

    def _shutdown_signal_handler(signum: int, frame: object) -> None:
        live_manager.request_shutdown()
        orig = orig_handlers.get(signum)
        if callable(orig):
            orig(signum, frame)

    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            orig_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, _shutdown_signal_handler)

    # Инициализация состояния пинов активных установок с учётом 5-уровневой иерархии
    db = SessionLocal()
    try:
        from server.db.models import Installation

        active_installations = (
            db.query(Installation).filter(Installation.is_archived.is_(False)).all()
        )
        sync_app_state_pins(compute_active_pin_states(db, active_installations))
    except Exception as e:  # noqa: BLE001
        logger.warning("Ошибка инициализации состояния пинов: %s", e)
    finally:
        db.close()

    task = asyncio.create_task(cleanup_timeouts_task())
    day_night_task = asyncio.create_task(day_night_sync_task())
    climate_task = asyncio.create_task(climate_scheduler_task())
    esp_adapter: EspTcpBridge | None = None
    server_settings = get_settings()
    if server_settings.esp_adapter_enabled:

        async def _adapter_forward(
            method: str, path: str, body: bytes
        ) -> tuple[int, bytes]:
            return await asyncio.to_thread(
                _handle_esp_adapter_request_sync, method, path, body
            )

        esp_adapter = EspTcpBridge(
            host=server_settings.esp_adapter_host,
            port=server_settings.esp_adapter_port,
            forward_fn=_adapter_forward,
        )
        await esp_adapter.start()
        logger.info(
            "Встроенный ESP TCP-адаптер запущен на %s:%s",
            server_settings.esp_adapter_host,
            esp_adapter.bound_port,
        )
    try:
        yield
    finally:
        live_manager.request_shutdown()
        if threading.current_thread() is threading.main_thread():
            for sig, handler in orig_handlers.items():
                if signal.getsignal(sig) is _shutdown_signal_handler:
                    signal.signal(sig, handler)
        if esp_adapter is not None:
            await esp_adapter.stop()
        for bg_task in (task, day_night_task, climate_task):
            bg_task.cancel()
            try:
                await bg_task
            except asyncio.CancelledError:
                pass


app = FastAPI(
    title="GrowVision Server",
    version=get_app_version(),
    lifespan=lifespan,
)

# Подключение API-роутеров
app.include_router(edge_router)
app.include_router(edge_setup_router)
app.include_router(auth_router)
app.include_router(installations_router)
app.include_router(cameras_router)
app.include_router(control_router)
app.include_router(timelapse_router)
app.include_router(system_router)
app.include_router(esp_router)
app.include_router(climate_router)

# Раздача статики строго после всех API-роутеров
media_dir = Path(__file__).resolve().parent / "media"
media_dir.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=media_dir), name="media")

web_dir = Path(__file__).resolve().parent.parent / "clients" / "web"
web_dir.mkdir(parents=True, exist_ok=True)
app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")


@app.exception_handler(IntegrityError)
async def integrity_exception_handler(
    request: Request, exc: IntegrityError
) -> JSONResponse:
    """Перехват конфликтов базы данных (IntegrityError -> 409 Conflict)."""
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": "Конфликт целостности данных"},
    )


def format_validation_error_message(exc: RequestValidationError) -> str:
    """Формирует короткое читаемое сообщение из ошибок валидации Pydantic."""
    parts = []
    for err in exc.errors():
        raw_loc = [str(x) for x in err.get("loc", []) if x != "body"]
        loc = " -> ".join(raw_loc)
        msg = err.get("msg", "Ошибка валидации")
        msg = msg.removeprefix("Value error, ")
        if loc:
            parts.append(f"{loc}: {msg}")
        else:
            parts.append(msg)
    return "; ".join(parts) if parts else "Ошибка валидации данных запроса"


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Единый формат ошибки валидации данных запроса (422 Unprocessable Content)."""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": format_validation_error_message(exc)},
    )


if __name__ == "__main__":
    import uvicorn

    from server.config import get_settings

    settings = get_settings()
    uvicorn.run(
        "server.main:app",
        host=settings.server_host,
        port=settings.server_port,
        workers=1,
    )
