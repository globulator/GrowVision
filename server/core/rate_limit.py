import threading
import time


class LoginRateLimiter:
    """Ограничитель частоты попыток входа для POST /api/auth/login.

    Не более 5 попыток в минуту на пару (client_type, external_id).
    """

    def __init__(self, max_attempts: int = 5, window_seconds: float = 60.0) -> None:
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._lock = threading.Lock()
        self._attempts: dict[tuple[str, str], list[float]] = {}

    def is_allowed(self, client_type: str, external_id: str) -> tuple[bool, int]:
        """Проверяет возможность совершения попытки и регистрирует её.

        Возвращает (allowed, retry_after_sec).
        """
        now = time.time()
        key = (client_type, external_id)
        with self._lock:
            history = self._attempts.get(key, [])
            # Очищаем попытки старше window_seconds
            cutoff = now - self._window_seconds
            history = [t for t in history if t > cutoff]

            if len(history) >= self._max_attempts:
                oldest = history[0]
                retry_after = max(1, int(self._window_seconds - (now - oldest)) + 1)
                self._attempts[key] = history
                return False, retry_after

            history.append(now)
            self._attempts[key] = history
            return True, 0

    def clear(self) -> None:
        """Очищает историю попыток (для тестов)."""
        with self._lock:
            self._attempts.clear()


login_limiter = LoginRateLimiter()
