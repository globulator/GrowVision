# Полный справочник команд GrowVision

Все команды ниже рассчитаны на запуск из корня репозитория (`cd ~/study/GrowVision`), если не указано иное.

## 1. Сервер (FastAPI + Web UI + ESP TCP-адаптер)

### 1.1. Запуск в тестовом режиме (Mock БД + адаптер ESP8266)

Используется для локальной разработки и ручной проверки на ноутбуке. Поднимает изолированную БД `growvision_mock.db`, Web UI на порту `8000` и TCP-мост для прошивки ESP8266 на порту `8080`.

```
cd ~/study/GrowVision && \
MOCK_MODE=true ESP_ADAPTER_ENABLED=true PYTHONPATH=. \
server/.venv/bin/python -m server.main

```

### 1.2. Запуск в боевом режиме (Production / Raspberry Pi)

Использует основную рабочую БД `growvision.db` и настройки из `server/.env`.

```
cd ~/study/GrowVision && \
MOCK_MODE=false ESP_ADAPTER_ENABLED=true PYTHONPATH=. \
server/.venv/bin/python -m server.main

```

### 1.3. Переменные окружения и флаги сервера

| Переменная / Флаг | Значения по умолчанию | Описание | 
| ----- | ----- | ----- | 
| `MOCK_MODE` | `false` | `true` — переключает сервер на отдельную тестовую БД `growvision_mock.db`, не трогая боевую базу; `false` — боевой режим. | 
| `ESP_ADAPTER_ENABLED` | `false` | `true` — запускает встроенный TCP-адаптер на порту `8080`, совместимый с нестандартным HTTP-протоколом прошивки ESP8266 (keep-alive без заголовка `Host:`). | 
| `ESP_ADAPTER_HOST` | `0.0.0.0` | Сетевой интерфейс, на котором слушает TCP-адаптер ESP. | 
| `ESP_ADAPTER_PORT` | `8080` | Порт для подключения платы ESP8266 и её имитатора. | 
| `HOST` | `0.0.0.0` | Хост HTTP-сервера FastAPI и Web UI. | 
| `PORT` | `8000` | Порт HTTP-сервера FastAPI и Web UI. | 
| `EDGE_API_KEY` | из `.env` | Секретный ключ авторизации Edge-агента (`X-Edge-Key`). Должен совпадать с ключом в `edge_agent/.env`. | 
| `CLIENT_API_KEY` | из `.env` | Секретный ключ авторизации клиентов (`X-Client-Key`) для бота и CLI. | 
| `BIOLOGIST_PASSWORD` | из `.env` | Пароль входа для роли **Биолог**. | 
| `DEVELOPER_PASSWORD` | из `.env` | Пароль входа для роли **Разработчик**. | 
| `PYTHONPATH=.` | — | Обязательный флаг, чтобы Python находил пакеты `server` и `shared` из корня репозитория. | 

## 2. Edge-агент (Raspberry Pi 5 и эмулятор)

### 2.1. Боевой запуск на Raspberry Pi (реальное железо + реальные камеры)

Используется на Raspberry Pi 5 с подключёнными USB/CSI-камерами и физическими GPIO-пинами.

```
cd ~/study/GrowVision && \
HARDWARE_MODE=rpi MOCK_CAMERAS=false PYTHONPATH=. \
edge_agent/.venv/bin/python -m edge_agent.main

```

### 2.2. Гибридный запуск на ноутбуке (эмулятор GPIO + настоящие USB-камеры)

Позволяет тестировать реальные камеры ноутбука/USB-вебкамеры без падения на отсутствии `RPi.GPIO` / `lgpio`.

```
cd ~/study/GrowVision && \
HARDWARE_MODE=mock MOCK_CAMERAS=false PYTHONPATH=. \
edge_agent/.venv/bin/python -m edge_agent.main

```

### 2.3. Полный эмулятор Edge-агента (без камер и без GPIO)

Генерирует синтетические кадры растений с ArUco-маркерами и эмулирует переключение всех реле/RGB-лент.

```
cd ~/study/GrowVision && \
HARDWARE_MODE=mock MOCK_CAMERAS=true MOCK_OVERLAY_ARUCO=true PYTHONPATH=. \
edge_agent/.venv/bin/python -m edge_agent.main

```

### 2.4. Генератор тестовых данных / симулятор цикла (`simulator.py`)

Позволяет прогнать ускоренную симуляцию роста и заполнить сервер историей кадров/метрик.

```
cd ~/study/GrowVision && \
PYTHONPATH=. edge_agent/.venv/bin/python -m edge_agent.simulator --help

```

### 2.5. Переменные окружения и системные права Edge-агента

| Переменная / Команда | Значения | Описание | 
| ----- | ----- | ----- | 
| `HARDWARE_MODE` | `rpi` / `mock` | `rpi` — управление реальными пинами Raspberry Pi; `mock` — виртуальные пины в памяти без обращения к `/dev/gpiomem`. | 
| `MOCK_CAMERAS` | `false` / `true` | `false` (по умолчанию) — работа с реальными камерами `/dev/video*` через V4L2/OpenCV; `true` — синтетические кадры. | 
| `MOCK_OVERLAY_ARUCO` | `true` / `false` | В `mock`-режиме камер накладывает на синтетический кадр виртуальные ArUco-маркеры для проверки калибровки площади. | 
| `SERVER_URL` | `http://127.0.0.1:8000` | Адрес центрального сервера (если Edge на отдельной RPi — указать `http://<IP_сервера>:8000`). | 
| `TEST_CAPTURE_INTERVAL_SEC` | `10` | Ускоренный интервал съёмки в секундах для отладки (в бою расписание берётся с сервера). | 
| `ls -la /dev/video*` | — | Проверка подключённых видеоустройств в Linux. | 
| `v4l2-ctl --list-devices` | — | Вывод человекочитаемых имён USB-камер и привязанных к ним `/dev/videoX`. | 
| `sudo usermod -aG video,gpio "$USER"` | — | Выдача пользователю прав на камеры (`/dev/video*`) и GPIO без `sudo` (требует перелогина). | 

## 3. ESP8266 (Полив, вентиляция и датчик SHT31)

### 3.1. Настройка реальной физической платы ESP8266 (без перепрошивки)

1. Дважды нажми кнопку на корпусе контроллера ESP8266 — плата поднимет Wi-Fi точку доступа `ESP_CONIG` (пароль: `34670000`).

2. Подключись с ноутбука к сети `ESP_CONIG`.

3. Выполни скрипт записи настроек в EEPROM платы (по UDP-порту `7931` на `192.168.4.1`):

```
cd ~/study/GrowVision && \
python3 scripts/configure_esp.py \
  --ssid "ИМЯ_ТВОЕГО_WIFI_2.4G" \
  --password "ПАРОЛЬ_ОТ_WIFI" \
  --host "192.168.1.100" \
  --port 8080

```

4. Верни ноутбук в домашнюю Wi-Fi сеть — через 5–15 секунд плата перезагрузится и выйдет в **Онлайн**.

| Флаг `configure_esp.py` | Обязательный | Описание | 
| ----- | ----- | ----- | 
| `--ssid` | Да | Имя Wi-Fi сети (**только 2.4 ГГц**, чип ESP8266 не поддерживает 5 ГГц). | 
| `--password` | Да | Пароль от Wi-Fi сети (WPA2-PSK). | 
| `--host` | Да | Локальный IP-адрес сервера (узнать командой `hostname -I`). | 
| `--port` | Нет (`8080`) | Порт TCP-адаптера ESP на сервере (по умолчанию `8080`). | 
| `--ap-ip` | Нет (`192.168.4.1`) | IP-адрес точки доступа самой платы в режиме конфигурации. | 
| `--ap-port` | Нет (`7931`) | UDP-порт приёма конфигурационного пакета на плате. | 

### 3.2. Запуск непрерывного имитатора прошивки ESP8266 (порт `8080`)

Эмулирует реальную плату: держит постоянное TCP-соединение (`keep-alive`), каждые 3 секунды пингует сервер и опрашивает команды полива/обдува, мгновенно подтверждает их (`ACK`) и раз в 30 секунд отправляет температуру/влажность `SHT31`.

```
python3 - <<'PY'
import json, socket, time

def req(s, raw):
    s.sendall(raw)
    buf = b""
    while b"\r\n\r\n" not in buf:
        c = s.recv(4096)
        if not c:
            raise RuntimeError("disconnected")
        buf += c
    head, body = buf.split(b"\r\n\r\n", 1)
    cl = 0
    for line in head.decode("ascii").split("\r\n")[1:]:
        if line.startswith("Content-Length:"):
            cl = int(line.split(":", 1)[1].strip())
    while len(body) < cl:
        body += s.recv(4096)
    return head.decode("ascii").split("\r\n")[0], body[:cl]

with socket.create_connection(("127.0.0.1", 8080), timeout=10) as s:
    print("ESP имитатор подключён к 127.0.0.1:8080 (Ctrl+C для остановки)")
    for i in range(10000):
        if i % 10 == 0:
            payload = json.dumps({
                "temperature": round(23.5 + (i % 5) * 0.2, 1),
                "humidity": round(58.0 + (i % 4), 1),
            }).encode()
            st, _ = req(s, b"POST /api/esp/sensors HTTP/1.1\r\nContent-Type: application/json\r\nContent-Length: " + str(len(payload)).encode() + b"\r\nConnection: keep-alive\r\n\r\n" + payload)
            print(f"[ESP] sensors -> {st} {payload.decode()}")
        req(s, b"GET /ping HTTP/1.1\r\nConnection: keep-alive\r\n\r\n")
        st, body = req(s, b"GET /api/esp/command HTTP/1.1\r\nConnection: keep-alive\r\n\r\n")
        cmd = json.loads(body.decode())
        if cmd["device"] != "None":
            print(f"[ESP] ПОЛУЧЕНА КОМАНДА: {cmd}")
            ack = json.dumps({"id": cmd["id"]}).encode()
            st2, _ = req(s, b"POST /api/esp/command HTTP/1.1\r\nContent-Type: application/json\r\nContent-Length: " + str(len(ack)).encode() + b"\r\nConnection: keep-alive\r\n\r\n" + ack)
            print(f"[ESP] ACK id={cmd['id']} -> {st2}")
        time.sleep(3)
PY

```

### 3.3. Ручная отправка тестового пинга и датчиков через `curl`

Флаг `--noproxy '*'` обязателен, если в терминале активны переменные системного прокси (`ALL_PROXY` / `http_proxy`), иначе `curl` попытается завернуть `127.0.0.1` в прокси.

```
# Разовый пинг (переводит ESP в Онлайн на 60 секунд)
curl --noproxy '*' -sS http://127.0.0.1:8000/ping && echo

# Отправка показаний температуры и влажности
curl --noproxy '*' -sS -X POST http://127.0.0.1:8000/api/esp/sensors \
  -H "Content-Type: application/json" \
  -d '{"temperature": 24.3, "humidity": 57.8}' && echo

# Проверка очереди команд для ESP
curl --noproxy '*' -sS http://127.0.0.1:8000/api/esp/command && echo

```

## 4. Telegram-бот

### 4.1. Обычный запуск (без прокси / при прямом доступе к Telegram)

```
cd ~/study/GrowVision && \
PYTHONPATH=.:clients/telegram_bot \
clients/telegram_bot/.venv/bin/python clients/telegram_bot/main.py

```

### 4.2. Запуск с локальным SOCKS5/HTTP-прокси (без общесистемного VPN)

Позволяет направить в прокси **только** запросы бота к `api.telegram.org`, оставляя обращение к серверу GrowVision (`127.0.0.1:8000`) и ESP8266 прямым.
*Важно:* библиотека `aiohttp-socks` требует схему `socks5://` (не `socks://`), а системные переменные `ALL_PROXY` лучше сбросить перед стартом через `env -u`.

```
cd ~/study/GrowVision && \
env -u ALL_PROXY -u all_proxy -u HTTP_PROXY -u http_proxy -u HTTPS_PROXY -u https_proxy \
TELEGRAM_PROXY="socks5://127.0.0.1:10808" \
PYTHONPATH=.:clients/telegram_bot \
clients/telegram_bot/.venv/bin/python clients/telegram_bot/main.py

```

### 4.3. Переменные окружения Telegram-бота

| Переменная | Описание | 
| ----- | ----- | 
| `BOT_TOKEN` | Токен бота от `@BotFather` (задаётся в `clients/telegram_bot/.env`). | 
| `SERVER_URL` | Адрес API сервера (по умолчанию `http://127.0.0.1:8000`). | 
| `CLIENT_API_KEY` | Ключ `X-Client-Key` (должен совпадать с ключом на сервере). | 
| `TELEGRAM_PROXY` | Адрес прокси для `aiogram`, например `socks5://127.0.0.1:10808` или `http://127.0.0.1:10809`. Требует установленного пакета `aiohttp-socks` в `.venv` бота. | 

## 5. CLI-клиент (управление из терминала)

Запуск консольного клиента (удобен для скриптов, крона и быстрой проверки API без браузера):

```
cd ~/study/GrowVision && \
PYTHONPATH=.:clients/cli clients/cli/.venv/bin/python clients/cli/main.py --help

```

### Основные команды CLI

```
# 1. Авторизация (сохраняет токен сессии локально)
PYTHONPATH=.:clients/cli clients/cli/.venv/bin/python clients/cli/main.py login --role developer

# 2. Общий статус системы, RPi и версий
PYTHONPATH=.:clients/cli clients/cli/.venv/bin/python clients/cli/main.py status

# 3. Список установок и подключённых камер
PYTHONPATH=.:clients/cli clients/cli/.venv/bin/python clients/cli/main.py units
PYTHONPATH=.:clients/cli clients/cli/.venv/bin/python clients/cli/main.py cameras

# 4. Управление светом (включить/выключить на конкретной установке или всех сразу)
PYTHONPATH=.:clients/cli clients/cli/.venv/bin/python clients/cli/main.py light --unit 1 --state on
PYTHONPATH=.:clients/cli clients/cli/.venv/bin/python clients/cli/main.py light --all --state off

# 5. Сделать тестовый снимок с установки
PYTHONPATH=.:clients/cli clients/cli/.venv/bin/python clients/cli/main.py snap --unit 1

# 6. Выход из сессии
PYTHONPATH=.:clients/cli clients/cli/.venv/bin/python clients/cli/main.py logout

```

## 6. Очистка баз данных, медиафайлов и очередей

*Перед удалением баз обязательно останови сервер (`Ctrl+C`), иначе SQLite пересоздаст или заблокирует WAL-файлы.*

### 6.1. Очистить только тестовую (Mock) базу данных

```
cd ~/study/GrowVision && \
rm -f growvision_mock.db growvision_mock.db-wal growvision_mock.db-shm

```

### 6.2. Полный сброс: боевая БД + Mock БД + все загруженные фото и таймлапсы

Удаляет все базы SQLite (включая журналы `-wal` и `-shm`) и очищает `server/media/`, сохраняя системный `.gitkeep`. При следующем запуске сервер автоматически создаст чистую БД с 13 таблицами.

```
cd ~/study/GrowVision && \
rm -f growvision.db growvision.db-wal growvision.db-shm \
      growvision_mock.db growvision_mock.db-wal growvision_mock.db-shm \
      server/growvision.db server/growvision.db-wal server/growvision.db-shm && \
find server/media -mindepth 1 ! -name '.gitkeep' -exec rm -rf {} +

```

### 6.3. Очистить локальную офлайн-очередь Edge-агента

Используется, если на Raspberry Pi скопились старые неотправленные кадры и их нужно сбросить.

```
cd ~/study/GrowVision && \
rm -rf edge_agent/offline_queue/*

```

### 6.4. Сбросить только данные климата и ESP в существующей БД (не удаляя установки и пользователей)

```
cd ~/study/GrowVision && \
server/.venv/bin/python -c '
import sqlite3
for db in ("growvision.db", "growvision_mock.db"):
    try:
        conn = sqlite3.connect(db)
        conn.executescript("""
            DELETE FROM climate_sensors;
            DELETE FROM ph_readings;
            DELETE FROM peripheral_logs;
        """)
        conn.commit()
        conn.close()
        print(f"Очищены таблицы климата в {db}")
    except Exception as e:
        print(f"{db}: {e}")
'

```

## 7. Сеть, порты, файрвол и диагностика

```
# Узнать локальный IP-адрес машины (нужен для configure_esp.py --host)
hostname -I | awk '{print $1}'

# Проверить, какие процессы слушают порты 8000 (HTTP API) и 8080 (ESP TCP-адаптер)
ss -tlnp | grep -E ':8000|:8080' || echo "Порты 8000/8080 свободны"

# Открыть порты в файрволе UFW (если включён на Linux / Raspberry Pi)
sudo ufw allow 8000/tcp comment "GrowVision Web & API"
sudo ufw allow 8080/tcp comment "GrowVision ESP8266 TCP Adapter"
sudo ufw status

# Проверка доступности сервера извне по mDNS (если установлен avahi-daemon)
ping -c 2 growvision.local

```

## 8. Автотесты и линтер (контроль качества после правок)

```
# Полный прогон всех unit-тестов (сервер, Edge, Telegram-бот, CLI)
cd ~/study/GrowVision && \
server/.venv/bin/pytest server/tests -q && \
edge_agent/.venv/bin/pytest edge_agent/tests -q && \
clients/telegram_bot/.venv/bin/pytest clients/telegram_bot/tests -q && \
clients/cli/.venv/bin/pytest clients/cli/tests -q

# Сквозные E2E-тесты (API, Web SPA, эмуляция RPi, бот, CLI)
cd ~/study/GrowVision && server/.venv/bin/pytest tests/e2e -q

# Проверка и автоисправление стиля кода (Ruff)
cd ~/study/GrowVision && \
server/.venv/bin/ruff check --fix . && \
server/.venv/bin/ruff format . && \
server/.venv/bin/ruff check .

```