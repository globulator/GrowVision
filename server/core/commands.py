"""Ожидание выполнения команд Edge-агента через asyncio.Event."""

import asyncio
import time

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from server.core.state import app_state
from server.db.models import EdgeCommand


async def wait_command_result(
    cmd_id: int, timeout_sec: float, db: Session
) -> EdgeCommand:
    """Ожидает завершения команды RPi с таймаутом. Сразу 504 при timeout."""
    event = app_state.register_command_event(cmd_id)
    deadline = time.monotonic() + timeout_sec
    try:
        while True:
            event.clear()
            db.expire_all()
            cmd = db.query(EdgeCommand).filter(EdgeCommand.id == cmd_id).first()
            if not cmd:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Команда не найдена",
                )
            if cmd.status == "timeout":
                raise HTTPException(
                    status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                    detail="Превышено время ожидания ответа Raspberry Pi",
                )
            if cmd.status in ("done", "error"):
                return cmd

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break

            try:
                await asyncio.wait_for(event.wait(), timeout=min(0.2, remaining))
            except TimeoutError:
                # Уступаем управление loop и сохраняем совместимость с тестами,
                # которые перехватывают asyncio.sleep для имитации ответа в БД.
                await asyncio.sleep(0)

        # По истечении ожидания
        db.expire_all()
        cmd = db.query(EdgeCommand).filter(EdgeCommand.id == cmd_id).first()
        if not cmd:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Команда не найдена",
            )
        if cmd.status in ("done", "error"):
            return cmd
        if cmd.status == "pending":
            cmd.status = "timeout"
            db.commit()

        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Превышено время ожидания ответа Raspberry Pi",
        )
    finally:
        app_state.unregister_command_event(cmd_id, event)
