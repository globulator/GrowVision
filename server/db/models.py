from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from server.db.database import Base


def utc_now() -> datetime:
    """Возвращает текущее время в UTC."""
    return datetime.now(UTC)


class Installation(Base):
    """3.1. installations — Гидропонные установки."""

    __tablename__ = "installations"
    __table_args__ = (
        CheckConstraint(
            "aruco_size_mm > 0",
            name="ck_installations_aruco_size_positive",
        ),
        {"sqlite_autoincrement": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), unique=True, nullable=False)
    culture_name = Column(String(100), nullable=False)
    light_gpio_pin = Column(Integer, nullable=False)
    flash_gpio_pin = Column(Integer, nullable=False)
    aruco_size_mm = Column(Float, nullable=True)
    capture_delay_sec = Column(Float, nullable=True)
    is_archived = Column(Boolean, nullable=False, default=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )

    cameras = relationship("Camera", back_populates="installation")
    captures = relationship("Capture", back_populates="installation")
    edge_commands = relationship(
        "EdgeCommand",
        back_populates="installation",
        passive_deletes=True,
    )


class Camera(Base):
    """3.2. cameras — Физические камеры на Raspberry Pi."""

    __tablename__ = "cameras"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id = Column(Integer, primary_key=True, autoincrement=True)
    hardware_id = Column(String(255), unique=True, nullable=False)
    auto_name = Column(String(150), nullable=False)
    installation_id = Column(
        Integer,
        ForeignKey("installations.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_online = Column(Boolean, nullable=False, default=False)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)

    installation = relationship("Installation", back_populates="cameras")
    captures = relationship("Capture", back_populates="camera")


class Capture(Base):
    """3.3. captures — Снимки (кадры)."""

    __tablename__ = "captures"
    __table_args__ = (
        CheckConstraint(
            "aruco_area_px > 0",
            name="ck_captures_aruco_area_positive",
        ),
        UniqueConstraint(
            "installation_id",
            "camera_id",
            "captured_at",
            name="uq_captures_inst_cam_time",
        ),
        Index("idx_captures_inst_time", "installation_id", "captured_at"),
        {"sqlite_autoincrement": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    installation_id = Column(
        Integer,
        ForeignKey("installations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    camera_id = Column(
        Integer,
        ForeignKey("cameras.id", ondelete="SET NULL"),
        nullable=True,
    )
    captured_at = Column(DateTime(timezone=True), nullable=False)
    is_manual = Column(Boolean, nullable=False, default=False)
    aruco_area_px = Column(Float, nullable=True)
    raw_image_path = Column(String(255), nullable=False)
    annotated_image_path = Column(String(255), nullable=False)

    installation = relationship("Installation", back_populates="captures")
    camera = relationship("Camera", back_populates="captures")
    plant_measurements = relationship(
        "PlantMeasurement",
        back_populates="capture",
        cascade="all, delete-orphan",
    )


class PlantMeasurement(Base):
    """3.4. plant_measurements — Замеры площади растений."""

    __tablename__ = "plant_measurements"
    __table_args__ = (
        CheckConstraint(
            "area_px >= 0",
            name="ck_plant_measurements_area_px_non_negative",
        ),
        UniqueConstraint(
            "capture_id",
            "plant_index",
            name="uq_plant_measurements_capture_plant_index",
        ),
        {"sqlite_autoincrement": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    capture_id = Column(
        Integer,
        ForeignKey("captures.id", ondelete="CASCADE"),
        nullable=False,
    )
    plant_index = Column(Integer, nullable=False, default=0)
    area_px = Column(Float, nullable=False)
    width_px = Column(Float, nullable=True)
    height_px = Column(Float, nullable=True)
    width_mm = Column(Float, nullable=True)
    height_mm = Column(Float, nullable=True)
    area_cm2 = Column(Float, nullable=True)

    capture = relationship("Capture", back_populates="plant_measurements")


class User(Base):
    """3.5. users — Авторизованные сессии (Telegram-бот и CLI)."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "client_type IN ('telegram', 'cli', 'web')",
            name="ck_users_client_type",
        ),
        CheckConstraint(
            "role IN ('biologist', 'developer')",
            name="ck_users_role",
        ),
        UniqueConstraint(
            "client_type",
            "external_id",
            name="uq_users_client_external",
        ),
        {"sqlite_autoincrement": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_type = Column(String(20), nullable=False)
    external_id = Column(String(100), nullable=False)
    login = Column(String(100), nullable=False)
    role = Column(String(20), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    authorized_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
    last_active_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
    logged_out_at = Column(DateTime(timezone=True), nullable=True)

    created_invites = relationship(
        "InviteCode",
        foreign_keys="InviteCode.created_by_user_id",
        back_populates="created_by",
    )
    used_invites = relationship(
        "InviteCode",
        foreign_keys="InviteCode.used_by_user_id",
        back_populates="used_by",
    )


class InviteCode(Base):
    """3.8. invite_codes — Одноразовые коды приглашения для биологов."""

    __tablename__ = "invite_codes"
    __table_args__ = (
        CheckConstraint(
            "role IN ('biologist', 'developer')",
            name="ck_invite_codes_role",
        ),
        {"sqlite_autoincrement": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(8), unique=True, nullable=False, index=True)
    role = Column(String(20), nullable=False, default="biologist")
    created_by_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
    used_by_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_revoked = Column(Boolean, nullable=False, default=False)

    created_by = relationship(
        "User", foreign_keys=[created_by_user_id], back_populates="created_invites"
    )
    used_by = relationship(
        "User", foreign_keys=[used_by_user_id], back_populates="used_invites"
    )


class EdgeCommand(Base):
    """3.6. edge_commands — Очередь команд Server -> Raspberry Pi."""

    __tablename__ = "edge_commands"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'done', 'error', 'timeout')",
            name="ck_edge_commands_status",
        ),
        CheckConstraint(
            "(is_global = 1 AND installation_id IS NULL) OR (is_global = 0 AND installation_id IS NOT NULL)",
            name="ck_edge_commands_global_installation",
        ),
        Index("idx_edge_commands_status_created", "status", "created_at"),
        {"sqlite_autoincrement": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    command_type = Column(String(50), nullable=False)
    installation_id = Column(
        Integer,
        ForeignKey("installations.id", ondelete="CASCADE"),
        nullable=True,
    )
    is_global = Column(Boolean, nullable=False, default=False)
    payload = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    attempts = Column(Integer, nullable=False, default=0)
    result_message = Column(Text, nullable=True)
    debug_log = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    installation = relationship("Installation", back_populates="edge_commands")


class SystemSetting(Base):
    """3.7. system_settings — Глобальные настройки и телеметрия RPi."""

    __tablename__ = "system_settings"

    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
        onupdate=utc_now,
    )


class TelegramContact(Base):
    """3.9. telegram_contacts — Связка username -> telegram_id для авторизации в Web SPA."""

    __tablename__ = "telegram_contacts"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username = Column(String(100), unique=True, nullable=True, index=True)
    first_name = Column(String(150), nullable=True)
    last_name = Column(String(150), nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
        onupdate=utc_now,
    )


class WebVerificationCode(Base):
    """3.10. web_verification_codes — Очередь кодов подтверждения регистрации Web SPA через Telegram."""

    __tablename__ = "web_verification_codes"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, nullable=False, index=True)
    code = Column(String(6), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
    expires_at = Column(DateTime(timezone=True), nullable=False)
    is_verified = Column(Boolean, nullable=False, default=False)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    is_delivered = Column(Boolean, nullable=False, default=False)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    delivery_error = Column(String(255), nullable=True)


class ClimateSensorReading(Base):
    """3.11. climate_sensors — Показания датчика температуры и влажности (ESP SHT31)."""

    __tablename__ = "climate_sensors"
    __table_args__ = (
        Index("idx_climate_sensors_recorded_at", "recorded_at"),
        {"sqlite_autoincrement": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    recorded_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
    temperature = Column(Float, nullable=False)
    humidity = Column(Float, nullable=False)


class PhReading(Base):
    """3.12. ph_readings — Замеры уровня кислотности pH (ручной ввод)."""

    __tablename__ = "ph_readings"
    __table_args__ = (
        CheckConstraint(
            "level >= 0 AND level <= 14",
            name="ck_ph_readings_level_range",
        ),
        Index("idx_ph_readings_recorded_at", "recorded_at"),
        {"sqlite_autoincrement": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    recorded_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
    level = Column(Float, nullable=False)


class PeripheralLog(Base):
    """3.13. peripheral_logs — Журнал подтверждённых переключений полива и вентиляции ESP."""

    __tablename__ = "peripheral_logs"
    __table_args__ = (
        CheckConstraint(
            "device IN ('water', 'fan')",
            name="ck_peripheral_logs_device",
        ),
        CheckConstraint(
            "state IN ('on', 'off')",
            name="ck_peripheral_logs_state",
        ),
        Index("idx_peripheral_logs_device_time", "device", "recorded_at"),
        {"sqlite_autoincrement": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    recorded_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
    device = Column(String(20), nullable=False)
    state = Column(String(10), nullable=False)
    source = Column(String(20), nullable=False, default="manual")
