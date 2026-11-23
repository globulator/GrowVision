import contextlib
import sqlite3
from collections.abc import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

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
    """Создаёт все зарегистрированные таблицы и производит безопасную миграцию схемы."""
    eng = target_engine or engine
    Base.metadata.create_all(bind=eng)

    # Безопасное добавление недостающих колонок при старте на существующей SQLite БД
    with eng.connect() as conn:
        with contextlib.suppress(Exception):
            res = conn.execute(text("PRAGMA table_info(installations);")).fetchall()
            inst_cols = {row[1] for row in res}
            if "capture_delay_sec" not in inst_cols:
                conn.execute(
                    text(
                        "ALTER TABLE installations ADD COLUMN capture_delay_sec FLOAT;"
                    )
                )
                conn.commit()

        with contextlib.suppress(Exception):
            res = conn.execute(text("PRAGMA table_info(users);")).fetchall()
            user_cols = {row[1] for row in res}
            if "is_active" not in user_cols:
                conn.execute(
                    text(
                        "ALTER TABLE users ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1;"
                    )
                )
                conn.commit()
            if "logged_out_at" not in user_cols:
                conn.execute(
                    text("ALTER TABLE users ADD COLUMN logged_out_at DATETIME;")
                )
                conn.commit()
            if "password_hash" not in user_cols:
                conn.execute(
                    text("ALTER TABLE users ADD COLUMN password_hash VARCHAR(255);")
                )
                conn.commit()

        # Безопасное удаление устаревших уникальных индексов на GPIO-пины (Этап 7.4)
        with contextlib.suppress(Exception):
            conn.execute(text("DROP INDEX IF EXISTS uq_active_light_pin;"))
            conn.execute(text("DROP INDEX IF EXISTS uq_active_flash_pin;"))
            conn.commit()
