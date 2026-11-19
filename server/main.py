from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from server.api.auth import router as auth_router
from server.api.cameras import router as cameras_router
from server.api.edge import router as edge_router
from server.api.installations import router as installations_router
from server.api.system import router as system_router
from server.db.database import init_db
from shared.version import get_app_version


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Жизненный цикл приложения: инициализация БД."""
    init_db()
    yield


app = FastAPI(
    title="GrowVision Server",
    version=get_app_version(),
    lifespan=lifespan,
)

# Подключение роутеров
app.include_router(edge_router)
app.include_router(auth_router)
app.include_router(installations_router)
app.include_router(cameras_router)
app.include_router(system_router)


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
