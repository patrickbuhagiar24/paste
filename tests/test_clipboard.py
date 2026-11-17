"""Clipboard/paste tests with a fake runner (no real clipboard)."""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pastex import clipboard


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
    result = clipboard.copy_text("hello", runner=_fake_runner(captured))
    assert result.ok and result.tool == "wl-copy"
    assert captured[0][1] == "hello"


def test_copy_falls_back_to_xclip(monkeypatch):
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(
        clipboard.shutil,
        "which",
        lambda name: "/usr/bin/xclip" if name == "xclip" else None,
    )
    captured = []
    result = clipboard.copy_text("hello", runner=_fake_runner(captured))
    assert result.ok and result.tool == "xclip"


def test_copy_fails_without_tools(monkeypatch):
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: None)
    result = clipboard.copy_text("hello", runner=_fake_runner([]))
    assert not result.ok


def test_simulate_paste_uses_ydotool_on_wayland(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: "/usr/bin/" + name)
    captured = []
    result = clipboard.simulate_paste(runner=_fake_runner(captured))
    assert result.ok and result.tool == "ydotool"
    assert captured[0][0][0] == "ydotool"
    # ydotool >= 1.0: `key` ONLY accepts keycodes (<code>:<1|0>).
    # 29=LeftCtrl, 47=V — not 42 (Shift) as in the v1.0.2 bug, and not
    # "ctrl+v" (key names are rejected) as in the v1.2.0 bug.
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
    assert (
        clipboard.ensure_ydotoold(
            runner=lambda cmd, **kw: _mk(count=4, stderr="no systemd")
        )
        is False
    )


def test_ensure_ydotoold_handles_missing_systemctl():
    def boom(cmd, **kw):
        raise OSError("no systemctl")

    assert clipboard.ensure_ydotoold(runner=boom) is False


def test_paste_retries_once_after_starting_daemon(monkeypatch):
    """The first attempt fails (daemon down), then succeeds after a start."""
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(clipboard, "_ydotoold_socket_exists", lambda: True)
    monkeypatch.setattr(clipboard, "_sleep", lambda _s: None)
    seq = []

    def runner(cmd, **kw):
        seq.append(cmd)
        if cmd[0] == "systemctl":
            return _mk(count=0)
        # fails on the first attempt, succeeds afterwards
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
    proc = clipboard._default_runner(["wl-copy"], input="hello")
    assert proc.returncode == 0  # blocking is expected, not a failure
    kw = _FakePopen.last.kw
    assert kw["stdout"] == clipboard.subprocess.DEVNULL
    assert kw["stderr"] == clipboard.subprocess.DEVNULL
    assert kw["start_new_session"] is True
    assert kw["text"] is True
    # The input really is written, then stdin is closed.
    assert _FakePopen.last.stdin.written == ["hello"]
    assert _FakePopen.last.stdin.closed is True


def test_forking_tool_finishing_returns_exit_code(monkeypatch):
    _FakePopen.hang = False
    monkeypatch.setattr(clipboard.subprocess, "Popen", _FakePopen)
    proc = clipboard._default_runner(["xclip", "-selection", "clipboard"], input="hello")
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
        return _Proc(rc=0)

    monkeypatch.setattr(clipboard.subprocess, "run", fake_run)
    clipboard._default_runner(["ydotool", "key", "29:1"])
    assert seen["capture_output"] is True


def test_ydotool_name_syntax_used_as_fallback(monkeypatch):
    """ydotool 0.1.x uses key names; try them after the keycodes."""
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setattr(clipboard.shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(clipboard, "ensure_ydotoold", lambda runner=None: False)
    seq = []

    def runner(cmd, **kw):
        seq.append(cmd)
        # keycodes rejected, key names accepted
        return _Proc(0 if cmd[-1] == "ctrl+v" else 1, err="")

    result = clipboard.simulate_paste(runner=runner)
    assert result.ok is True
    assert len(seq) == 2
    assert seq[0][2] == "29:1"  # first attempt: keycodes
    assert seq[1][2] == "ctrl+v"  # fallback: key names


def test_type_text_uses_ydotool_type(monkeypatch):
    monkeypatch.setattr(clipboard.shutil, "which", lambda n: "/usr/bin/" + n)
    seen = []
    result = clipboard.type_text(
        "hello", runner=lambda cmd, **kw: seen.append(cmd) or _Proc(rc=0)
    )
    assert result.ok is True
    assert seen == [["ydotool", "type", "hello"]]


def test_type_text_reports_failure(monkeypatch):
    monkeypatch.setattr(clipboard.shutil, "which", lambda n: "/usr/bin/" + n)
    result = clipboard.type_text(
        "x", runner=lambda cmd, **kw: _Proc(1, "", "no socket")
    )
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
        return _Proc(0, out="clipboard-body")

    assert clipboard.read_text_from_clipboard(runner=runner) == "clipboard-body"
    assert seen == [["wl-paste", "--no-newline"]]


def test_read_text_from_clipboard_unavailable(monkeypatch):
    monkeypatch.setattr(clipboard.shutil, "which", lambda n: None)
    assert clipboard.read_text_from_clipboard() is None


def test_copy_text_uses_successful_clipboard_tool_without_readback(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setattr(clipboard.shutil, "which", lambda n: "/usr/bin/" + n)
    captured = []

    def runner(cmd, input=None, **kw):
        captured.append((cmd, input))
        return _Proc(rc=0)

    result = clipboard.copy_text("hello", runner=runner)

    assert result.ok is True
    assert result.tool == "wl-copy"
    assert result.message == "Text copied with wl-copy."
    assert captured == [(["wl-copy"], "hello")]


# --- Wayland: jangan percaya xdotool -------------------------------------


def test_wayland_never_uses_xdotool(monkeypatch):
    """On a Wayland session, xdotool uses XTEST and does NOT reach native
    Wayland clients, yet still exits 0. Reporting "success" in that case is
    misleading, so xdotool must not be used at all there.
    """
    from pastex import clipboard

    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(
        clipboard.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name == "xdotool" else None,
    )
    calls = []
    result = clipboard.simulate_paste(
        runner=lambda cmd: calls.append(cmd) or _Proc(rc=0)
    )
    assert calls == [], f"xdotool dipanggil di Wayland: {calls}"
    assert result.ok is False
    assert "ydotool" in result.message


def test_wayland_message_explains_ydotool_requirement(monkeypatch):
    """Without ydotool on Wayland the message must explain why."""
    from pastex import clipboard

    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: None)
    result = clipboard.simulate_paste(runner=lambda cmd: _Proc(rc=0))
    assert result.ok is False
    assert "Wayland" in result.message
    assert "Ctrl+V" in result.message


def test_x11_paste_marks_result_not_confident(monkeypatch):
    """On X11 xdotool is still used, but its result is marked unconfirmed
    (a return code alone does not prove the text was really pasted)."""
    from pastex import clipboard

    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(
        clipboard.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name == "xdotool" else None,
    )
    result = clipboard.simulate_paste(runner=lambda cmd: _Proc(rc=0))
    assert result.ok is True
    assert result.tool == "xdotool"
    assert result.confident is False


def test_wayland_ydotool_success_is_confident(monkeypatch):
    from pastex import clipboard

    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(
        clipboard.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name == "ydotool" else None,
    )
    monkeypatch.setattr(clipboard, "ensure_ydotoold", lambda **kw: True)
    result = clipboard.simulate_paste(runner=lambda cmd: _Proc(rc=0))
    assert result.ok is True
    assert result.tool == "ydotool"
    assert result.confident is True


# --- Plans: PATH lookups happen once, not on every click ------------------


def test_copy_text_skips_path_lookups_when_a_plan_is_given(monkeypatch):
    """The prewarmed plan keeps shutil.which off the click path."""
    from pastex import clipboard

    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    lookups = []

    def which(name):
        lookups.append(name)
        return f"/usr/bin/{name}"

    monkeypatch.setattr(clipboard.shutil, "which", which)

    plan = clipboard.resolve_copy_plan()
    assert lookups == ["wl-copy", "xclip", "xsel"]

    lookups.clear()
    captured = []
    result = clipboard.copy_text("hello", runner=_fake_runner(captured), plan=plan)

    assert result.ok and result.tool == "wl-copy"
    assert lookups == [], "the click path must not call shutil.which again"


def test_simulate_paste_skips_path_lookups_when_a_plan_is_given(monkeypatch):
    from pastex import clipboard

    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    lookups = []

    def which(name):
        lookups.append(name)
        return f"/usr/bin/{name}"

    monkeypatch.setattr(clipboard.shutil, "which", which)
    monkeypatch.setattr(clipboard, "ensure_ydotoold", lambda **kw: True)

    plan = clipboard.resolve_paste_plan()
    lookups.clear()
    captured = []

    result = clipboard.simulate_paste(runner=_fake_runner(captured), plan=plan)

    assert result.ok and result.tool == "ydotool"
    assert captured[0][0] == ["ydotool", "key", "29:1", "47:1", "47:0", "29:0"]
    assert lookups == []


def test_plans_keep_only_the_tools_that_exist(monkeypatch):
    from pastex import clipboard

    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setattr(
        clipboard.shutil, "which", lambda name: "/usr/bin/xsel" if name == "xsel" else None
    )

    copy_plan = clipboard.resolve_copy_plan()
    paste_plan = clipboard.resolve_paste_plan()

    assert copy_plan.tool_names() == ["xsel"]
    assert copy_plan.candidates == [("xsel", ["xsel", "--clipboard", "--input"])]
    assert paste_plan.candidates == []
    assert bool(paste_plan) is False


def test_prewarm_starts_ydotoold_before_the_first_click(monkeypatch):
    from pastex import clipboard

    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(clipboard, "_ydotoold_socket_exists", lambda: False)
    started = []
    monkeypatch.setattr(
        clipboard, "ensure_ydotoold", lambda **kwargs: started.append(kwargs) or True
    )

    copy_plan, paste_plan = clipboard.prewarm()

    assert started == [{}], "ydotoold must be started in the background at startup"
    assert copy_plan.tool_names() == ["wl-copy", "xclip", "xsel"]
    assert paste_plan.tool_names() == ["ydotool"]


def test_prewarm_leaves_a_running_ydotoold_alone(monkeypatch):
    from pastex import clipboard

    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(clipboard, "_ydotoold_socket_exists", lambda: True)
    started = []
    monkeypatch.setattr(
        clipboard, "ensure_ydotoold", lambda **kwargs: started.append(kwargs) or True
    )

    clipboard.prewarm()

    assert started == []


def test_prewarm_never_starts_ydotoold_on_x11(monkeypatch):
    from pastex import clipboard

    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: f"/usr/bin/{name}")
    started = []
    monkeypatch.setattr(
        clipboard, "ensure_ydotoold", lambda **kwargs: started.append(kwargs) or True
    )

    copy_plan, paste_plan = clipboard.prewarm()

    assert started == []
    assert copy_plan.tool_names()[0] == "xclip"
    assert paste_plan.tool_names() == ["xdotool", "ydotool"]


# --- Portal fallback -------------------------------------------------------


def test_try_portal_is_skipped_when_the_portal_is_unavailable(monkeypatch):
    from pastex import clipboard, portal

    monkeypatch.setattr(portal, "available", lambda: False)

    assert clipboard._try_portal() is None


def test_simulate_paste_falls_back_to_the_portal(monkeypatch):
    """Without ydotool/uinput the portal can still send Ctrl+V without root."""
    from pastex import clipboard, portal

    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: None)
    monkeypatch.setattr(portal, "available", lambda: True)
    monkeypatch.setattr(
        portal, "send_ctrl_v", lambda: (True, "Automatic paste sent through the desktop portal.")
    )

    result = clipboard.simulate_paste(runner=_fake_runner([]))

    assert result.ok is True
    assert result.tool == "portal"
    assert result.confident is True


def test_portal_failure_is_part_of_the_error_message(monkeypatch):
    from pastex import clipboard, portal

    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: None)
    monkeypatch.setattr(portal, "available", lambda: True)
    monkeypatch.setattr(portal, "send_ctrl_v", lambda: (False, "user dismissed the prompt"))

    result = clipboard.simulate_paste(runner=_fake_runner([]))

    assert result.ok is False
    assert "portal: user dismissed the prompt" in result.message


def test_a_broken_portal_never_breaks_the_paste(monkeypatch):
    from pastex import clipboard, portal

    monkeypatch.setattr(clipboard.shutil, "which", lambda name: "/usr/bin/ydotool")
    monkeypatch.setattr(portal, "available", lambda: True)

    def boom():
        raise RuntimeError("portal rusak")

    monkeypatch.setattr(portal, "send_ctrl_v", boom)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")

    # ydotool succeeds, so the portal is never consulted.
    result = clipboard.simulate_paste(runner=_fake_runner([]))
    assert result.ok is True

    # With ydotool failing, the portal error is reported instead of raising.
    failing = clipboard.simulate_paste(runner=_fake_runner([], returncode=1))
    assert failing.ok is False
    assert "portal error" in failing.message


# --- pemilik clipboard Wayland (opt-in, 1.8.0) ---------------------------
class FakeOwner:
    def __init__(self, ok=True, explode=False):
        self.ok = ok
        self.explode = explode
        self.copied: list[str] = []

    def copy(self, text):
        if self.explode:
            raise RuntimeError("helper mati")
        self.copied.append(text)
        return self.ok


def test_owner_copies_without_spawning_a_tool():
    owner = FakeOwner()
    calls = []

    result = clipboard.copy_text("hello", runner=calls.append, owner=owner)

    assert result.ok is True
    assert result.tool == "wayland-owner"
    assert owner.copied == ["hello"]
    assert calls == [], "wl-copy must not be run"


def _plan():
    return clipboard.ToolPlan("wayland", [("wl-copy", ["wl-copy"])])


def test_owner_failure_falls_back_to_the_tools():
    owner = FakeOwner(ok=False)
    calls = []

    def runner(cmd, **_kwargs):
        calls.append(cmd[0])
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    result = clipboard.copy_text("hello", runner=runner, plan=_plan(), owner=owner)

    assert result.ok is True
    assert result.tool == "wl-copy"
    assert calls == ["wl-copy"]


def test_owner_exception_never_breaks_the_copy():
    owner = FakeOwner(explode=True)

    def runner(cmd, **_kwargs):
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    result = clipboard.copy_text("hello", runner=runner, plan=_plan(), owner=owner)

    assert result.ok is True
    assert result.tool == "wl-copy"


def test_module_level_owner_is_used_by_default():
    owner = FakeOwner()
    clipboard.set_clipboard_owner(owner)
    try:
        assert clipboard.clipboard_owner() is owner
        result = clipboard.copy_text("hello", runner=lambda *_a, **_k: None)
        assert result.tool == "wayland-owner"
        assert owner.copied == ["hello"]
    finally:
        clipboard.set_clipboard_owner(None)
    assert clipboard.clipboard_owner() is None


def test_simulate_paste_bounds_every_tool_call(monkeypatch):
    """Every attempt has a timeout so the GUI never waits forever."""
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(clipboard, "ensure_ydotoold", lambda runner=None: False)
    seen = []

    def runner(cmd, **kwargs):
        seen.append(kwargs.get("timeout"))
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no daemon")

    result = clipboard.simulate_paste(runner=runner)

    assert result.ok is False
    assert seen and all(t == clipboard.PASTE_KEY_TIMEOUT for t in seen)


def test_simulate_paste_stops_before_the_portal_when_aborted(monkeypatch):
    """Once the window is restored, the portal must not be touched again."""
    from pastex import clipboard, portal

    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(clipboard, "ensure_ydotoold", lambda runner=None: False)
    monkeypatch.setattr(portal, "available", lambda: True)
    pressed = []
    monkeypatch.setattr(portal, "send_ctrl_v", lambda *a, **k: pressed.append(1) or (True, "ok"))

    result = clipboard.simulate_paste(
        runner=lambda cmd, **kw: _mk(count=1), should_stop=lambda: True
    )

    assert result.ok is False
    assert pressed == []
    assert "aborted" in result.message


def test_the_abort_flag_is_checked_between_the_two_ydotool_syntaxes(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(clipboard, "ensure_ydotoold", lambda runner=None: False)
    calls = []

    def runner(cmd, **kwargs):
        calls.append(cmd)
        return _mk(count=1)

    result = clipboard.simulate_paste(
        runner=runner, should_stop=lambda: len(calls) >= 1
    )

    assert result.ok is False
    assert len(calls) == 1  # the second attempt is skipped


def test_the_private_daemon_is_started_with_our_own_socket(monkeypatch, tmp_path):
    """Distros may ship ydotoold.service as a system unit: we start our own."""
    spawned = []

    socket = tmp_path / "pastex-ydotool.sock"

    def fake_popen(cmd, **kwargs):
        spawned.append((cmd, kwargs))
        # A real daemon creates its socket; here we create it by hand.
        socket.write_text("", encoding="utf-8")

    monkeypatch.setattr(clipboard.shutil, "which", lambda name: "/usr/bin/ydotoold")
    monkeypatch.setattr(clipboard.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(clipboard, "_runtime_dir", lambda: str(tmp_path))
    monkeypatch.setattr(clipboard, "_sleep", lambda _s: None)
    monkeypatch.delenv("YDOTOOL_SOCKET", raising=False)

    ok = clipboard._spawn_private_ydotoold()

    assert spawned, "ydotoold must be started"
    cmd, kwargs = spawned[0]
    assert cmd[0] == "/usr/bin/ydotoold"
    assert "--socket-path" in cmd and str(socket) in cmd
    assert "--socket-own" in cmd
    assert kwargs.get("start_new_session") is True
    # ydotool only talks to our daemon when YDOTOOL_SOCKET is set.
    assert clipboard.os.environ["YDOTOOL_SOCKET"] == str(socket)
    assert ok is True
    clipboard.os.environ.pop("YDOTOOL_SOCKET", None)


def test_ensure_ydotoold_falls_back_to_the_private_daemon(monkeypatch):
    """No user unit on disk, so the private daemon is used."""
    started = []
    monkeypatch.setattr(clipboard, "_ydotoold_socket_exists", lambda: False)
    monkeypatch.setattr(
        clipboard,
        "_spawn_private_ydotoold",
        lambda: started.append(True) or True,
    )

    ok = clipboard.ensure_ydotoold(runner=lambda cmd, **kw: _mk(count=1))

    assert ok is True
    assert started == [True]


def test_ensure_ydotoold_gives_up_when_no_socket_appears(monkeypatch, tmp_path):
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: "/usr/bin/ydotoold")
    monkeypatch.setattr(clipboard.subprocess, "Popen", lambda cmd, **kw: None)
    monkeypatch.setattr(clipboard, "_ydotoold_socket_exists", lambda: False)
    monkeypatch.setattr(clipboard, "_runtime_dir", lambda: str(tmp_path))
    monkeypatch.setattr(clipboard, "_sleep", lambda _s: None)
    monkeypatch.delenv("YDOTOOL_SOCKET", raising=False)

    assert clipboard.ensure_ydotoold(runner=lambda cmd, **kw: _mk(count=1)) is False
    assert "YDOTOOL_SOCKET" not in clipboard.os.environ


def test_ydotoold_socket_lookup_honours_YDOTOOL_SOCKET(monkeypatch, tmp_path):
    socket = tmp_path / "custom.sock"
    monkeypatch.setenv("YDOTOOL_SOCKET", str(socket))
    # No file yet: the lookup must not invent one.
    assert clipboard._ydotoold_socket_exists() is False

    socket.write_text("", encoding="utf-8")

    assert clipboard._ydotoold_socket_exists() is True
    assert str(socket) in clipboard._ydotoold_socket_paths()




def _x11_runner(captured, *, rc=0, out=""):
    """Record the command and answer like a CompletedProcess."""

    def run(cmd, **kwargs):
        captured.append(list(cmd))
        return SimpleNamespace(returncode=rc, stdout=out, stderr="")

    return run


def test_x11_window_unmapped_reads_the_x_server_answer(monkeypatch):
    """Tk's state can lag; the X server says whether the window is mapped."""
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: "/usr/bin/" + name)

    captured = []
    runner = _x11_runner(captured, out="  Map State: IsUnMapped\n")
    assert clipboard.x11_window_unmapped("0x400001", runner=runner) is True
    assert captured == [["xwininfo", "-id", "0x400001"]]

    runner = _x11_runner([], out="  Map State: IsViewable\n")
    assert clipboard.x11_window_unmapped("0x400001", runner=runner) is False

    runner = _x11_runner([], out="  Map State: IsUnviewable\n")
    assert clipboard.x11_window_unmapped("0x400001", runner=runner) is False


def test_x11_window_unmapped_reports_unknown_when_the_query_fails():
    assert clipboard.x11_window_unmapped("0x1", runner=_x11_runner([], rc=1)) is None
    assert (
        clipboard.x11_window_unmapped("0x1", runner=_x11_runner([], out="garbage"))
        is None
    )

    def missing(cmd, **kwargs):
        raise FileNotFoundError("xwininfo")

    assert clipboard.x11_window_unmapped("0x1", runner=missing) is None


def test_force_minimize_asks_the_window_manager_synchronously(monkeypatch):
    """EWMH minimize works where Tk's iconify() is silently dropped."""
    monkeypatch.setattr(
        clipboard.shutil, "which", lambda name: f"/usr/bin/{name}"
    )
    captured = []
    assert (
        clipboard.force_minimize_window(
            "0x400001", runner=_x11_runner(captured)
        )
        is True
    )
    # --sync makes xdotool wait until the WM really unmapped the window.
    assert captured == [["xdotool", "windowminimize", "--sync", "0x400001"]]

    assert (
        clipboard.force_minimize_window("0x1", runner=_x11_runner([], rc=1))
        is False
    )


def test_force_minimize_needs_xdotool(monkeypatch):
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: None)
    called = []
    assert clipboard.force_minimize_window("0x1", runner=_x11_runner(called)) is False
    assert called == []


def test_window_manager_names_the_wm(monkeypatch):
    monkeypatch.setattr(
        clipboard.shutil, "which", lambda name: f"/usr/bin/{name}"
    )
    captured = []
    runner = _x11_runner(
        captured, out="Name: Mutter (Muffin)\nClass: N/A\nPID: 1234\n"
    )

    assert clipboard.window_manager(runner=runner) == "Mutter (Muffin)"
    assert captured == [["wmctrl", "-m"]]


def test_window_manager_reports_none_without_a_wm(monkeypatch):
    """No WM means iconify() cannot work, which the log must make obvious."""

    def which(name):
        return f"/usr/bin/{name}" if name == "xprop" else None

    monkeypatch.setattr(clipboard.shutil, "which", which)
    captured = []
    runner = _x11_runner(
        captured, out='_NET_SUPPORTING_WM_CHECK:  not found.\n'
    )

    assert clipboard.window_manager(runner=runner) is None
    assert captured == [["xprop", "-root", "-notype", "_NET_SUPPORTING_WM_CHECK"]]


def test_window_manager_falls_back_to_xprop(monkeypatch):
    def which(name):
        return f"/usr/bin/{name}" if name == "xprop" else None

    monkeypatch.setattr(clipboard.shutil, "which", which)
    runner = _x11_runner([], out="_NET_SUPPORTING_WM_CHECK: window id # 0x2e00003\n")

    # The property exists, so a WM is there even though wmctrl is missing.
    assert clipboard.window_manager(runner=runner) == "unnamed"
