"""Uji clipboard/paste dengan runner palsu (tanpa clipboard asli)."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paste_teks import clipboard


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
    # Keycode Ctrl=29, V=47 (42=Shift — bug lama mengirim Shift+V).
    assert captured[0][0] == ["ydotool", "key", "29:1", "47:1", "47:0", "29:0"]


def test_detect_session(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    assert clipboard.detect_session() == "wayland"
    monkeypatch.delenv("WAYLAND_DISPLAY")
    monkeypatch.setenv("DISPLAY", ":0")
    assert clipboard.detect_session() == "x11"
