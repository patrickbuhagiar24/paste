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
