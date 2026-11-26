import asyncio
import contextlib
import threading
import urllib.error
import urllib.request

from shared.esp_bridge import EspTcpBridge
from shared.logger import get_logger

logger = get_logger("edge_agent.esp_bridge")


def _forward_http_sync(
    server_url: str,
    method: str,
    path: str,
    body: bytes,
    timeout_sec: float = 5.0,
) -> tuple[int, bytes]:
    """Синхронно проксирует ESP-запрос на центральный сервер GrowVision (без системного прокси)."""
    url = f"{server_url.rstrip('/')}{path}"
    headers: dict[str, str] = {}
    data: bytes | None = None
    if method == "POST":
        headers["Content-Type"] = "application/json"
        data = body

    req = urllib.request.Request(url=url, data=data, headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout_sec) as resp:
            return int(resp.status), resp.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Ошибка проксирования ESP %s %s: %s", method, path, exc)
        return 502, b""


class EdgeEspBridgeRunner:
    """Управляет локальным ESP-мостом в отдельном фоновом asyncio-потоке."""

    def __init__(
        self,
        *,
        server_url: str,
        host: str = "0.0.0.0",
        port: int = 8080,
        timeout_sec: float = 5.0,
    ) -> None:
        self._server_url = server_url.rstrip("/")
        self._host = host
        self._port = port
        self._timeout_sec = timeout_sec
        self._loop: asyncio.AbstractEventLoop | None = None
        self._bridge: EspTcpBridge | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._start_error: Exception | None = None
        self._bound_port: int = port

    @property
    def bound_port(self) -> int:
        return self._bound_port

    def start(self) -> int:
        """Запускает мост и ждёт открытия сокета (возвращает фактический порт)."""
        if self._thread is not None and self._thread.is_alive():
            return self._bound_port

        self._ready.clear()
        self._start_error = None
        self._thread = threading.Thread(
            target=self._run_loop,
            name="edge-esp-bridge",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait(timeout=5.0)
        if self._start_error is not None:
            raise self._start_error
        return self._bound_port

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop

        async def _forward(method: str, path: str, body: bytes) -> tuple[int, bytes]:
            return await asyncio.to_thread(
                _forward_http_sync,
                self._server_url,
                method,
                path,
                body,
                self._timeout_sec,
            )

        bridge = EspTcpBridge(
            host=self._host,
            port=self._port,
            forward_fn=_forward,
        )
        self._bridge = bridge

        try:
            loop.run_until_complete(bridge.start())
            self._bound_port = bridge.bound_port
            logger.info(
                "Локальный ESP-мост запущен на %s:%s -> %s",
                self._host,
                self._bound_port,
                self._server_url,
            )
            self._ready.set()
            loop.run_forever()
        except Exception as exc:  # noqa: BLE001
            self._start_error = exc
            self._ready.set()
        finally:
            with contextlib.suppress(Exception):
                loop.run_until_complete(bridge.stop())
            loop.close()
            self._loop = None

    def stop(self) -> None:
        """Корректно останавливает ESP-мост и фоновый поток."""
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
