"""Uji clipboard/paste dengan runner palsu (tanpa clipboard asli)."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paste_teks import clipboard


def _mk(count=0, stderr=""):
    return subprocess.CompletedProcess(["x"], count, stdout="", stderr=stderr)


def _fake_runner(captured, returncode=0):
    def run(cmd, input=None, **kwargs):
        captured.append((cmd, input))
        return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr="")
    return run


def test_copy_prefers_wl_copy_on_wayland(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: "/usr/bin/" + name)
    captured = []
    result = clipboard.copy_text("halo", runner=_fake_runner(captured))
    assert result.ok and result.tool == "wl-copy"
    assert captured[0][1] == "halo"


def test_copy_falls_back_to_xclip(monkeypatch):
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(clipboard.shutil, "which",
                        lambda name: "/usr/bin/xclip" if name == "xclip" else None)
    captured = []
    result = clipboard.copy_text("halo", runner=_fake_runner(captured))
    assert result.ok and result.tool == "xclip"


def test_copy_fails_without_tools(monkeypatch):
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: None)
    result = clipboard.copy_text("halo", runner=_fake_runner([]))
    assert not result.ok


def test_simulate_paste_uses_ydotool_on_wayland(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: "/usr/bin/" + name)
    captured = []
    result = clipboard.simulate_paste(runner=_fake_runner(captured))
    assert result.ok and result.tool == "ydotool"
    assert captured[0][0][0] == "ydotool"
    # `ydotool key` hanya menerima SATU argumen kunci: "ctrl+v" (bukan
    # rangkaian keycode, dan bukan keycode 42=Shift seperti bug v1.0.2).
    assert captured[0][0] == ["ydotool", "key", "ctrl+v"]


def test_detect_session(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    assert clipboard.detect_session() == "wayland"
    monkeypatch.delenv("WAYLAND_DISPLAY")
    monkeypatch.setenv("DISPLAY", ":0")
    assert clipboard.detect_session() == "x11"


def test_ensure_ydotoold_starts_service(monkeypatch):
    monkeypatch.setattr(clipboard, "_ydotoold_socket_exists", lambda: True)
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        return _mk(count=0)

    assert clipboard.ensure_ydotoold(runner=runner) is True
    assert calls == [["systemctl", "--user", "start", "ydotoold.service"]]


def test_ensure_ydotoold_failure_returns_false():
    assert clipboard.ensure_ydotoold(
        runner=lambda cmd, **kw: _mk(count=4, stderr="no systemd")) is False


def test_ensure_ydotoold_handles_missing_systemctl():
    def boom(cmd, **kw):
        raise OSError("systemctl tidak ada")

    assert clipboard.ensure_ydotoold(runner=boom) is False


def test_paste_retries_once_after_starting_daemon(monkeypatch):
    """Percobaan pertama gagal (daemon mati), lalu berhasil setelah start."""
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(clipboard, "_ydotoold_socket_exists", lambda: True)
    monkeypatch.setattr(clipboard, "_sleep", lambda _s: None)
    seq = []

    def runner(cmd, **kw):
        seq.append(cmd)
        if cmd[0] == "systemctl":
            return _mk(count=0)
        # gagal pada percobaan pertama, berhasil setelahnya
        return _mk(count=0 if len([c for c in seq if c[0] == "ydotool"]) > 1 else 3)

    result = clipboard.simulate_paste(runner=runner)
    assert result.ok and result.tool == "ydotool"
    names = [c[0] for c in seq]
    assert names == ["ydotool", "systemctl", "ydotool"]
