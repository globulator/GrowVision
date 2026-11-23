import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError

from server.api.auth import router as auth_router
from server.api.cameras import router as cameras_router
from server.api.control import router as control_router
from server.api.edge import router as edge_router
from server.api.edge_setup import router as edge_setup_router
from server.api.installations import router as installations_router
from server.api.system import router as system_router
from server.api.timelapse import router as timelapse_router
from server.core.timeouts import expire_stale_commands
from server.db.database import SessionLocal, init_db
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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Жизненный цикл приложения: инициализация БД, пинов и фоновая очистка таймаутов."""
    init_db()

    # Инициализация состояния пинов активных установок: свет ON, вспышка OFF
    db = SessionLocal()
    try:
        from server.core.state import app_state
        from server.db.models import Installation

        active_installations = (
            db.query(Installation).filter(Installation.is_archived.is_(False)).all()
        )
        for inst in active_installations:
            app_state.set_pin_state(inst.light_gpio_pin, True)
            if inst.flash_gpio_pin != inst.light_gpio_pin:
                app_state.set_pin_state(inst.flash_gpio_pin, False)
    except Exception as e:  # noqa: BLE001
        logger.warning("Ошибка инициализации состояния пинов: %s", e)
    finally:
        db.close()

    task = asyncio.create_task(cleanup_timeouts_task())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
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
