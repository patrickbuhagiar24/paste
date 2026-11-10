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


def test_paste_flow_withdraws_then_pastes():
    """Inti perbaikan: jendela DILEPAS (withdraw) sebelum Ctrl+V dikirim.

    `lower()` tidak memindahkan fokus (jendela kita tetap fokus) dan
    `iconify()` diabaikan sebagian compositor — hanya withdraw yang
    benar-benar menyerahkan fokus ke aplikasi tujuan.
    """
    from paste_teks.app import PasteTeksApp

    calls = []

    class FakeApp:
        settings = {"focus_delay": 0.6}
        status_var = type("V", (), {"set": lambda self, v: calls.append(("status", v))})()

        def withdraw(self):
            calls.append(("withdraw", None))

        def deiconify(self):
            calls.append(("deiconify", None))

        def lift(self):
            calls.append(("lift", None))

        def after(self, ms, fn=None):
            calls.append(("after", ms))
            if fn is not None:
                fn()

        def lower(self):
            calls.append(("lower", None))

        def _send_paste_after_release(self, text):
            calls.append(("send", text))

    app = FakeApp()
    PasteTeksApp._release_and_paste(app, "teks")
    assert ("withdraw", None) in calls
    assert ("lower", None) not in calls       # lower tidak memindahkan fokus
    after = [c for c in calls if c[0] == "after"]
    assert after and after[0][1] == 600        # 0,6 detik


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
