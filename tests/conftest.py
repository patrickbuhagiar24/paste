"""Shared test tools: a fake GUI with no display.

The GUI paths are tested with stand-in objects (not real Tk) because CI has
no X or Wayland. What is tested is the *logic* of each method: the arguments
sent to the Treeview, the contents of settings.json, and the write targets.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pastex import app as app_module
from pastex.app import PastexApp


class FakeTree:
    """Fake Treeview: records every inserted row."""

    def __init__(self):
        self.rows: list[dict] = []
        self.deleted = 0
        self.inserts = 0
        self.moves = 0
        self.item_updates = 0
        # When True, options are read back as strings, like real Tcl/Tk ("1", "dir").
        self.string_options = False
        self._selection: tuple[str, ...] = ()
        self._focus = ""
        self._child_order: dict[str, list[str]] = {}
        self.packed = None

    # -- the API app.py uses --
    def delete(self, *items):
        """Delete the given row and its descendants, like Tk."""
        self.deleted += 1
        if not items:
            self.rows.clear()
            self._child_order.clear()
            self._selection = ()
            return
        doomed = set(items)
        changed = True
        while changed:  # also delete every descendant
            changed = False
            for row in self.rows:
                if row["parent"] in doomed and row["iid"] not in doomed:
                    doomed.add(row["iid"])
                    changed = True
        self.rows = [row for row in self.rows if row["iid"] not in doomed]
        for order in self._child_order.values():
            order[:] = [iid for iid in order if iid not in doomed]
        self._selection = tuple(iid for iid in self._selection if iid not in doomed)
        if self._focus in doomed:
            self._focus = ""

    def get_children(self, item=""):
        children = [r["iid"] for r in self.rows if r["parent"] == item]
        saved = self._child_order.get(item, [])
        rank = {iid: index for index, iid in enumerate(saved)}
        return tuple(sorted(children, key=lambda iid: rank.get(iid, len(rank))))

    def parent(self, iid):
        return next(r["parent"] for r in self.rows if r["iid"] == iid)

    def move(self, iid, parent, index):
        self.moves += 1
        children = list(self.get_children(parent))
        children.remove(iid)
        children.insert(int(index), iid)
        self._child_order[parent] = children

    def exists(self, iid):
        return any(r["iid"] == iid for r in self.rows)

    def insert(self, parent, index, iid=None, **kw):
        assert index == "end", f"wrong index: {index!r}"
        assert iid is not None, "iid must be a keyword"
        self.inserts += 1
        self.rows.append(
            {
                "parent": parent,
                "iid": iid,
                "text": kw.get("text"),
                "open": kw.get("open"),
                "tags": kw.get("tags"),
            }
        )
        return iid

    def selection(self):
        return self._selection

    def selection_set(self, *iids):
        self._selection = tuple(iids)

    def selection_add(self, *iids):
        self._selection = tuple(dict.fromkeys((*self._selection, *iids)))

    def selection_remove(self, *iids):
        removed = set(iids)
        self._selection = tuple(iid for iid in self._selection if iid not in removed)

    def focus(self, iid=None):
        if iid is not None:
            self._focus = iid
        return self._focus

    def item(self, iid, option=None, **kwargs):
        row = next(r for r in self.rows if r["iid"] == iid)
        if kwargs:
            self.item_updates += 1
        row.update(kwargs)
        if option is None:
            return row
        value = row.get(option)
        if self.string_options:
            if option == "open":
                return "1" if value else "0"
            if option == "tags":
                tags = value if isinstance(value, (tuple, list)) else (value,)
                return " ".join(tag for tag in tags if tag)
        return value

    def bind(self, *_a, **_kw):
        pass

    def pack(self, **kw):
        self.packed = dict(kw)

    def pack_forget(self):
        self.packed = "forgotten"

    # -- test helpers --
    def row(self, name_contains: str) -> dict:
        for row in self.rows:
            if row["text"] and name_contains in row["text"]:
                return row
        raise AssertionError(
            f"no row matching {name_contains!r}: {[r['text'] for r in self.rows]}"
        )

    def by_iid(self, iid) -> dict:
        for row in self.rows:
            if row["iid"] == str(iid):
                return row
        raise AssertionError(f"no iid {iid!r}")


class FakeWidget:
    """Minimal stand-in widget (frame/scrollbar).

    ``packed`` = None (never packed), "forgotten" (hidden),
    or a dict of pack options (currently shown).
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
    """Build a fake PastexApp (no __init__, no display)."""
    app = PastexApp.__new__(PastexApp)
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
    app._select_mode = False
    app._search_after_id = None
    app.current_file = None
    app.writable = app_module.writable_root(app.roots)
    return app


class FakeDialogs:
    """Stand-in for tkinter.filedialog/simpledialog (no Tk)."""

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


@pytest.fixture(autouse=True)
def _no_clipboard_owner_between_tests():
    """Do not let a clipboard owner leak from one test into another."""
    from pastex import clipboard

    clipboard.set_clipboard_owner(None)
    yield
    clipboard.set_clipboard_owner(None)


@pytest.fixture
def dialogs(monkeypatch):
    """Replace filedialog + simpledialog + messagebox with stand-ins."""
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
    """A source folder with two categories and two .txt files."""
    root = tmp_path / "source"
    (root / "General").mkdir(parents=True)
    (root / "Email").mkdir(parents=True)
    (root / "General" / "Greeting.txt").write_text("hello", encoding="utf-8")
    (root / "Email" / "Reply.txt").write_text("thank you", encoding="utf-8")
    return root
