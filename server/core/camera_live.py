"""Сервис Live-стриминга камер и проксирования MJPEG-потока от ustreamer."""

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from server.core.live_manager import live_manager
from server.core.rgb import apply_rgb_to_jpeg_bytes, clamp_rgb_dict
from server.db.models import Camera, EdgeCommand, SystemSetting


def _default_normalize_seg_mode(raw_val: Any) -> str:
    """Нормализует режим сегментации по умолчанию к 'auto', 'onnx' или 'hsv'."""
    val = str(raw_val or "auto").strip().lower()
    if val in ("onnx", "hsv"):
        return val
    return "auto"


class CameraLiveService:
    """Инкапсулирует управление Live-сессиями, проксирование ustreamer и выдачу кадров."""

    @staticmethod
    async def probe_ustreamer_online_state(session: Any) -> None:
        """Проверяет /state у ustreamer при прямом проксировании для выявления отключения камеры."""
        if not session.stream_url or not session.is_active or session.is_paused:
            return
        now_m = time.monotonic()
        if (now_m - session._last_state_probe_at) < 0.8:
            return
        session._last_state_probe_at = now_m
        state_url = f"{session.stream_url.rstrip('/')}/state"
        with contextlib.suppress(Exception):
            async with httpx.AsyncClient(timeout=0.6, trust_env=False) as http_client:
                resp = await http_client.get(state_url)
                if resp.status_code == 200:
                    data = resp.json()
                    res = (
                        data.get("result")
                        if isinstance(data.get("result"), dict)
                        else data
                    )
                    src = (
                        res.get("source")
                        if isinstance(res, dict) and isinstance(res.get("source"), dict)
                        else None
                    )
                    if src is not None and "online" in src:
                        is_on = bool(src["online"])
                        if not is_on and (
                            session.last_frame_at == 0.0
                            and (time.time() - getattr(session, "started_at", 0.0))
                            < 4.0
                        ):
                            # Камера прогревается при старте — не показываем ложный офлайн до первого кадра
                            return
                        session.device_online = is_on
                        session.stream_error = None if is_on else "Камера отключена"

    @staticmethod
    async def proxy_ustreamer_frames(
        session: Any,
        shutdown_event: asyncio.Event | None = None,
    ) -> AsyncIterator[bytes]:
        """Читает MJPEG-поток напрямую из ustreamer и выдаёт отдельные JPEG-кадры."""
        shut_evt = shutdown_event or live_manager.shutdown_event
        if (
            shut_evt.is_set()
            or live_manager.is_shutting_down
            or not session.stream_url
            or session.mode != "raw"
            or not session.device_online
        ):
            return
        stream_url = f"{session.stream_url.rstrip('/')}/stream"
        timeout = httpx.Timeout(connect=2.0, read=6.0, write=5.0, pool=2.0)
        buf = bytearray()
        last_frame_t = time.perf_counter()

        async with (
            httpx.AsyncClient(timeout=timeout, trust_env=False) as http_client,
            http_client.stream("GET", stream_url) as resp,
        ):
            if resp.status_code != 200:
                return
            byte_iter = resp.aiter_bytes(chunk_size=32768).__aiter__()
            while (
                not shut_evt.is_set()
                and not live_manager.is_shutting_down
                and session.is_active
                and session.mode == "raw"
                and session.device_online
            ):
                if session.is_paused:
                    buf.clear()
                    return
                next_task = asyncio.create_task(anext(byte_iter))
                shut_task = asyncio.create_task(shut_evt.wait())
                try:
                    done, pending = await asyncio.wait(
                        {next_task, shut_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await task
                except BaseException:
                    for task in (next_task, shut_task):
                        if not task.done():
                            task.cancel()
                            with contextlib.suppress(asyncio.CancelledError, Exception):
                                await task
                    raise

                if (
                    shut_task in done
                    or shut_evt.is_set()
                    or live_manager.is_shutting_down
                ):
                    break
                try:
                    chunk = next_task.result()
                except StopAsyncIteration:
                    break

                buf.extend(chunk)
                # Защита от разрастания буфера на некорректном потоке
                if len(buf) > 4 * 1024 * 1024:
                    buf.clear()
                    continue
                while True:
                    if shut_evt.is_set() or live_manager.is_shutting_down:
                        return
                    start = buf.find(b"\xff\xd8")
                    if start == -1:
                        if len(buf) > 2:
                            del buf[:-2]
                        break
                    if start > 0:
                        del buf[:start]
                    end = buf.find(b"\xff\xd9", 2)
                    if end == -1:
                        break
                    frame = bytes(buf[: end + 2])
                    del buf[: end + 2]
                    now_t = time.perf_counter()
                    lat_ms = max(1, min(500, int((now_t - last_frame_t) * 1000)))
                    last_frame_t = now_t
                    session.latest_raw_frame = frame
                    session.latest_frame = frame
                    session.record_proxied_frame(latency_ms=lat_ms)
                    yield frame

    @staticmethod
    async def wait_for_frame_or_shutdown(
        session: Any,
        shut_evt: asyncio.Event,
        last_frame_id: int,
        timeout: float,
    ) -> None:
        """Ожидает новый кадр в condition или мгновенно просыпается при shutdown_event."""
        if shut_evt.is_set() or live_manager.is_shutting_down or not session.is_active:
            return

        async def _wait_cond() -> None:
            async with session.condition:
                if (
                    session.frame_counter == last_frame_id
                    and session.is_active
                    and not shut_evt.is_set()
                    and not live_manager.is_shutting_down
                ):
                    await session.condition.wait()

        cond_task = asyncio.create_task(_wait_cond())
        shut_task = asyncio.create_task(shut_evt.wait())
        try:
            done, pending = await asyncio.wait(
                {cond_task, shut_task},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            if cond_task in done:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    cond_task.result()
        except BaseException:
            for task in (cond_task, shut_task):
                if not task.done():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task
            raise

    @classmethod
    async def mjpeg_generator(
        cls,
        hardware_id: str,
        shutdown_event: asyncio.Event | None = None,
        proxy_frames_fn: Callable[..., AsyncIterator[bytes]] | None = None,
    ) -> AsyncIterator[bytes]:
        """Низколатентный генератор потока MJPEG (прямое проксирование ustreamer или push-буфер)."""
        shut_evt = shutdown_event or live_manager.shutdown_event
        if shut_evt.is_set() or live_manager.is_shutting_down:
            return

        proxy_fn = proxy_frames_fn or cls.proxy_ustreamer_frames
        session = live_manager.get_or_create(hardware_id)
        session.active_subscribers += 1
        session.is_active = True
        last_frame_id = -1
        next_proxy_retry_at = 0.0
        proxy_backoff = 0.5

        try:
            while (
                session.is_active
                and not shut_evt.is_set()
                and not live_manager.is_shutting_down
            ):
                # 1. В режиме 'raw' пробуем прямое проксирование от ustreamer
                now_m = time.monotonic()
                if (
                    session.mode == "raw"
                    and session.device_online
                    and session.stream_url
                    and not session.is_paused
                    and now_m >= next_proxy_retry_at
                ):
                    got_any = False
                    with contextlib.suppress(Exception):
                        proxy_stream = (
                            proxy_fn(session)
                            if shut_evt is live_manager.shutdown_event
                            else proxy_fn(session, shutdown_event=shut_evt)
                        )
                        async for frame in proxy_stream:
                            if shut_evt.is_set() or live_manager.is_shutting_down:
                                break
                            got_any = True
                            proxy_backoff = 0.5
                            next_proxy_retry_at = 0.0
                            yield (
                                b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                                + frame
                                + b"\r\n"
                            )
                    if shut_evt.is_set() or live_manager.is_shutting_down:
                        break
                    if (
                        not got_any
                        and session.is_active
                        and not session.is_paused
                        and session.mode == "raw"
                    ):
                        next_proxy_retry_at = time.monotonic() + proxy_backoff
                        proxy_backoff = min(5.0, proxy_backoff * 2.0)

                # 2. Режим 'annotated' или fallback через push-буфер от Edge-агента
                if session.frame_counter == last_frame_id:
                    wait_timeout = (
                        0.3 if (session.stream_url and session.mode == "raw") else 0.5
                    )
                    await cls.wait_for_frame_or_shutdown(
                        session, shut_evt, last_frame_id, wait_timeout
                    )
                if (
                    shut_evt.is_set()
                    or live_manager.is_shutting_down
                    or not session.is_active
                ):
                    break

                async with session.condition:
                    frame = (
                        session.latest_annotated_frame
                        if session.mode == "annotated"
                        else session.latest_frame
                    )
                    last_frame_id = session.frame_counter

                if frame is not None and session.device_online:
                    yield (
                        b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                    )
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(shut_evt.wait(), timeout=0.02)
        except asyncio.CancelledError:
            pass
        finally:
            session.active_subscribers = max(0, session.active_subscribers - 1)

    @staticmethod
    def start_session(
        db: Session,
        cam: Camera,
        cur_fmt: str,
        cur_res: str,
        normalize_seg_fn: Callable[[Any], str] = _default_normalize_seg_mode,
    ) -> dict[str, Any]:
        """Инициализирует Live-сессию камеры и ставит команду START_LIVE для Edge-агента."""
        session = live_manager.get_or_create(cam.hardware_id, camera_id=cam.id)
        session.is_active = True
        session.is_paused = False
        session.mode = "raw"
        session.device_online = True
        session.color_marker_detected = False
        session.stream_error = None
        session.stream_url = None
        session.latest_frame = None
        session.latest_raw_frame = None
        session.latest_annotated_frame = None
        session.last_frame_at = 0.0
        session.last_proxied_at = 0.0
        session._last_state_probe_at = 0.0
        session.fps = 0.0
        session.latency_ms = 0
        session._frame_times.clear()
        session.last_heartbeat = time.time()
        session.started_at = time.time()
        session.resolution = cur_res

        saved_rgb = {"r": 1.0, "g": 1.0, "b": 1.0}
        rgb_setting = (
            db.query(SystemSetting)
            .filter(SystemSetting.key == f"camera_rgb_{cam.hardware_id}")
            .first()
        )
        if rgb_setting and rgb_setting.value:
            with contextlib.suppress(Exception):
                saved_rgb = clamp_rgb_dict(json.loads(rgb_setting.value))

        seg_setting = (
            db.query(SystemSetting)
            .filter(SystemSetting.key == f"camera_seg_mode_{cam.hardware_id}")
            .first()
        )
        saved_seg_mode = normalize_seg_fn(seg_setting.value if seg_setting else None)
        session.set_segmentation_mode(saved_seg_mode)

        parts = cur_res.split("x")
        w = int(parts[0]) if len(parts) == 2 else 1280
        h = int(parts[1]) if len(parts) == 2 else 720

        cmd = EdgeCommand(
            command_type="START_LIVE",
            installation_id=cam.installation_id,
            is_global=cam.installation_id is None,
            payload=json.dumps(
                {
                    "camera_hardware_id": cam.hardware_id,
                    "width": w,
                    "height": h,
                    "pixel_format": cur_fmt,
                    "rgb": saved_rgb,
                    "segmentation_mode": saved_seg_mode,
                }
            ),
            status="pending",
        )
        db.add(cmd)
        db.commit()

        return {
            "status": "started",
            "camera_id": cam.id,
            "hardware_id": cam.hardware_id,
            "resolution": f"{w}x{h}",
            "format": cur_fmt,
            "segmentation_mode": saved_seg_mode,
        }

    @staticmethod
    def stop_session(db: Session, cam: Camera) -> dict[str, Any]:
        """Останавливает Live-сессию камеры и ставит команду STOP_LIVE для Edge-агента."""
        live_manager.stop(cam.hardware_id)

        cmd = EdgeCommand(
            command_type="STOP_LIVE",
            installation_id=cam.installation_id,
            is_global=cam.installation_id is None,
            payload=json.dumps({"camera_hardware_id": cam.hardware_id}),
            status="pending",
        )
        db.add(cmd)
        db.commit()

        return {
            "status": "stopped",
            "camera_id": cam.id,
            "hardware_id": cam.hardware_id,
        }

    @staticmethod
    async def set_mode(cam: Camera, mode: str) -> dict[str, Any]:
        """Переключает режим Live-стрима ('raw' / 'annotated') и будит подписчиков."""
        session = live_manager.get_or_create(cam.hardware_id, camera_id=cam.id)
        session.set_mode(mode)
        async with session.condition:
            session.condition.notify_all()
        return {
            "status": "ok",
            "camera_id": cam.id,
            "hardware_id": cam.hardware_id,
            "mode": session.mode,
        }

    @classmethod
    async def get_status_payload(
        cls,
        db: Session,
        cam: Camera,
        cur_res: str,
        normalize_seg_fn: Callable[[Any], str] = _default_normalize_seg_mode,
        probe_fn: Callable[[Any], Any] | None = None,
    ) -> dict[str, Any]:
        """Возвращает словарь статуса Live-сессии (активной или неактивной)."""
        session = live_manager.get(cam.hardware_id)
        if not session:
            seg_setting = (
                db.query(SystemSetting)
                .filter(SystemSetting.key == f"camera_seg_mode_{cam.hardware_id}")
                .first()
            )
            saved_seg_mode = normalize_seg_fn(
                seg_setting.value if seg_setting else None
            )
            return {
                "hardware_id": cam.hardware_id,
                "camera_id": cam.id,
                "is_active": False,
                "is_paused": False,
                "mode": "raw",
                "segmentation_mode": saved_seg_mode,
                "device_online": True,
                "color_marker_detected": False,
                "stream_error": None,
                "fps": 0.0,
                "latency_ms": 0,
                "resolution": cur_res,
                "active_subscribers": 0,
            }
        probe = probe_fn or cls.probe_ustreamer_online_state
        await probe(session)
        return session.to_status_dict()

    @staticmethod
    async def capture_snapshot_bytes(
        cam: Camera,
        r: float | None = None,
        g: float | None = None,
        b: float | None = None,
    ) -> bytes:
        """Получает свежий JPEG-снимок из Live-сессии или ustreamer с применением RGB-фильтра."""
        session = live_manager.get(cam.hardware_id)
        frame: bytes | None = None

        if session:
            is_fresh = session.latest_frame is not None and (
                session.is_paused or (time.time() - session.last_frame_at) <= 1.5
            )
            if is_fresh:
                frame = session.latest_frame
            elif session.stream_url and not session.is_paused:
                snap_url = f"{session.stream_url.rstrip('/')}/snapshot"
                with contextlib.suppress(Exception):
                    async with httpx.AsyncClient(
                        timeout=3.0, trust_env=False
                    ) as http_client:
                        resp = await http_client.get(snap_url)
                        if resp.status_code == 200 and resp.content:
                            frame = resp.content
                            session.latest_frame = frame
                            session.last_frame_at = time.time()

            if frame is None and session.latest_frame:
                frame = session.latest_frame

        if not frame:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Live-стрим недоступен или кадр ещё не получен",
            )

        if r is not None or g is not None or b is not None:
            frame = apply_rgb_to_jpeg_bytes(
                frame,
                r=r if r is not None else 1.0,
                g=g if g is not None else 1.0,
                b=b if b is not None else 1.0,
            )
        return frame


camera_live_service = CameraLiveService()
probe_ustreamer_online_state = camera_live_service.probe_ustreamer_online_state
proxy_ustreamer_frames = camera_live_service.proxy_ustreamer_frames
wait_for_frame_or_shutdown = camera_live_service.wait_for_frame_or_shutdown
mjpeg_generator = camera_live_service.mjpeg_generator

__all__ = [
    "CameraLiveService",
    "camera_live_service",
    "mjpeg_generator",
    "probe_ustreamer_online_state",
    "proxy_ustreamer_frames",
    "wait_for_frame_or_shutdown",
]
