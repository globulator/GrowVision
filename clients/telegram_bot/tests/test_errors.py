import httpx

from clients.telegram_bot.utils.errors import ApiError, extract_detail, format_api_error


def test_extract_detail_variants():
    """Проверка извлечения поля detail из различных форматов JSON."""
    assert extract_detail({"detail": "Конфликт"}) == "Конфликт"
    assert (
        extract_detail(
            {"detail": [{"loc": ["body", "pin"], "msg": "Число обязательно"}]}
        )
        == "body -> pin: Число обязательно"
    )
    assert extract_detail({"error": "something"}) == ""
    assert extract_detail("not a dict") == ""


def test_format_api_error_codes():
    """Проверка маппинга статус-кодов по BOT_SPEC.md (раздел 6.2)."""
    # 401 защищенный
    err_401 = ApiError(401, "Неверный пароль")
    res_401 = format_api_error(err_401)
    assert "Сессия устарела" in res_401 or "Войти" in res_401

    # 401 публичный (проблема CLIENT_API_KEY)
    res_401_pub = format_api_error(401, is_public_endpoint=True)
    assert res_401_pub == "Сервис временно недоступен"

    # 403
    err_403 = ApiError(403, "Действие требует роли Developer")
    res_403 = format_api_error(err_403)
    assert res_403 == "Недостаточно прав: Действие требует роли Developer"

    # 404
    err_404 = ApiError(404, "Установка #5 не найдена")
    res_404 = format_api_error(err_404)
    assert "Не найдено: Установка #5 не найдена" in res_404
    assert "Возможно, объект был удален" in res_404

    # 409
    err_409 = ApiError(409, "Установка имеет снимки и не может быть удалена")
    res_409 = format_api_error(err_409)
    assert (
        res_409 == "Действие невозможно: Установка имеет снимки и не может быть удалена"
    )

    # 422
    err_422 = ApiError(422, "Интервал должен быть >= 1")
    res_422 = format_api_error(err_422)
    assert res_422 == "Ошибка данных: Интервал должен быть >= 1"

    # 429
    err_429 = ApiError(429, "Too Many Requests", retry_after=45)
    res_429 = format_api_error(err_429)
    assert "Слишком много попыток входа" in res_429
    assert "45 сек" in res_429

    # 503
    err_503 = ApiError(503, "Heartbeat отсутствует более 90 секунд")
    res_503 = format_api_error(err_503)
    assert (
        res_503
        == "Raspberry Pi сейчас не в сети: Heartbeat отсутствует более 90 секунд"
    )

    # 504 без control
    err_504 = ApiError(504, "RPi не ответила", is_control_command=False)
    res_504 = format_api_error(err_504)
    assert res_504 == "Raspberry Pi не ответила вовремя: RPi не ответила"

    # 504 с control (команда управления или съёмки)
    err_504_ctrl = ApiError(504, "RPi не ответила", is_control_command=True)
    res_504_ctrl = format_api_error(err_504_ctrl)
    assert "Raspberry Pi не ответила вовремя" in res_504_ctrl
    assert "Результат неизвестен, проверьте состояние установки" in res_504_ctrl

    # 500
    err_500 = ApiError(500, "Internal Server Error")
    res_500 = format_api_error(err_500)
    assert res_500 == "Внутренняя ошибка сервера (HTTP 500). Сообщите разработчику"


def test_format_network_errors():
    """Проверка обработки сетевых сбоев httpx."""
    conn_err = httpx.ConnectError("Connection refused")
    timeout_err = httpx.TimeoutException("Read timeout")

    assert (
        format_api_error(conn_err)
        == "Сервер GrowVision временно недоступен. Попробуйте позже"
    )
    assert (
        format_api_error(timeout_err)
        == "Сервер GrowVision временно недоступен. Попробуйте позже"
    )
