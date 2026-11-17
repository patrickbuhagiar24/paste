"""Uji CLI Pastex (tanpa GUI/display)."""

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
    (tmp_path / "General" / "Greeting.txt").write_text("halo", encoding="utf-8")
    assert run_cli(["--dir", str(tmp_path), "--list"]) == 0
    out = capsys.readouterr().out
    assert "General" in out and "Greeting" in out


class _FakeTree:
    """Treeview palsu: memvalidasi signature insert(parent, index, iid=...)."""

    def __init__(self):
        self.calls = []
        self._exists = set()

    def exists(self, iid):
        return iid in self._exists

    def insert(self, parent, index, iid=None, **kw):
        assert index == "end" or isinstance(index, int), f"index salah: {index!r}"
        assert iid is not None, "iid harus di-pass sebagai keyword"
        self.calls.append((parent, index, iid, kw))
        self._exists.add(iid)
        return iid


def test_insert_node_uses_end_index(tmp_path):
    """Regresi: insert(parent, iid, ...) crash (TclError: expected integer)."""
    from pastex.app import PastexApp
    from pastex.snippets import Node

    app = PastexApp.__new__(PastexApp)  # tanpa __init__ (tanpa display)
    app.tree = _FakeTree()
    # Tk.__getattr__ memerlukan interpreter yang tidak dibuat oleh __new__;
    # inisialisasi atribut yang dibaca _insert_node secara eksplisit.
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
    """--check harus memeriksa clipboard, bukan hanya tool yang ada."""
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
    """Hide dimulai lebih dulu, jadi compositor dan tool clipboard jalan paralel."""
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

    PastexApp._paste_text(app, "isi")

    # Hide and arm happen on the click path; the copy runs in the worker.
    assert calls == [("hide",), ("arm",), ("start", "_paste_worker", "isi")]
    # Ctrl+V must not be released before the clipboard owns the text.
    assert app._paste_clipboard_ready is False
    assert app._paste_restore_window is True


def test_do_paste_uses_the_preview_text(monkeypatch):
    """Klik memakai teks yang sudah dibaca preview, tanpa menyentuh disk lagi."""
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
            return "isi"

        def _paste_text(self, text, hide=None):
            calls.append(("paste", text, hide))

    monkeypatch.setattr(
        app_module,
        "read_text",
        lambda _path: (_ for _ in ()).throw(AssertionError("disk dibaca ulang")),
    )

    PastexApp.do_paste(FakeApp())

    assert calls == [("cached", "Greeting.txt"), ("paste", "isi", None)]


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
    """Saat copy gagal, pastikan jendela terlihat untuk membaca pesan."""
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
    PastexApp._paste_failed(app, "tidak ada tool clipboard")
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
    assert ("deiconify", None) in calls  # jendela kembali
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
    PastexApp._paste_done(app, PasteResult(False, "", "gagal"))
    assert ("lift", None) in calls  # kegagalan harus terlihat
    assert ("dialog", "gagal") in calls


def test_activate_always_pastes():
    """Klik/Enter = satu tindakan: paste. Tidak ada mode 'salin saja'."""
    from pastex.app import PastexApp

    calls = []

    class FakeApp:
        def do_paste(self):
            calls.append("paste")

    # atribut do_copy sudah tidak ada di versi ini
    assert not hasattr(PastexApp, "do_copy")
    PastexApp.on_activate(FakeApp())
    assert calls == ["paste"]


def test_no_legacy_controls_in_source():
    """Tombol Salin / pintasan / hitung mundur sudah dihapus."""
    src = (Path(__file__).resolve().parent.parent / "pastex" / "app.py").read_text()
    for gone in (
        "do_copy",
        "auto_var",
        "delay_var",
        "_start_countdown",
        "install-shortcut",
        "paste-foreground",
    ):
        assert gone not in src, f"masih ada sisa: {gone}"


# --- Debounce pencarian ---------------------------------------------------


def test_search_debounces_instead_of_scanning_every_keystroke():
    """Regresi: _on_search_changed() didefinisikan tetapi TIDAK pernah
    tersambung ke trace_add, dan _search_after_id tidak pernah diinisialisasi.
    Akibatnya setiap huruf memicu pemindaian penuh folder.
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
    assert scans == [], "refresh_tree dipanggil langsung, bukan lewat debounce"
    timers = [c for c in calls if c[0] == "after"]
    assert len(timers) == 3 and all(ms == 180 for _, ms in timers)
    cancels = [c for c in calls if c[0] == "cancel"]
    assert len(cancels) == 2, "timer sebelumnya harus dibatalkan"


def test_search_var_connected_to_debounce_handler():
    """trace_add harus memanggil _on_search_changed, bukan refresh_tree."""
    src = (Path(__file__).resolve().parent.parent / "pastex" / "app.py").read_text()
    line = next(ln for ln in src.splitlines() if "trace_add" in ln)
    assert "_on_search_changed" in line, line
    assert "refresh_tree" not in line, line


def test_search_after_id_initialised():
    """_search_after_id harus dibuat sebelum handler dipakai."""
    src = (Path(__file__).resolve().parent.parent / "pastex" / "app.py").read_text()
    assert "_search_after_id: str | None = None" in src


# --- Hasil paste yang belum pasti ----------------------------------------


def test_paste_done_does_not_claim_success_when_not_confident():
    """xdotool di Wayland bisa lolos dengan kode 0 tanpa menempelkan apa pun."""
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
        True, "xdotool", "Paste otomatis terkirim via xdotool.", confident=False
    )
    PastexApp._paste_done(FakeApp(), result)
    status = next(v for k, v in calls if k == "status")
    assert "Ctrl+V" in status, "pengguna harus diberi langkah lanjutan"
    assert ("lift", None) in calls, "jendela harus terlihat agar pesan terbaca"


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
    assert done.wait(5), "target tidak dijalankan"
    assert seen == [(1, 2)]


# --- Dock icon during paste ------------------------------------------------


def test_hide_for_paste_minimizes_instead_of_unmapping():
    """Minimizing keeps the window mapped so the dock entry survives."""
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


def test_hide_for_paste_keeps_the_window_when_minimize_is_refused():
    """A refusing window manager leaves the window visible; no unmapping."""
    from pastex.app import PastexApp

    calls = []

    class FakeApp:
        def iconify(self):
            calls.append("iconify")

        def update_idletasks(self):
            calls.append("update")

        def state(self):
            return "normal"

    assert PastexApp._hide_for_paste(FakeApp()) is False
    assert calls == ["iconify", "update"]


def test_hide_for_paste_keeps_the_window_when_iconify_fails():
    from pastex.app import PastexApp

    calls = []

    class FakeApp:
        def iconify(self):
            raise AttributeError("no display")

    assert PastexApp._hide_for_paste(FakeApp()) is False
    assert calls == []


def test_the_window_is_never_unmapped():
    """The dock icon must never disappear, so withdraw() is gone for good."""
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
    """Unmap boleh datang lebih dulu, tapi Ctrl+V menunggu clipboard siap."""
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

    assert calls == [], "Ctrl+V tidak boleh dikirim sebelum clipboard siap"
    assert app._paste_pending is True

    # The clipboard result releases it.
    PastexApp._after_copy(app, ClipboardResultLike(True))

    assert calls == ["_paste_sender"]
    assert app._paste_pending is False


def test_unmap_after_the_copy_sends_immediately(monkeypatch):
    """Bila clipboard siap lebih dulu, unmap langsung mengirim Ctrl+V."""
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
        lambda plan=None: seen.append(("paste", plan)) or PasteResultLike(),
    )

    PastexApp._paste_worker(app, "isi")
    PastexApp._paste_sender(app)

    assert ("copy", "isi", "COPY-PLAN") in seen
    assert ("paste", "PASTE-PLAN") in seen


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

    PastexApp._paste_worker(app, "isi")

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
    """Tool clipboard diresolusi lewat thread, bukan di jalur klik."""
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
    assert app._copy_plan is None, "startup tidak boleh menunggu prewarm"

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
    """Startup harus memanggil prewarm supaya klik pertama tidak lebih lambat."""
    from pastex import app as app_module

    source = Path(app_module.__file__).read_text(encoding="utf-8")

    assert "def _prewarm_paste(" in source
    assert "self._prewarm_paste()" in source
    assert "prewarm as prewarm_clipboard" in source


def test_paste_skips_ctrl_v_when_the_window_cannot_be_minimized(monkeypatch):
    """Minimize gagal: ikon dipertahankan, Ctrl+V tidak dikirim, ada petunjuk."""
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

    PastexApp._paste_text(app, "isi")

    assert calls == [("hide",), ("start", "_paste_worker", "isi")]
    assert "arm" not in calls, "Ctrl+V tidak boleh diarm bila window masih tampil"
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
            raise AssertionError("Ctrl+V tidak boleh dikirim")

        def _paste_failed(self, message):  # pragma: no cover
            raise AssertionError("copy berhasil")

    app = FakeApp()
    PastexApp._after_copy(app, ClipboardResultLike(True))

    assert app._pasting is False
    assert statuses[-1] == app_module.PASTE_MANUAL_HINT
    assert "press Ctrl+V" in app_module.PASTE_MANUAL_HINT


# --- Index, watcher, picker and single instance integration ---------------


def test_refresh_tree_reuses_the_cached_index(gui, tmp_path):
    """Repaint dengan force=False tidak menelusuri disk; force=True memindai ulang."""
    root = tmp_path / "snippets"
    root.mkdir()
    (root / "A.txt").write_text("a", encoding="utf-8")
    app = gui(roots=[root])
    app.refresh_tree()
    scans = app.index.scans
    assert scans >= 1

    app.refresh_tree(force=False)
    assert app.index.scans == scans, "repaint tidak boleh memindai disk"

    app.refresh_tree(force=True)
    assert app.index.scans == scans + 1


def test_search_filters_the_cache_instead_of_rescanning(gui, tmp_path):
    root = tmp_path / "snippets"
    root.mkdir()
    (root / "Greeting.txt").write_text("halo", encoding="utf-8")
    (root / "Other.txt").write_text("lain", encoding="utf-8")
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
            raise AssertionError("pohon tidak boleh dibangun ulang saat paste")

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
    """Pemilihan dari popup: window utama tidak disembunyikan/di-deiconify."""
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
            return "isi"

        def _hide_for_paste(self):  # pragma: no cover - must not be used
            raise AssertionError("window utama tidak boleh diminimize")

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

    assert calls == [], "jangan mencuri fokus dari aplikasi target"
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
    """Ketikan memakai pohon yang sudah di-cache, bukan memindai ulang disk."""
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
    """Repaint menambal baris yang ada, tidak menghapus lalu membangun ulang."""
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
    assert app.tree.deleted == deleted_before, "tidak boleh ada baris yang dihapus"
    assert app.tree.inserts == 3, "baris lama tidak boleh disisipkan ulang"
    assert app.tree.moves == 0
    assert app.tree.item_updates == 0, "tidak ada nilai yang berubah"


def test_refresh_adds_only_the_new_row(gui, tmp_path):
    root = tmp_path / "snippets"
    root.mkdir()
    (root / "A.txt").write_text("a", encoding="utf-8")
    app = gui(roots=[root])
    app.refresh_tree()
    first = app.tree.by_iid(root / "A.txt")

    (root / "B.txt").write_text("b", encoding="utf-8")
    app.refresh_tree()

    assert app.tree.by_iid(root / "A.txt") is first, "baris lama dipakai ulang"
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

    assert app.tree.by_iid(sample_root / "Email") is folder, "baris yang sama"
    assert folder["open"] is False, "status ciut harus menimpa baris lama"


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
    """Root ganda (checkout + salinan sistem) tidak boleh bertabrakan iid."""
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
    """Tk mengembalikan "1"/"dir" untuk opsi; itu bukan perubahan."""
    root = tmp_path / "snippets"
    (root / "Folder").mkdir(parents=True)
    (root / "Folder" / "A.txt").write_text("a", encoding="utf-8")
    app = gui(roots=[root])
    app.tree.string_options = True
    app.refresh_tree()

    app.tree.item_updates = 0
    app.refresh_tree(force=False)

    assert app.tree.item_updates == 0, "nilai Tcl yang setara tidak boleh ditulis ulang"


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

    def copy(self, text, mimes=None):  # pragma: no cover - lewat verify tiruan
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
    assert installed == [], "owner yang gagal tidak boleh dipasang"


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

    app.destroy()

    assert owner.stopped == 1
    assert installed == [None], "copy harus kembali ke wl-copy"
    assert app._clipboard_owner is None


def test_releasing_without_an_owner_does_nothing(gui, monkeypatch):
    from pastex import app as app_module

    installed = []
    monkeypatch.setattr(app_module, "set_clipboard_owner", installed.append)
    app = gui(roots=[])

    app.destroy()

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
