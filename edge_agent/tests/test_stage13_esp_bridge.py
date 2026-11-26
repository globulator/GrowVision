import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from edge_agent.config import load_config
from edge_agent.esp_bridge import EdgeEspBridgeRunner


def _recv_http_response(sock: socket.socket) -> tuple[bytes, bytes]:
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        assert chunk, "Соединение закрыто до окончания заголовков"
        buf += chunk
    head, rest = buf.split(b"\r\n\r\n", 1)
    content_length = 0
    for line in head.decode("ascii").split("\r\n")[1:]:
        if line.lower().startswith("content-length:"):
            content_length = int(line.split(":", 1)[1].strip())
    while len(rest) < content_length:
        chunk = sock.recv(4096)
        assert chunk, "Соединение закрыто до окончания тела"
        rest += chunk
    return head, rest[:content_length]


def test_edge_config_esp_bridge_options(tmp_path, monkeypatch):
    monkeypatch.delenv("ESP_BRIDGE_ENABLED", raising=False)
    monkeypatch.delenv("ESP_BRIDGE_PORT", raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "SERVER_URL=http://127.0.0.1:8000\n"
        "EDGE_API_KEY=k\n"
        "ESP_BRIDGE_ENABLED=true\n"
        "ESP_BRIDGE_PORT=18080\n",
        encoding="utf-8",
    )
    cfg = load_config(env_file)
    assert cfg.esp_bridge_enabled is True
    assert cfg.esp_bridge_port == 18080


def test_esp_bridge_raw_firmware_protocol_over_keepalive():
    received: list[tuple[str, str, bytes]] = []

    class FakeServerHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002
            return

        def do_GET(self):  # noqa: N802
            received.append(("GET", self.path, b""))
            if self.path == "/ping":
                body = b'{"status":"ok"}'
            elif self.path == "/api/esp/command":
                body = json.dumps(
                    {"queue_size": 0, "id": 7, "device": "water", "action": "on"}
                ).encode("utf-8")
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            received.append(("POST", self.path, body))
            resp = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

    http_server = ThreadingHTTPServer(("127.0.0.1", 0), FakeServerHandler)
    srv_port = http_server.server_address[1]
    srv_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
    srv_thread.start()

    runner = EdgeEspBridgeRunner(
        server_url=f"http://127.0.0.1:{srv_port}",
        host="127.0.0.1",
        port=0,
    )
    try:
        bridge_port = runner.start()
        with socket.create_connection(("127.0.0.1", bridge_port), timeout=5.0) as sock:
            # 1. GET /ping без заголовка Host (как в main.cpp) -> пустое тело Content-Length: 0
            sock.sendall(b"GET /ping HTTP/1.1\r\nConnection: keep-alive\r\n\r\n")
            head, body = _recv_http_response(sock)
            assert b"HTTP/1.1 200 OK" in head
            assert b"Content-Length: 0" in head
            assert body == b""

            # 2. На том же keep-alive сокете: GET /api/esp/command -> точный регистр Content-Length:
            sock.sendall(
                b"GET /api/esp/command HTTP/1.1\r\nConnection: keep-alive\r\n\r\n"
            )
            head, body = _recv_http_response(sock)
            assert b"HTTP/1.1 200 OK" in head
            assert f"Content-Length: {len(body)}".encode("ascii") in head
            assert json.loads(body.decode("utf-8")) == {
                "queue_size": 0,
                "id": 7,
                "device": "water",
                "action": "on",
            }

            # 3. POST /api/esp/sensors без Host
            sensors_body = b'{"temperature":24.5,"humidity":58.1}'
            sock.sendall(
                b"POST /api/esp/sensors HTTP/1.1\r\n"
                b"Content-Type: application/json\r\n"
                + f"Content-Length: {len(sensors_body)}\r\n".encode("ascii")
                + b"Connection: keep-alive\r\n\r\n"
                + sensors_body
            )
            head, body = _recv_http_response(sock)
            assert b"HTTP/1.1 200 OK" in head
            assert b"Content-Length: 0" in head

            # 4. POST /api/esp/command (ACK) без Host
            ack_body = b'{"id":7}'
            sock.sendall(
                b"POST /api/esp/command HTTP/1.1\r\n"
                b"Content-Type: application/json\r\n"
                + f"Content-Length: {len(ack_body)}\r\n".encode("ascii")
                + b"Connection: keep-alive\r\n\r\n"
                + ack_body
            )
            head, body = _recv_http_response(sock)
            assert b"HTTP/1.1 200 OK" in head
            assert b"Content-Length: 0" in head

            # 5. Чужой путь режется 404 и не доходит до сервера
            sock.sendall(
                b"GET /api/installations HTTP/1.1\r\nConnection: keep-alive\r\n\r\n"
            )
            head, _ = _recv_http_response(sock)
            assert b"HTTP/1.1 404" in head

        assert [r[:2] for r in received] == [
            ("GET", "/ping"),
            ("GET", "/api/esp/command"),
            ("POST", "/api/esp/sensors"),
            ("POST", "/api/esp/command"),
        ]
        assert json.loads(received[2][2].decode("utf-8")) == {
            "temperature": 24.5,
            "humidity": 58.1,
        }
        assert json.loads(received[3][2].decode("utf-8")) == {"id": 7}
    finally:
        runner.stop()
        http_server.shutdown()
        http_server.server_close()
