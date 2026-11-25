import signal
import sys
import time

from edge_agent.config import load_config
from edge_agent.orchestrator import EdgeOrchestrator
from shared.logger import setup_logger


def main() -> None:
    """Точка входа запуска Edge-агента для Raspberry Pi 5."""
    config = load_config()
    logger = setup_logger("edge_agent", debug_mode=config.debug_mode)
    logger.info("=" * 60)
    logger.info("GrowVision Edge Agent запускается...")
    cam_mode = (
        "MOCK"
        if (config.hardware_mode == "mock" and config.mock_cameras)
        else "REAL (V4L2)"
    )
    logger.info(f"Режим железа: {config.hardware_mode.upper()}, камеры: {cam_mode}")
    logger.info(f"Сервер: {config.server_url}")
    logger.info("=" * 60)

    try:
        config.validate_api_key()
    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)

    orchestrator = EdgeOrchestrator(config=config)

    def shutdown_handler(signum: int, frame: object) -> None:
        signame = signal.Signals(signum).name
        logger.info(f"Получен сигнал {signame}. Завершение работы...")
        orchestrator.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    orchestrator.start()

    try:
        while orchestrator.running.is_set():
            time.sleep(1.0)
    except (KeyboardInterrupt, SystemExit):
        orchestrator.stop()


if __name__ == "__main__":
    main()
