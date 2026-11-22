import argparse
import itertools
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from edge_agent.client import EdgeAPIClient
from edge_agent.config import load_config
from edge_agent.synthetic import (
    compute_green_area_px,
    detect_aruco_area_px,
    generate_synthetic_frame,
)
from shared.schemas import (
    CaptureMeta,
    HeartbeatHardware,
    HeartbeatRequest,
    HeartbeatSoftware,
    PlantMeasurementMeta,
)

logger = logging.getLogger("edge_agent.simulator")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m edge_agent.simulator",
        description="Инструменты симуляции и тестирования Edge-агента GrowVision",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    seed_parser = subparsers.add_parser(
        "seed",
        help="Посев истории плановых кадров на сервер через Edge API",
    )
    seed_parser.add_argument(
        "--installation",
        type=int,
        required=True,
        help="Идентификатор установки (ID)",
    )
    seed_parser.add_argument(
        "--frames",
        type=int,
        default=48,
        help="Количество плановых кадров на каждую камеру (по умолчанию 48)",
    )
    seed_parser.add_argument(
        "--step-hours",
        type=float,
        default=1.0,
        help="Шаг времени между кадрами в часах (по умолчанию 1.0)",
    )
    seed_parser.add_argument(
        "--start",
        type=str,
        default=None,
        help="Временная метка первого кадра в формате ISO (например: 2026-11-20T00:00:00Z)",
    )
    seed_parser.add_argument(
        "--cameras",
        type=str,
        default="all",
        help="Список hardware_id камер через запятую или 'all' (по умолчанию 'all')",
    )
    seed_parser.add_argument(
        "--from-dir",
        type=str,
        default=None,
        help="Путь к каталогу с готовыми JPEG-снимками (при нехватке берутся по кругу)",
    )

    return parser.parse_args(argv)


def run_seed(args: argparse.Namespace) -> int:
    """Выполняет посев плановых кадров на сервер."""
    try:
        config = load_config()
        config.validate_api_key()
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"Ошибка конфигурации агента: {exc}\n")
        return 1

    client = EdgeAPIClient(
        base_url=config.server_url,
        api_key=config.edge_api_key,
        upload_timeout=config.upload_timeout_sec,
    )

    # 1. Получаем список активных установок и привязанных камер через Heartbeat
    hb_req = HeartbeatRequest(
        software=HeartbeatSoftware(
            edge_version="simulator-0.1.0",
            python_version=sys.version.split()[0],
            model_name="simulator",
        ),
        hardware=HeartbeatHardware(
            board_model="Simulator Host",
            os_info="Linux Simulator",
            uptime_sec=1000,
            cpu_temp_c=40.0,
            cpu_usage_percent=5.0,
            ram_used_mb=256,
            ram_total_mb=2048,
            disk_free_gb=10.0,
            gpio_mode="simulator",
        ),
        offline_queue_count=0,
        detected_cameras=[],
    )

    hb_resp = client.send_heartbeat(hb_req)
    if hb_resp is None:
        sys.stderr.write(
            f"Ошибка: Не удалось связаться с сервером {config.server_url}. "
            "Проверьте, что сервер запущен и доступен.\n"
        )
        return 1

    # 2. Поиск установки
    installation_id: int = args.installation
    target_inst = next(
        (inst for inst in hb_resp.active_installations if inst.id == installation_id),
        None,
    )
    if target_inst is None:
        available_ids = [inst.id for inst in hb_resp.active_installations]
        sys.stderr.write(
            f"Ошибка: Установка #{installation_id} не найдена среди активных установок агента. "
            f"Доступные активные установки: {available_ids}. "
            "Убедитесь, что установка создана и не архивирована.\n"
        )
        return 1

    if not target_inst.camera_hardware_ids:
        sys.stderr.write(
            f"Ошибка: К установке #{installation_id} не привязано ни одной камеры. "
            "Привяжите камеру к установке перед посевом истории кадров.\n"
        )
        return 1

    # 3. Валидация выбранных камер
    if args.cameras.strip().lower() == "all":
        selected_cameras = target_inst.camera_hardware_ids
    else:
        requested = [c.strip() for c in args.cameras.split(",") if c.strip()]
        for cam in requested:
            if cam not in target_inst.camera_hardware_ids:
                sys.stderr.write(
                    f"Ошибка: Камера '{cam}' не привязана к установке #{installation_id}. "
                    f"Привязанные камеры установки: {target_inst.camera_hardware_ids}\n"
                )
                return 1
        selected_cameras = requested

    # 4. Определение временной шкалы
    frames_count: int = max(1, args.frames)
    step_hours: float = max(0.01, args.step_hours)

    if args.start:
        try:
            start_str = args.start.strip().replace("Z", "+00:00")
            start_time = datetime.fromisoformat(start_str).astimezone(UTC)
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(
                f"Ошибка: Некорректный формат времени --start='{args.start}': {exc}\n"
            )
            return 1
    else:
        now_utc = datetime.now(UTC)
        start_time = now_utc - timedelta(hours=frames_count * step_hours)

    # 5. Подготовка источника кадров
    file_cycler = None
    if args.from_dir:
        dir_path = Path(args.from_dir)
        if not dir_path.is_dir():
            sys.stderr.write(f"Ошибка: Каталог снимков не найден: {dir_path}\n")
            return 1
        jpeg_files = sorted(
            [f for f in dir_path.glob("*") if f.suffix.lower() in (".jpg", ".jpeg")]
        )
        if not jpeg_files:
            sys.stderr.write(
                f"Ошибка: В каталоге {dir_path} не найдено файлов JPEG (.jpg, .jpeg)\n"
            )
            return 1
        file_cycler = itertools.cycle(jpeg_files)

    print(
        f"Запуск посева кадров: установка #{installation_id}, камер: {len(selected_cameras)} "
        f"({', '.join(selected_cameras)}), кадров на камеру: {frames_count}, шаг: {step_hours} ч..."
    )

    success_count = 0
    duplicate_count = 0
    error_count = 0

    # 6. Цикл генерации и отправки кадров
    for i in range(frames_count):
        frame_time = start_time + i * timedelta(hours=step_hours)
        time_iso = frame_time.strftime("%Y-%m-%dT%H:%M:%SZ")

        for cam_hw_id in selected_cameras:
            if file_cycler is not None:
                img_path = next(file_cycler)
                raw_bytes = img_path.read_bytes()
            else:
                raw_bytes = generate_synthetic_frame(
                    frame_index=i,
                    camera_id=cam_hw_id,
                )

            # При посеве аннотированное изображение равно исходному
            ann_bytes = raw_bytes
            area_px = compute_green_area_px(raw_bytes)
            aruco_area_px = detect_aruco_area_px(raw_bytes)

            measurements = (
                [PlantMeasurementMeta(plant_index=0, area_px=area_px)]
                if area_px > 0
                else []
            )

            meta = CaptureMeta(
                installation_id=installation_id,
                camera_hardware_id=cam_hw_id,
                captured_at=time_iso,
                is_manual=False,
                aruco_area_px=aruco_area_px,
                command_id=None,
                measurements=measurements,
            )

            status_code, resp_body = client.upload_capture(
                meta,
                raw_bytes,
                ann_bytes,
            )

            if status_code == 201:
                success_count += 1
            elif status_code == 200:
                duplicate_count += 1
            else:
                error_count += 1
                sys.stderr.write(
                    f"Предупреждение: Сбой загрузки кадра {cam_hw_id} @ {time_iso}: "
                    f"HTTP {status_code} ({resp_body})\n"
                )

    total_uploaded = success_count + duplicate_count
    print(
        f"Посев успешно завершён: новых кадров: {success_count}, существующих дублей: {duplicate_count}, "
        f"ошибок: {error_count} (всего обработано: {total_uploaded})."
    )
    return 0 if error_count == 0 else 1


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if args.subcommand == "seed":
        exit_code = run_seed(args)
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
