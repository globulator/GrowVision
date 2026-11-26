#!/usr/bin/env python3
"""Отправка конфигурации Wi-Fi и адреса сервера в ESP8266 по TCP (порт 7931).

Перед запуском:
1. Дважды нажмите кнопку на коробочке ESP (включится точка доступа ESP_CONIG / 34670000 на 60 сек).
2. Подключитесь к Wi-Fi сети ESP_CONIG.
3. Запустите:
   python3 scripts/configure_esp.py --ssid "WiFi" --password "PASS" --host "192.168.1.10" --port 8080
"""

import argparse
import socket
import struct

MAGIC = b"\xDE\xAD\xBE\xEF"


def build_config_packet(
    new_ssid: str,
    new_password: str,
    new_host: str,
    new_port: int,
) -> bytes:
    """Формирует бинарный пакет конфигурации, совместимый с прошивкой ESP8266."""
    ssid_bytes = new_ssid.encode("utf-8")
    pass_bytes = new_password.encode("utf-8")
    host_bytes = new_host.encode("utf-8")

    if len(ssid_bytes) > 32:
        raise ValueError("SSID слишком длинный (максимум 32 байта)")
    if len(pass_bytes) > 64:
        raise ValueError("Пароль слишком длинный (максимум 64 байта)")
    if len(host_bytes) > 64:
        raise ValueError("Host слишком длинный (максимум 64 байта)")
    if not (1 <= new_port <= 65535):
        raise ValueError("Порт должен быть в диапазоне 1..65535")

    return (
        MAGIC
        + struct.pack("B", len(ssid_bytes))
        + ssid_bytes
        + struct.pack("B", len(pass_bytes))
        + pass_bytes
        + struct.pack("B", len(host_bytes))
        + host_bytes
        + struct.pack("<H", new_port)
    )


def send_config_packet(
    server_ip: str,
    server_port: int,
    new_ssid: str,
    new_password: str,
    new_host: str,
    new_port: int,
) -> None:
    packet = build_config_packet(new_ssid, new_password, new_host, new_port)
    with socket.create_connection((server_ip, server_port), timeout=5.0) as sock:
        sock.sendall(packet)
    print(f"Пакет отправлен на {server_ip}:{server_port}")
    print(f"  SSID: {new_ssid}")
    print(f"  Host: {new_host}:{new_port}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Настройка Wi-Fi и адреса сервера для коробочки ESP8266"
    )
    parser.add_argument("--server-host", default="192.168.4.1")
    parser.add_argument("--server-port", type=int, default=7931)
    parser.add_argument("--ssid", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    send_config_packet(
        args.server_host,
        args.server_port,
        args.ssid,
        args.password,
        args.host,
        args.port,
    )


if __name__ == "__main__":
    main()
