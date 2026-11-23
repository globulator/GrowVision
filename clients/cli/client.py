import sys
from collections.abc import Generator
from typing import Any

import httpx

from clients.cli.config import Config, load_config
from clients.cli.formatting import err_console, print_error


class APIClient:
    """HTTP-клиент для взаимодействия с GrowVision Client API."""

    def __init__(
        self, config: Config | None = None, transport: httpx.BaseTransport | None = None
    ) -> None:
        self.config = config or load_config()
        self.transport = transport
        self.headers = {
            "X-Client-Key": self.config.client_api_key,
            "X-Client-Type": "cli",
            "X-External-Id": self.config.external_id,
        }

    def _get_client(self, timeout: float | httpx.Timeout) -> httpx.Client:
        return httpx.Client(
            base_url=self.config.server_url,
            headers=self.headers,
            timeout=timeout,
            transport=self.transport,
            trust_env=False,
        )

    def _log_request(self, method: str, url: str) -> None:
        if self.config.cli_debug:
            err_console.print(f"[dim]> {method} {self.config.server_url}{url}[/dim]")

    def _log_response(self, response: httpx.Response, is_binary: bool = False) -> None:
        if self.config.cli_debug:
            if is_binary:
                body_info = f"<двоичные данные, размер: {len(response.content)} байт>"
            else:
                body_info = response.text
            err_console.print(f"[dim]< HTTP {response.status_code}\n{body_info}[/dim]")

    def handle_http_error(self, exc: httpx.HTTPStatusError, context: str) -> None:
        """Единая обработка HTTP-ошибок по CLI_SPEC.md разд. 4."""
        status_code = exc.response.status_code
        detail = ""
        try:
            body = exc.response.json()
            if isinstance(body, dict):
                raw_detail = body.get("detail", "")
                if isinstance(raw_detail, list):
                    # Pydantic validation errors
                    parts = []
                    for item in raw_detail:
                        loc = " -> ".join(str(p) for p in item.get("loc", []))
                        msg = item.get("msg", "")
                        parts.append(f"{loc}: {msg}" if loc else msg)
                    detail = "; ".join(parts)
                else:
                    detail = str(raw_detail)
            else:
                detail = str(body)
        except (ValueError, TypeError):
            detail = exc.response.text or f"HTTP {status_code}"

        hint = None
        if status_code == 401:
            if "ключ" in detail.lower() or "client-key" in detail.lower():
                hint = "Проверьте CLIENT_API_KEY в clients/cli/.env."
            else:
                hint = "Выполните вход: auth login."
        elif status_code == 403:
            hint = "Эта операция доступна только для роли Developer. Проверьте текущую роль: auth status."
        elif status_code == 404:
            if "плановые снимки" in detail.lower() or "кадр" in detail.lower():
                hint = "Плановые снимки появятся после первой автосъёмки по расписанию; ручные снимки (snap take) в таймлапс не входят."
            else:
                hint = "Проверьте список доступных объектов через unit list или camera list."
        elif status_code == 409:
            if (
                "архив" in detail.lower()
                or "снимк" in detail.lower()
                or "кадр" in detail.lower()
            ):
                hint = "Используйте unit archive <target> вместо удаления или проверьте статус установки."
            elif "нет активных" in detail.lower():
                hint = "Создайте активную установку через unit add."
            else:
                hint = "Укажите другое имя или свободные GPIO-пины (--light-pin / --flash-pin)."
        elif status_code == 422:
            if "camera_id" in detail.lower():
                hint = "Укажите --camera-id <id> из списка камер в сообщении выше."
            elif "step" in detail.lower():
                hint = "Укажите целое число не меньше 1."
            else:
                hint = "Проверьте правильность введенных аргументов и параметров."
        elif status_code == 429:
            retry_after = exc.response.headers.get("Retry-After", "несколько")
            hint = f"Повторите попытку через {retry_after} сек."
        elif status_code == 503:
            hint = "Проверьте питание Raspberry Pi 5 и сетевое подключение (system status)."
        elif status_code == 504:
            hint = "Если выполнялась команда управления светом или съёмкой, её фактический результат неизвестен — проверьте состояние через system status или unit show."
        elif status_code >= 500:
            err_console.print(
                f"Ошибка ({context}): Сервер вернул внутреннюю ошибку (HTTP {status_code}). Проверьте логи службы server.\n",
                style="bold red",
            )
            sys.exit(1)

        print_error(context=context, detail=detail, hint=hint)
        sys.exit(1)

    def request(
        self,
        method: str,
        url: str,
        context: str,
        params: dict[str, Any] | None = None,
        json_data: Any | None = None,
        timeout: float = 10.0,
    ) -> dict[str, Any] | list[Any] | None:
        """Выполняет HTTP-запрос и возвращает распарсенный JSON."""
        self._log_request(method, url)
        try:
            with self._get_client(timeout=timeout) as client:
                resp = client.request(method, url, params=params, json=json_data)
                self._log_response(resp)
                resp.raise_for_status()
                if resp.status_code == 204 or not resp.content:
                    return None
                try:
                    return resp.json()
                except ValueError:
                    err_console.print(
                        f"Ошибка ({context}): Получен некорректный ответ от сервера (HTTP {resp.status_code}). "
                        "Запустите команду с флагом --verbose для просмотра сырого ответа.\n",
                        style="bold red",
                    )
                    sys.exit(1)
        except httpx.ConnectError:
            err_console.print(
                f"Ошибка ({context}): Не удалось подключиться к серверу по адресу {self.config.server_url}. "
                "Убедитесь, что сервис server запущен и доступен по сети.\n",
                style="bold red",
            )
            sys.exit(1)
        except httpx.TimeoutException:
            err_console.print(
                f"Ошибка ({context}): Сервер не ответил за {int(timeout)} с. "
                "Для управляющих команд фактическое выполнение на Raspberry Pi неизвестно — проверьте статус через system status.\n",
                style="bold red",
            )
            sys.exit(1)
        except httpx.HTTPStatusError as e:
            self.handle_http_error(e, context)
            sys.exit(1)
        except httpx.RequestError as e:
            err_console.print(f"Ошибка ({context}): {e}\n", style="bold red")
            sys.exit(1)

    def download_stream(
        self,
        method: str,
        url: str,
        context: str,
        json_data: Any | None = None,
        timeout: httpx.Timeout | None = None,
    ) -> Generator[bytes, None, tuple[int, dict[str, str]]]:
        """Стриминг скачивания данных с сервера (для видео/файлов)."""
        if timeout is None:
            timeout = httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=10.0)
        self._log_request(method, url)
        client = self._get_client(timeout=timeout)
        try:
            req = client.build_request(method, url, json=json_data)
            resp = client.send(req, stream=True)
            self._log_response(resp, is_binary=True)
            if resp.is_error:
                resp.read()
                resp.raise_for_status()

            def generator():
                try:
                    yield from resp.iter_bytes()
                finally:
                    resp.close()
                    client.close()

            return generator(), (resp.status_code, dict(resp.headers))
        except httpx.ConnectError:
            client.close()
            err_console.print(
                f"Ошибка ({context}): Не удалось подключиться к серверу по адресу {self.config.server_url}. "
                "Убедитесь, что сервис server запущен и доступен по сети.\n",
                style="bold red",
            )
            sys.exit(1)
        except httpx.TimeoutException:
            client.close()
            err_console.print(
                f"Ошибка ({context}): Сервер не ответил за 600 с. "
                "Для управляющих команд фактическое выполнение на Raspberry Pi неизвестно — проверьте статус через system status.\n",
                style="bold red",
            )
            sys.exit(1)
        except httpx.HTTPStatusError as e:
            client.close()
            self.handle_http_error(e, context)
            sys.exit(1)
        except httpx.RequestError as e:
            client.close()
            err_console.print(f"Ошибка ({context}): {e}\n", style="bold red")
            sys.exit(1)
