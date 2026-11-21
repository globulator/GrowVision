import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """Исключение при вызове HTTP Client API сервера GrowVision."""

    def __init__(
        self,
        status_code: int,
        detail: str = "",
        retry_after: int | None = None,
        is_control_command: bool = False,
    ):
        super().__init__(f"HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail
        self.retry_after = retry_after
        self.is_control_command = is_control_command


def extract_detail(response_json: Any) -> str:
    """Извлекает поле detail из JSON-ответа сервера."""
    if isinstance(response_json, dict):
        detail_val = response_json.get("detail")
        if isinstance(detail_val, str):
            return detail_val
        if isinstance(detail_val, list):
            # Бывает валидационный массив pydantic 422
            msgs = []
            for item in detail_val:
                if isinstance(item, dict) and "msg" in item:
                    loc = " -> ".join(str(x) for x in item.get("loc", []))
                    msgs.append(f"{loc}: {item['msg']}" if loc else item["msg"])
                else:
                    msgs.append(str(item))
            return "; ".join(msgs) if msgs else str(detail_val)
    return ""


def format_api_error(
    error: Exception | int,
    detail: str = "",
    retry_after: int | None = None,
    is_control: bool = False,
    is_public_endpoint: bool = False,
) -> str:
    """Форматирует ошибку сервера по правилам BOT_SPEC.md (раздел 6.2)."""
    # Сетевые ошибки httpx
    if isinstance(error, (httpx.ConnectError, httpx.TimeoutException)):
        return "Сервер GrowVision временно недоступен. Попробуйте позже"

    if isinstance(error, ApiError):
        status_code = error.status_code
        detail = error.detail or detail
        retry_after = (
            error.retry_after if error.retry_after is not None else retry_after
        )
        is_control = error.is_control_command or is_control
    elif isinstance(error, int):
        status_code = error
    elif isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code
        try:
            body = error.response.json()
            extracted = extract_detail(body)
            detail = extracted or detail or error.response.text
        except Exception:  # noqa: BLE001
            detail = detail or error.response.text
        if "retry-after" in error.response.headers:
            try:
                retry_after = int(error.response.headers["retry-after"])
            except ValueError:
                pass
    else:
        return "Получен некорректный ответ от сервера"

    detail_str = detail.strip() if isinstance(detail, str) else ""

    if status_code == 401:
        if is_public_endpoint:
            logger.error(
                "Проверьте CLIENT_API_KEY (получен HTTP 401 на публичном эндпоинте)"
            )
            return "Сервис временно недоступен"
        return "Сессия устарела или не найдены права доступа. Пожалуйста, выполните вход через [ 🔑 Войти ]"

    if status_code == 403:
        msg = (
            f"Недостаточно прав: {detail_str}"
            if detail_str
            else "Недостаточно прав для выполнения действия"
        )
        return msg

    if status_code == 404:
        msg = (
            f"Не найдено: {detail_str}"
            if detail_str
            else "Запрошенный объект не найден"
        )
        return f"{msg}. Возможно, объект был удален."

    if status_code == 409:
        return (
            f"Действие невозможно: {detail_str}"
            if detail_str
            else "Действие невозможно из-за конфликта состояния"
        )

    if status_code == 422:
        return (
            f"Ошибка данных: {detail_str}"
            if detail_str
            else "Ошибка валидации переданных данных"
        )

    if status_code == 429:
        if retry_after is not None:
            return f"Слишком много попыток входа. Повторите через {retry_after} сек."
        return f"Слишком много попыток входа. {detail_str}".strip()

    if status_code == 503:
        return (
            f"Raspberry Pi сейчас не в сети: {detail_str}"
            if detail_str
            else "Raspberry Pi сейчас не в сети"
        )

    if status_code == 504:
        base = (
            f"Raspberry Pi не ответила вовремя: {detail_str}"
            if detail_str
            else "Raspberry Pi не ответила вовремя"
        )
        if is_control:
            return f"{base}\nРезультат неизвестен, проверьте состояние установки."
        return base

    if 500 <= status_code < 600:
        return f"Внутренняя ошибка сервера (HTTP {status_code}). Сообщите разработчику"

    if not detail_str:
        return "Получен некорректный ответ от сервера"

    return f"Ошибка сервера (HTTP {status_code}): {detail_str}"
