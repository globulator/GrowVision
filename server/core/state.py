import threading


class InMemoryState:
    """Хранилище временного состояния в памяти одного воркера сервера.

    Используется для связи command_id -> capture_id и результатов сканирования
    камер на время ожидания клиентских запросов по API_CONTRACT.md п. 3.3, 3.4.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._command_to_capture: dict[int, int] = {}
        self._command_to_scanned_cameras: dict[int, list[str]] = {}

    def bind_command_capture(self, command_id: int, capture_id: int) -> None:
        """Связывает команду со снимком."""
        with self._lock:
            self._command_to_capture[command_id] = capture_id

    def get_capture_by_command(self, command_id: int) -> int | None:
        """Возвращает ID снимка по ID команды."""
        with self._lock:
            return self._command_to_capture.get(command_id)

    def save_scanned_cameras(self, command_id: int, hardware_ids: list[str]) -> None:
        """Сохраняет список обнаруженных камер для команды сканирования."""
        with self._lock:
            self._command_to_scanned_cameras[command_id] = list(hardware_ids)

    def get_scanned_cameras(self, command_id: int) -> list[str] | None:
        """Возвращает список обнаруженных камер для команды сканирования."""
        with self._lock:
            return self._command_to_scanned_cameras.get(command_id)

    def clear(self) -> None:
        """Очищает память процесса (для тестов)."""
        with self._lock:
            self._command_to_capture.clear()
            self._command_to_scanned_cameras.clear()


app_state = InMemoryState()
