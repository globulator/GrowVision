import threading
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy.orm import Session

from server.db.models import (
    ClimateSensorReading,
    PeripheralLog,
    PhReading,
    SystemSetting,
)

EspDevice = Literal["water", "fan"]
EspAction = Literal["on", "off"]
ClimateMode = Literal["auto", "manual"]

ALLOWED_ESP_DEVICES: tuple[str, ...] = ("water", "fan")
ALLOWED_ESP_ACTIONS: tuple[str, ...] = ("on", "off")
ALLOWED_CLIMATE_MODES: tuple[str, ...] = ("auto", "manual")

ESP_ONLINE_TIMEOUT_SEC = 60.0
SCHEDULER_COOLDOWN_SEC = 30.0

KEY_CLIMATE_MODE = "climate_mode"
KEY_WATER_INTERVAL_HOURS = "water_interval_hours"
KEY_WATER_DURATION_MINUTES = "water_duration_minutes"
KEY_FAN_INTERVAL_HOURS = "fan_interval_hours"
KEY_FAN_DURATION_MINUTES = "fan_duration_minutes"
KEY_LAST_WATER_START = "last_water_start"
KEY_LAST_FAN_START = "last_fan_start"
KEY_WATER_STATE = "climate_water_state"
KEY_FAN_STATE = "climate_fan_state"
KEY_ESP_LAST_SEEN = "esp_last_seen_at"

DEFAULT_CLIMATE_MODE: ClimateMode = "manual"
DEFAULT_WATER_INTERVAL_HOURS = 10
DEFAULT_WATER_DURATION_MINUTES = 1
DEFAULT_FAN_INTERVAL_HOURS = 5
DEFAULT_FAN_DURATION_MINUTES = 1


@dataclass(frozen=True)
class QueuedEspCommand:
    """Команда в очереди Server -> ESP."""

    id: int
    device: EspDevice
    action: EspAction
    source: str
    created_at: datetime


class EspCommandQueue:
    """Потокобезопасная очередь команд для ESP8266 (только water и fan)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queue: deque[QueuedEspCommand] = deque()
        self._next_id = 1
        self._last_scheduler_enqueued_at: dict[str, datetime] = {}

    def enqueue(
        self,
        device: str,
        action: str,
        *,
        source: str = "manual",
        now: datetime | None = None,
    ) -> QueuedEspCommand:
        """Добавляет команду в очередь. Команда light для ESP запрещена."""
        if device not in ALLOWED_ESP_DEVICES:
            raise ValueError(
                f"Недопустимое устройство ESP '{device}'. Допустимы только: water, fan"
            )
        if action not in ALLOWED_ESP_ACTIONS:
            raise ValueError(
                f"Недопустимое действие '{action}'. Допустимы только: on, off"
            )
        ts = _ensure_utc(now or datetime.now(UTC))
        with self._lock:
            cmd = QueuedEspCommand(
                id=self._next_id,
                device=device,  # type: ignore[arg-type]
                action=action,  # type: ignore[arg-type]
                source=source,
                created_at=ts,
            )
            self._next_id += 1
            self._queue.append(cmd)
            if source == "auto":
                self._last_scheduler_enqueued_at[device] = ts
            return cmd

    def has_pending(self, device: str, action: str | None = None) -> bool:
        """Проверяет, есть ли уже ожидающая команда для устройства."""
        with self._lock:
            for item in self._queue:
                if item.device == device and (action is None or item.action == action):
                    return True
            return False

    def in_scheduler_cooldown(
        self,
        device: str,
        now: datetime,
        cooldown_sec: float = SCHEDULER_COOLDOWN_SEC,
    ) -> bool:
        """Проверяет анти-спам кулдаун планировщика для устройства."""
        ts = _ensure_utc(now)
        with self._lock:
            last_ts = self._last_scheduler_enqueued_at.get(device)
            if last_ts is None:
                return False
            return (ts - last_ts).total_seconds() < cooldown_sec

    def peek_payload(self) -> dict[str, Any]:
        """Возвращает первую команду строго в формате прошивки ESP8266."""
        with self._lock:
            if not self._queue:
                return {
                    "queue_size": -1,
                    "id": 0,
                    "device": "None",
                    "action": "off",
                }
            head = self._queue[0]
            return {
                "queue_size": len(self._queue) - 1,
                "id": head.id,
                "device": head.device,
                "action": head.action,
            }

    def ack(self, cmd_id: int) -> QueuedEspCommand | None:
        """Подтверждает выполнение головы очереди по id и удаляет её."""
        with self._lock:
            if not self._queue or self._queue[0].id != cmd_id:
                return None
            return self._queue.popleft()

    def size(self) -> int:
        """Текущий размер очереди команд."""
        with self._lock:
            return len(self._queue)

    def clear(self) -> None:
        """Очищает очередь и кулдауны (для тестов)."""
        with self._lock:
            self._queue.clear()
            self._next_id = 1
            self._last_scheduler_enqueued_at.clear()


esp_queue = EspCommandQueue()


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def format_utc_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return _ensure_utc(dt).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc_iso(raw: str | None) -> datetime | None:
    if not raw or not raw.strip():
        return None
    val = raw.strip()
    if val.endswith("Z"):
        val = val[:-1] + "+00:00"
    try:
        return _ensure_utc(datetime.fromisoformat(val))
    except ValueError:
        return None


def _get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if row is None:
        return default
    return row.value


def _set_setting(db: Session, key: str, value: str) -> None:
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if row is None:
        db.add(SystemSetting(key=key, value=value))
    else:
        row.value = value


def _get_positive_int(db: Session, key: str, default: int) -> int:
    raw = _get_setting(db, key, str(default))
    try:
        parsed = int(raw)
        return parsed if parsed >= 1 else default
    except ValueError:
        return default


def touch_esp_seen(db: Session, now: datetime | None = None) -> datetime:
    """Обновляет метку последней активности ESP в SQLite."""
    ts = _ensure_utc(now or datetime.now(UTC))
    _set_setting(db, KEY_ESP_LAST_SEEN, format_utc_iso(ts) or "")
    return ts


def get_esp_last_seen(db: Session) -> datetime | None:
    """Возвращает время последнего запроса ESP или None."""
    return parse_utc_iso(_get_setting(db, KEY_ESP_LAST_SEEN, ""))


def is_esp_online(
    db: Session,
    now: datetime | None = None,
    timeout_sec: float = ESP_ONLINE_TIMEOUT_SEC,
) -> bool:
    """Определяет статус ESP (онлайн, если была активность за последние timeout_sec)."""
    last_seen = get_esp_last_seen(db)
    if last_seen is None:
        return False
    ts = _ensure_utc(now or datetime.now(UTC))
    delta = (ts - last_seen).total_seconds()
    return delta <= timeout_sec


def get_climate_settings(db: Session) -> dict[str, Any]:
    """Возвращает актуальные настройки режима, расписания и состояния периферии."""
    raw_mode = _get_setting(db, KEY_CLIMATE_MODE, DEFAULT_CLIMATE_MODE)
    mode: ClimateMode = "auto" if raw_mode == "auto" else "manual"

    raw_water_state = _get_setting(db, KEY_WATER_STATE, "off")
    raw_fan_state = _get_setting(db, KEY_FAN_STATE, "off")

    return {
        "mode": mode,
        "water_interval_hours": _get_positive_int(
            db, KEY_WATER_INTERVAL_HOURS, DEFAULT_WATER_INTERVAL_HOURS
        ),
        "water_duration_minutes": _get_positive_int(
            db, KEY_WATER_DURATION_MINUTES, DEFAULT_WATER_DURATION_MINUTES
        ),
        "fan_interval_hours": _get_positive_int(
            db, KEY_FAN_INTERVAL_HOURS, DEFAULT_FAN_INTERVAL_HOURS
        ),
        "fan_duration_minutes": _get_positive_int(
            db, KEY_FAN_DURATION_MINUTES, DEFAULT_FAN_DURATION_MINUTES
        ),
        "last_water_start": _get_setting(db, KEY_LAST_WATER_START, "") or None,
        "last_fan_start": _get_setting(db, KEY_LAST_FAN_START, "") or None,
        "water_state": "on" if raw_water_state == "on" else "off",
        "fan_state": "on" if raw_fan_state == "on" else "off",
    }


def set_climate_mode(db: Session, mode: str) -> ClimateMode:
    """Переключает режим климата (auto / manual)."""
    if mode not in ALLOWED_CLIMATE_MODES:
        raise ValueError("Режим должен быть 'auto' или 'manual'")
    _set_setting(db, KEY_CLIMATE_MODE, mode)
    return mode  # type: ignore[return-value]


def update_climate_schedule(
    db: Session,
    *,
    water_interval_hours: int | None = None,
    water_duration_minutes: int | None = None,
    fan_interval_hours: int | None = None,
    fan_duration_minutes: int | None = None,
) -> dict[str, Any]:
    """Сохраняет параметры расписания полива и вентиляции (значения >= 1)."""
    updates: list[tuple[str, int | None, str]] = [
        (KEY_WATER_INTERVAL_HOURS, water_interval_hours, "water_interval_hours"),
        (
            KEY_WATER_DURATION_MINUTES,
            water_duration_minutes,
            "water_duration_minutes",
        ),
        (KEY_FAN_INTERVAL_HOURS, fan_interval_hours, "fan_interval_hours"),
        (KEY_FAN_DURATION_MINUTES, fan_duration_minutes, "fan_duration_minutes"),
    ]
    for key, val, label in updates:
        if val is None:
            continue
        if isinstance(val, bool) or not isinstance(val, int) or val < 1:
            raise ValueError(f"Параметр {label} должен быть целым числом >= 1")
        _set_setting(db, key, str(val))
    return get_climate_settings(db)


def record_sensor_reading(
    db: Session,
    temperature: float,
    humidity: float,
    *,
    now: datetime | None = None,
) -> ClimateSensorReading:
    """Сохраняет показания температуры и влажности от ESP и обновляет esp_last_seen."""
    ts = touch_esp_seen(db, now=now)
    reading = ClimateSensorReading(
        recorded_at=ts,
        temperature=float(temperature),
        humidity=float(humidity),
    )
    db.add(reading)
    db.commit()
    db.refresh(reading)
    return reading


def record_ph_reading(
    db: Session,
    level: float,
    *,
    recorded_at: datetime | None = None,
) -> PhReading:
    """Сохраняет ручной замер уровня pH (строго 0..14)."""
    val = float(level)
    if val < 0.0 or val > 14.0:
        raise ValueError("Уровень pH должен находиться в диапазоне от 0 до 14")
    ts = _ensure_utc(recorded_at or datetime.now(UTC))
    reading = PhReading(recorded_at=ts, level=val)
    db.add(reading)
    db.commit()
    db.refresh(reading)
    return reading


def enqueue_manual_command(
    db: Session,
    device: str,
    action: str,
    *,
    now: datetime | None = None,
) -> QueuedEspCommand:
    """Ставит ручную команду в очередь ESP. В режиме auto бросает PermissionError."""
    settings = get_climate_settings(db)
    if settings["mode"] == "auto":
        raise PermissionError(
            "Ручное управление недоступно в автоматическом режиме. Переключите режим на «Ручной»."
        )
    return esp_queue.enqueue(device, action, source="manual", now=now)


def confirm_esp_command(
    db: Session,
    cmd_id: int,
    *,
    now: datetime | None = None,
) -> QueuedEspCommand | None:
    """Подтверждает команду от ESP, обновляет состояние и пишет в peripheral_logs."""
    ts = touch_esp_seen(db, now=now)
    cmd = esp_queue.ack(cmd_id)
    if cmd is None:
        db.commit()
        return None

    state_key = KEY_WATER_STATE if cmd.device == "water" else KEY_FAN_STATE
    start_key = KEY_LAST_WATER_START if cmd.device == "water" else KEY_LAST_FAN_START

    _set_setting(db, state_key, cmd.action)
    if cmd.action == "on":
        _set_setting(db, start_key, format_utc_iso(ts) or "")

    db.add(
        PeripheralLog(
            recorded_at=ts,
            device=cmd.device,
            state=cmd.action,
            source=cmd.source,
        )
    )
    db.commit()
    return cmd


def run_climate_scheduler_step(
    db: Session,
    *,
    now: datetime | None = None,
) -> list[QueuedEspCommand]:
    """Один шаг планировщика полива и вентиляции (вызывается в фоне раз в ~5 с)."""
    ts = _ensure_utc(now or datetime.now(UTC))
    settings = get_climate_settings(db)
    if settings["mode"] != "auto":
        return []

    enqueued: list[QueuedEspCommand] = []
    db_changed = False

    device_specs: tuple[tuple[EspDevice, str, str, int, int], ...] = (
        (
            "water",
            KEY_LAST_WATER_START,
            settings["water_state"],
            settings["water_interval_hours"],
            settings["water_duration_minutes"],
        ),
        (
            "fan",
            KEY_LAST_FAN_START,
            settings["fan_state"],
            settings["fan_interval_hours"],
            settings["fan_duration_minutes"],
        ),
    )

    for device, start_key, state, interval_hours, duration_minutes in device_specs:
        if esp_queue.in_scheduler_cooldown(device, ts, SCHEDULER_COOLDOWN_SEC):
            continue

        last_start = parse_utc_iso(_get_setting(db, start_key, ""))

        if state == "on":
            elapsed_sec = (
                (ts - last_start).total_seconds()
                if last_start is not None
                else float(duration_minutes * 60)
            )
            if elapsed_sec >= duration_minutes * 60 and not esp_queue.has_pending(
                device, "off"
            ):
                cmd = esp_queue.enqueue(device, "off", source="auto", now=ts)
                enqueued.append(cmd)
        else:
            should_start = (
                last_start is None
                or (ts - last_start).total_seconds() >= interval_hours * 3600
            )
            if should_start and not esp_queue.has_pending(device, "on"):
                cmd = esp_queue.enqueue(device, "on", source="auto", now=ts)
                _set_setting(db, start_key, format_utc_iso(ts) or "")
                db_changed = True
                enqueued.append(cmd)

    if db_changed:
        db.commit()

    return enqueued


def get_climate_summary(
    db: Session,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Сводка состояния ESP, датчиков, pH, полива, вентиляции и расписания."""
    ts = _ensure_utc(now or datetime.now(UTC))
    settings = get_climate_settings(db)
    last_seen = get_esp_last_seen(db)

    latest_sensor = (
        db.query(ClimateSensorReading)
        .order_by(
            ClimateSensorReading.recorded_at.desc(), ClimateSensorReading.id.desc()
        )
        .first()
    )
    latest_ph = (
        db.query(PhReading)
        .order_by(PhReading.recorded_at.desc(), PhReading.id.desc())
        .first()
    )

    return {
        "esp_online": is_esp_online(db, now=ts),
        "esp_last_seen": format_utc_iso(last_seen),
        "queue_size": esp_queue.size(),
        "mode": settings["mode"],
        "temperature": latest_sensor.temperature if latest_sensor else None,
        "humidity": latest_sensor.humidity if latest_sensor else None,
        "sensors_updated_at": (
            format_utc_iso(latest_sensor.recorded_at) if latest_sensor else None
        ),
        "ph": latest_ph.level if latest_ph else None,
        "ph_updated_at": (format_utc_iso(latest_ph.recorded_at) if latest_ph else None),
        "water_state": settings["water_state"],
        "fan_state": settings["fan_state"],
        "water_pending": esp_queue.has_pending("water"),
        "fan_pending": esp_queue.has_pending("fan"),
        "schedule": {
            "water_interval_hours": settings["water_interval_hours"],
            "water_duration_minutes": settings["water_duration_minutes"],
            "fan_interval_hours": settings["fan_interval_hours"],
            "fan_duration_minutes": settings["fan_duration_minutes"],
        },
        "last_water_start": settings["last_water_start"],
        "last_fan_start": settings["last_fan_start"],
    }


def get_climate_history(
    db: Session,
    *,
    period: Literal["24h", "7d"] = "24h",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Возвращает историю показаний температуры, влажности, pH, полива и вентиляции."""
    ts = _ensure_utc(now or datetime.now(UTC))
    since = ts - (timedelta(days=7) if period == "7d" else timedelta(hours=24))

    sensors = (
        db.query(ClimateSensorReading)
        .filter(ClimateSensorReading.recorded_at >= since)
        .order_by(ClimateSensorReading.recorded_at.asc(), ClimateSensorReading.id.asc())
        .all()
    )
    ph_rows = (
        db.query(PhReading)
        .filter(PhReading.recorded_at >= since)
        .order_by(PhReading.recorded_at.asc(), PhReading.id.asc())
        .all()
    )
    logs = (
        db.query(PeripheralLog)
        .filter(PeripheralLog.recorded_at >= since)
        .order_by(PeripheralLog.recorded_at.asc(), PeripheralLog.id.asc())
        .all()
    )

    return {
        "period": period,
        "temperature": [
            {"timestamp": format_utc_iso(r.recorded_at), "value": r.temperature}
            for r in sensors
        ],
        "humidity": [
            {"timestamp": format_utc_iso(r.recorded_at), "value": r.humidity}
            for r in sensors
        ],
        "ph": [
            {"timestamp": format_utc_iso(r.recorded_at), "value": r.level}
            for r in ph_rows
        ],
        "water": [
            {
                "timestamp": format_utc_iso(r.recorded_at),
                "state": r.state,
                "value": 1 if r.state == "on" else 0,
                "source": r.source,
            }
            for r in logs
            if r.device == "water"
        ],
        "fan": [
            {
                "timestamp": format_utc_iso(r.recorded_at),
                "state": r.state,
                "value": 1 if r.state == "on" else 0,
                "source": r.source,
            }
            for r in logs
            if r.device == "fan"
        ],
    }
