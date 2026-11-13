"""Uji CLI Pastex (tanpa GUI/display)."""

import sys
from pathlib import Path
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


def test_paste_minimizes_before_copying():
    """Jendela diminimalkan SEBELUM penyalinan agar target menerima fokus.

    Bila penyalinan lambat atau gagal, Pastex tetap harus melepas fokus sebelum
    proses salin dimulai agar Ctrl+V dapat dikirim ke aplikasi target.
    """
    from pastex.app import PastexApp

    order = []

    class FakePath:
        name = "Greeting.txt"

        def is_dir(self):
            return False

    class FakeApp:
        settings: ClassVar[dict[str, float]] = {"restore_delay": 0.1}
        _pasting = False
        status_var = type(
            "V", (), {"set": staticmethod(lambda v: order.append("status"))}
        )()

        def selected_path(self):
            return FakePath()

        def iconify(self):
            order.append("iconify")

        def after(self, ms, fn=None):
            order.append("after")

        def _paste_worker(self, text):
            order.append("copy")

    app = FakeApp()
    monkey = __import__("pastex.app", fromlist=["read_text"])
    original = monkey.read_text
    monkey.read_text = lambda p: "isi"
    try:
        PastexApp.do_paste(app)
    finally:
        monkey.read_text = original
    # iconify harus muncul sebelum pekerjaan salin dimulai
    assert order.index("iconify") < order.index("copy")


def test_do_paste_ignores_repeat_while_paste_is_in_progress():
    from pastex.app import PastexApp

    calls = []

    class FakeApp:
        _pasting = True
        status_var = type("V", (), {"set": staticmethod(calls.append)})()

    PastexApp.do_paste(FakeApp())

    assert calls == ["Paste in progress..."]


def test_after_copy_sends_paste_without_focus_wait():
    from pastex.app import PastexApp
    from pastex.clipboard import ClipboardResult

    calls = []

    class FakeApp:
        def _send_paste(self):
            calls.append("send")

    PastexApp._after_copy(FakeApp(), ClipboardResult(True, "wl-copy", "Text copied."))
    assert calls == ["send"]


def test_paste_done_waits_100ms_after_sending_before_restoring():
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
    assert calls == [("wait", 100)]
    app.finish()
    assert calls[-1] == ("deiconify", None)
    assert app._pasting is False


def test_copy_failure_restores_window():
    """Salinan gagal: jendela yang sudah dilepas WAJIB dimunculkan lagi."""
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

        def refresh_tree(self):
            calls.append(("scan",))

    app = FakeApp()
    PastexApp._on_search_changed(app)  # ketikan ke-1
    PastexApp._on_search_changed(app)  # ketikan ke-2
    PastexApp._on_search_changed(app)  # ketikan ke-3
    scans = [c for c in calls if c == ("scan",)]
    assert scans == [], "refresh_tree dipanggil langsung, bukan lewat debounce"
    timers = [c for c in calls if c[0] == "after"]
    assert len(timers) == 3 and all(ms == 300 for _, ms in timers)
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
