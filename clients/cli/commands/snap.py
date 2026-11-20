import contextlib
from pathlib import Path
from typing import Any

import httpx
import typer
from rich.table import Table

from clients.cli.client import APIClient
from clients.cli.formatting import console, err_console, print_debug_info
from clients.cli.resolver import resolve_installation_target

snap_app = typer.Typer(
    name="snap", help="Ручная съёмка и экспорт таймлапса", no_args_is_help=True
)


def _get_timeout(target: int | str, client: APIClient) -> float:
    if target == "all":
        try:
            items = client.request(
                method="GET",
                url="/api/installations",
                context="get active count",
                params={"include_archived": False},
            )
            n = len(items) if isinstance(items, list) else 1
        except (httpx.HTTPError, SystemExit):
            n = 1
        return float(30 + 10 * n)
    return 30.0


@snap_app.command(name="info")
def snap_info() -> None:
    """Справка по разделу snap."""
    info_text = """[bold cyan]Раздел snap — Ручные снимки и экспорт таймлапса[/bold cyan]

[bold]Назначение:[/bold] Выполнение неплановых тестовых снимков и сборка MP4-видео таймлапса.
[bold]Минимальная роль:[/bold] Biologist / Developer.

[bold]Список подкоманд:[/bold]
  [green]snap info[/green]                                              - Показать эту справку
  [green]snap take <target> [--download <папка>][/green]                - Выполнить ручной снимок
  [green]snap video <target> [--camera-id <id>] [--step N] [--out путь][/green] - Собрать видео таймлапса

[bold]Примеры:[/bold]
  snap take 1
  snap take all --download ./photos
  snap video 1
  snap video "Установка 1" --step 2 --out ./my_video.mp4
"""
    console.print(info_text)


@snap_app.command(name="take")
def snap_take(
    target: str = typer.Argument(..., help="ID, название установки или 'all'"),
    download: str = typer.Option(
        None, "--download", "-d", help="Папка для скачивания снимков (raw и ann)"
    ),
) -> None:
    """Выполнение ручного тестового снимка (Biologist / Developer)."""
    client = APIClient()
    unit_target = resolve_installation_target(
        client=client,
        target_str=target,
        context="snap take",
        allow_all=True,
        include_archived=False,
    )

    timeout = _get_timeout(unit_target, client)
    target_str = (
        f"установки #{unit_target}"
        if isinstance(unit_target, int)
        else "всех активных установок"
    )
    console.print(f"[dim]Выполняется съёмка для {target_str}...[/dim]")

    resp = client.request(
        method="POST",
        url="/api/control/snap",
        context="snap take",
        json_data={"target": unit_target},
        timeout=timeout,
    )

    if not isinstance(resp, dict):
        return

    status = resp.get("status")
    result_msg = resp.get("result_message", "")

    if status == "error":
        print_debug_info(resp.get("debug_info"))
        err_console.print(result_msg or "Ошибка выполнения снимка")
        raise typer.Exit(code=1)

    captures = resp.get("captures") or []
    if not captures:
        console.print(f"[yellow]{result_msg or 'Снимки не получены.'}[/yellow]")
    else:
        table = Table(title="Полученные снимки", header_style="bold cyan")
        table.add_column("Установка", justify="right")
        table.add_column("Камера", justify="right")
        table.add_column("Время (UTC)", justify="center")
        table.add_column("Площадь", justify="right")
        table.add_column("URL оригинала", justify="left")
        table.add_column("URL разметки", justify="left")

        for cap in captures:
            table.add_row(
                str(cap.get("installation_id")),
                str(cap.get("camera_id")),
                str(cap.get("captured_at")),
                str(cap.get("area_display") or "—"),
                str(cap.get("raw_url")),
                str(cap.get("annotated_url")),
            )

        console.print(table)

        # Скачивание файлов, если указана папка
        if download:
            download_dir = Path(download)
            download_dir.mkdir(parents=True, exist_ok=True)
            console.print(
                f"[dim]Скачивание снимков в директорию {download_dir}...[/dim]"
            )

            # Для скачивания статики используем HTTP-клиент
            with client._get_client(timeout=30.0) as dl_client:
                for cap in captures:
                    for url_key in ("raw_url", "annotated_url"):
                        rel_url = cap.get(url_key)
                        if rel_url:
                            filename = Path(rel_url).name
                            dest_file = download_dir / filename
                            try:
                                r_file = dl_client.get(rel_url)
                                r_file.raise_for_status()
                                dest_file.write_bytes(r_file.content)
                                console.print(f"[green]Сохранен: {dest_file}[/green]")
                            except httpx.HTTPError as e:
                                err_console.print(
                                    f"[red]Ошибка скачивания {rel_url}: {e}[/red]"
                                )

    print_debug_info(resp.get("debug_info"))


@snap_app.command(name="video")
def snap_video(
    target: str = typer.Argument(
        ..., help="ID или название установки (включая архивные)"
    ),
    camera_id: int = typer.Option(
        None, "--camera-id", help="ID камеры (если у установки несколько камер)"
    ),
    step: int = typer.Option(
        1, "--step", help="Шаг прореживания кадров (целое число >= 1)"
    ),
    out: str = typer.Option(
        None, "--out", "-o", help="Путь для сохранения видео (или фото)"
    ),
) -> None:
    """Сборка MP4-таймлапса или экспорт фото (Biologist / Developer)."""
    if step < 1:
        err_console.print(
            "Ошибка (snap video): Параметр --step должен быть целым числом не меньше 1.\n",
            style="bold red",
        )
        raise typer.Exit(code=1)

    client = APIClient()
    unit_id = resolve_installation_target(
        client=client,
        target_str=target,
        context="snap video",
        allow_all=False,
        include_archived=True,
    )

    payload: dict[str, Any] = {"step": step}
    if camera_id is not None:
        payload["camera_id"] = camera_id

    console.print(
        "[dim]Сборка видео... (длинные таймлапсы могут собираться несколько минут)[/dim]"
    )

    stream_gen, (_, resp_headers) = client.download_stream(
        method="POST",
        url=f"/api/timelapse/{unit_id}/video",
        context="snap video",
        json_data=payload,
    )

    content_type = resp_headers.get("content-type", "").lower()
    is_jpeg = "image/jpeg" in content_type

    if out:
        out_path = Path(out)
        if is_jpeg and out_path.suffix.lower() != ".jpg":
            out_path = out_path.with_suffix(".jpg")
    else:
        ext = ".jpg" if is_jpeg else ".mp4"
        out_path = Path(f"./timelapse_unit_{unit_id}{ext}")

    part_path = out_path.with_name(f"{out_path.name}.part")

    try:
        part_path.parent.mkdir(parents=True, exist_ok=True)
        with open(part_path, "wb") as f:
            f.writelines(stream_gen)
        # Переименовываем .part в финальный файл
        if out_path.exists():
            out_path.unlink()
        part_path.rename(out_path)
    except Exception as e:
        if part_path.exists():
            with contextlib.suppress(OSError):
                part_path.unlink()
        err_console.print(
            "Ошибка (snap video): Загрузка видео прервана. Повторите команду snap video.\n",
            style="bold red",
        )
        raise typer.Exit(code=1) from e

    file_size_bytes = out_path.stat().st_size
    if file_size_bytes >= 1024 * 1024:
        size_str = f"{file_size_bytes / (1024 * 1024):.2f} МБ"
    else:
        size_str = f"{file_size_bytes / 1024:.1f} КБ"

    if is_jpeg:
        console.print(
            f"[green]Плановый кадр только один — вместо видео сохранено фото: {out_path} ({size_str})[/green]"
        )
    else:
        console.print(
            f"[green]Видео таймлапса сохранено: {out_path} ({size_str})[/green]"
        )
