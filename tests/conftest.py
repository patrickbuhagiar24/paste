"""Perkakas uji bersama: GUI tiruan tanpa display.

Baris-baris GUI diuji dengan objek tiruan (bukan Tk sungguhan) karena CI
tidak punya X/Wayland. Yang diuji adalah *logika* metode: argumen yang
dikirim ke Treeview, isi settings.json, dan folder tujuan penulisan.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paste_teks import app as app_module  # noqa: E402
from paste_teks.app import PasteTeksApp  # noqa: E402


class FakeTree:
    """Treeview tiruan: merekam setiap baris yang disisipkan."""

    def __init__(self):
        self.rows: list[dict] = []
        self.deleted = 0
        self._selection: tuple[str, ...] = ()
        self._focus = ""
        self.packed = None

    # -- API yang dipakai app.py --
    def delete(self, *items):
        self.deleted += 1
        self.rows.clear()

    def get_children(self, item=""):
        return tuple(r["iid"] for r in self.rows if r["parent"] == item)

    def exists(self, iid):
        return any(r["iid"] == iid for r in self.rows)

    def insert(self, parent, index, iid=None, **kw):
        assert index == "end", f"index salah: {index!r}"
        assert iid is not None, "iid harus keyword"
        self.rows.append({"parent": parent, "iid": iid, "text": kw.get("text"),
                          "open": kw.get("open"), "tags": kw.get("tags")})
        return iid

    def selection(self):
        return self._selection

    def selection_set(self, iid):
        self._selection = (iid,)

    def focus(self):
        return self._focus

    def bind(self, *_a, **_kw):
        pass

    def pack(self, **kw):
        self.packed = dict(kw)

    def pack_forget(self):
        self.packed = "forgotten"

    # -- pembantu uji --
    def row(self, name_contains: str) -> dict:
        for row in self.rows:
            if row["text"] and name_contains in row["text"]:
                return row
        raise AssertionError(f"baris {name_contains!r} tidak ada: "
                             f"{[r['text'] for r in self.rows]}")

    def by_iid(self, iid) -> dict:
        for row in self.rows:
            if row["iid"] == str(iid):
                return row
        raise AssertionError(f"iid {iid!r} tidak ada")


class FakeWidget:
    """Widget tiruan minimal (frame/scrollbar).

    ``packed`` = None (belum pernah di-pack), "forgotten" (disembunyikan),
    atau dict opsi pack (sedang tampil).
    """

    def __init__(self):
        self.packed = None

    def pack(self, **kw):
        self.packed = dict(kw)

    def pack_forget(self):
        self.packed = "forgotten"


class FakeVar:
    def __init__(self, value=""):
        self.value = value
        self.set_calls: list[str] = []

    def get(self):
        return self.value

    def set(self, value):
        self.value = value
        self.set_calls.append(value)


def make_app(roots=None, settings=None, *, transient_roots=False):
    """Buat PasteTeksApp tiruan (tanpa __init__, tanpa display)."""
    app = PasteTeksApp.__new__(PasteTeksApp)
    app.tree = FakeTree()
    app.tree_area = FakeWidget()
    app.empty_frame = FakeWidget()
    app.scroll = FakeWidget()
    app.search_var = FakeVar("")
    app.status_var = FakeVar("Siap.")
    app.source_var = FakeVar("")
    app.info_var = FakeVar("")
    app.roots = [Path(r) for r in (roots or [])]
    app.transient_roots = transient_roots
    base = dict(app_module.DEFAULT_SETTINGS)
    base.update(settings or {})
    app.settings = base
    app.collapsed = set()
    app.current_file = None
    app.writable = app_module.writable_root(app.roots)
    return app


class FakeDialogs:
    """Pengganti tkinter.filedialog/simpledialog (tanpa Tk)."""

    def __init__(self, **answers):
        self.answers = answers
        self.asked: list[tuple] = []

    def askdirectory(self, **kw):
        self.asked.append(("askdirectory", kw))
        return self.answers.get("directory", "")

    def askstring(self, *a, **kw):
        self.asked.append(("askstring", a, kw))
        return self.answers.get("string")

    def askyesno(self, *a, **kw):
        self.asked.append(("askyesno", a, kw))
        return self.answers.get("yes", True)

    def showerror(self, title, text, **kw):
        self.asked.append(("error", title, text))

    def showinfo(self, title, text, **kw):
        self.asked.append(("info", title, text))

    def showwarning(self, title, text, **kw):
        self.asked.append(("warning", title, text))


@pytest.fixture
def dialogs(monkeypatch):
    """Ganti filedialog + simpledialog + messagebox dengan tiruan."""
    box = FakeDialogs()
    monkeypatch.setattr(app_module, "filedialog", box)
    monkeypatch.setattr(app_module, "simpledialog", box)
    monkeypatch.setattr(app_module, "messagebox", box)
    return box


@pytest.fixture
def gui(monkeypatch):
    """Factory `gui(roots=..., settings=...)` + perekam save_settings()."""
    saved: list[dict] = []

    def _save(settings):
        saved.append(dict(settings))

    monkeypatch.setattr(app_module, "save_settings", _save)

    def factory(roots=None, settings=None, *, transient_roots=False):
        app = make_app(roots, settings, transient_roots=transient_roots)
        app.saved_settings = saved
        return app

    factory.saved = saved
    return factory


@pytest.fixture
def sample_root(tmp_path):
    """Folder sumber berisi dua kategori + dua berkas .txt."""
    root = tmp_path / "sumber"
    (root / "Umum").mkdir(parents=True)
    (root / "Email").mkdir(parents=True)
    (root / "Umum" / "Sapaan.txt").write_text("halo", encoding="utf-8")
    (root / "Email" / "Balas.txt").write_text("terima kasih", encoding="utf-8")
    return root
