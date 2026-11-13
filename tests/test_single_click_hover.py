from __future__ import annotations

from types import SimpleNamespace

from pastex.app import PastexApp


class FakeTree:
    def __init__(self, row: str, tags=()):
        self.row_id = row
        self.tags = tuple(tags)
        self.selected = ()
        self.focused = ""

    def identify_row(self, _y):
        return self.row_id

    def exists(self, iid):
        return iid == self.row_id

    def item(self, iid, option=None, **kwargs):
        assert iid == self.row_id
        if "tags" in kwargs:
            self.tags = tuple(kwargs["tags"])
        if option == "tags":
            return self.tags
        return {"tags": self.tags}

    def selection_set(self, iid):
        self.selected = (iid,)

    def focus(self, iid):
        self.focused = iid


def test_hover_adds_and_removes_highlight_tag(tmp_path):
    row = str(tmp_path / "snippet.txt")
    app = PastexApp.__new__(PastexApp)
    app.tree = FakeTree(row, tags=("file",))
    app._hover_iid = ""

    app._on_tree_motion(SimpleNamespace(y=10))
    assert app.tree.tags == ("file", "hover")

    app._clear_tree_hover()
    assert app.tree.tags == ("file",)
    assert app._hover_iid == ""


def test_single_click_selects_and_pastes_immediately(tmp_path):
    snippet = tmp_path / "snippet.txt"
    snippet.write_text("hello", encoding="utf-8")
    app = PastexApp.__new__(PastexApp)
    app.tree = FakeTree(str(snippet))
    calls = []
    app.do_paste = lambda: calls.append("paste")

    app._on_single_click(SimpleNamespace(y=4))

    assert app.tree.selected == (str(snippet),)
    assert app.tree.focused == str(snippet)
    assert calls == ["paste"]


def test_modifier_click_preserves_multiselection_without_pasting(tmp_path):
    snippet = tmp_path / "snippet.txt"
    snippet.write_text("hello", encoding="utf-8")
    app = PastexApp.__new__(PastexApp)
    app.tree = FakeTree(str(snippet))
    calls = []
    app.do_paste = lambda: calls.append("paste")

    app._on_single_click(SimpleNamespace(y=4, state=0x0004))

    assert app.tree.selected == ()
    assert app.tree.focused == str(snippet)
    assert calls == []


def test_rapid_repeat_while_pasting_is_ignored(tmp_path):
    snippet = tmp_path / "snippet.txt"
    snippet.write_text("hello", encoding="utf-8")
    app = PastexApp.__new__(PastexApp)
    app.tree = FakeTree(str(snippet))
    app._pasting = False
    calls = []

    def guarded_paste():
        if app._pasting:
            return
        app._pasting = True
        calls.append("paste")

    app.do_paste = guarded_paste
    app._on_single_click(SimpleNamespace(y=4))
    app._on_single_click(SimpleNamespace(y=4))

    assert calls == ["paste"]


def test_single_click_on_folder_does_not_paste(tmp_path):
    folder = tmp_path / "folder"
    folder.mkdir()
    app = PastexApp.__new__(PastexApp)
    app.tree = FakeTree(str(folder))
    calls = []
    app.do_paste = lambda: calls.append("paste")

    app._on_single_click(SimpleNamespace(y=4))

    assert app.tree.selected == (str(folder),)
    assert calls == []
