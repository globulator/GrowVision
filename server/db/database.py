import contextlib
import sqlite3
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

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


def upsert_system_setting(
    db: Session, key: str, value: str, now: datetime | None = None
) -> None:
    """Создаёт или обновляет запись SystemSetting по ключу."""
    from server.db.models import SystemSetting

    ts = now or datetime.now(UTC)
    existing = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if existing:
        existing.value = value
        existing.updated_at = ts
    else:
        db.add(SystemSetting(key=key, value=value, updated_at=ts))


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

        with contextlib.suppress(Exception):
            res = conn.execute(
                text("PRAGMA table_info(web_verification_codes);")
            ).fetchall()
            wvc_cols = {row[1] for row in res}
            if "delivery_error" not in wvc_cols:
                conn.execute(
                    text(
                        "ALTER TABLE web_verification_codes ADD COLUMN delivery_error VARCHAR(255);"
                    )
                )
                conn.commit()

        with contextlib.suppress(Exception):
            res = conn.execute(
                text("PRAGMA table_info(plant_measurements);")
            ).fetchall()
            pm_cols = {row[1] for row in res}
            for col_name in (
                "width_px",
                "height_px",
                "width_mm",
                "height_mm",
                "area_cm2",
            ):
                if col_name not in pm_cols:
                    conn.execute(
                        text(
                            f"ALTER TABLE plant_measurements ADD COLUMN {col_name} FLOAT;"
                        )
                    )
            conn.commit()

        # Безопасное удаление устаревших уникальных индексов на GPIO-пины (Этап 7.4)
        with contextlib.suppress(Exception):
            conn.execute(text("DROP INDEX IF EXISTS uq_active_light_pin;"))
            conn.execute(text("DROP INDEX IF EXISTS uq_active_flash_pin;"))
            conn.commit()

        # Изоляция Mock-режима (Этап 8.5): синхронизация пользователей из growvision.db в growvision_mock.db
        with contextlib.suppress(Exception):
            db_name = str(eng.url)
            if "growvision_mock.db" in db_name:
                main_db_path = Path("growvision.db")
                if main_db_path.exists():
                    conn.execute(text("ATTACH DATABASE 'growvision.db' AS main_db;"))
                    conn.execute(
                        text(
                            "INSERT OR IGNORE INTO users (id, client_type, external_id, login, role, is_active, authorized_at, last_active_at, logged_out_at) "
                            "SELECT id, client_type, external_id, login, role, is_active, authorized_at, last_active_at, logged_out_at FROM main_db.users;"
                        )
                    )
                    conn.execute(
                        text(
                            "INSERT OR IGNORE INTO invite_codes (id, code, role, created_by_user_id, created_at, expires_at, used_at, used_by_user_id, is_revoked) "
                            "SELECT id, code, role, created_by_user_id, created_at, expires_at, used_at, used_by_user_id, is_revoked FROM main_db.invite_codes;"
                        )
                    )
                    conn.execute(
                        text(
                            "INSERT OR IGNORE INTO telegram_contacts (id, telegram_id, username, first_name, last_name, updated_at) "
                            "SELECT id, telegram_id, username, first_name, last_name, updated_at FROM main_db.telegram_contacts;"
                        )
                    )
                    conn.execute(text("DETACH DATABASE main_db;"))
                    conn.commit()
