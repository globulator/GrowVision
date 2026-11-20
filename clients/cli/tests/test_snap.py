from pathlib import Path

import httpx

from clients.cli.main import app


def test_snap_info_command(cli_runner, temp_session_file):
    """Справка snap info."""
    result = cli_runner.invoke(app, ["snap", "info"])
    assert result.exit_code == 0
    assert "Раздел snap — Ручные снимки и экспорт таймлапса" in result.stdout
    assert "snap take" in result.stdout
    assert "snap video" in result.stdout


def test_snap_take_success_and_download(
    cli_runner, temp_session_file, mock_client_factory, tmp_path: Path
):
    """Команда snap take с выводом таблицы и скачиванием кадров (--download)."""
    download_dir = tmp_path / "downloaded_frames"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/control/snap":
            return httpx.Response(
                200,
                json={
                    "status": "done",
                    "command_id": 88,
                    "captures": [
                        {
                            "installation_id": 1,
                            "camera_id": 2,
                            "captured_at": "2026-11-20T12:00:00Z",
                            "area_display": "50.0 см²",
                            "raw_url": "/media/1/raw_1.jpg",
                            "annotated_url": "/media/1/ann_1.jpg",
                        }
                    ],
                },
            )
        if request.url.path in ("/media/1/raw_1.jpg", "/media/1/ann_1.jpg"):
            return httpx.Response(200, content=b"fake-image-bytes")
        return httpx.Response(404)

    mock_client_factory(handler)

    res = cli_runner.invoke(app, ["snap", "take", "1", "--download", str(download_dir)])
    assert res.exit_code == 0
    assert "50.0 см²" in res.stdout
    assert "/media/1/raw_1.jpg" in res.stdout

    # Проверяем скачанные файлы
    assert (download_dir / "raw_1.jpg").exists()
    assert (download_dir / "ann_1.jpg").exists()
    assert (download_dir / "raw_1.jpg").read_bytes() == b"fake-image-bytes"


def test_snap_take_error_status(cli_runner, temp_session_file, mock_client_factory):
    """Команда snap take при статусе error."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "error",
                "command_id": 99,
                "result_message": "Камера недоступна, попробуйте позже",
            },
        )

    mock_client_factory(handler)
    res = cli_runner.invoke(app, ["snap", "take", "1"])
    assert res.exit_code == 1
    assert "Камера недоступна, попробуйте позже" in (res.stderr + res.stdout)


def test_snap_video_mp4(
    cli_runner, temp_session_file, mock_client_factory, tmp_path: Path
):
    """Команда snap video сохраняет MP4 файл через временный .part."""
    out_file = tmp_path / "result.mp4"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/timelapse/1/video":
            return httpx.Response(
                200,
                headers={"Content-Type": "video/mp4"},
                content=b"mock-mp4-data" * 100,
            )
        return httpx.Response(404)

    mock_client_factory(handler)
    res = cli_runner.invoke(app, ["snap", "video", "1", "--out", str(out_file)])
    assert res.exit_code == 0
    assert "Видео таймлапса сохранено" in res.stdout
    assert out_file.exists()
    assert not out_file.with_name("result.mp4.part").exists()


def test_snap_video_single_frame_jpeg(
    cli_runner, temp_session_file, mock_client_factory, tmp_path: Path
):
    """Команда snap video при 1 кадре сохраняет JPEG с расширением .jpg."""
    out_file = tmp_path / "timelapse.mp4"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "image/jpeg"},
            content=b"single-jpeg-bytes",
        )

    mock_client_factory(handler)
    res = cli_runner.invoke(app, ["snap", "video", "1", "--out", str(out_file)])
    assert res.exit_code == 0
    assert "вместо видео сохранено фото" in res.stdout

    expected_jpg = tmp_path / "timelapse.jpg"
    assert expected_jpg.exists()
    assert expected_jpg.read_bytes() == b"single-jpeg-bytes"


def test_snap_video_invalid_step(cli_runner, temp_session_file):
    """Ошибка при step < 1."""
    res = cli_runner.invoke(app, ["snap", "video", "1", "--step", "0"])
    assert res.exit_code == 1
    assert "Параметр --step должен быть целым числом не меньше 1" in res.output
