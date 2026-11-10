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


def test_paste_foreground_uses_selected_snippet(tmp_path, monkeypatch, capsys):
    """Pintasan global: salin teks terpilih lalu kirim Ctrl+V."""
    from paste_teks import app, clipboard

    snippet = _snippet(tmp_path)
    monkeypatch.setattr(app, "SETTINGS_DIR", tmp_path / "cfg")
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "last-selection.txt").write_text(str(snippet))

    copied = {}

    def fake_copy(text, runner=None):
        copied["text"] = text
        return clipboard.ClipboardResult(ok=True, tool="wl-copy", verified=True,
                                         message="Teks disalin dengan wl-copy.")

    monkeypatch.setattr(clipboard, "copy_text", fake_copy)
    monkeypatch.setattr(clipboard, "read_text_from_clipboard",
                        lambda runner=None: snippet.read_text())
    monkeypatch.setattr(clipboard, "simulate_paste",
                        lambda: clipboard.PasteResult(True, "ydotool", "ok"))

    code = app.run_paste_foreground([tmp_path / "Snippets"])
    assert code == 0
    assert copied["text"] == "Halo"
    assert "ok" in capsys.readouterr().out


def test_paste_foreground_without_selection(tmp_path, monkeypatch, capsys):
    from paste_teks import app

    monkeypatch.setattr(app, "SETTINGS_DIR", tmp_path / "cfg")
    (tmp_path / "cfg").mkdir()
    # dua berkas -> tidak bisa menebak
    d = tmp_path / "Snippets"
    d.mkdir()
    (d / "a.txt").write_text("a")
    (d / "b.txt").write_text("b")
    assert app.run_paste_foreground([d]) == 4
    assert "Tidak ada teks terpilih" in capsys.readouterr().err


def test_paste_foreground_single_file_fallback(tmp_path, monkeypatch):
    from paste_teks import app, clipboard

    d = tmp_path / "Snippets"
    d.mkdir()
    (d / "satu.txt").write_text("satu-satunya")
    monkeypatch.setattr(app, "SETTINGS_DIR", tmp_path / "cfg")
    (tmp_path / "cfg").mkdir()
    got = {}
    monkeypatch.setattr(clipboard, "copy_text",
                        lambda text, runner=None: got.update(text=text) or
                        clipboard.ClipboardResult(True, "wl-copy", "ok", True))
    monkeypatch.setattr(clipboard, "read_text_from_clipboard", lambda runner=None: None)
    monkeypatch.setattr(clipboard, "simulate_paste",
                        lambda: clipboard.PasteResult(True, "ydotool", "ok"))
    assert app.run_paste_foreground([d]) == 0
    assert got["text"] == "satu-satunya"


def test_paste_foreground_reports_ydotool_failure(tmp_path, monkeypatch, capsys):
    from paste_teks import app, clipboard

    snippet = _snippet(tmp_path)
    monkeypatch.setattr(app, "SETTINGS_DIR", tmp_path / "cfg")
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "last-selection.txt").write_text(str(snippet))
    monkeypatch.setattr(clipboard, "copy_text",
                        lambda text, runner=None: clipboard.ClipboardResult(
                            True, "wl-copy", "ok", True))
    monkeypatch.setattr(clipboard, "read_text_from_clipboard", lambda runner=None: "Halo")
    monkeypatch.setattr(clipboard, "simulate_paste",
                        lambda: clipboard.PasteResult(False, "", "gagal"))
    assert app.run_paste_foreground([tmp_path / "Snippets"]) == 2
    assert "gagal" in capsys.readouterr().err


def test_install_shortcut_flags(monkeypatch, capsys):
    from paste_teks import app, shortcut
    monkeypatch.setattr(shortcut, "install", lambda binding=None: (True, "dipasang"))
    assert app.run_cli(["--install-shortcut"]) == 0
    assert "dipasang" in capsys.readouterr().out
    monkeypatch.setattr(shortcut, "remove", lambda: (True, "dihapus"))
    assert app.run_cli(["--remove-shortcut"]) == 0
    assert "dihapus" in capsys.readouterr().out
