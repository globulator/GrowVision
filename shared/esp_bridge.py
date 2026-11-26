import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from http import HTTPStatus

ALLOWED_ESP_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/ping"),
        ("POST", "/api/esp/sensors"),
        ("GET", "/api/esp/command"),
        ("POST", "/api/esp/command"),
    }
)

MAX_REQUEST_BODY_BYTES = 65_536

ForwardFn = Callable[[str, str, bytes], Awaitable[tuple[int, bytes]]]


def _status_phrase(code: int) -> str:
    try:
        return HTTPStatus(code).phrase
    except ValueError:
        return "OK"


def build_esp_http_response(
    status_code: int,
    body: bytes = b"",
    *,
    content_type: str = "application/json",
) -> bytes:
    """Формирует HTTP/1.1 ответ с точным регистром Content-Length для прошивки ESP8266."""
    lines = [f"HTTP/1.1 {status_code} {_status_phrase(status_code)}"]
    if body:
        lines.append(f"Content-Type: {content_type}")
    lines.append(f"Content-Length: {len(body)}")
    lines.append("Connection: keep-alive")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii") + body


class EspTcpBridge:
    """Асинхронный TCP-адаптер для «сырых» HTTP/1.1-запросов прошивки ESP8266."""

    def __init__(
        self,
        *,
        host: str = "0.0.0.0",
        port: int = 8080,
        forward_fn: ForwardFn,
        idle_timeout_sec: float = 30.0,
    ) -> None:
        self.host = host
        self.port = port
        self._forward_fn = forward_fn
        self._idle_timeout_sec = idle_timeout_sec
        self._server: asyncio.AbstractServer | None = None

    @property
    def bound_port(self) -> int:
        if self._server is None or not self._server.sockets:
            return self.port
        return int(self._server.sockets[0].getsockname()[1])

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_client, host=self.host, port=self.port, reuse_address=True
        )

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        with contextlib.suppress(Exception):
            await self._server.wait_closed()
        self._server = None

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            while True:
                try:
                    raw_line = await asyncio.wait_for(
                        reader.readline(), timeout=self._idle_timeout_sec
                    )
                except TimeoutError:
                    break
                if not raw_line:
                    break

                request_line = raw_line.decode("latin-1").strip()
                if not request_line:
                    continue

                parts = request_line.split(" ")
                if len(parts) < 2:
                    writer.write(build_esp_http_response(400))
                    await writer.drain()
                    break

                method, path = parts[0].upper(), parts[1].split("?", 1)[0]
                content_length, bad_headers = 0, False

                while True:
                    try:
                        hdr_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                    except TimeoutError:
                        bad_headers = True
                        break
                    if not hdr_line:
                        bad_headers = True
                        break
                    decoded = hdr_line.decode("latin-1").strip()
                    if decoded == "":
                        break
                    if ":" in decoded:
                        k, v = decoded.split(":", 1)
                        if k.strip().lower() == "content-length":
                            try:
                                content_length = int(v.strip())
                            except ValueError:
                                bad_headers = True
                                break

                if bad_headers or content_length < 0:
                    writer.write(build_esp_http_response(400))
                    await writer.drain()
                    break
                if content_length > MAX_REQUEST_BODY_BYTES:
                    writer.write(build_esp_http_response(413))
                    await writer.drain()
                    break

                body = b""
                if content_length > 0:
                    try:
                        body = await asyncio.wait_for(
                            reader.readexactly(content_length), timeout=5.0
                        )
                    except (TimeoutError, asyncio.IncompleteReadError):
                        break

                if (method, path) not in ALLOWED_ESP_ROUTES:
                    writer.write(build_esp_http_response(404))
                    await writer.drain()
                    continue

                try:
                    status_code, resp_body = await self._forward_fn(method, path, body)
                except Exception:  # noqa: BLE001
                    status_code, resp_body = 502, b""

                if (method, path) != ("GET", "/api/esp/command"):
                    resp_body = b""

                writer.write(build_esp_http_response(status_code, resp_body))
                await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
