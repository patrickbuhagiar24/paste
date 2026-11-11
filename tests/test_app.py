"""Uji CLI Paste Teks (tanpa GUI/display)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paste_teks.app import run_cli


def test_version(capsys):
    assert run_cli(["--version"]) == 0
    assert "paste-teks" in capsys.readouterr().out


def test_check(capsys):
    assert run_cli(["--check"]) == 0
    out = capsys.readouterr().out
    assert "Python:" in out
    assert "tkinter:" in out
    assert "Folder snippets:" in out


def test_list(tmp_path, capsys):
    (tmp_path / "Umum").mkdir()
    (tmp_path / "Umum" / "Sapaan.txt").write_text("halo", encoding="utf-8")
    assert run_cli(["--dir", str(tmp_path), "--list"]) == 0
    out = capsys.readouterr().out
    assert "Umum" in out and "Sapaan" in out


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
    from paste_teks.app import PasteTeksApp
    from paste_teks.snippets import Node

    app = PasteTeksApp.__new__(PasteTeksApp)  # tanpa __init__ (tanpa display)
    app.tree = _FakeTree()
    # Tk.__getattr__ memerlukan interpreter yang tidak dibuat oleh __new__;
    # inisialisasi atribut yang dibaca _insert_node secara eksplisit.
    app.collapsed = set()
    root = Node(name="snippets", path=tmp_path, is_dir=True)
    child = Node(name="Sapaan", path=tmp_path / "Sapaan.txt", is_dir=False)
    root.children = [child]
    app._insert_node("", root)
    assert len(app.tree.calls) == 2
    assert app.tree.calls[0][1] == "end"
    assert app.tree.calls[0][2] == str(tmp_path)


def test_check_reports_clipboard(capsys):
    """--check harus memeriksa clipboard, bukan hanya tool yang ada."""
    assert run_cli(["--check"]) == 0
    out = capsys.readouterr().out
    assert "Clipboard:" in out
    assert "tool:" in out


def test_clipboard_status_without_session(monkeypatch):
    from paste_teks import app
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr("paste_teks.clipboard.shutil.which", lambda n: None)
    lines = app.clipboard_status()
    assert any("tidak terdeteksi" in line for line in lines)


def test_test_paste_flag_exists():
    from paste_teks import app
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
    f = d / "Sapaan.txt"
    f.write_text(text, encoding="utf-8")
    return f


def test_paste_withdraws_before_copying():
    """Jendela dilepas SEBELUM menyalin.

    Bug 1.4.0: withdraw baru dilakukan setelah penyalinan berhasil. Bila
    penyalinan lambat atau gagal, jendela tetap memegang fokus dan Ctrl+V
    tidak pernah terkirim (log berhenti di "[paste] mulai").
    """
    from paste_teks.app import PasteTeksApp

    order = []

    class FakePath:
        name = "Sapaan.txt"

        def is_dir(self):
            return False

    class FakeApp:
        settings = {"focus_delay": 0.6}
        _pasting = False
        status_var = type("V", (), {"set": staticmethod(lambda v: order.append("status"))})()

        def selected_path(self):
            return FakePath()

        def withdraw(self):
            order.append("withdraw")

        def after(self, ms, fn=None):
            order.append("after")

        def _paste_worker(self, text):
            order.append("copy")

    app = FakeApp()
    monkey = __import__("paste_teks.app", fromlist=["read_text"])
    original = monkey.read_text
    monkey.read_text = lambda p: "isi"
    try:
        PasteTeksApp.do_paste(app)
    finally:
        monkey.read_text = original
    # withdraw harus muncul sebelum pekerjaan salin dimulai
    assert order.index("withdraw") < order.index("copy")


def test_after_copy_schedules_focus_delay():
    from paste_teks.app import PasteTeksApp
    from paste_teks.clipboard import ClipboardResult

    calls = []

    class FakeApp:
        settings = {"focus_delay": 0.8}
        status_var = type("V", (), {"set": staticmethod(lambda v: None)})()

        def after(self, ms, fn=None):
            calls.append(("after", ms))

        def _send_paste_after_release(self):
            calls.append(("send", None))
            return 0

    PasteTeksApp._after_copy(FakeApp(), ClipboardResult(True, "wl-copy", "ok", True))
    assert calls[0] == ("after", 800)


def test_copy_failure_restores_window():
    """Salinan gagal: jendela yang sudah dilepas WAJIB dimunculkan lagi."""
    from paste_teks.app import PasteTeksApp

    calls = []

    class FakeApp:
        _pasting = True
        status_var = type("V", (), {"set": staticmethod(lambda v: calls.append(("status", v)))})()
        settings = {}

        def deiconify(self):
            calls.append(("deiconify", None))

        def lift(self):
            calls.append(("lift", None))

        def _show_paste_failure(self, message):
            calls.append(("dialog", message))

    app = FakeApp()
    PasteTeksApp._paste_failed(app, "tidak ada tool clipboard")
    assert ("deiconify", None) in calls
    assert app._pasting is False


def test_paste_done_restores_window():
    from paste_teks.app import PasteTeksApp
    from paste_teks.clipboard import PasteResult

    calls = []

    class FakeApp:
        status_var = type("V", (), {"set": lambda self, v: calls.append(("status", v))})()
        _pasting = True

        def deiconify(self):
            calls.append(("deiconify", None))

        def lift(self):
            calls.append(("lift", None))

    app = FakeApp()
    PasteTeksApp._paste_done(app, PasteResult(True, "ydotool", "ok"))
    assert ("deiconify", None) in calls        # jendela kembali
    assert app._pasting is False


def test_paste_done_failure_raises_window(monkeypatch):
    from paste_teks.app import PasteTeksApp
    from paste_teks.clipboard import PasteResult

    calls = []

    class FakeApp:
        status_var = type("V", (), {"set": lambda self, v: calls.append(("status", v))})()
        _pasting = True

        def deiconify(self):
            calls.append(("deiconify", None))

        def lift(self):
            calls.append(("lift", None))

        def _show_paste_failure(self, message):
            calls.append(("dialog", message))

    app = FakeApp()
    PasteTeksApp._paste_done(app, PasteResult(False, "", "gagal"))
    assert ("lift", None) in calls             # kegagalan harus terlihat
    assert ("dialog", "gagal") in calls


def test_activate_always_pastes():
    """Klik/Enter = satu tindakan: paste. Tidak ada mode 'salin saja'."""
    from paste_teks.app import PasteTeksApp

    calls = []

    class FakeApp:
        def do_paste(self):
            calls.append("paste")

    # atribut do_copy sudah tidak ada di versi ini
    assert not hasattr(PasteTeksApp, "do_copy")
    PasteTeksApp.on_activate(FakeApp())
    assert calls == ["paste"]


def test_no_legacy_controls_in_source():
    """Tombol Salin / pintasan / hitung mundur sudah dihapus."""
    src = (Path(__file__).resolve().parent.parent / "paste_teks" / "app.py").read_text()
    for gone in ("do_copy", "auto_var", "delay_var", "_start_countdown",
                 "install-shortcut", "paste-foreground"):
        assert gone not in src, f"masih ada sisa: {gone}"


# --- Debounce pencarian ---------------------------------------------------

def test_search_debounces_instead_of_scanning_every_keystroke():
    """Regresi: _on_search_changed() didefinisikan tetapi TIDAK pernah
    tersambung ke trace_add, dan _search_after_id tidak pernah diinisialisasi.
    Akibatnya setiap huruf memicu pemindaian penuh folder.
    """
    from paste_teks.app import PasteTeksApp

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
    PasteTeksApp._on_search_changed(app)          # ketikan ke-1
    PasteTeksApp._on_search_changed(app)          # ketikan ke-2
    PasteTeksApp._on_search_changed(app)          # ketikan ke-3
    scans = [c for c in calls if c == ("scan",)]
    assert scans == [], "refresh_tree dipanggil langsung, bukan lewat debounce"
    timers = [c for c in calls if c[0] == "after"]
    assert len(timers) == 3 and all(ms == 300 for _, ms in timers)
    cancels = [c for c in calls if c[0] == "cancel"]
    assert len(cancels) == 2, "timer sebelumnya harus dibatalkan"


def test_search_var_connected_to_debounce_handler():
    """trace_add harus memanggil _on_search_changed, bukan refresh_tree."""
    src = (Path(__file__).resolve().parent.parent / "paste_teks" / "app.py").read_text()
    line = next(ln for ln in src.splitlines() if "trace_add" in ln)
    assert "_on_search_changed" in line, line
    assert "refresh_tree" not in line, line


def test_search_after_id_initialised():
    """_search_after_id harus dibuat sebelum handler dipakai."""
    src = (Path(__file__).resolve().parent.parent / "paste_teks" / "app.py").read_text()
    assert "_search_after_id: str | None = None" in src


# --- Hasil paste yang belum pasti ----------------------------------------

def test_paste_done_does_not_claim_success_when_not_confident():
    """xdotool di Wayland bisa lolos dengan kode 0 tanpa menempelkan apa pun."""
    from paste_teks.app import PasteTeksApp
    from paste_teks.clipboard import PasteResult

    calls = []

    class FakeApp:
        status_var = type("V", (), {"set": lambda self, v: calls.append(("status", v))})()
        _pasting = True

        def deiconify(self):
            calls.append(("deiconify", None))

        def lift(self):
            calls.append(("lift", None))

        def _show_paste_failure(self, message):
            calls.append(("dialog", message))

    result = PasteResult(True, "xdotool", "Paste otomatis terkirim via xdotool.",
                         confident=False)
    PasteTeksApp._paste_done(FakeApp(), result)
    status = next(v for k, v in calls if k == "status")
    assert "Ctrl+V" in status, "pengguna harus diberi langkah lanjutan"
    assert ("lift", None) in calls, "jendela harus terlihat agar pesan terbaca"


# --- Helper thread -------------------------------------------------------

def test_thread_helper_runs_target_with_args():
    from paste_teks.app import _run_in_thread

    seen = []
    import threading
    done = threading.Event()

    def work(a, b):
        seen.append((a, b))
        done.set()

    _run_in_thread(work, 1, 2)
    assert done.wait(5), "target tidak dijalankan"
    assert seen == [(1, 2)]
