import contextlib
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import httpx

from clients.telegram_bot.config import BotConfig
from clients.telegram_bot.utils.errors import ApiError, extract_detail

logger = logging.getLogger(__name__)

TG_VIDEO_LIMIT = 50_000_000  # 50 МБ


class GrowVisionApiClient:
    """Асинхронный HTTP-клиент к Client API GrowVision."""

    def __init__(self, config: BotConfig, client: httpx.AsyncClient | None = None):
        self.config = config
        self.base_url = config.server_url.rstrip("/")
        self.client_api_key = config.client_api_key
        self._client = client
        self._owns_client = client is None

    async def get_client(self) -> httpx.AsyncClient:
        """Возвращает или создает экземпляр httpx.AsyncClient."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(base_url=self.base_url)
            self._owns_client = True
        return self._client

    async def close(self) -> None:
        """Закрывает HTTP-клиент."""
        if self._owns_client and self._client and not self._client.is_closed:
            await self._client.aclose()

    def _headers(self, user_id: int | str | None) -> dict[str, str]:
        """Формирует обязательные служебные заголовки для сервера."""
        headers = {
            "X-Client-Key": self.client_api_key,
            "X-Client-Type": "telegram",
        }
        if user_id is not None:
            headers["X-External-Id"] = str(user_id)
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        user_id: int | str | None = None,
        timeout: float = 15.0,
        is_control_command: bool = False,
        is_public_endpoint: bool = False,
        **kwargs,
    ) -> Any:
        """Выполняет HTTP-запрос к API с обработкой ошибок."""
        client = await self.get_client()
        headers = self._headers(user_id)
        if "headers" in kwargs:
            headers.update(kwargs.pop("headers"))

        try:
            resp = await client.request(
                method=method,
                url=path,
                headers=headers,
                timeout=timeout,
                **kwargs,
            )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            logger.warning(f"Сетевая ошибка при запросе {method} {path}: {exc}")
            raise

        if resp.status_code in (200, 201, 204):
            if resp.status_code == 204 or not resp.content:
                return {}
            try:
                return resp.json()
            except Exception:  # noqa: BLE001
                return resp.text

        # Разбор ошибки
        detail = ""
        retry_after = None
        try:
            body = resp.json()
            detail = extract_detail(body) or resp.text
        except Exception:  # noqa: BLE001
            detail = resp.text

        if "retry-after" in resp.headers:
            try:
                retry_after = int(resp.headers["retry-after"])
            except ValueError:
                pass

        logger.info(
            f"API {method} {path} -> {resp.status_code}: {detail} (user={user_id})"
        )
        raise ApiError(
            status_code=resp.status_code,
            detail=detail,
            retry_after=retry_after,
            is_control_command=is_control_command,
        )

    # ------------------ 1. Справка и версия ------------------

    async def get_version(self) -> dict[str, Any]:
        """Возвращает версию сервера через публичный эндпоинт."""
        return await self._request(
            "GET",
            "/api/version",
            user_id=None,
            timeout=10.0,
            is_public_endpoint=True,
        )

    # ------------------ 2. Авторизация и профиль ------------------

    async def get_me(self, user_id: int) -> dict[str, Any] | None:
        """Возвращает профиль текущей сессии пользователя или None, если 401."""
        try:
            return await self._request(
                "GET", "/api/auth/me", user_id=user_id, timeout=15.0
            )
        except ApiError as exc:
            if exc.status_code == 401:
                return None
            raise

    async def login(self, user_id: int, username: str, password: str) -> dict[str, Any]:
        """Вход пользователя в систему."""
        payload = {
            "username": username,
            "password": password,
            "client_type": "telegram",
            "external_id": str(user_id),
        }
        return await self._request(
            "POST",
            "/api/auth/login",
            user_id=user_id,
            json=payload,
            timeout=15.0,
        )

    async def logout(self, user_id: int) -> dict[str, Any]:
        """Выход пользователя из системы."""
        return await self._request(
            "POST", "/api/auth/logout", user_id=user_id, timeout=15.0
        )

    # ------------------ 3. Установки ------------------

    async def get_installations(
        self, user_id: int, include_archived: bool = False
    ) -> list[dict[str, Any]]:
        """Список установок."""
        params = {"include_archived": "true" if include_archived else "false"}
        res = await self._request(
            "GET",
            "/api/installations",
            user_id=user_id,
            params=params,
            timeout=15.0,
            is_public_endpoint=True,
        )
        return res if isinstance(res, list) else []

    async def get_installation(self, user_id: int, inst_id: int) -> dict[str, Any]:
        """Карточка установки."""
        return await self._request(
            "GET",
            f"/api/installations/{inst_id}",
            user_id=user_id,
            timeout=15.0,
            is_public_endpoint=True,
        )

    async def create_installation(
        self, user_id: int, data: dict[str, Any]
    ) -> dict[str, Any]:
        """Создание новой установки (Developer)."""
        return await self._request(
            "POST",
            "/api/installations",
            user_id=user_id,
            json=data,
            timeout=15.0,
        )

    async def update_installation(
        self, user_id: int, inst_id: int, data: dict[str, Any]
    ) -> dict[str, Any]:
        """Редактирование параметров установки."""
        return await self._request(
            "PATCH",
            f"/api/installations/{inst_id}",
            user_id=user_id,
            json=data,
            timeout=15.0,
        )

    async def archive_installation(self, user_id: int, inst_id: int) -> dict[str, Any]:
        """Перевод установки в архив (Developer)."""
        return await self._request(
            "POST",
            f"/api/installations/{inst_id}/archive",
            user_id=user_id,
            timeout=15.0,
        )

    async def delete_installation(self, user_id: int, inst_id: int) -> dict[str, Any]:
        """Удаление установки (Developer)."""
        return await self._request(
            "DELETE",
            f"/api/installations/{inst_id}",
            user_id=user_id,
            timeout=15.0,
        )

    # ------------------ 4. Камеры ------------------

    async def get_cameras(self, user_id: int) -> list[dict[str, Any]]:
        """Список всех зарегистрированных камер (Developer)."""
        res = await self._request("GET", "/api/cameras", user_id=user_id, timeout=15.0)
        return res if isinstance(res, list) else []

    async def scan_cameras(self, user_id: int) -> dict[str, Any]:
        """Сканирование USB-камер на Raspberry Pi (Developer)."""
        return await self._request(
            "POST",
            "/api/cameras/scan",
            user_id=user_id,
            timeout=35.0,
            is_control_command=True,
        )

    async def bind_camera(
        self, user_id: int, camera_id: int, installation_id: int | None
    ) -> dict[str, Any]:
        """Привязка или отвязка камеры к установке (Developer)."""
        payload = {"installation_id": installation_id}
        return await self._request(
            "POST",
            f"/api/cameras/{camera_id}/bind",
            user_id=user_id,
            json=payload,
            timeout=15.0,
        )

    # ------------------ 5. Управление (свет, реле, снимок) ------------------

    async def control_light_main(
        self, user_id: int, installation_id: int | None, state: bool
    ) -> dict[str, Any]:
        """Управление основным светом (Developer)."""
        payload = {"installation_id": installation_id, "state": state}
        return await self._request(
            "POST",
            "/api/control/light/main",
            user_id=user_id,
            json=payload,
            timeout=35.0,
            is_control_command=True,
        )

    async def control_light_flash(
        self, user_id: int, installation_id: int | None, state: bool
    ) -> dict[str, Any]:
        """Управление вспышкой (Developer)."""
        payload = {"installation_id": installation_id, "state": state}
        return await self._request(
            "POST",
            "/api/control/light/flash",
            user_id=user_id,
            json=payload,
            timeout=35.0,
            is_control_command=True,
        )

    async def control_relay_test(
        self, user_id: int, installation_id: int | None
    ) -> dict[str, Any]:
        """Кратковременный тест реле на 3 сек (Developer)."""
        payload = {"installation_id": installation_id}
        return await self._request(
            "POST",
            "/api/control/relay-test",
            user_id=user_id,
            json=payload,
            timeout=35.0,
            is_control_command=True,
        )

    async def take_snap(
        self, user_id: int, installation_id: int | None, active_count: int = 1
    ) -> dict[str, Any]:
        """Ручной снимок одной установки или всех (Biologist, Developer)."""
        timeout = (
            35.0
            if installation_id is not None
            else (30.0 + 10.0 * max(1, active_count))
        )
        payload = {"installation_id": installation_id}
        return await self._request(
            "POST",
            "/api/control/snap",
            user_id=user_id,
            json=payload,
            timeout=timeout,
            is_control_command=True,
        )

    # ------------------ 6. Таймлапс и медиа ------------------

    async def get_timelapse_frames(
        self, user_id: int, inst_id: int, camera_id: int | None = None
    ) -> list[dict[str, Any]]:
        """Список плановых кадров таймлапса."""
        params = {}
        if camera_id is not None:
            params["camera_id"] = str(camera_id)
        res = await self._request(
            "GET",
            f"/api/timelapse/{inst_id}/frames",
            user_id=user_id,
            params=params,
            timeout=15.0,
            is_public_endpoint=True,
        )
        return res if isinstance(res, list) else []

    async def download_file(self, user_id: int, file_path_or_url: str) -> bytes:
        """Скачивает бинарный файл (фото снимка, аннотацию) по URL или относительному пути."""
        client = await self.get_client()
        url = (
            file_path_or_url
            if file_path_or_url.startswith(("http://", "https://"))
            else f"{self.base_url}/{file_path_or_url.lstrip('/')}"
        )
        headers = self._headers(user_id)
        resp = await client.get(url, headers=headers, timeout=30.0)
        resp.raise_for_status()
        return resp.content

    async def download_timelapse_video_stream(
        self,
        user_id: int,
        inst_id: int,
        camera_id: int | None = None,
        step: int = 1,
        max_size_bytes: int = TG_VIDEO_LIMIT,
    ) -> tuple[str, Path | bytes | None, int]:
        """Потоковая загрузка видео таймлапса с проверкой размера.

        Возвращает:
        (content_type, content_path_or_bytes, total_size_bytes)
        Если размер превышает max_size_bytes, возвращает:
        ("video/mp4", None, total_size_bytes)
        """
        client = await self.get_client()
        url = f"{self.base_url}/api/timelapse/{inst_id}/video"
        headers = self._headers(user_id)
        payload = {"camera_id": camera_id, "step": step}
        timeout = httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=10.0)

        async with client.stream(
            "POST",
            url,
            headers=headers,
            json=payload,
            timeout=timeout,
        ) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                detail = ""
                try:
                    import json

                    parsed = json.loads(body.decode("utf-8"))
                    detail = extract_detail(parsed) or body.decode("utf-8")
                except Exception:  # noqa: BLE001
                    detail = body.decode("utf-8", errors="ignore")
                raise ApiError(status_code=resp.status_code, detail=detail)

            content_type = resp.headers.get("content-type", "").lower()

            # Если плановый кадр один — сервер отдает image/jpeg
            if "image/jpeg" in content_type:
                data = await resp.aread()
                return "image/jpeg", data, len(data)

            # Для видео mp4 проверяем Content-Length заранее
            content_length_header = resp.headers.get("content-length")
            if content_length_header:
                try:
                    expected_len = int(content_length_header)
                    if expected_len > max_size_bytes:
                        logger.info(
                            f"Таймлапс #{inst_id} превышает лимит по Content-Length: {expected_len} > {max_size_bytes}"
                        )
                        return "video/mp4", None, expected_len
                except ValueError:
                    pass

            # Потоковая запись во временный файл
            tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)  # noqa: SIM115
            total_read = 0
            exceeded = False

            try:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    total_read += len(chunk)
                    if total_read > max_size_bytes:
                        exceeded = True
                        break
                    tmp.write(chunk)
            finally:
                tmp.close()

            if exceeded:
                logger.info(
                    f"Таймлапс #{inst_id} превысил лимит в процессе скачивания: {total_read} > {max_size_bytes}"
                )
                with contextlib.suppress(Exception):
                    os.unlink(tmp.name)
                return "video/mp4", None, total_read

            return "video/mp4", Path(tmp.name), total_read

    # ------------------ 7. Система и настройки ------------------

    async def get_system_status(self, user_id: int) -> dict[str, Any]:
        """Статус системы (сервер, Raspberry Pi, настройки)."""
        return await self._request(
            "GET", "/api/system/status", user_id=user_id, timeout=15.0
        )

    async def update_system_settings(
        self,
        user_id: int,
        capture_interval_hours: int | None = None,
        debug_mode: bool | None = None,
    ) -> dict[str, Any]:
        """Обновление системных настроек (Developer)."""
        payload: dict[str, Any] = {}
        if capture_interval_hours is not None:
            payload["capture_interval_hours"] = capture_interval_hours
        if debug_mode is not None:
            payload["debug_mode"] = debug_mode
        return await self._request(
            "PATCH",
            "/api/system/settings",
            user_id=user_id,
            json=payload,
            timeout=15.0,
        )
