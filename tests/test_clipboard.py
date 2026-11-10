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
    # ydotool >= 1.0: `key` HANYA menerima keycode (<kode>:<1|0>).
    # 29=LeftCtrl, 47=V — bukan 42 (Shift) seperti bug v1.0.2, dan bukan
    # "ctrl+v" (nama kunci ditolak) seperti bug v1.2.0.
    assert captured[0][0] == ["ydotool", "key", "29:1", "47:1", "47:0", "29:0"]


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


class _Proc:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


class _FakePopen:
    def __init__(self, cmd, **kw):
        self.cmd, self.kw = cmd, kw
        self.stdin = _FakeStdin() if kw.get("stdin") else None
        _FakePopen.last = self

    def wait(self, timeout=None):
        if _FakePopen.hang:
            raise clipboard.subprocess.TimeoutExpired(self.cmd, timeout)
        return 0


class _FakeStdin:
    def __init__(self):
        self.written = []
        self.closed = False

    def write(self, data):
        self.written.append(data)

    def close(self):
        self.closed = True


def test_forking_tool_is_not_waited_on(monkeypatch):
    """wl-copy mem-fork & tetap hidup: jangan tunggu, jangan tangkap output."""
    _FakePopen.hang = True
    monkeypatch.setattr(clipboard.subprocess, "Popen", _FakePopen)
    proc = clipboard._default_runner(["wl-copy"], input="halo")
    assert proc.returncode == 0          # menggantung = normal, bukan gagal
    kw = _FakePopen.last.kw
    assert kw["stdout"] == clipboard.subprocess.DEVNULL
    assert kw["stderr"] == clipboard.subprocess.DEVNULL
    assert kw["start_new_session"] is True
    assert kw["text"] is True
    # Masukan benar-benar ditulis lalu stdin ditutup.
    assert _FakePopen.last.stdin.written == ["halo"]
    assert _FakePopen.last.stdin.closed is True


def test_forking_tool_finishing_returns_exit_code(monkeypatch):
    _FakePopen.hang = False
    monkeypatch.setattr(clipboard.subprocess, "Popen", _FakePopen)
    proc = clipboard._default_runner(["xclip", "-selection", "clipboard"],
                                     input="halo")
    assert proc.returncode == 0


def test_forking_tool_survives_broken_pipe(monkeypatch):
    _FakePopen.hang = True
    monkeypatch.setattr(clipboard.subprocess, "Popen", _FakePopen)

    def boom(_data):
        raise BrokenPipeError

    monkeypatch.setattr(_FakeStdin, "write", lambda self, d: boom(d))
    assert clipboard._default_runner(["wl-copy"], input="x").returncode == 0


def test_non_forking_tool_captures_output(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw)
        return _Proc(0)

    monkeypatch.setattr(clipboard.subprocess, "run", fake_run)
    clipboard._default_runner(["ydotool", "key", "29:1"])
    assert seen["capture_output"] is True


def test_ydotool_name_syntax_used_as_fallback(monkeypatch):
    """ydotool 0.1.x memakai nama kunci; dicoba setelah keycode."""
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setattr(clipboard.shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(clipboard, "ensure_ydotoold", lambda runner=None: False)
    seq = []

    def runner(cmd, **kw):
        seq.append(cmd)
        # keycode ditolak, nama kunci diterima
        return _Proc(0 if cmd[-1] == "ctrl+v" else 1, err="")

    result = clipboard.simulate_paste(runner=runner)
    assert result.ok is True
    assert len(seq) == 2
    assert seq[0][2] == "29:1"      # percobaan pertama: keycode
    assert seq[1][2] == "ctrl+v"    # cadangan: nama kunci


def test_type_text_uses_ydotool_type(monkeypatch):
    monkeypatch.setattr(clipboard.shutil, "which", lambda n: "/usr/bin/" + n)
    seen = []
    result = clipboard.type_text("halo", runner=lambda cmd, **kw: seen.append(cmd) or _Proc(0))
    assert result.ok is True
    assert seen == [["ydotool", "type", "halo"]]


def test_type_text_reports_failure(monkeypatch):
    monkeypatch.setattr(clipboard.shutil, "which", lambda n: "/usr/bin/" + n)
    result = clipboard.type_text("x", runner=lambda cmd, **kw: _Proc(1, "", "no socket"))
    assert result.ok is False and "no socket" in result.message


def test_type_text_without_ydotool(monkeypatch):
    monkeypatch.setattr(clipboard.shutil, "which", lambda n: None)
    assert clipboard.type_text("x").ok is False


def test_read_text_from_clipboard_wayland(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setattr(clipboard.shutil, "which", lambda n: "/usr/bin/" + n)
    seen = []

    def runner(cmd, **kw):
        seen.append(cmd)
        return _Proc(0, out="isi-clipboard")

    assert clipboard.read_text_from_clipboard(runner=runner) == "isi-clipboard"
    assert seen == [["wl-paste", "--no-newline"]]


def test_read_text_from_clipboard_unavailable(monkeypatch):
    monkeypatch.setattr(clipboard.shutil, "which", lambda n: None)
    assert clipboard.read_text_from_clipboard() is None
