import contextlib
import threading
import time
from typing import Any, Self

import httpx

from shared.logger import get_logger
from shared.schemas import (
    CaptureMeta,
    CommandItem,
    CommandResult,
    HeartbeatRequest,
    HeartbeatResponse,
)

logger = get_logger("edge_agent.client")


class EdgeAPIClient:
    """HTTP-клиент для взаимодействия с сервером GrowVision по Edge API."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float = 15.0,
        upload_timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.upload_timeout = upload_timeout
        self.transport = transport
        self._client_lock = threading.Lock()
        self._http_client: httpx.Client | None = None
        self._live_http_client: httpx.Client | None = None

    def _get_headers(self) -> dict[str, str]:
        """Возвращает обязательные заголовки запроса к Edge API."""
        return {
            "X-Edge-Key": self.api_key,
        }

    def _create_client(self, timeout: float | None = None) -> httpx.Client:
        """Создает экземпляр синхронного httpx.Client с отключенным trust_env."""
        return httpx.Client(
            base_url=self.base_url,
            headers=self._get_headers(),
            timeout=timeout if timeout is not None else self.timeout,
            transport=self.transport,
            trust_env=False,
        )

    def _get_client(self) -> httpx.Client:
        """Возвращает долгоживущий HTTP-клиент с Keep-Alive для основных запросов."""
        with self._client_lock:
            if self._http_client is None or self._http_client.is_closed:
                self._http_client = self._create_client()
            return self._http_client

    def close(self) -> None:
        """Закрывает долгоживущие HTTP-клиенты и освобождает пул соединений."""
        with self._client_lock:
            if self._http_client is not None:
                with contextlib.suppress(Exception):
                    self._http_client.close()
                self._http_client = None
            if self._live_http_client is not None:
                with contextlib.suppress(Exception):
                    self._live_http_client.close()
                self._live_http_client = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def send_heartbeat(self, telemetry: HeartbeatRequest) -> HeartbeatResponse | None:
        """Отправляет телеметрию на POST /api/edge/heartbeat и получает актуальный конфиг."""
        url = "/api/edge/heartbeat"
        try:
            resp = self._get_client().post(
                url,
                content=telemetry.model_dump_json(),
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                logger.debug(f"Heartbeat успешно доставлен: {data}")
                return HeartbeatResponse.model_validate(data)
            logger.warning(f"Heartbeat вернул статус {resp.status_code}: {resp.text}")
            return None
        except httpx.RequestError as exc:
            logger.warning(f"Ошибка сети при отправке Heartbeat: {exc}")
            return None
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Неожиданная ошибка при Heartbeat: {exc}")
            return None

    def claim_command(self) -> list[CommandItem]:
        """Забирает одну ожидающую команду из очереди: POST /api/edge/commands/claim."""
        url = "/api/edge/commands/claim"
        try:
            resp = self._get_client().post(url)
            if resp.status_code == 200:
                raw_items = resp.json()
                if isinstance(raw_items, list):
                    return [CommandItem.model_validate(item) for item in raw_items]
                return []
            logger.warning(f"Claim вернул статус {resp.status_code}: {resp.text}")
            return []
        except httpx.RequestError as exc:
            logger.debug(f"Ошибка сети при опросе claim: {exc}")
            return []
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Ошибка при получении команд claim: {exc}")
            return []

    def send_command_result(
        self,
        command_id: int,
        result: CommandResult,
        max_retries: int = 3,
        retry_delay_sec: float = 1.0,
    ) -> bool:
        """Отправляет отчет о выполнении команды: POST /api/edge/commands/{command_id}/result.

        Выполняет до max_retries (по умолчанию 3) попыток при сетевых сбоях.
        """
        url = f"/api/edge/commands/{command_id}/result"
        payload_json = result.model_dump_json()

        for attempt in range(1, max_retries + 1):
            try:
                resp = self._get_client().post(
                    url,
                    content=payload_json,
                    headers={"Content-Type": "application/json"},
                )
                if resp.status_code == 200:
                    logger.info(
                        f"Отчет по команде #{command_id} успешно доставлен: {result.status}"
                    )
                    return True
                if resp.status_code == 409:
                    logger.warning(
                        f"Команда #{command_id} уже не running (409 Conflict, возможно timeout). Отчет отброшен."
                    )
                    return False
                logger.warning(
                    f"Отправка результата #{command_id} вернула {resp.status_code}: {resp.text}"
                )
                return False
            except httpx.RequestError as exc:
                logger.warning(
                    f"Ошибка сети при отправке результата #{command_id} (попытка {attempt}/{max_retries}): {exc}"
                )
                if attempt < max_retries and retry_delay_sec > 0:
                    time.sleep(retry_delay_sec)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    f"Неожиданная ошибка при отправке результата #{command_id}: {exc}"
                )
                return False

        logger.error(
            f"Не удалось отправить результат команды #{command_id} после {max_retries} попыток"
        )
        return False

    def upload_capture(
        self,
        metadata: CaptureMeta,
        raw_bytes: bytes,
        annotated_bytes: bytes,
    ) -> tuple[int, dict[str, Any]]:
        """Загружает кадр и замеры на сервер: POST /api/edge/captures (таймаут 60 с)."""
        url = "/api/edge/captures"
        data = {
            "metadata": metadata.model_dump_json(),
        }
        files = {
            "raw_image": ("raw.jpg", raw_bytes, "image/jpeg"),
            "annotated_image": ("ann.jpg", annotated_bytes, "image/jpeg"),
        }
        try:
            # Используем upload_timeout (60 с)
            resp = self._get_client().post(
                url, data=data, files=files, timeout=self.upload_timeout
            )
            try:
                resp_json = resp.json()
            except Exception:  # noqa: BLE001
                resp_json = {"raw": resp.text}
            return resp.status_code, resp_json
        except httpx.RequestError as exc:
            logger.warning(f"Ошибка соединения при выгрузке кадра: {exc}")
            return 0, {"error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Неожиданная ошибка при выгрузке кадра: {exc}")
            return 0, {"error": str(exc)}

    def _get_live_client(self) -> httpx.Client:
        """Возвращает переиспользуемый HTTP-клиент для частой отправки live-кадров."""
        with self._client_lock:
            if self._live_http_client is None or self._live_http_client.is_closed:
                self._live_http_client = self._create_client(timeout=2.0)
            return self._live_http_client

    def send_live_frame(
        self,
        hardware_id: str,
        frame_bytes: bytes,
        frame_timestamp: float | None = None,
        is_paused: bool = False,
        resolution: str = "1280x720",
        latency_ms: int | None = None,
        stream_url: str | None = None,
        device_online: bool = True,
        frame_mode: str = "raw",
        color_marker_detected: bool | None = None,
    ) -> dict[str, Any] | None:
        """Отправляет свежий кадр Live-стрима: POST /api/edge/cameras/{hardware_id}/live-frame."""
        url = f"/api/edge/cameras/{hardware_id}/live-frame"
        headers = {
            "Content-Type": "image/jpeg",
            "X-Timestamp": str(frame_timestamp or time.time()),
            "X-Paused": "1" if is_paused else "0",
            "X-Resolution": resolution,
            "X-Device-Online": "1" if device_online else "0",
            "X-Frame-Mode": frame_mode,
        }
        if color_marker_detected is not None:
            headers["X-Color-Marker-Detected"] = "1" if color_marker_detected else "0"
        if latency_ms is not None:
            headers["X-Latency-Ms"] = str(max(0, int(latency_ms)))
        if stream_url:
            headers["X-Stream-Url"] = stream_url

        try:
            resp = self._get_live_client().post(
                url, content=frame_bytes, headers=headers
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception:  # noqa: BLE001
            with self._client_lock:
                with contextlib.suppress(Exception):
                    if self._live_http_client is not None:
                        self._live_http_client.close()
                self._live_http_client = None
        return None

    def send_camera_controls_state(
        self,
        hardware_id: str,
        formats: list[dict[str, Any]],
        controls: list[dict[str, Any]],
        current_format: str | None = None,
        current_resolution: str | None = None,
    ) -> bool:
        """Отправляет реальные форматы и V4L2-контролы камеры на сервер."""
        url = f"/api/edge/cameras/{hardware_id}/controls-state"
        payload: dict[str, Any] = {
            "formats": formats,
            "controls": controls,
        }
        if current_format:
            payload["current_format"] = current_format
        if current_resolution:
            payload["current_resolution"] = current_resolution

        with contextlib.suppress(Exception):
            resp = self._get_client().post(url, json=payload, timeout=5.0)
            return resp.status_code == 200
        return False

    def get_camera_settings(
        self, hardware_id: str | None = None
    ) -> dict[str, Any] | None:
        """Запрашивает настройки камер (RGB и segmentation_mode): GET /api/edge/camera-settings."""
        url = "/api/edge/camera-settings"
        params = {"hardware_id": hardware_id} if hardware_id else None
        with contextlib.suppress(Exception):
            resp = self._get_client().get(url, params=params, timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict):
                    return data
        return None
