import os
import stat
import subprocess
import sys
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import installer


def test_install_sh_exists_and_executable():
    """install.sh существует, исполняемый и пробрасывает аргументы в scripts/installer.py."""
    repo_root = Path(__file__).resolve().parent.parent
    install_sh = repo_root / "install.sh"
    assert install_sh.is_file()
    mode = install_sh.stat().st_mode
    assert mode & stat.S_IXUSR
    content = install_sh.read_text(encoding="utf-8")
    assert "scripts/installer.py" in content
    assert '"$@"' in content


@pytest.mark.parametrize(
    ("arg", "expected_id", "expected_components"),
    [
        ("1", "1", ("server", "edge_agent", "telegram_bot", "cli")),
        ("full", "1", ("server", "edge_agent", "telegram_bot", "cli")),
        ("2", "2", ("server",)),
        ("server", "2", ("server",)),
        ("3", "3", ("edge_agent",)),
        ("edge", "3", ("edge_agent",)),
        ("4", "4", ("telegram_bot",)),
        ("bot", "4", ("telegram_bot",)),
        ("5", "5", ("cli",)),
        ("cli", "5", ("cli",)),
        ("6", "6", ("server", "edge_agent", "telegram_bot", "cli")),
        ("dev", "6", ("server", "edge_agent", "telegram_bot", "cli")),
        ("mock", "6", ("server", "edge_agent", "telegram_bot", "cli")),
    ],
)
def test_resolve_profile_all_variants(
    arg: str, expected_id: str, expected_components: tuple[str, ...]
):
    """Все 6 профилей и их строковые алиасы корректно разрешаются."""
    spec = installer.resolve_profile(arg)
    assert spec.id == expected_id
    assert spec.components == expected_components


def test_resolve_profile_invalid_raises():
    with pytest.raises(ValueError):
        installer.resolve_profile("unknown-profile")


@pytest.mark.parametrize("topo", ["A", "B", "C", "a", "b", "c"])
def test_resolve_topology_valid(topo: str):
    assert installer.resolve_topology(topo) == topo.upper()


def test_resolve_topology_invalid_raises():
    with pytest.raises(ValueError):
        installer.resolve_topology("D")


def test_topologies_abc_url_routing():
    """Топологии A, B, C корректно маршрутизируют SERVER_URL для компонентов."""
    full_profile = installer.resolve_profile("full")
    remote_url = "http://100.64.0.10:8000"

    ov_a = installer._compute_component_overrides(
        full_profile, "A", remote_url, "ek", "ck"
    )
    assert ov_a["edge_agent"]["SERVER_URL"] == "http://127.0.0.1:8000"
    assert ov_a["telegram_bot"]["SERVER_URL"] == "http://127.0.0.1:8000"
    assert ov_a["cli"]["SERVER_URL"] == "http://127.0.0.1:8000"

    ov_b = installer._compute_component_overrides(
        full_profile, "B", remote_url, "ek", "ck"
    )
    assert ov_b["telegram_bot"]["SERVER_URL"] == "http://127.0.0.1:8000"
    assert ov_b["edge_agent"]["SERVER_URL"] == remote_url

    ov_c = installer._compute_component_overrides(
        full_profile, "C", remote_url, "ek", "ck"
    )
    assert ov_c["telegram_bot"]["SERVER_URL"] == remote_url
    assert ov_c["edge_agent"]["SERVER_URL"] == remote_url
    assert ov_c["cli"]["SERVER_URL"] == remote_url

    dev_profile = installer.resolve_profile("dev")
    ov_dev = installer._compute_component_overrides(
        dev_profile, "A", "http://127.0.0.1:8000", "ek", "ck"
    )
    assert ov_dev["server"]["MOCK_MODE"] == "true"
    assert ov_dev["edge_agent"]["HARDWARE_MODE"] == "mock"
    assert ov_dev["edge_agent"]["MOCK_CAMERAS"] == "true"


def test_dependency_manager_parse_and_compare(tmp_path: Path):
    """DependencyManager парсит requirements.txt и сравнивает версии."""
    req = tmp_path / "requirements.txt"
    req.write_text(
        "# comment\n"
        "aiogram>=3.4.0,<4.0.0\n"
        "httpx==0.27.2  # inline\n"
        "pydantic>=2.0.0\n"
        "-r other.txt\n",
        encoding="utf-8",
    )
    specs = installer.DependencyManager.parse_requirements(req)
    assert [s.name for s in specs] == ["aiogram", "httpx", "pydantic"]
    assert specs[0].constraints == ((">=", "3.4.0"), ("<", "4.0.0"))

    assert installer.DependencyManager.compare_versions("3.10.0", ">=", "3.4.0") is True
    assert installer.DependencyManager.compare_versions("3.10.0", "<", "4.0.0") is True
    assert installer.DependencyManager.compare_versions("4.0.0", "<", "4.0.0") is False
    assert (
        installer.DependencyManager.compare_versions("0.27.2", "==", "0.27.2") is True
    )
    assert (
        installer.DependencyManager.compare_versions("0.27.1", "==", "0.27.2") is False
    )
    assert installer.DependencyManager.compare_versions("2.0.5", "~=", "2.0.0") is True
    assert installer.DependencyManager.compare_versions("2.1.0", "~=", "2.0") is True
    assert installer.DependencyManager.compare_versions("2.1.0", "~=", "2.0.0") is False


def test_dependency_manager_check_and_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Проверка check_component, ensure_venv и install_requirements с моками."""
    (tmp_path / "server").mkdir()
    (tmp_path / "server" / "requirements.txt").write_text(
        "fastapi>=0.110.0\nuvicorn==0.30.0\nmissing-pkg>=1.0.0\n",
        encoding="utf-8",
    )
    venv_py = tmp_path / "server" / ".venv" / "bin" / "python"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_text("", encoding="utf-8")

    monkeypatch.setattr(
        installer.DependencyManager,
        "get_installed_versions",
        classmethod(lambda cls, py: {"fastapi": "0.115.0", "uvicorn": "0.29.0"}),
    )

    report = installer.DependencyManager.check_component(tmp_path, "server")
    assert report.venv_exists is True
    assert report.ok is False
    assert any("fastapi==0.115.0" in s for s in report.satisfied)
    assert any("missing-pkg" in m for m in report.missing)
    assert any("uvicorn==0.30.0" in o for o in report.outdated)

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append([str(c) for c in cmd])
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    new_venv = tmp_path / "new_venv"
    py = installer.DependencyManager.ensure_venv(new_venv, system_site_packages=True)
    assert py == new_venv / "bin" / "python"
    assert "--system-site-packages" in calls[0]

    installed = installer.DependencyManager.install_requirements(
        py, tmp_path / "server" / "requirements.txt"
    )
    assert installed is True
    assert any("-r" in c for c in calls[-1])


def test_hardware_detector_rpi_and_pc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """HardwareDetector корректно определяет RPi с камерами/GPIO и обычный ПК."""
    dev_dir = tmp_path / "dev"
    (dev_dir / "v4l" / "by-id").mkdir(parents=True)
    (dev_dir / "video0").write_text("", encoding="utf-8")
    (dev_dir / "v4l" / "by-id" / "usb-cam-video-index0").write_text(
        "", encoding="utf-8"
    )
    (dev_dir / "gpiomem").write_text("", encoding="utf-8")
    (dev_dir / "gpiochip0").write_text("", encoding="utf-8")

    model_file = tmp_path / "model"
    model_file.write_text("Raspberry Pi 5 Model B Rev 1.0\x00", encoding="utf-8")

    monkeypatch.setattr(
        installer.shutil,
        "which",
        lambda name: (
            f"/usr/bin/{name}" if name in {"systemctl", "v4l2-ctl", "netbird"} else None
        ),
    )

    detector = installer.HardwareDetector(
        dev_dir=dev_dir,
        proc_model_path=model_file,
        fw_model_path=tmp_path / "nonexistent",
        systemd_run_dir=tmp_path / "run_systemd",
    )
    info = detector.detect()
    assert info.is_raspberry_pi is True
    assert "Raspberry Pi 5" in (info.rpi_model or "")
    assert len(info.video_devices) == 1
    assert len(info.v4l_by_id_devices) == 1
    assert len(info.gpio_devices) == 2
    assert info.tools["systemd"] is True
    assert info.tools["v4l2-ctl"] is True
    assert info.tools["netbird"] is True
    assert info.tools["tailscale"] is False

    # Случай без RPi и без /dev устройств
    empty_detector = installer.HardwareDetector(
        dev_dir=tmp_path / "empty_dev",
        proc_model_path=tmp_path / "missing1",
        fw_model_path=tmp_path / "missing2",
        systemd_run_dir=tmp_path / "missing_systemd",
    )
    empty_info = empty_detector.detect()
    assert empty_info.is_raspberry_pi is False
    assert empty_info.video_devices == []
    assert empty_info.gpio_devices == []


def test_env_manager_preserves_secrets_and_filters_keys(tmp_path: Path):
    """EnvManager фильтрует ключи, генерирует секреты и сохраняет существующие."""
    ex = tmp_path / ".env.example"
    ex.write_text(
        "SERVER_HOST=0.0.0.0\n"
        "EDGE_API_KEY=...\n"
        "CLIENT_API_KEY=...\n"
        "UNSUPPORTED_FAKE_KEY=should_be_dropped\n",
        encoding="utf-8",
    )
    target = tmp_path / ".env"

    # Первый запуск: генерация секретов и фильтрация
    first = installer.EnvManager.build_env("server", ex, target, overrides={})
    assert "UNSUPPORTED_FAKE_KEY" not in first
    assert first["EDGE_API_KEY"] not in ("", "...")
    assert len(first["EDGE_API_KEY"]) >= 16
    installer.EnvManager.write_env(target, first, secret_mode=True)

    if os.name == "posix":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600

    # Повторный запуск без overwrite_secrets: секреты сохраняются
    second = installer.EnvManager.build_env(
        "server", ex, target, overrides={}, overwrite_secrets=False
    )
    assert second["EDGE_API_KEY"] == first["EDGE_API_KEY"]
    assert second["CLIENT_API_KEY"] == first["CLIENT_API_KEY"]

    # С overwrite_secrets=True и пустым override: секреты перегенерируются
    third = installer.EnvManager.build_env(
        "server",
        ex,
        target,
        overrides={"EDGE_API_KEY": ""},
        overwrite_secrets=True,
    )
    assert third["EDGE_API_KEY"] != first["EDGE_API_KEY"]


def test_network_checker_vpn_and_health(monkeypatch: pytest.MonkeyPatch):
    """NetworkChecker проверяет NetBird/Tailscale и fallback HTTP -> TCP."""
    monkeypatch.setattr(
        installer.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in {"netbird", "tailscale"} else None,
    )

    def fake_run(cmd, **kwargs):
        if cmd[0] == "netbird":
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout="Status: Connected\nNetBird IP: 100.64.10.5/16\n",
                stderr="",
            )
        if cmd[0] == "tailscale":
            return subprocess.CompletedProcess(
                cmd, 0, stdout="100.100.1.2\n", stderr=""
            )
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    nb = installer.NetworkChecker.check_netbird()
    assert nb.installed is True
    assert nb.connected is True
    assert nb.ip == "100.64.10.5"

    ts = installer.NetworkChecker.check_tailscale()
    assert ts.installed is True
    assert ts.connected is True
    assert ts.ip == "100.100.1.2"

    # Health HTTP 200
    resp_mock = MagicMock()
    resp_mock.status = 200
    resp_mock.__enter__ = lambda s: s
    resp_mock.__exit__ = lambda s, *a: None
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=3.0: resp_mock)
    hc_ok = installer.NetworkChecker.check_server_health("http://127.0.0.1:8000")
    assert hc_ok.reachable is True
    assert hc_ok.method == "http"

    # Health fallback на TCP при URLError
    def raise_url_err(req, timeout=3.0):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", raise_url_err)
    sock_mock = MagicMock()
    sock_mock.__enter__ = lambda s: s
    sock_mock.__exit__ = lambda s, *a: None
    monkeypatch.setattr(
        installer.socket, "create_connection", lambda addr, timeout=3.0: sock_mock
    )
    hc_tcp = installer.NetworkChecker.check_server_health("http://127.0.0.1:8000")
    assert hc_tcp.reachable is True
    assert hc_tcp.method == "tcp"

    # Ошибка и по TCP
    def raise_os_err(addr, timeout=3.0):
        raise OSError("unreachable")

    monkeypatch.setattr(installer.socket, "create_connection", raise_os_err)
    hc_fail = installer.NetworkChecker.check_server_health("http://127.0.0.1:8000")
    assert hc_fail.reachable is False


def test_systemd_generator_creates_valid_units(tmp_path: Path):
    """SystemdGenerator создаёт корректные .service файлы с путями к .venv."""
    repo_root = tmp_path / "GrowVision"
    out_dir = tmp_path / "systemd_out"
    created = installer.SystemdGenerator.generate_units(
        repo_root=repo_root,
        output_dir=out_dir,
        components=("server", "edge_agent", "telegram_bot"),
        user="growuser",
    )
    assert len(created) == 3

    srv = (out_dir / "growvision-server.service").read_text(encoding="utf-8")
    assert f"WorkingDirectory={repo_root}" in srv
    assert f"ExecStart={repo_root}/server/.venv/bin/python -m server.main" in srv
    assert "User=growuser" in srv

    edge = (out_dir / "growvision-edge.service").read_text(encoding="utf-8")
    assert (
        f"ExecStart={repo_root}/edge_agent/.venv/bin/python -m edge_agent.main" in edge
    )

    bot = (out_dir / "growvision-bot.service").read_text(encoding="utf-8")
    assert (
        f"ExecStart={repo_root}/clients/telegram_bot/.venv/bin/python -m clients.telegram_bot.main"
        in bot
    )
    assert "growvision-server.service" in bot

    cmds = installer.SystemdGenerator.install_instructions(created)
    assert any("/etc/systemd/system/" in c for c in cmds)


def test_cli_check_non_interactive_makes_no_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    """CLI --check --non-interactive не создаёт .env и .venv в рабочем каталоге."""
    for comp_dir in ("server", "edge_agent", "clients/telegram_bot", "clients/cli"):
        (tmp_path / comp_dir).mkdir(parents=True)
        (tmp_path / comp_dir / "requirements.txt").write_text("", encoding="utf-8")
        (tmp_path / comp_dir / ".env.example").write_text(
            "SERVER_URL=http://127.0.0.1:8000\n", encoding="utf-8"
        )

    monkeypatch.setattr(
        installer.NetworkChecker,
        "check_server_health",
        staticmethod(
            lambda url, timeout=3.0: installer.HealthCheckResult(
                url=url, reachable=False, method="tcp", detail="offline"
            )
        ),
    )

    rc = installer.run_installer(
        [
            "--check",
            "--non-interactive",
            "--profile",
            "6",
            "--topology",
            "A",
            "--repo-root",
            str(tmp_path),
        ]
    )
    assert rc == 0
    assert not (tmp_path / "server" / ".env").exists()
    assert not (tmp_path / "server" / ".venv").exists()
    out = capsys.readouterr().out
    assert "Режим --check: изменения на диск не вносились." in out
