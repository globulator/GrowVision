from datetime import UTC, datetime, timedelta


def compute_next_capture_at(
    last_captured_at: datetime | str | None,
    interval_hours: int = 1,
    now: datetime | None = None,
) -> datetime:
    """Вычисляет UTC-время следующей плановой съёмки с выравниванием по ровным часам.

    Пример: если текущее время 22:12 и интервал 2 ч -> следующий кадр в 23:00, затем 01:00, 03:00.
    Если на текущем ровном часе еще не снимали (например, ровно 22:00:00 и last < 22:00:00), то 22:00:00.
    """
    if now is None:
        now = datetime.now(UTC)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    interval_hours = max(1, int(interval_hours))

    # Парсинг last_captured_at, если передана строка
    last_dt: datetime | None = None
    if isinstance(last_captured_at, str):
        try:
            clean_str = last_captured_at.replace("Z", "+00:00")
            last_dt = datetime.fromisoformat(clean_str)
        except (ValueError, TypeError):
            last_dt = None
    elif isinstance(last_captured_at, datetime):
        last_dt = last_captured_at

    if last_dt is not None and last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=UTC)

    # Ближайший следующий ровный час от now
    current_hour_slot = now.replace(minute=0, second=0, microsecond=0)
    if now.minute == 0 and now.second == 0 and now.microsecond == 0:
        next_hour_slot = current_hour_slot
    else:
        next_hour_slot = current_hour_slot + timedelta(hours=1)

    if last_dt is None:
        return next_hour_slot

    # Выравниваем последнюю съемку по началу часа
    last_slot = last_dt.replace(minute=0, second=0, microsecond=0)

    # Если последняя съемка была в текущем часовом слоте (или в будущем),
    # то следующий кадр строго через interval_hours от last_slot
    if last_slot >= current_hour_slot:
        return last_slot + timedelta(hours=interval_hours)

    # Если последняя съемка была раньше: проверяем шаг слотов
    cand = last_slot + timedelta(hours=interval_hours)
    if cand > now:
        return cand

    # Если съемка была давно: берем ближайший следующий ровный час
    return next_hour_slot
