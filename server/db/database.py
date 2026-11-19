import sqlite3
from collections.abc import Generator
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from server.config import get_settings

Base = declarative_base()


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """Настраивает обязательные PRAGMA для соединений SQLite."""
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")
        cursor.execute("PRAGMA journal_mode = WAL;")
        cursor.execute("PRAGMA busy_timeout = 5000;")
        cursor.close()


def create_db_engine(database_url: str | None = None) -> Engine:
    """Создаёт SQLAlchemy Engine с учётом особенностей SQLite."""
    if database_url is None:
        database_url = get_settings().database_url
    connect_args = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(database_url, connect_args=connect_args)


engine = create_db_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """Генератор сессий БД для внедрения зависимостей FastAPI."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db(target_engine: Engine | None = None) -> None:
    """Создаёт все зарегистрированные таблицы в целевой БД."""
    eng = target_engine or engine
    Base.metadata.create_all(bind=eng)
