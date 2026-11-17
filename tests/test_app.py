"""Pastex CLI tests (no GUI or display)."""

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pastex.app import run_cli


def test_version(capsys):
    assert run_cli(["--version"]) == 0
    assert "pastex" in capsys.readouterr().out


def test_check(capsys):
    assert run_cli(["--check"]) == 0
    out = capsys.readouterr().out
    assert "Python:" in out
    assert "tkinter:" in out
    assert "Snippet folders:" in out


def test_list(tmp_path, capsys):
    (tmp_path / "General").mkdir()
    (tmp_path / "General" / "Greeting.txt").write_text("hello", encoding="utf-8")
    assert run_cli(["--dir", str(tmp_path), "--list"]) == 0
    out = capsys.readouterr().out
    assert "General" in out and "Greeting" in out


class _FakeTree:
    """Fake Treeview: validates the insert(parent, index, iid=...) signature."""

    def __init__(self):
        self.calls = []
        self._exists = set()

    def exists(self, iid):
        return iid in self._exists

    def insert(self, parent, index, iid=None, **kw):
        assert index == "end" or isinstance(index, int), f"wrong index: {index!r}"
        assert iid is not None, "iid must be passed as a keyword"
        self.calls.append((parent, index, iid, kw))
        self._exists.add(iid)
        return iid


def test_insert_node_uses_end_index(tmp_path):
    """Regresi: insert(parent, iid, ...) crash (TclError: expected integer)."""
    from pastex.app import PastexApp
    from pastex.snippets import Node

    app = PastexApp.__new__(PastexApp)  # no __init__ (no display)
    app.tree = _FakeTree()
    # Tk.__getattr__ needs an interpreter, which __new__ does not create;
    # initialise the attributes _insert_node reads, explicitly.
    app.collapsed = set()
    root = Node(name="snippets", path=tmp_path, is_dir=True)
    child = Node(name="Sapaan", path=tmp_path / "Greeting.txt", is_dir=False)
    root.children = [child]
    app._insert_node("", root)
    assert len(app.tree.calls) == 2
    assert app.tree.calls[0][1] == "end"
    assert app.tree.calls[0][2] == str(tmp_path)


def test_startup_setup_reports_missing_clipboard_tools():
    from pastex.app import startup_setup_issues

    issues, can_enable = startup_setup_issues(
        "x11",
        {"wl-copy": False, "xclip": False, "xsel": False, "xdotool": True},
    )

    assert len(issues) == 1
    assert "sudo apt install wl-clipboard xclip xsel" in issues[0]
    assert can_enable is False


def test_startup_setup_reports_wayland_permission_and_daemon_gaps():
    from types import SimpleNamespace

    from pastex.app import startup_setup_issues

    status = SimpleNamespace(
        device_writable=False,
        helper=True,
        pkexec=True,
        ydotoold_binary=True,
        daemon_ready=False,
    )
    issues, can_enable = startup_setup_issues(
        "wayland",
        {
            "wl-copy": True,
            "xclip": False,
            "xsel": False,
            "ydotool": True,
            "xdotool": False,
        },
        status,
    )

    assert any("/dev/uinput" in issue for issue in issues)
    assert any("ydotoold" in issue for issue in issues)
    assert can_enable is True


def test_check_reports_clipboard(capsys):
    """--check must exercise the clipboard, not just list the tools."""
    assert run_cli(["--check"]) == 0
    out = capsys.readouterr().out
    assert "Clipboard:" in out
    assert "tool:" in out


def test_clipboard_status_without_session(monkeypatch):
    from pastex import app

    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr("pastex.clipboard.shutil.which", lambda n: None)
    lines = app.clipboard_status()
    assert any("session detected" in line for line in lines)


def test_test_paste_flag_exists():
    from pastex import app

    status = {"called": False}

    def fake(**kw):
        status["called"] = True
        status.update(kw)
        return 0

    original = app.run_test_paste
    app.run_test_paste = fake
    try:
        assert app.run_cli(["--test-paste"]) == 0
        assert status["called"] is True
        assert status["ctrl_v"] is False
        app.run_cli(["--test-paste", "--ctrl-v"])
        assert status["ctrl_v"] is True
    finally:
        app.run_test_paste = original


def _snippet(tmp_path, text="Halo"):
    d = tmp_path / "Snippets"
    d.mkdir(exist_ok=True)
    f = d / "Greeting.txt"
    f.write_text(text, encoding="utf-8")
    return f


def test_paste_text_hides_before_copying(monkeypatch):
    """Hiding starts first, so the compositor and the clipboard tool run in parallel."""
    from pastex import app as app_module
    from pastex.app import PastexApp

    calls = []

    class FakeApp:
        _pasting = False
        _paste_clipboard_ready = True
        status_var = type("V", (), {"set": staticmethod(lambda _value: None)})()

        def _hide_for_paste(self):
            calls.append(("hide",))
            return True

        def _send_paste(self):
            calls.append(("arm",))

        def _paste_worker(self, text):
            calls.append(("copy", text))

    app = FakeApp()
    monkeypatch.setattr(
        app_module,
        "_run_in_thread",
        lambda target, *args: calls.append(("start", target.__name__, *args)),
    )

    PastexApp._paste_text(app, "body")

    # Hide and arm happen on the click path; the copy runs in the worker.
    assert calls == [("hide",), ("arm",), ("start", "_paste_worker", "body")]
    # Ctrl+V must not be released before the clipboard owns the text.
    assert app._paste_clipboard_ready is False
    assert app._paste_restore_window is True


def test_do_paste_uses_the_preview_text(monkeypatch):
    """A click reuses the text the preview already read, without touching the disk."""
    from pastex import app as app_module
    from pastex.app import PastexApp

    calls = []

    class FakePath:
        name = "Greeting.txt"

        def is_dir(self):
            return False

    class FakeApp:
        _pasting = False
        status_var = type("V", (), {"set": staticmethod(lambda _value: None)})()

        def selected_path(self):
            return FakePath()

        def preview_text(self, path):
            calls.append(("cached", path.name))
            return "body"

        def _paste_text(self, text, hide=None):
            calls.append(("paste", text, hide))

    monkeypatch.setattr(
        app_module,
        "read_text",
        lambda _path: (_ for _ in ()).throw(AssertionError("the disk was read again")),
    )

    PastexApp.do_paste(FakeApp())

    assert calls == [("cached", "Greeting.txt"), ("paste", "body", None)]


def test_after_copy_releases_the_armed_paste():
    from pastex.app import PastexApp
    from pastex.clipboard import ClipboardResult

    calls = []

    class FakeApp:
        def __init__(self):
            # An instance attribute on purpose: _after_copy() reads __dict__ so
            # partially-built test doubles keep working.
            self._paste_pending = True

        def _send_paste_now(self):
            calls.append(("send_now", self._paste_clipboard_ready))

    app = FakeApp()
    PastexApp._after_copy(app, ClipboardResult(True, "wl-copy", "Text copied."))
    assert calls == [("send_now", True)]


def test_copy_failure_does_not_withdraw_or_send():
    from pastex.app import PastexApp
    from pastex.clipboard import ClipboardResult

    calls = []

    class FakeApp:
        def _paste_failed(self, message):
            calls.append(("failed", message))

        def withdraw(self):
            calls.append("withdraw")

        def _send_paste(self):
            calls.append("send")

    PastexApp._after_copy(FakeApp(), ClipboardResult(False, "", "no clipboard"))
    assert calls == [("failed", "no clipboard")]


def test_do_paste_ignores_repeat_while_paste_is_in_progress():
    from pastex.app import PastexApp

    calls = []

    class FakeApp:
        _pasting = True
        status_var = type("V", (), {"set": staticmethod(calls.append)})()

    PastexApp.do_paste(FakeApp())

    assert calls == ["Paste in progress..."]


def test_paste_done_waits_30ms_after_sending_before_restoring():
    from pastex.app import DEFAULT_SETTINGS, PastexApp
    from pastex.clipboard import PasteResult

    calls = []

    class FakeApp:
        settings: ClassVar[dict] = dict(DEFAULT_SETTINGS)
        _pasting = True
        status_var = type("V", (), {"set": staticmethod(lambda _value: None)})()

        def after(self, ms, callback, *args):
            calls.append(("wait", ms))
            self.finish = lambda: callback(*args)

        def _finish_paste(self, result):
            PastexApp._finish_paste(self, result)

        def deiconify(self):
            calls.append(("deiconify", None))

        def lift(self):
            calls.append(("lift", None))

    app = FakeApp()
    PastexApp._paste_done(app, PasteResult(True, "ydotool", "Pasted."))
    assert calls == [("wait", 30)]
    app.finish()
    assert calls[-1] == ("deiconify", None)
    assert app._pasting is False


def test_copy_failure_restores_window():
    """When the copy fails, the window must be visible so the message can be read."""
    from pastex.app import PastexApp

    calls = []

    class FakeApp:
        _pasting = True
        status_var = type(
            "V", (), {"set": staticmethod(lambda v: calls.append(("status", v)))}
        )()
        settings: ClassVar[dict[str, float]] = {}

        def deiconify(self):
            calls.append(("deiconify", None))

        def lift(self):
            calls.append(("lift", None))

        def _show_paste_failure(self, message):
            calls.append(("dialog", message))

    app = FakeApp()
    PastexApp._paste_failed(app, "no clipboard tool available")
    assert ("deiconify", None) in calls
    assert app._pasting is False


def test_paste_done_restores_window():
    from pastex.app import PastexApp
    from pastex.clipboard import PasteResult

    calls = []

    class FakeApp:
        settings: ClassVar[dict[str, float]] = {"restore_delay": 0}
        status_var = type(
            "V", (), {"set": lambda self, v: calls.append(("status", v))}
        )()
        _pasting = True

        def _finish_paste(self, result):
            PastexApp._finish_paste(self, result)

        def deiconify(self):
            calls.append(("deiconify", None))

        def lift(self):
            calls.append(("lift", None))

    app = FakeApp()
    PastexApp._paste_done(app, PasteResult(True, "ydotool", "ok"))
    assert ("deiconify", None) in calls  # the window is back
    assert app._pasting is False


def test_paste_done_failure_raises_window(monkeypatch):
    from pastex.app import PastexApp
    from pastex.clipboard import PasteResult

    calls = []

    class FakeApp:
        settings: ClassVar[dict[str, float]] = {"restore_delay": 0}
        status_var = type(
            "V", (), {"set": lambda self, v: calls.append(("status", v))}
        )()
        _pasting = True

        def deiconify(self):
            calls.append(("deiconify", None))

        def lift(self):
            calls.append(("lift", None))

        def _show_paste_failure(self, message):
            calls.append(("dialog", message))

    app = FakeApp()
    PastexApp._paste_done(app, PasteResult(False, "", "failed"))
    assert ("lift", None) in calls  # the failure must be visible
    assert ("dialog", "failed") in calls


def test_activate_always_pastes():
    """Click/Enter is one action: paste. There is no copy-only mode."""
    from pastex.app import PastexApp

    calls = []

    class FakeApp:
        def do_paste(self):
            calls.append("paste")

    # the do_copy attribute no longer exists in this version
    assert not hasattr(PastexApp, "do_copy")
    PastexApp.on_activate(FakeApp())
    assert calls == ["paste"]


def test_no_legacy_controls_in_source():
    """The Copy button, its shortcut and the countdown were removed."""
    src = (Path(__file__).resolve().parent.parent / "pastex" / "app.py").read_text()
    for gone in (
        "do_copy",
        "auto_var",
        "delay_var",
        "_start_countdown",
        "install-shortcut",
        "paste-foreground",
    ):
        assert gone not in src, f"leftover: {gone}"


# --- Debounce pencarian ---------------------------------------------------


def test_search_debounces_instead_of_scanning_every_keystroke():
    """Regression: _on_search_changed() existed but was never bound to
    trace_add, and _search_after_id was never initialised, so every keystroke
    triggered a full folder scan.
    """
    from pastex.app import PastexApp

    calls = []

    class FakeApp:
        _search_after_id = None

        def after(self, ms, fn=None):
            calls.append(("after", ms))
            return f"id{len(calls)}"

        def after_cancel(self, ident):
            calls.append(("cancel", ident))

        def refresh_tree(self, force=True):
            calls.append(("scan", force))

    app = FakeApp()
    PastexApp._on_search_changed(app)  # ketikan ke-1
    PastexApp._on_search_changed(app)  # ketikan ke-2
    PastexApp._on_search_changed(app)  # ketikan ke-3
    scans = [c for c in calls if c[0] == "scan"]
    assert scans == [], "refresh_tree was called directly instead of through the debounce"
    timers = [c for c in calls if c[0] == "after"]
    assert len(timers) == 3 and all(ms == 180 for _, ms in timers)
    cancels = [c for c in calls if c[0] == "cancel"]
    assert len(cancels) == 2, "the previous timer must be cancelled"


def test_search_var_connected_to_debounce_handler():
    """trace_add must call _on_search_changed, not refresh_tree."""
    src = (Path(__file__).resolve().parent.parent / "pastex" / "app.py").read_text()
    line = next(ln for ln in src.splitlines() if "trace_add" in ln)
    assert "_on_search_changed" in line, line
    assert "refresh_tree" not in line, line


def test_search_after_id_initialised():
    """_search_after_id must exist before the handler can use it."""
    src = (Path(__file__).resolve().parent.parent / "pastex" / "app.py").read_text()
    assert "_search_after_id: str | None = None" in src


# --- Paste results that cannot be confirmed -----------------------------


def test_paste_done_does_not_claim_success_when_not_confident():
    """On Wayland xdotool can exit 0 without pasting anything."""
    from pastex.app import PastexApp
    from pastex.clipboard import PasteResult

    calls = []

    class FakeApp:
        settings: ClassVar[dict[str, float]] = {"restore_delay": 0}
        status_var = type(
            "V", (), {"set": lambda self, v: calls.append(("status", v))}
        )()
        _pasting = True

        def deiconify(self):
            calls.append(("deiconify", None))

        def lift(self):
            calls.append(("lift", None))

        def _show_paste_failure(self, message):
            calls.append(("dialog", message))

    result = PasteResult(
        True, "xdotool", "Paste sent via xdotool.", confident=False
    )
    PastexApp._paste_done(FakeApp(), result)
    status = next(v for k, v in calls if k == "status")
    assert "Ctrl+V" in status, "the user must be told the next step"
    assert ("lift", None) in calls, "the window must be visible so the message can be read"


# --- Helper thread -------------------------------------------------------


def test_thread_helper_runs_target_with_args():
    from pastex.app import _run_in_thread

    seen = []
    import threading

    done = threading.Event()

    def work(a, b):
        seen.append((a, b))
        done.set()

    _run_in_thread(work, 1, 2)
    assert done.wait(5), "the target did not run"
    assert seen == [(1, 2)]


# --- Dock icon during paste ------------------------------------------------


def test_hide_for_paste_minimizes_instead_of_unmapping():
    """Minimizing keeps the window mapped so the panel entry survives."""
    from pastex.app import PastexApp

    calls = []

    class FakeApp:
        # No withdraw() at all: calling it would raise and fail this test.
        def iconify(self):
            calls.append("iconify")

        def update_idletasks(self):
            calls.append("update")

        def state(self):
            return "iconic"

    assert PastexApp._hide_for_paste(FakeApp()) is True
    assert calls == ["iconify", "update"]


def test_hide_for_paste_arms_the_paste_before_the_compositor_answers():
    """The compositor answers the minimize asynchronously: state=normal is not a failure yet."""
    from pastex.app import PastexApp

    calls = []

    class FakeApp:
        def iconify(self):
            calls.append("iconify")

        def update_idletasks(self):
            calls.append("update")

        def state(self):
            return "normal"

    assert PastexApp._hide_for_paste(FakeApp()) is True
    assert calls == ["iconify", "update"]


def _polling_app(states, *, tk_ready=True):
    """Test double that answers state() from a list and records scheduling."""
    from pastex.app import PastexApp

    scheduled = []
    sends = []
    calls = []

    class FakeApp:
        status_var = type("V", (), {"set": staticmethod(lambda _v: None)})()

        def __init__(self):
            if tk_ready:
                self.tk = object()
            self._paste_pending = True
            self._paste_clipboard_ready = True

        def state(self):
            return states.pop(0) if states else "normal"

        def after(self, delay, callback=None):
            scheduled.append((delay, callback))
            return f"id-{len(scheduled)}"

        def after_cancel(self, _id):
            return None

        def iconify(self):
            calls.append("iconify")

        def _paste_sender(self):
            return None

    app = FakeApp()
    app.scheduled = scheduled
    app.sends = sends
    app.iconify_calls = calls
    return app


def test_the_hide_check_is_polled_until_the_compositor_answers(monkeypatch):
    """A window that only hides a few milliseconds later still gets the paste."""
    from pastex import app as app_module
    from pastex.app import PastexApp

    app = _polling_app(["normal", "normal", "iconic"])
    monkeypatch.setattr(
        app_module,
        "_run_in_thread",
        lambda target, *args: app.sends.append(target.__name__),
    )

    PastexApp._send_paste_now(app)  # normal -> tunggu
    assert app.sends == []

    delay, callback = app.scheduled.pop(0)
    assert delay == app_module.MINIMIZE_RETRY_MS
    callback()  # still normal -> wait again
    assert app.sends == []

    delay, callback = app.scheduled.pop(0)
    callback()  # iconic -> send
    assert app.sends == ["_paste_sender"]


def test_a_window_that_never_hides_keeps_ctrl_v_unsent(monkeypatch):
    """When the WM really refuses: the text is copied, Ctrl+V is not sent."""
    from pastex import app as app_module
    from pastex.app import PastexApp

    app = _polling_app(["normal"], tk_ready=False)
    shown = []
    app.status_var = type("V", (), {"set": staticmethod(shown.append)})()
    monkeypatch.setattr(
        app_module, "_run_in_thread", lambda *a, **k: app.sends.append("sender")
    )

    PastexApp._send_paste_now(app)

    assert app.sends == []
    assert shown == [app_module.PASTE_MANUAL_HINT]
    assert app._paste_pending is False
    assert app._paste_finished is True


def test_the_minimize_request_is_repeated_when_the_first_one_is_ignored(monkeypatch):
    """Some compositors ignore the first request; ask once more."""
    from pastex import app as app_module
    from pastex.app import PastexApp

    app = _polling_app(["normal"] * 80)
    monkeypatch.setattr(app_module, "_run_in_thread", lambda *a, **k: None)

    PastexApp._send_paste_now(app)
    steps = 0
    while app.scheduled:
        _delay, callback = app.scheduled.pop(0)
        callback()
        steps += 1
        if steps > 80:  # pragma: no cover - safety net
            break

    assert steps * app_module.MINIMIZE_RETRY_MS >= app_module.MINIMIZE_WAIT_MS
    assert app.iconify_calls == ["iconify"], "only one retry was requested"
    assert app._paste_finished is True


def test_hide_for_paste_keeps_the_window_when_iconify_fails():
    from pastex.app import PastexApp

    calls = []

    class FakeApp:
        def iconify(self):
            raise AttributeError("no display")

    assert PastexApp._hide_for_paste(FakeApp()) is False
    assert calls == []


def test_the_window_is_never_unmapped():
    """The panel icon must never disappear, so withdraw() is gone for good."""
    from pastex import app as app_module

    source = Path(app_module.__file__).read_text(encoding="utf-8")

    assert "self.withdraw()" not in source


def test_send_paste_sends_immediately_when_window_unmaps(monkeypatch):
    from pastex import app as app_module
    from pastex.app import PastexApp

    calls = []
    app = PastexApp.__new__(PastexApp)
    app.tk = object()
    app.after = lambda ms, callback: calls.append(("arm", ms, callback))
    monkeypatch.setattr(
        app_module, "_run_in_thread", lambda target: calls.append(("run", target.__name__))
    )

    PastexApp._send_paste(app)
    assert calls == [("arm", app_module.FOCUS_SETTLE_MS, app._send_paste_now)]

    # The window manager reports the unmap long before the fallback timer.
    PastexApp._on_window_unmap(app, SimpleNamespace(widget=app))

    assert calls[-1] == ("run", "_paste_sender")
    assert app._paste_pending is False

    # A second unmap (or a late timeout) must not send again.
    PastexApp._send_paste_now(app)
    assert [call[0] for call in calls].count("run") == 1


def test_unmap_from_another_widget_does_not_send(monkeypatch):
    from pastex import app as app_module
    from pastex.app import PastexApp

    calls = []
    app = PastexApp.__new__(PastexApp)
    app._paste_pending = True
    monkeypatch.setattr(
        app_module, "_run_in_thread", lambda target: calls.append(target.__name__)
    )

    PastexApp._on_window_unmap(app, SimpleNamespace(widget=object()))

    assert calls == []
    assert app._paste_pending is True


def test_paste_falls_back_to_the_timeout_when_no_unmap_arrives(monkeypatch):
    from pastex import app as app_module
    from pastex.app import PastexApp

    calls = []
    app = PastexApp.__new__(PastexApp)
    app.tk = object()
    app.after = lambda ms, callback: calls.append(("arm", ms, callback)) or "timer-1"
    app.after_cancel = lambda name: calls.append(("cancel", name))
    monkeypatch.setattr(
        app_module, "_run_in_thread", lambda target: calls.append(("run", target.__name__))
    )

    PastexApp._send_paste(app)
    calls[0][2]()  # run the fallback timer

    assert ("cancel", "timer-1") in calls
    assert calls[-1] == ("run", "_paste_sender")


def test_finishing_a_paste_clears_the_pending_send():
    from pastex.app import PastexApp
    from pastex.clipboard import PasteResult

    calls = []

    class FakeApp:
        settings: ClassVar[dict[str, float]] = {"restore_delay": 0}
        status_var = type("V", (), {"set": staticmethod(lambda _value: None)})()
        _pasting = True
        _paste_pending = True

        def _finish_paste(self, result):
            PastexApp._finish_paste(self, result)

        def deiconify(self):
            calls.append("deiconify")

        def lift(self):
            calls.append("lift")

    app = FakeApp()
    PastexApp._paste_done(app, PasteResult(True, "ydotool", "ok"))

    assert app._paste_pending is False
    assert calls[-1] == "deiconify"


def test_settings_shorten_the_previous_restore_delay_default(tmp_path, monkeypatch):
    from pastex import app as app_module

    config = tmp_path / "pastex"
    config.mkdir()
    (config / "settings.json").write_text('{"restore_delay": 0.1}', encoding="utf-8")
    monkeypatch.setattr(app_module, "SETTINGS_FILE", config / "settings.json")

    assert app_module.load_settings()["restore_delay"] == app_module.DEFAULT_SETTINGS["restore_delay"]


def test_settings_keep_a_deliberate_restore_delay(tmp_path, monkeypatch):
    from pastex import app as app_module

    config = tmp_path / "pastex"
    config.mkdir()
    (config / "settings.json").write_text('{"restore_delay": 0.25}', encoding="utf-8")
    monkeypatch.setattr(app_module, "SETTINGS_FILE", config / "settings.json")

    assert app_module.load_settings()["restore_delay"] == 0.25


def test_send_paste_waits_for_focus_to_settle(monkeypatch):
    from pastex import app as app_module
    from pastex.app import PastexApp

    calls = []
    app = PastexApp.__new__(PastexApp)
    # A real interpreter marker makes the app schedule the settle timer.
    app.tk = object()
    app.after = lambda ms, callback: calls.append(("wait", ms, callback))
    monkeypatch.setattr(
        app_module, "_run_in_thread", lambda target: calls.append(("run", target.__name__))
    )

    PastexApp._send_paste(app)

    assert calls[0][0] == "wait"
    assert calls[0][1] == app_module.FOCUS_SETTLE_MS
    calls[0][2]()
    assert calls[-1] == ("run", "_paste_sender")


def test_send_paste_falls_back_to_immediate_send_without_tk(monkeypatch):
    from pastex import app as app_module
    from pastex.app import PastexApp

    calls = []
    app = PastexApp.__new__(PastexApp)
    monkeypatch.setattr(
        app_module, "_run_in_thread", lambda target: calls.append(target.__name__)
    )

    PastexApp._send_paste(app)

    assert calls == ["_paste_sender"]


def test_window_unmap_is_bound_to_the_paste_sender():
    """The unmap signal is what makes the paste start without a fixed delay."""
    from pastex import app as app_module

    source = Path(app_module.__file__).read_text(encoding="utf-8")

    assert 'self.bind("<Unmap>", self._on_window_unmap, add="+")' in source
    assert "def _on_window_unmap(" in source
    assert "def _send_paste_now(" in source


# --- Overlapped hide/copy and prewarmed tool plans ------------------------


def test_ctrl_v_waits_until_the_clipboard_is_ready(monkeypatch):
    """The unmap may arrive first, but Ctrl+V waits for the clipboard."""
    from pastex import app as app_module
    from pastex.app import PastexApp

    calls = []
    app = PastexApp.__new__(PastexApp)
    app._pasting = True
    app._paste_pending = True
    app._paste_clipboard_ready = False
    monkeypatch.setattr(
        app_module, "_run_in_thread", lambda target: calls.append(target.__name__)
    )

    # The window manager hides the window while wl-copy is still running.
    PastexApp._send_paste_now(app)

    assert calls == [], "Ctrl+V must not be sent before the clipboard is ready"
    assert app._paste_pending is True

    # The clipboard result releases it.
    PastexApp._after_copy(app, ClipboardResultLike(True))

    assert calls == ["_paste_sender"]
    assert app._paste_pending is False


def test_unmap_after_the_copy_sends_immediately(monkeypatch):
    """When the clipboard is ready first, the unmap sends Ctrl+V immediately."""
    from pastex import app as app_module
    from pastex.app import PastexApp

    calls = []
    app = PastexApp.__new__(PastexApp)
    app._paste_pending = True
    app._paste_clipboard_ready = True
    monkeypatch.setattr(
        app_module, "_run_in_thread", lambda target: calls.append(target.__name__)
    )

    PastexApp._on_window_unmap(app, SimpleNamespace(widget=app))

    assert calls == ["_paste_sender"]


class ClipboardResultLike:
    """Minimal stand-in for ClipboardResult in the paste-state tests."""

    def __init__(self, ok):
        self.ok = ok
        self.tool = "wl-copy"
        self.message = "Text copied."


def test_paste_passes_the_prewarmed_plans(monkeypatch):
    from pastex import app as app_module
    from pastex.app import PastexApp

    seen = []
    app = PastexApp.__new__(PastexApp)
    app._copy_plan = "COPY-PLAN"
    app._paste_plan = "PASTE-PLAN"
    app.after = lambda *_args: None
    monkeypatch.setattr(
        app_module,
        "copy_text",
        lambda text, plan=None: seen.append(("copy", text, plan)) or ClipboardResultLike(True),
    )
    monkeypatch.setattr(
        app_module,
        "simulate_paste",
        lambda plan=None, should_stop=None: (
            seen.append(("paste", plan, should_stop)) or PasteResultLike()
        ),
    )

    PastexApp._paste_worker(app, "body")
    PastexApp._paste_sender(app)

    assert ("copy", "body", "COPY-PLAN") in seen
    paste_calls = [entry for entry in seen if entry[0] == "paste"]
    assert paste_calls[0][1] == "PASTE-PLAN"
    # The sender must hand the abort flag to simulate_paste so a paste that
    # overran its deadline stops before pressing more keys.
    assert callable(paste_calls[0][2])


class PasteResultLike:
    ok = True
    tool = "ydotool"
    message = "ok"
    confident = True


def test_failed_copy_drops_the_cached_plan(monkeypatch):
    from pastex import app as app_module
    from pastex.app import PastexApp

    app = PastexApp.__new__(PastexApp)
    app._copy_plan = "STALE"
    app.after = lambda *_args: None
    monkeypatch.setattr(
        app_module,
        "copy_text",
        lambda text, plan=None: ClipboardResultLike(False),
    )

    PastexApp._paste_worker(app, "body")

    assert app._copy_plan is None


def test_failed_paste_drops_the_cached_plan(monkeypatch):
    from pastex import app as app_module
    from pastex.app import PastexApp

    app = PastexApp.__new__(PastexApp)
    app._paste_plan = "STALE"
    app.after = lambda *_args: None
    monkeypatch.setattr(
        app_module,
        "simulate_paste",
        lambda plan=None: type("R", (), {"ok": False, "tool": "", "message": "no tool"})(),
    )

    PastexApp._paste_sender(app)

    assert app._paste_plan is None


def test_prewarm_runs_in_the_background_and_stores_the_plans(monkeypatch):
    """Clipboard tools are resolved on a thread, not on the click path."""
    from pastex import app as app_module
    from pastex.app import PastexApp

    started = []
    monkeypatch.setattr(
        app_module, "_run_in_thread", lambda target, *args: started.append(target)
    )
    class FakePlan:
        def __init__(self, names):
            self.names = names

        def tool_names(self):
            return self.names

    copy_plan = FakePlan(["wl-copy"])
    paste_plan = FakePlan(["ydotool"])
    monkeypatch.setattr(
        app_module,
        "prewarm_clipboard",
        lambda: (copy_plan, paste_plan),
    )

    app = PastexApp.__new__(PastexApp)
    app._copy_plan = None
    app._paste_plan = None

    PastexApp._prewarm_paste(app)

    assert started == [app._prewarm_paste_worker]
    assert app._copy_plan is None, "startup must not wait for prewarm"

    started[0]()

    assert app._copy_plan is copy_plan
    assert app._paste_plan is paste_plan


def test_settings_migrate_the_previous_005_restore_delay(tmp_path, monkeypatch):
    from pastex import app as app_module

    config = tmp_path / "pastex"
    config.mkdir()
    (config / "settings.json").write_text('{"restore_delay": 0.05}', encoding="utf-8")
    monkeypatch.setattr(app_module, "SETTINGS_FILE", config / "settings.json")

    settings = app_module.load_settings()

    assert settings["restore_delay"] == app_module.DEFAULT_SETTINGS["restore_delay"]
    assert app_module.DEFAULT_SETTINGS["restore_delay"] < 0.05


def test_startup_schedules_the_prewarm():
    """Startup must call prewarm so the first click is not slower."""
    from pastex import app as app_module

    source = Path(app_module.__file__).read_text(encoding="utf-8")

    assert "def _prewarm_paste(" in source
    assert "self._prewarm_paste()" in source
    assert "prewarm as prewarm_clipboard" in source


def test_paste_skips_ctrl_v_when_the_window_cannot_be_minimized(monkeypatch):
    """Minimize refused: the icon is kept, Ctrl+V is not sent, a hint is shown."""
    from pastex import app as app_module
    from pastex.app import PastexApp

    calls = []

    class FakeApp:
        _pasting = False

        def __init__(self):
            self.status_var = type("V", (), {"set": staticmethod(lambda _v: None)})()
            self._paste_clipboard_ready = True

        def _hide_for_paste(self):
            calls.append(("hide",))
            return False

        def _send_paste(self):
            calls.append("arm")

        def _paste_worker(self, text):
            calls.append(("copy", text))

    app = FakeApp()
    monkeypatch.setattr(
        app_module,
        "_run_in_thread",
        lambda target, *args: calls.append(("start", target.__name__, *args)),
    )

    PastexApp._paste_text(app, "body")

    assert calls == [("hide",), ("start", "_paste_worker", "body")]
    assert "arm" not in calls, "Ctrl+V must not be armed while the window is visible"
    assert app._paste_clipboard_ready is False


def test_finished_copy_without_an_armed_send_shows_the_manual_hint():
    from pastex import app as app_module
    from pastex.app import PastexApp

    statuses = []

    class FakeApp:
        def __init__(self):
            self._pasting = True
            self._paste_clipboard_ready = False
            self.status_var = type(
                "V", (), {"set": staticmethod(statuses.append)}
            )()

        def _send_paste_now(self):  # pragma: no cover - must not be reached
            raise AssertionError("Ctrl+V must not be sent")

        def _paste_failed(self, message):  # pragma: no cover
            raise AssertionError("the copy succeeded")

    app = FakeApp()
    PastexApp._after_copy(app, ClipboardResultLike(True))

    assert app._pasting is False
    assert statuses[-1] == app_module.PASTE_MANUAL_HINT
    assert "press Ctrl+V" in app_module.PASTE_MANUAL_HINT


# --- Index, watcher, picker and single instance integration ---------------


def test_refresh_tree_reuses_the_cached_index(gui, tmp_path):
    """A repaint with force=False does not walk the disk; force=True rescans."""
    root = tmp_path / "snippets"
    root.mkdir()
    (root / "A.txt").write_text("a", encoding="utf-8")
    app = gui(roots=[root])
    app.refresh_tree()
    scans = app.index.scans
    assert scans >= 1

    app.refresh_tree(force=False)
    assert app.index.scans == scans, "a repaint must not scan the disk"

    app.refresh_tree(force=True)
    assert app.index.scans == scans + 1


def test_search_filters_the_cache_instead_of_rescanning(gui, tmp_path):
    root = tmp_path / "snippets"
    root.mkdir()
    (root / "Greeting.txt").write_text("hello", encoding="utf-8")
    (root / "Other.txt").write_text("other", encoding="utf-8")
    app = gui(roots=[root])
    app.refresh_tree()
    scan_count = app.index.scans

    app.search_var.set("greet")
    app.refresh_tree(force=False)

    assert app.index.scans == scan_count
    assert [row["iid"] for row in app.tree.rows if row["iid"].endswith(".txt")] == [
        str(root / "Greeting.txt")
    ]


def test_folder_change_refreshes_through_the_index(monkeypatch):
    from pastex import app as app_module
    from pastex.app import PastexApp

    calls = []
    monkeypatch.setattr(
        app_module,
        "DirectoryWatcher",
        lambda roots, on_change: type(
            "W",
            (),
            {
                "start": lambda self: True,
                "set_roots": lambda self, roots: calls.append(("roots", tuple(roots))),
            },
        )(),
    )

    app = PastexApp.__new__(PastexApp)
    app.roots = [Path("/snippets")]
    app.transient_roots = False
    app.settings = {"source_dir": "/snippets"}
    app.watcher = None

    PastexApp._start_watcher(app)

    assert app.watcher is not None
    # Starting twice must not add a second watcher.
    first = app.watcher
    PastexApp._start_watcher(app)
    assert app.watcher is first


def test_watcher_callback_marshals_to_the_gui_thread():
    from pastex.app import PastexApp

    calls = []

    class FakeApp:
        def after(self, ms, callback, *args):
            calls.append((ms, callback.__name__, args))

        def _refresh_from_disk(self):  # pragma: no cover - only referenced
            pass

    PastexApp._on_folders_changed(FakeApp())

    assert calls == [(0, "_refresh_from_disk", ())]


def test_refresh_from_disk_waits_while_pasting():
    from pastex.app import PastexApp

    calls = []

    class FakeApp:
        _pasting = True

        def after(self, ms, callback, *args):
            calls.append(("after", ms))

        def _refresh_from_disk(self):  # pragma: no cover - only referenced
            pass

        def refresh_tree(self, force=True):  # pragma: no cover - must not run
            raise AssertionError("the tree must not be rebuilt during a paste")

    PastexApp._refresh_from_disk(FakeApp())

    assert calls == [("after", 150)]


def test_instance_commands_are_handled(monkeypatch):
    from pastex.app import PastexApp

    calls = []

    class FakeApp:
        def open_picker(self):
            calls.append("pick")

        def destroy(self):
            calls.append("quit")

        def show_window(self):
            calls.append("show")

    app = FakeApp()
    for command in ("pick", "quit", "show"):
        PastexApp._run_instance_command(app, command)

    assert calls == ["pick", "quit", "show"]


def test_instance_command_is_marshalled_from_the_socket_thread():
    from pastex.app import PastexApp

    calls = []

    class FakeApp:
        def after(self, ms, callback, *args):
            calls.append((ms, callback.__name__, args))

        def _run_instance_command(self, command):  # pragma: no cover - referenced
            pass

    PastexApp._handle_instance_command(FakeApp(), "pick")

    assert calls == [(0, "_run_instance_command", ("pick",))]


def test_picker_paste_does_not_touch_the_main_window(monkeypatch):
    """Picking from the popup: the main window is neither hidden nor deiconified."""
    from pastex import app as app_module
    from pastex.app import PastexApp

    calls = []

    class FakePopup:
        def destroy(self):
            calls.append("popup-destroy")

    class FakeApp:
        _pasting = False
        settings: ClassVar[dict[str, float]] = {"restore_delay": 0}
        status_var = type("V", (), {"set": staticmethod(lambda _v: None)})()
        _paste_text = PastexApp._paste_text
        _hide_widget = PastexApp._hide_widget
        _paste_done = PastexApp._paste_done
        _finish_paste = PastexApp._finish_paste

        def preview_text(self, path):
            return "body"

        def _hide_for_paste(self):  # pragma: no cover - must not be used
            raise AssertionError("the main window must not be minimised")

        def _send_paste(self):
            calls.append("arm")

        def _paste_worker(self, text):
            calls.append(("copy", text))

        def deiconify(self):
            calls.append("deiconify")

        def lift(self):
            calls.append("lift")

    monkeypatch.setattr(
        app_module,
        "_run_in_thread",
        lambda target, *args: calls.append(("start", target.__name__)),
    )

    popup = FakePopup()
    app = FakeApp()
    PastexApp.paste_path(app, Path("/snippets/A.txt"), hide=popup)

    assert calls == ["popup-destroy", "arm", ("start", "_paste_worker")]
    assert app._paste_restore_window is False
    assert app._paste_hide_widget is popup

    # Finishing must not restore (and steal focus back to) the main window.
    from pastex.clipboard import PasteResult

    PastexApp._paste_done(app, PasteResult(True, "ydotool", "ok"))

    assert calls == ["popup-destroy", "arm", ("start", "_paste_worker")]
    assert app._paste_hide_widget is None
    assert app._pasting is False


def test_finishing_a_picker_paste_leaves_the_target_app_focused():
    from pastex.app import PastexApp
    from pastex.clipboard import PasteResult

    calls = []

    class FakeApp:
        settings: ClassVar[dict[str, float]] = {"restore_delay": 0}
        status_var = type("V", (), {"set": staticmethod(lambda _v: None)})()

        def __init__(self):
            self._pasting = True
            self._paste_pending = True
            self._paste_restore_window = False
            self._paste_hide_widget = object()

        def deiconify(self):
            calls.append("deiconify")

        def lift(self):
            calls.append("lift")

    app = FakeApp()
    PastexApp._finish_paste(app, PasteResult(True, "ydotool", "ok"))

    assert calls == [], "do not steal focus from the target application"
    assert app._paste_hide_widget is None


def test_unmap_of_the_picker_popup_sends_the_paste(monkeypatch):
    from pastex import app as app_module
    from pastex.app import PastexApp

    sent = []
    popup = object()
    app = PastexApp.__new__(PastexApp)
    app._paste_pending = True
    app._paste_clipboard_ready = True
    app._paste_hide_widget = popup
    monkeypatch.setattr(
        app_module, "_run_in_thread", lambda target: sent.append(target.__name__)
    )

    PastexApp._on_window_unmap(app, SimpleNamespace(widget=popup))

    assert sent == ["_paste_sender"]


def test_main_window_unmap_is_ignored_while_the_picker_is_hidden(monkeypatch):
    from pastex import app as app_module
    from pastex.app import PastexApp

    sent = []
    app = PastexApp.__new__(PastexApp)
    app._paste_pending = True
    app._paste_clipboard_ready = True
    app._paste_hide_widget = object()  # the picker popup
    monkeypatch.setattr(
        app_module, "_run_in_thread", lambda target: sent.append(target.__name__)
    )

    PastexApp._on_window_unmap(app, SimpleNamespace(widget=app))

    assert sent == []
    assert app._paste_pending is True


def test_open_picker_reports_a_missing_display(monkeypatch):
    from pastex import app as app_module
    from pastex.app import PastexApp

    statuses = []
    monkeypatch.setattr(app_module, "open_picker", lambda app: None)

    class FakeApp:
        status_var = type("V", (), {"set": staticmethod(statuses.append)})()

    PastexApp.open_picker(FakeApp())

    assert statuses == ["The picker needs a display."]


def test_cli_forwards_show_to_a_running_instance(monkeypatch):
    from pastex import app as app_module

    seen = []
    monkeypatch.setattr(app_module, "tk", object())
    monkeypatch.setattr(
        app_module,
        "send_command",
        lambda command: seen.append(command) or {"ok": True},
    )
    monkeypatch.setattr(
        app_module, "PastexApp", lambda *a, **k: (_ for _ in ()).throw(AssertionError())
    )

    assert app_module.run_cli([]) == 0
    assert seen == ["show"]


def test_cli_pick_forwards_pick_and_never_starts_a_window(monkeypatch):
    from pastex import app as app_module

    seen = []
    monkeypatch.setattr(app_module, "tk", object())
    monkeypatch.setattr(
        app_module,
        "send_command",
        lambda command: seen.append(command) or {"ok": True},
    )
    monkeypatch.setattr(
        app_module, "PastexApp", lambda *a, **k: (_ for _ in ()).throw(AssertionError())
    )

    assert app_module.run_cli(["--pick"]) == 0
    assert seen == ["pick"]


def test_cli_starts_a_window_when_no_instance_answers(monkeypatch):
    from pastex import app as app_module

    started = []

    class FakeApp:
        def __init__(self, roots=None, *, transient_roots=False):
            started.append("app")

        def mainloop(self):
            started.append("loop")

    monkeypatch.setattr(app_module, "tk", object())
    monkeypatch.setattr(app_module, "send_command", lambda command: None)
    monkeypatch.setattr(app_module, "PastexApp", FakeApp)
    monkeypatch.setattr(
        app_module,
        "SingleInstance",
        lambda handler: type(
            "I",
            (),
            {"claim": lambda self: False, "serve": lambda self: True, "stop": lambda self: None},
        )(),
    )

    assert app_module.run_cli([]) == 0
    assert started == ["app", "loop"]


def test_cli_dir_sessions_are_never_forwarded(monkeypatch, tmp_path):
    from pastex import app as app_module

    seen = []

    class FakeApp:
        def __init__(self, roots=None, *, transient_roots=False):
            pass

        def mainloop(self):
            pass

    monkeypatch.setattr(app_module, "tk", object())
    monkeypatch.setattr(
        app_module, "send_command", lambda command: seen.append(command) or {"ok": True}
    )
    monkeypatch.setattr(app_module, "PastexApp", FakeApp)
    monkeypatch.setattr(
        app_module,
        "SingleInstance",
        lambda handler: type(
            "I",
            (),
            {"claim": lambda self: False, "serve": lambda self: True, "stop": lambda self: None},
        )(),
    )

    assert app_module.run_cli(["--dir", str(tmp_path)]) == 0
    assert seen == []


def test_search_repaints_from_the_cache_after_the_debounce():
    """Typing uses the cached tree instead of walking the disk again."""
    from pastex.app import PastexApp

    timers = []
    forces = []

    class FakeApp:
        _search_after_id = None

        def after(self, ms, callback, *args):
            timers.append((ms, callback))
            return "timer"

        def after_cancel(self, ident):  # pragma: no cover - single keystroke
            pass

        def refresh_tree(self, force=True):
            forces.append(force)

    app = FakeApp()
    PastexApp._on_search_changed(app)
    timers[0][1]()

    assert forces == [False]


# --- Incremental treeview updates -----------------------------------------


def test_refresh_reuses_rows_instead_of_rebuilding(gui, tmp_path):
    """A repaint patches existing rows instead of deleting and rebuilding them."""
    root = tmp_path / "snippets"
    (root / "Folder").mkdir(parents=True)
    (root / "Folder" / "A.txt").write_text("a", encoding="utf-8")
    (root / "B.txt").write_text("b", encoding="utf-8")
    app = gui(roots=[root])
    app.refresh_tree()

    rows_before = [(row["iid"], row["parent"]) for row in app.tree.rows]
    deleted_before = app.tree.deleted

    app.refresh_tree(force=False)

    assert [(row["iid"], row["parent"]) for row in app.tree.rows] == rows_before
    assert app.tree.deleted == deleted_before, "no row may be deleted"
    assert app.tree.inserts == 3, "existing rows must not be inserted again"
    assert app.tree.moves == 0
    assert app.tree.item_updates == 0, "no value changed"


def test_refresh_adds_only_the_new_row(gui, tmp_path):
    root = tmp_path / "snippets"
    root.mkdir()
    (root / "A.txt").write_text("a", encoding="utf-8")
    app = gui(roots=[root])
    app.refresh_tree()
    first = app.tree.by_iid(root / "A.txt")

    (root / "B.txt").write_text("b", encoding="utf-8")
    app.refresh_tree()

    assert app.tree.by_iid(root / "A.txt") is first, "the existing row is reused"
    assert app.tree.by_iid(root / "B.txt")["text"].endswith("B")
    assert len(app.tree.rows) == 2


def test_refresh_drops_rows_that_left_the_disk(gui, tmp_path):
    root = tmp_path / "snippets"
    root.mkdir()
    (root / "Keep.txt").write_text("keep", encoding="utf-8")
    (root / "Gone.txt").write_text("gone", encoding="utf-8")
    app = gui(roots=[root])
    app.refresh_tree()

    (root / "Gone.txt").unlink()
    app.refresh_tree()

    assert not app.tree.exists(str(root / "Gone.txt"))
    assert app.tree.exists(str(root / "Keep.txt"))
    assert len(app.tree.rows) == 1


def test_refresh_updates_the_open_state_in_place(gui, sample_root):
    from pastex import app as app_module

    app = gui(roots=[sample_root])
    app.refresh_tree()
    folder = app.tree.by_iid(sample_root / "Email")
    assert folder["open"] is True

    app.settings["collapsed"] = [app_module._norm_path(sample_root / "Email")]
    app.refresh_tree(force=False)

    assert app.tree.by_iid(sample_root / "Email") is folder, "the same row"
    assert folder["open"] is False, "the collapsed state must overwrite the existing row"


def test_refresh_keeps_the_selection(gui, tmp_path):
    root = tmp_path / "snippets"
    root.mkdir()
    (root / "A.txt").write_text("a", encoding="utf-8")
    app = gui(roots=[root])
    app.refresh_tree()
    app.tree.selection_set(str(root / "A.txt"))

    app.refresh_tree(force=False)

    assert app.tree.selection() == (str(root / "A.txt"),)


def test_refresh_reorders_rows_without_rebuilding(gui, tmp_path):
    from pastex import app as app_module

    root = tmp_path / "snippets"
    root.mkdir()
    (root / "A.txt").write_text("a", encoding="utf-8")
    (root / "B.txt").write_text("b", encoding="utf-8")
    app = gui(roots=[root])
    app.refresh_tree()
    first = app.tree.by_iid(root / "A.txt")
    deleted_before = app.tree.deleted

    app.settings["order"] = {app_module._norm_path(root): ["B.txt", "A.txt"]}
    app.refresh_tree(force=False)

    assert list(app.tree.get_children("")) == [
        str(root / "B.txt"),
        str(root / "A.txt"),
    ]
    assert app.tree.by_iid(root / "A.txt") is first
    assert app.tree.deleted == deleted_before


def test_two_roots_with_the_same_path_keep_stable_ids(gui, tmp_path):
    """Two roots (checkout + system copy) must not collide on iid."""
    root = tmp_path / "snippets"
    root.mkdir()
    (root / "A.txt").write_text("a", encoding="utf-8")
    app = gui(roots=[root, root])
    app.refresh_tree()

    ids = [row["iid"] for row in app.tree.rows]
    assert len(ids) == len(set(ids)) == 2
    assert ids == [str(root / "A.txt"), f"{root / 'A.txt'}#{root}"]

    app.refresh_tree(force=False)

    assert [row["iid"] for row in app.tree.rows] == ids


def test_row_update_accepts_tcl_string_options(gui, tmp_path):
    """Tk returns "1"/"dir" for options; that is not a change."""
    root = tmp_path / "snippets"
    (root / "Folder").mkdir(parents=True)
    (root / "Folder" / "A.txt").write_text("a", encoding="utf-8")
    app = gui(roots=[root])
    app.tree.string_options = True
    app.refresh_tree()

    app.tree.item_updates = 0
    app.refresh_tree(force=False)

    assert app.tree.item_updates == 0, "equivalent Tcl values must not be rewritten"


# --- pemilik clipboard Wayland (opt-in) ----------------------------------
class _FakeWlOwner:
    started = 0
    stopped = 0

    def __init__(self, start_ok=True, verify_ok=True, error="", detail=""):
        type(self).started = 0
        type(self).stopped = 0
        self.start_ok = start_ok
        self.verify_ok = verify_ok
        self.error = error
        self._detail = detail
        self.copies = 0

    def start(self):
        type(self).started += 1
        return self.start_ok

    def stop(self):
        type(self).stopped += 1

    def copy(self, text, mimes=None):  # pragma: no cover - reached via the fake verify
        self.copies += 1
        return True


def _fake_wlowner(monkeypatch, owner, *, available=True, verify_result=(True, "")):
    from pastex import wlowner

    calls = {"verify": 0}

    def verify(_owner, _reader, restore=None, **_kwargs):
        calls["verify"] += 1
        return verify_result

    monkeypatch.setattr(wlowner, "available", lambda: available)
    monkeypatch.setattr(wlowner, "OwnerClipboard", lambda *a, **k: owner)
    monkeypatch.setattr(wlowner, "verify", verify)
    return calls


def test_clipboard_owner_is_not_started_on_a_non_wayland_session(gui, monkeypatch):
    from pastex import clipboard

    owner = _FakeWlOwner()
    _fake_wlowner(monkeypatch, owner, available=False)
    monkeypatch.setattr(clipboard, "set_clipboard_owner", lambda _o: None)
    app = gui(roots=[])

    assert app._start_clipboard_owner() is False
    assert owner.started == 0


def test_clipboard_owner_reports_a_helper_that_will_not_start(gui, monkeypatch):
    owner = _FakeWlOwner(start_ok=False, error="no compositor")
    calls = _fake_wlowner(monkeypatch, owner)
    app = gui(roots=[])

    assert app._start_clipboard_owner() is False
    assert owner.started == 1
    assert calls["verify"] == 0


def test_clipboard_owner_that_fails_its_self_test_is_stopped(gui, monkeypatch):
    from pastex import app as app_module

    owner = _FakeWlOwner()
    installed = []
    _fake_wlowner(monkeypatch, owner, verify_result=(False, "not readable"))
    monkeypatch.setattr(app_module, "set_clipboard_owner", installed.append)
    app = gui(roots=[])

    assert app._start_clipboard_owner() is False
    assert owner.stopped == 1
    assert installed == [], "a failing owner must not be installed"


def test_verified_owner_becomes_the_copy_path(gui, monkeypatch):
    from pastex import app as app_module

    owner = _FakeWlOwner()
    _fake_wlowner(monkeypatch, owner)
    installed = []
    monkeypatch.setattr(app_module, "set_clipboard_owner", installed.append)
    app = gui(roots=[])

    assert app._start_clipboard_owner() is True
    assert installed == [owner]
    assert app._clipboard_owner is owner


def test_releasing_the_owner_restores_the_tools(gui, monkeypatch):
    from pastex import app as app_module

    owner = _FakeWlOwner()
    installed = []
    monkeypatch.setattr(app_module, "set_clipboard_owner", installed.append)
    app = gui(roots=[])
    app._clipboard_owner = owner

    app._release_clipboard_owner()

    assert owner.stopped == 1
    assert installed == [None], "copies must fall back to wl-copy"
    assert app._clipboard_owner is None


def test_releasing_without_an_owner_does_nothing(gui, monkeypatch):
    from pastex import app as app_module

    installed = []
    monkeypatch.setattr(app_module, "set_clipboard_owner", installed.append)
    app = gui(roots=[])

    app._release_clipboard_owner()

    assert installed == []


def test_prewarm_only_starts_the_owner_when_asked(gui, monkeypatch):
    from pastex import app as app_module

    from pastex.clipboard import ToolPlan

    plan = ToolPlan("wayland", [("wl-copy", ["wl-copy"])])
    monkeypatch.setattr(app_module, "prewarm_clipboard", lambda: (plan, plan))
    owner = _FakeWlOwner()
    _fake_wlowner(monkeypatch, owner)

    app = gui(roots=[], settings={"clipboard_owner": False})
    app._prewarm_paste_worker()
    assert owner.started == 0

    monkeypatch.setattr(app_module, "set_clipboard_owner", lambda _owner: None)
    app = gui(roots=[], settings={"clipboard_owner": True})
    app._prewarm_paste_worker()
    assert owner.started == 1


def test_wayland_clipboard_flag_saves_the_choice(tmp_path, monkeypatch):
    from pastex import app as app_module

    saved = []
    monkeypatch.setattr(app_module, "load_settings", lambda: dict(app_module.DEFAULT_SETTINGS))
    monkeypatch.setattr(app_module, "save_settings", saved.append)
    monkeypatch.setattr(app_module, "tk", object())
    monkeypatch.setattr(app_module, "SingleInstance", lambda handler: None)

    class FakeApp:
        def __init__(self, roots=None, transient_roots=False):
            self.roots = roots

        def mainloop(self):
            pass

    monkeypatch.setattr(app_module, "PastexApp", FakeApp)
    assert app_module.run_cli(["--wayland-clipboard"]) == 0
    assert saved and saved[0]["clipboard_owner"] is True


def test_check_reports_the_clipboard_owner(capsys):
    assert run_cli(["--check"]) == 0
    out = capsys.readouterr().out
    assert "Clipboard owner:" in out
    assert "--wayland-clipboard" in out


def test_closing_the_main_loop_releases_the_owner(monkeypatch):
    """Every exit path must release the clipboard owner."""
    from pastex import app as app_module

    released = []

    class FakeApp:
        def __init__(self, roots=None, transient_roots=False):
            self.roots = roots

        def mainloop(self):
            released.append("loop-ended")

        def _release_clipboard_owner(self):
            released.append("owner-released")

    monkeypatch.setattr(app_module, "tk", object())
    monkeypatch.setattr(app_module, "PastexApp", FakeApp)
    monkeypatch.setattr(app_module, "SingleInstance", lambda handler: None)

    assert app_module.run_cli([]) == 0
    assert released == ["loop-ended", "owner-released"]


def test_a_crashing_main_loop_still_releases_the_owner(monkeypatch):
    import pytest

    from pastex import app as app_module

    released = []

    class FakeApp:
        def __init__(self, roots=None, transient_roots=False):
            self.roots = roots

        def mainloop(self):
            raise RuntimeError("Tk mati")

        def _release_clipboard_owner(self):
            released.append("owner-released")

    monkeypatch.setattr(app_module, "tk", object())
    monkeypatch.setattr(app_module, "PastexApp", FakeApp)
    monkeypatch.setattr(app_module, "SingleInstance", lambda handler: None)

    with pytest.raises(RuntimeError):
        app_module.run_cli([])
    assert released == ["owner-released"]


# --- Watchdog: the window must never stay minimized ------------------------


def _stalled_app(**extra):
    """A half-built app whose send stage never reports back."""
    from pastex.app import PastexApp

    calls = []

    class FakeApp:
        _pasting = True
        _paste_finished = False
        _paste_aborted = False
        settings: ClassVar[dict[str, float]] = {"restore_delay": 0}
        status_var = type("V", (), {"set": staticmethod(lambda _v: None)})()
        _cancel_paste_send_timeout = PastexApp._cancel_paste_send_timeout
        _cancel_paste_watchdog = PastexApp._cancel_paste_watchdog
        _show_paste_failure = lambda self, message: calls.append(("dialog", message))
        _finish_paste = PastexApp._finish_paste

        def deiconify(self):
            calls.append("deiconify")

        def lift(self):
            calls.append("lift")

        def after(self, *_args):
            return "after-id"

        def after_cancel(self, _id):
            return None

    app = FakeApp()
    # State lives on the instance: PastexApp reads it through __dict__ so that
    # half-built test doubles never recurse through Tk's __getattr__.
    app._pasting = True
    app._paste_finished = False
    app._paste_aborted = False
    for name, value in extra.items():
        setattr(app, name, value)
    return app, calls


def test_the_watchdog_restores_the_window_when_the_sender_stalls():
    """If the sender stalls, the window is still restored and the reason reported."""
    from pastex.app import PastexApp

    app, calls = _stalled_app()

    PastexApp._paste_watchdog(app)

    assert "deiconify" in calls
    assert app._paste_aborted is True, "the remaining attempts must be abandoned"
    assert app._paste_finished is True
    assert app._pasting is False
    assert app._paste_ready is False
    assert any(isinstance(c, tuple) and c[0] == "dialog" for c in calls)


def test_the_watchdog_does_nothing_after_a_finished_paste():
    from pastex.app import PastexApp

    app, calls = _stalled_app(_paste_finished=True, _pasting=False)

    PastexApp._paste_watchdog(app)

    assert calls == []


def test_a_late_result_after_the_watchdog_does_not_move_the_window_again():
    from pastex.app import PastexApp
    from pastex.clipboard import PasteResult

    app, calls = _stalled_app(_paste_finished=True, _pasting=False, _paste_ready=False)

    PastexApp._finish_paste(app, PasteResult(ok=True, tool="ydotool", message="ok"))

    assert "deiconify" not in calls
    assert app._paste_ready is True, "a late result is still recorded"


def test_arming_the_watchdog_needs_a_real_interpreter():
    """A test double without tk must not touch after(): that recurses through Tk."""
    from pastex.app import PastexApp

    app = PastexApp.__new__(PastexApp)

    PastexApp._arm_paste_watchdog(app)
    PastexApp._cancel_paste_watchdog(app)

    assert app.__dict__.get("_paste_watchdog_id") is None


def test_a_failed_paste_stops_hiding_the_window_on_the_next_click(monkeypatch):
    """The failure is remembered: the next click only copies and keeps the window."""
    from pastex import app as app_module
    from pastex.app import PastexApp
    from pastex.clipboard import PasteResult

    calls = []

    def _paste_worker(self, text):
        calls.append(("copy", text))

    class FakeApp:
        _pasting = False
        settings: ClassVar[dict[str, float]] = {"restore_delay": 0}
        status_var = type("V", (), {"set": staticmethod(lambda _v: None)})()
        _cancel_paste_send_timeout = PastexApp._cancel_paste_send_timeout
        _cancel_paste_watchdog = PastexApp._cancel_paste_watchdog

        def _hide_for_paste(self):  # pragma: no cover - must not be used
            raise AssertionError("the window must not be minimised")

        def _send_paste(self):  # pragma: no cover - must not be used
            raise AssertionError("Ctrl+V must not be armed")

        def deiconify(self):
            calls.append("deiconify")

        def lift(self):
            calls.append("lift")

        def _show_paste_failure(self, message):
            calls.append(("dialog", message))

    app = FakeApp()
    app._paste_worker = _paste_worker
    monkeypatch.setattr(
        app_module,
        "_run_in_thread",
        lambda target, *args: calls.append(("start", target.__name__, *args)),
    )

    PastexApp._finish_paste(app, PasteResult(ok=False, tool="", message="failed"))
    assert app._paste_ready is False
    calls.clear()

    PastexApp._paste_text(app, "body")

    assert calls == [("start", "_paste_worker", "body")]
    assert app._paste_restore_window is False


def test_an_empty_paste_plan_means_ctrl_v_cannot_be_sent():
    from pastex.app import PastexApp
    from pastex.clipboard import ToolPlan

    app = PastexApp.__new__(PastexApp)
    assert PastexApp._paste_tools_ready(app) is True  # not known yet

    app._paste_plan = ToolPlan("wayland", [])
    assert PastexApp._paste_tools_ready(app) is False

    app._paste_plan = ToolPlan("wayland", [("ydotool", ["ydotool", "key"])])
    assert PastexApp._paste_tools_ready(app) is True


def test_a_click_with_a_hidden_window_arms_the_watchdog(monkeypatch):
    """Whenever the window is hidden, the watchdog must be armed."""
    from pastex import app as app_module
    from pastex.app import PastexApp

    scheduled = []

    class FakeApp:
        status_var = type("V", (), {"set": staticmethod(lambda _v: None)})()

        def __init__(self):
            self.tk = object()
            self._paste_clipboard_ready = True

        def _hide_for_paste(self):
            return True

        def _send_paste(self):
            return None

        def _paste_worker(self, text):
            return None

        def after(self, delay, callback=None):
            scheduled.append(delay)
            return "after-id"

        def after_cancel(self, _id):
            return None

    app = FakeApp()
    monkeypatch.setattr(app_module, "_run_in_thread", lambda *a, **k: None)

    PastexApp._paste_text(app, "body")

    assert scheduled == [app_module.PASTE_DEADLINE_MS]

# --- The window must never be left minimized ------------------------------


def _restore_app(states, *, tk_ready=True, restore=True):
    """Test double that answers state() and records iconify/deiconify/lift."""
    calls = []
    scheduled = []

    class FakeApp:
        def __init__(self):
            if tk_ready:
                self.tk = object()
            self._paste_pending = True
            self._paste_clipboard_ready = True
            self._paste_restore_window = restore
            self._paste_finished = False
            self._pasting = True
            self.statuses = []

        def state(self):
            return states.pop(0) if states else "normal"

        def after(self, delay, callback=None):
            scheduled.append((delay, callback))
            return f"id-{len(scheduled)}"

        def after_cancel(self, _id):
            return None

        def iconify(self):
            calls.append("iconify")

        def deiconify(self):
            calls.append("deiconify")

        def lift(self):
            calls.append("lift")

        def _paste_sender(self):
            return None

    app = FakeApp()
    app.status_var = type("V", (), {"set": staticmethod(app.statuses.append)})()
    app.scheduled = scheduled
    app.calls = calls
    return app


def test_the_window_is_restored_when_the_hide_check_gives_up(monkeypatch):
    """The state read lags the WM: giving up must not leave it minimized."""
    from pastex import app as app_module
    from pastex.app import PastexApp

    app = _restore_app(["normal"] * 60, tk_ready=False)
    monkeypatch.setattr(app_module, "_run_in_thread", lambda *a, **k: None)

    PastexApp._send_paste_now(app)

    assert app._paste_finished is True
    assert app.calls.count("deiconify") == 1
    assert app.statuses[-1] == app_module.PASTE_MANUAL_HINT


def test_a_late_minimize_is_undone_after_the_give_up(monkeypatch):
    """The window finishes minimizing right after the check gave up."""
    from pastex import app as app_module
    from pastex.app import PastexApp

    # The polling is driven by after(); answer "normal" until the budget is
    # exhausted, then let the WM report the window as hidden.
    states = ["normal"] * 60
    app = _restore_app(states, tk_ready=True)
    monkeypatch.setattr(app_module, "_run_in_thread", lambda *a, **k: None)

    PastexApp._send_paste_now(app)
    assert app.calls.count("deiconify") == 0  # still polling, nothing decided

    steps = 0
    while app.scheduled and not app._paste_finished:
        _delay, callback = app.scheduled.pop(0)
        callback()
        steps += 1
        if steps > 80:  # pragma: no cover - safety net
            break

    assert app._paste_finished is True
    assert app.calls.count("deiconify") == 1  # the give-up restored it

    # The window finishes minimizing right now, so the restore check sees it.
    states.insert(0, "iconic")
    while app.scheduled:
        _delay, callback = app.scheduled.pop(0)
        callback()

    assert app.calls.count("deiconify") >= 2  # repeated, not a single ask


def test_the_restore_is_repeated_until_the_window_is_back():
    """Cinnamon drops deiconify() that lands during the minimize animation."""
    from pastex import app as app_module
    from pastex.app import PastexApp
    from pastex.clipboard import PasteResult

    app = _restore_app(["iconic", "iconic", "normal"])
    result = PasteResult(ok=True, tool="xdotool", message="Paste sent.")

    PastexApp._finish_paste(app, result)
    assert app.calls.count("deiconify") == 1
    assert app.scheduled, "a restore check must be scheduled"

    while app.scheduled:
        _delay, callback = app.scheduled.pop(0)
        callback()

    # Repeated at least while the state said "iconic" (the repeat window adds
    # a few more asks; see test_the_restore_keeps_asking_even_when_the_state_says_normal).
    assert app.calls.count("deiconify") >= 3
    assert app._restore_waited_ms >= 2 * app_module.RESTORE_RETRY_MS


def test_the_restore_keeps_asking_even_when_the_state_says_normal():
    """Muffin reported "normal" for a window it had left minimized.

    Trusting that state cut the repeat loop short after one dropped
    deiconify(), so the window stayed minimized after a successful paste.
    The request is now repeated for RESTORE_REPEAT_MS whatever the state says.
    """
    from pastex import app as app_module
    from pastex.app import PastexApp
    from pastex.clipboard import PasteResult

    app = _restore_app(["normal"] * 50)
    result = PasteResult(ok=True, tool="xdotool", message="Paste sent.")

    PastexApp._finish_paste(app, result)
    assert app.calls.count("deiconify") == 1

    steps = 0
    while app.scheduled:
        _delay, callback = app.scheduled.pop(0)
        callback()
        steps += 1
        if steps > 60:  # pragma: no cover - safety net
            break

    repeats = app_module.RESTORE_REPEAT_MS // app_module.RESTORE_RETRY_MS
    assert app.calls.count("deiconify") >= repeats
    assert app._restore_waited_ms > app_module.RESTORE_REPEAT_MS
    # It still stops: the asks are bounded by the repeat window.
    assert app.calls.count("deiconify") <= repeats + 3


def test_the_window_is_reactivated_when_deiconify_is_dropped(monkeypatch):
    """EWMH activation is the last resort when the WM ignores deiconify()."""
    from pastex import app as app_module
    from pastex.app import PastexApp
    from pastex.clipboard import PasteResult

    activated = []
    app = _restore_app(["iconic"] * 50)
    app.winfo_id = lambda: 41943043  # 0x2800003
    monkeypatch.setattr(
        app_module,
        "_run_in_thread",
        lambda target, *args: activated.append((target, args)),
    )

    PastexApp._finish_paste(app, PasteResult(ok=True, tool="xdotool", message="ok"))
    steps = 0
    while app.scheduled:
        _delay, callback = app.scheduled.pop(0)
        callback()
        steps += 1
        if steps > 60:  # pragma: no cover - safety net
            break

    # Asked exactly once, with the real window id.
    assert [args for _fn, args in activated] == [("0x2800003",)]
    assert activated[0][0] is app_module.activate_window


def test_the_restore_gives_up_after_the_wait_budget():
    from pastex import app as app_module
    from pastex.app import PastexApp
    from pastex.clipboard import PasteResult

    app = _restore_app(["iconic"] * 400)
    PastexApp._finish_paste(app, PasteResult(ok=True, tool="xdotool", message="ok"))

    steps = 0
    while app.scheduled:
        _delay, callback = app.scheduled.pop(0)
        callback()
        steps += 1
        if steps > 100:  # pragma: no cover - safety net
            break

    assert steps <= app_module.RESTORE_WAIT_MS // app_module.RESTORE_RETRY_MS + 1


def test_the_minimize_wait_covers_a_cinnamon_minimize_animation():
    from pastex import app as app_module

    assert app_module.MINIMIZE_WAIT_MS >= 600
    assert app_module.RESTORE_WAIT_MS >= 1000


def test_a_late_copy_result_does_not_overwrite_a_running_paste():
    """The <Unmap> signal can release the send before the copy reports back."""
    from pastex.app import PastexApp
    from pastex.clipboard import ClipboardResult

    app = _restore_app(["normal"])
    app._paste_pending = False
    app._paste_started = True

    PastexApp._after_copy(app, ClipboardResult(ok=True, tool="xclip"))

    assert app._pasting is True
    assert app.statuses == []
    assert app._paste_clipboard_ready is True


def test_a_picker_paste_does_not_wait_for_the_hidden_main_window():
    """The popup is destroyed, the main window stays up: that is hidden enough."""
    from pastex.app import PastexApp

    class FakeApp:
        def state(self):
            return "normal"

    app = FakeApp()
    app._paste_hide_widget = object()  # the popup, not the main window

    assert PastexApp._paste_window_hidden(app) is True


def test_an_x11_verdict_of_unmapped_sends_the_paste(monkeypatch):
    """A window manager that forgets to update WM_STATE must not block Ctrl+V."""
    from pastex import app as app_module
    from pastex.app import PastexApp

    scheduled = []

    class FakeApp:
        def state(self):
            return "normal"  # Tk never noticed the minimize

        def after(self, delay, callback=None):
            scheduled.append((delay, callback))
            return "id"

    app = FakeApp()
    app._window_unmapped = True

    assert PastexApp._paste_window_hidden(app) is True
    assert scheduled == []  # nothing was sent by the check itself


def test_the_x_server_can_end_the_minimize_wait_early(monkeypatch):
    from pastex import app as app_module
    from pastex.app import PastexApp

    scheduled = []
    monkeypatch.setattr(app_module, "x11_focus_moved", lambda *a, **k: None)
    monkeypatch.setattr(app_module, "x11_wm_state", lambda *a, **k: "iconic")
    monkeypatch.setattr(app_module, "x11_window_unmapped", lambda window_id: True)

    class FakeApp:
        def after(self, delay, callback=None, *args):
            scheduled.append((delay, callback, args))
            return "id"

    app = FakeApp()
    PastexApp._x11_confirm_hidden_worker(app, "0x2800007")

    assert app._window_unmapped is True
    assert scheduled == [(0, PastexApp._send_paste_now, (app,))]


def test_a_mapped_window_is_not_declared_hidden_by_the_x_server(monkeypatch):
    from pastex import app as app_module
    from pastex.app import PastexApp

    scheduled = []
    monkeypatch.setattr(app_module, "x11_focus_moved", lambda *a, **k: None)
    monkeypatch.setattr(app_module, "x11_wm_state", lambda *a, **k: "normal")
    monkeypatch.setattr(app_module, "x11_window_unmapped", lambda window_id: False)

    class FakeApp:
        def after(self, delay, callback=None, *args):
            scheduled.append((delay, callback, args))
            return "id"

    app = FakeApp()
    PastexApp._x11_confirm_hidden_worker(app, "0x2800007")

    assert app.__dict__.get("_window_unmapped", False) is False
    assert scheduled == []


def test_a_zoomed_window_is_unzoomed_before_the_minimize(monkeypatch):
    """Tk/X11 drops iconify() for a maximized window, so unzoom first."""
    from pastex.app import PastexApp

    calls = []
    states = ["zoomed", "normal"]

    class FakeApp:
        def state(self, new=None):
            if new is not None:
                calls.append(("state", new))
                return None
            return states.pop(0) if states else "normal"

        def iconify(self):
            calls.append("iconify")

        def update_idletasks(self):
            return None

    app = FakeApp()

    assert PastexApp._hide_for_paste(app) is True
    assert calls == [("state", "normal"), "iconify"]
    assert app._paste_was_zoomed is True


def test_a_normal_window_is_not_touched_by_the_unzoom(monkeypatch):
    from pastex.app import PastexApp

    calls = []

    class FakeApp:
        def state(self, new=None):
            if new is not None:
                calls.append(("state", new))
                return None
            return "normal"

        def iconify(self):
            calls.append("iconify")

        def update_idletasks(self):
            return None

    app = FakeApp()

    assert PastexApp._hide_for_paste(app) is True
    assert calls == ["iconify"]
    assert app._paste_was_zoomed is False


def test_the_zoom_comes_back_with_the_window(monkeypatch):
    from pastex.app import PastexApp

    calls = []

    class FakeApp:
        def deiconify(self):
            calls.append("deiconify")

        def state(self, new=None):
            if new is not None:
                calls.append(("state", new))
            return "normal"

        def after(self, delay, callback=None):
            return "id"

    app = FakeApp()
    app._paste_was_zoomed = True
    monkeypatch.setattr(PastexApp, "_wait_for_the_restore", lambda self: None)

    PastexApp._restore_window(app)

    assert calls == ["deiconify", ("state", "zoomed")]
    assert app.__dict__.get("_paste_was_zoomed") is None


def test_the_window_manager_is_asked_when_iconify_is_ignored(monkeypatch):
    """A WM that ignores iconify() still has to answer xdotool's request."""
    from pastex import app as app_module
    from pastex.app import PastexApp

    threads = []
    monkeypatch.setattr(
        app_module, "_run_in_thread", lambda fn, *a, **k: threads.append((fn, a))
    )
    app = _polling_app(["normal"] * 80)

    def winfo_id():
        return 41943043  # 0x2800003

    app.winfo_id = winfo_id

    PastexApp._send_paste_now(app)
    steps = 0
    while app.scheduled:
        _delay, callback = app.scheduled.pop(0)
        callback()
        steps += 1
        if steps > 80:  # pragma: no cover - safety net
            break

    forced = [c for c in threads if c[0] is app_module.force_minimize_window]
    # Asked exactly once, with the real X11 window id.
    assert forced == [(app_module.force_minimize_window, ("0x2800003",))]
    # The focus is re-read every 120 ms: a WM may hand it over late.
    checked = [
        args[-1]
        for fn, args in threads
        if fn is PastexApp._x11_confirm_hidden_worker
    ]
    assert checked == list(
        range(
            app_module.MINIMIZE_FOCUS_CHECK_AT_MS,
            app_module.MINIMIZE_WAIT_MS,
            app_module.MINIMIZE_FOCUS_RETRY_MS,
        )
    )


def test_a_window_without_an_x11_id_still_gives_up_quietly(monkeypatch):
    from pastex import app as app_module
    from pastex.app import PastexApp

    threads = []
    monkeypatch.setattr(
        app_module, "_run_in_thread", lambda fn, *a, **k: threads.append((fn, a))
    )
    app = _polling_app(["normal"] * 80)  # no winfo_id at all

    PastexApp._send_paste_now(app)
    steps = 0
    while app.scheduled:
        _delay, callback = app.scheduled.pop(0)
        callback()
        steps += 1
        if steps > 80:  # pragma: no cover - safety net
            break

    assert threads == []
    assert app._paste_finished is True


def test_a_focus_that_moved_away_is_enough_to_paste(monkeypatch):
    """The paste only needs the focus back on the target app.

    On a real Cinnamon session Muffin reported ``normal`` from ``wm state``
    for the whole budget while the focus had in fact left Pastex, which is
    why 1.10.3 still sent no Ctrl+V.
    """
    from pastex import app as app_module
    from pastex.app import PastexApp

    scheduled = []
    monkeypatch.setattr(app_module, "x11_focus_moved", lambda *a, **k: True)
    monkeypatch.setattr(app_module, "x11_wm_state", lambda *a, **k: "normal")
    monkeypatch.setattr(app_module, "x11_window_unmapped", lambda *a, **k: None)

    class FakeApp:
        def after(self, delay, callback=None, *args):
            scheduled.append((delay, callback, args))
            return "id"

    app = FakeApp()
    PastexApp._x11_confirm_hidden_worker(app, "0x2a00009", 200)

    assert app._window_unmapped is True
    assert app._hide_signal == "focus"
    assert app._focus_state == "moved"
    assert scheduled == [(0, PastexApp._send_paste_now, (app,))]


def test_a_focus_that_still_belongs_to_pastex_blocks_the_paste(monkeypatch):
    from pastex import app as app_module
    from pastex.app import PastexApp

    scheduled = []
    monkeypatch.setattr(app_module, "x11_focus_moved", lambda *a, **k: False)
    # Even a WM that claims iconic must not override the focus.
    monkeypatch.setattr(app_module, "x11_wm_state", lambda *a, **k: "iconic")

    class FakeApp:
        def after(self, delay, callback=None, *args):
            scheduled.append((delay, callback, args))
            return "id"

    app = FakeApp()
    PastexApp._x11_confirm_hidden_worker(app, "0x2a00009", 320)

    assert app.__dict__.get("_window_unmapped", False) is False
    assert app._focus_state == "ours"
    assert scheduled == []


def test_the_wm_state_is_used_when_the_focus_cannot_be_read(monkeypatch):
    from pastex import app as app_module
    from pastex.app import PastexApp

    scheduled = []
    monkeypatch.setattr(app_module, "x11_focus_moved", lambda *a, **k: None)
    monkeypatch.setattr(app_module, "x11_wm_state", lambda *a, **k: "iconic")

    class FakeApp:
        def after(self, delay, callback=None, *args):
            scheduled.append((delay, callback, args))
            return "id"

    app = FakeApp()
    PastexApp._x11_confirm_hidden_worker(app, "0x2a00009", 440)

    assert app._window_unmapped is True
    assert app._hide_signal == "wm-state"
    assert scheduled == [(0, PastexApp._send_paste_now, (app,))]


def test_a_second_focus_verdict_is_not_repeated(monkeypatch):
    from pastex import app as app_module
    from pastex.app import PastexApp

    calls = []
    scheduled = []
    monkeypatch.setattr(
        app_module,
        "x11_focus_moved",
        lambda *a, **k: calls.append(1) or True,
    )

    class FakeApp:
        def after(self, delay, callback=None, *args):
            scheduled.append((delay, callback, args))
            return "id"

    app = FakeApp()
    app._window_unmapped = True  # already decided by the previous check
    PastexApp._x11_confirm_hidden_worker(app, "0x2a00009", 560)

    assert calls == []  # no more xdotool calls once the window is known hidden
    assert scheduled == []


def _clipboard_app(monkeypatch, *, verdicts, claim=True):
    """Test double for the copy worker: records Tk clipboard ownership."""
    from pastex import app as app_module
    from pastex.app import PastexApp

    claimed = []
    answers = list(verdicts)
    calls = []

    class FakeApp:
        def __init__(self):
            self.statuses = []

        def after(self, delay, callback=None, *args):
            calls.append((delay, args))
            if callback is not None:
                callback(*args)
            return "id"

        def clipboard_clear(self):
            claimed.append("clear")

        def clipboard_append(self, text):
            if not claim:
                raise RuntimeError("no display")
            claimed.append(text)

        def update_idletasks(self):
            claimed.append("idle")

    app = FakeApp()
    app.status_var = type("V", (), {"set": staticmethod(app.statuses.append)})()
    monkeypatch.setattr(
        app_module,
        "verify_clipboard",
        lambda text: answers.pop(0) if answers else None,
    )
    return app, claimed, calls


def test_the_clipboard_is_claimed_before_it_is_read(monkeypatch):
    """Tk owns the selection first: instant, in-process, and it cannot be lost.

    Reading first cost about a second per click on a machine where both
    clipboard readers are slow, which is why the text appeared late.
    """
    from pastex.app import PastexApp

    app, claimed, _calls = _clipboard_app(monkeypatch, verdicts=[True])
    result = type("R", (), {"ok": True, "tool": "xclip", "message": ""})()

    assert PastexApp._clipboard_holds(app, "body", result) is True
    assert claimed == ["clear", "body", "idle"]


def test_an_empty_clipboard_is_reported_even_though_tk_owns_it(monkeypatch):
    from pastex.app import PastexApp

    app, claimed, _calls = _clipboard_app(monkeypatch, verdicts=[False])
    result = type("R", (), {"ok": True, "tool": "xclip", "message": ""})()

    assert PastexApp._clipboard_holds(app, "body", result) is False
    assert claimed == ["clear", "body", "idle"]


def test_an_unreadable_clipboard_keeps_the_tool_result(monkeypatch):
    from pastex.app import PastexApp

    app, claimed, _calls = _clipboard_app(monkeypatch, verdicts=[None])
    result = type("R", (), {"ok": True, "tool": "xclip", "message": ""})()

    assert PastexApp._clipboard_holds(app, "body", result) is True
    assert claimed == ["clear", "body", "idle"]


def test_a_clipboard_that_stays_empty_blocks_the_paste(monkeypatch):
    from pastex.app import PastexApp

    app, claimed, _calls = _clipboard_app(monkeypatch, verdicts=[False], claim=False)
    result = type("R", (), {"ok": True, "tool": "xclip", "message": ""})()

    assert PastexApp._clipboard_holds(app, "body", result) is False
    # It tried once up front and once more after the failed read.
    assert claimed == ["clear", "clear"]


def test_the_worker_marks_an_empty_clipboard(monkeypatch):
    """A tool that "succeeds" while the clipboard stays empty must not paste."""
    from pastex import app as app_module
    from pastex.app import PastexApp

    scheduled = []
    app = PastexApp.__new__(PastexApp)
    app.after = lambda delay, cb=None, *args: scheduled.append((cb, args))
    monkeypatch.setattr(
        app_module,
        "copy_text",
        lambda text, plan=None: ClipboardResultLike(True),
    )
    monkeypatch.setattr(app_module, "verify_clipboard", lambda text: False)
    monkeypatch.setattr(PastexApp, "_claim_the_clipboard", lambda self, text: False)

    PastexApp._paste_worker(app, "body")

    callback, args = scheduled[-1]
    assert getattr(callback, "__func__", callback) is PastexApp._after_copy
    assert args[1] is True  # the clipboard was empty
    assert "does not hold the text" in args[0].message


def test_an_empty_clipboard_does_not_send_ctrl_v(monkeypatch):
    from pastex import app as app_module
    from pastex.app import PastexApp

    shown = []
    restored = []
    app = PastexApp.__new__(PastexApp)
    app.status_var = type("V", (), {"set": staticmethod(shown.append)})()
    monkeypatch.setattr(PastexApp, "_restore_window", lambda self, **k: restored.append(1))
    monkeypatch.setattr(
        app_module, "messagebox", type("M", (), {"showerror": staticmethod(lambda *a: shown.append(a))})
    )

    PastexApp._after_copy(
        app, ClipboardResultLike(False), True
    )

    assert app._paste_clipboard_ready is False
    assert app._paste_finished is True
    assert restored == [1]  # the window comes back instead of staying hidden
    assert "Could not copy to the clipboard" in shown[0]


def test_the_text_is_typed_when_the_clipboard_stays_empty(monkeypatch):
    """A clipboard that cannot hold the text no longer cancels the paste."""
    from pastex import app as app_module
    from pastex.app import PastexApp

    scheduled = []
    app = PastexApp.__new__(PastexApp)
    app.after = lambda delay, cb=None, *args: scheduled.append((cb, args))
    monkeypatch.setattr(
        app_module, "copy_text", lambda text, plan=None: ClipboardResultLike(True)
    )
    monkeypatch.setattr(app_module, "verify_clipboard", lambda text: False)
    monkeypatch.setattr(PastexApp, "_claim_the_clipboard", lambda self, text: False)
    monkeypatch.setattr(app_module, "can_type_text", lambda text: True)

    PastexApp._paste_worker(app, "body")

    callback, args = scheduled[-1]
    assert getattr(callback, "__func__", callback) is PastexApp._after_copy
    assert args[0].ok is True  # the paste goes ahead, by typing
    assert args[1] is False
    assert app._paste_by_typing is True


def test_nothing_is_pasted_when_the_text_cannot_be_typed_either(monkeypatch):
    from pastex import app as app_module
    from pastex.app import PastexApp

    scheduled = []
    app = PastexApp.__new__(PastexApp)
    app.after = lambda delay, cb=None, *args: scheduled.append((cb, args))
    monkeypatch.setattr(
        app_module, "copy_text", lambda text, plan=None: ClipboardResultLike(True)
    )
    monkeypatch.setattr(app_module, "verify_clipboard", lambda text: False)
    monkeypatch.setattr(PastexApp, "_claim_the_clipboard", lambda self, text: False)
    monkeypatch.setattr(app_module, "can_type_text", lambda text: False)

    PastexApp._paste_worker(app, "body")

    _callback, args = scheduled[-1]
    assert args[0].ok is False
    assert args[1] is True  # reported as an empty clipboard
    assert "cannot be typed" in args[0].message


def test_the_sender_types_instead_of_pressing_ctrl_v(monkeypatch):
    from pastex import app as app_module
    from pastex.app import PastexApp

    calls = []
    scheduled = []
    app = PastexApp.__new__(PastexApp)
    app._paste_by_typing = True
    app._paste_body = "body"
    app.after = lambda delay, cb=None, *args: scheduled.append((cb, args))
    monkeypatch.setattr(app_module, "type_text_x11", lambda text: calls.append(("type", text)) or PasteResultLike())
    monkeypatch.setattr(
        app_module,
        "simulate_paste",
        lambda plan=None, should_stop=None: calls.append(("ctrl+v", None)),
    )

    PastexApp._paste_sender(app)

    assert calls == [("type", "body")]
    assert scheduled


def test_the_sender_uses_ctrl_v_when_the_clipboard_was_verified(monkeypatch):
    from pastex import app as app_module
    from pastex.app import PastexApp

    calls = []
    app = PastexApp.__new__(PastexApp)
    app._paste_body = "body"
    app.after = lambda *args: None
    monkeypatch.setattr(app_module, "type_text_x11", lambda text: calls.append("type"))
    monkeypatch.setattr(
        app_module,
        "simulate_paste",
        lambda plan=None, should_stop=None: calls.append("ctrl+v") or PasteResultLike(),
    )

    PastexApp._paste_sender(app)

    assert calls == ["ctrl+v"]


def test_a_click_resets_the_typing_decision():
    """A previous failing click must not make the next one type."""

    class FakeApp:
        def __init__(self):
            self._paste_by_typing = True
            self._pasting = False
            self.status_var = type("V", (), {"set": staticmethod(lambda _v: None)})()

        def state(self):
            return "iconic"

        def iconify(self):
            return None

        def update_idletasks(self):
            return None

        def after(self, delay, callback=None, *args):
            return "id"

        def after_cancel(self, _id):
            return None

        def _hide_for_paste(self):
            return False

        def _paste_worker(self, text):
            return None

        def _send_paste(self):
            return None

    from pastex.app import PastexApp

    app = FakeApp()
    PastexApp._paste_text(app, "body")

    assert app._paste_by_typing is False
    assert app._paste_body == "body"


def test_the_clipboard_is_not_checked_again_once_it_proved_unusable(monkeypatch):
    """A failing check costs a GUI round trip plus two reads; do it once."""
    from pastex import app as app_module
    from pastex.app import PastexApp

    claims = []
    checks = []
    app = PastexApp.__new__(PastexApp)
    app._clipboard_unreliable = True
    monkeypatch.setattr(
        PastexApp, "_claim_the_clipboard", lambda self, text: claims.append(1) or True
    )
    monkeypatch.setattr(
        app_module, "verify_clipboard", lambda text: checks.append(1) or True
    )
    result = type("R", (), {"ok": True, "tool": "xclip", "message": ""})()

    assert PastexApp._clipboard_holds(app, "body", result) is False
    assert claims == []
    assert checks == []


def test_every_paste_log_carries_the_time_since_the_click():
    from pastex.app import PastexApp

    app = PastexApp.__new__(PastexApp)
    assert PastexApp._since_click(app) == "+? ms"  # no click yet

    app._paste_clicked_at = 0.0  # long ago
    offset = PastexApp._since_click(app)
    assert offset.startswith("+") and offset.endswith(" ms")
    # The number must be the real distance from the click, not a fixed zero.
    assert float(offset[1:-3]) > 100


def test_typing_is_not_cut_short_by_the_paste_deadline(monkeypatch):
    from pastex import app as app_module
    from pastex.app import PastexApp

    cancelled = []
    app = PastexApp.__new__(PastexApp)
    app._paste_by_typing = True
    app._paste_body = "body"
    app.after = lambda *args: None
    monkeypatch.setattr(
        PastexApp, "_cancel_paste_watchdog", lambda self: cancelled.append(1)
    )
    monkeypatch.setattr(
        app_module, "type_text_x11", lambda text: PasteResultLike()
    )

    PastexApp._paste_sender(app)

    assert cancelled == [1]


def test_a_slow_tree_refresh_is_logged(monkeypatch):
    """A slow repaint blocks the click behind it; make that visible in the log."""
    from pastex import app as app_module
    from pastex.app import PastexApp

    logged = []
    monkeypatch.setattr(app_module, "log_error", logged.append)
    stamps = iter([0.0, 0.8])
    monkeypatch.setattr(app_module.time, "monotonic", lambda: next(stamps, 1.0))
    monkeypatch.setattr(PastexApp, "_refresh_tree_now", lambda self, force=True: None)

    app = PastexApp.__new__(PastexApp)
    PastexApp.refresh_tree(app, force=True)

    assert logged and "the refresh took" in logged[0]
    assert "800 ms" in logged[0]
