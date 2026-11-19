from datetime import UTC, datetime

from sqlalchemy.orm import Session

from server.db.models import EdgeCommand, Installation


def expire_stale_commands(db: Session, now: datetime | None = None) -> int:
    """Переводит просроченные pending и running команды в timeout по API_CONTRACT п. 1.4.

    - pending старше 30 с -> timeout;
    - running одиночные старше 60 с -> timeout;
    - running глобальный SNAP старше (15 + 10*N) + 15 с -> timeout,
      где N — число активных установок.

    Возвращает количество обновленных команд.
    """
    if now is None:
        now = datetime.now(UTC)

    active_installations_count = (
        db.query(Installation).filter(Installation.is_archived.is_(False)).count()
    )

    updated_count = 0

    # 1. Running команды
    running_commands = (
        db.query(EdgeCommand).filter(EdgeCommand.status == "running").all()
    )
    for cmd in running_commands:
        if not cmd.started_at:
            continue
        cmd_started = (
            cmd.started_at
            if cmd.started_at.tzinfo
            else cmd.started_at.replace(tzinfo=UTC)
        )
        elapsed_sec = (now - cmd_started).total_seconds()
        if cmd.is_global and cmd.command_type == "SNAP":
            timeout_limit = (15 + 10 * active_installations_count) + 15
        else:
            timeout_limit = 60.0

        if elapsed_sec > timeout_limit:
            cmd.status = "timeout"
            updated_count += 1

    # 2. Pending команды (> 30 с)
    pending_commands = (
        db.query(EdgeCommand).filter(EdgeCommand.status == "pending").all()
    )
    for cmd in pending_commands:
        cmd_created = (
            cmd.created_at
            if cmd.created_at.tzinfo
            else cmd.created_at.replace(tzinfo=UTC)
        )
        if (now - cmd_created).total_seconds() > 30.0:
            cmd.status = "timeout"
            updated_count += 1

    if updated_count > 0:
        db.commit()

    return updated_count
