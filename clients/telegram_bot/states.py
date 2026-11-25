from aiogram.fsm.state import State, StatesGroup


class AuthStates(StatesGroup):
    """FSM-состояния авторизации."""

    waiting_password = State()
    waiting_full_name = State()
    waiting_login = State()  # обратная совместимость


class InstallationCreateStates(StatesGroup):
    """FSM-состояния пошагового мастера создания установки."""

    waiting_name = State()
    waiting_crop = State()
    waiting_light_pin = State()
    waiting_flash_pin = State()
    waiting_aruco = State()


class InstallationEditStates(StatesGroup):
    """FSM-состояния редактирования установки."""

    waiting_field = State()
    waiting_value = State()


class InstallationDeleteStates(StatesGroup):
    """FSM-состояния подтверждения удаления/архивации."""

    waiting_confirm_delete = State()
    waiting_confirm_archive = State()


class CameraBindStates(StatesGroup):
    """FSM-состояния привязки камеры к установке."""

    waiting_installation_selection = State()


class TimelapseStates(StatesGroup):
    """FSM-состояния просмотра и сборки таймлапса."""

    browsing_frames = State()
    selecting_camera_for_video = State()
    waiting_duration = State()
    waiting_fps = State()
    confirming_build = State()


class SystemSettingsStates(StatesGroup):
    """FSM-состояния изменения системных настроек."""

    waiting_interval = State()
    waiting_delay = State()


class CaptureDelayStates(StatesGroup):
    """FSM-состояния настройки задержки съёмки."""

    waiting_delay = State()


class ProfileStates(StatesGroup):
    """FSM-состояния управления профилем."""

    waiting_new_name = State()


class SessionStates(StatesGroup):
    """FSM-состояния управления сессиями."""

    waiting_rename_name = State()


class ControlColorStates(StatesGroup):
    """FSM-состояния задания HEX-цвета для RGB-лампы."""

    waiting_hex = State()


class ControlDayNightStates(StatesGroup):
    """FSM-состояния задания интервала светового дня (HH:MM-HH:MM)."""

    waiting_interval = State()
