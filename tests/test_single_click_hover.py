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

    def selection(self):
        return self.selected

    def selection_set(self, *iids):
        self.selected = tuple(iids)

    def selection_add(self, *iids):
        self.selected = tuple(dict.fromkeys((*self.selected, *iids)))

    def selection_remove(self, *iids):
        removed = set(iids)
        self.selected = tuple(iid for iid in self.selected if iid not in removed)

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


def test_single_click_selects_pastes_and_clears_hover(tmp_path):
    snippet = tmp_path / "snippet.txt"
    snippet.write_text("hello", encoding="utf-8")
    app = PastexApp.__new__(PastexApp)
    app.tree = FakeTree(str(snippet), tags=("hover",))
    app._hover_iid = str(snippet)
    app._select_mode = False
    calls = []
    app.do_paste = lambda: calls.append("paste")

    app._on_single_click(SimpleNamespace(y=4))

    assert app.tree.selected == (str(snippet),)
    assert app.tree.focused == str(snippet)
    assert "hover" not in app.tree.tags
    assert app._hover_iid == ""
    assert calls == ["paste"]


def test_click_outside_tree_clears_hover(tmp_path):
    row = str(tmp_path / "snippet.txt")
    app = PastexApp.__new__(PastexApp)
    app.tree = FakeTree(row, tags=("hover",))
    app._hover_iid = row
    app._select_mode = False

    app._exit_select_mode_outside_tree(SimpleNamespace(widget=object()))

    assert app.tree.tags == ()
    assert app._hover_iid == ""


def test_select_mode_left_click_adds_and_unselects_without_pasting(tmp_path):
    snippet = tmp_path / "snippet.txt"
    snippet.write_text("hello", encoding="utf-8")
    app = PastexApp.__new__(PastexApp)
    app.tree = FakeTree(str(snippet))
    app._select_mode = True
    app.status_var = SimpleNamespace(set=lambda _value: None)
    app.show_preview = lambda: None
    calls = []
    app.do_paste = lambda: calls.append("paste")

    app._on_single_click(SimpleNamespace(y=4))
    assert app.tree.selected == (str(snippet),)
    app._on_single_click(SimpleNamespace(y=4))

    assert app.tree.selected == ()
    assert app.tree.focused == str(snippet)
    assert calls == []


def test_click_on_blank_tree_area_exits_select_mode(tmp_path):
    app = PastexApp.__new__(PastexApp)
    app.tree = FakeTree("")
    app._select_mode = True
    statuses = []
    app.status_var = SimpleNamespace(set=statuses.append)

    app._on_single_click(SimpleNamespace(y=1000))

    assert app._select_mode is False
    assert statuses == ["Selection mode ended."]


def test_rapid_repeat_while_pasting_is_ignored(tmp_path):
    snippet = tmp_path / "snippet.txt"
    snippet.write_text("hello", encoding="utf-8")
    app = PastexApp.__new__(PastexApp)
    app.tree = FakeTree(str(snippet))
    app._select_mode = False
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
    app._select_mode = False
    calls = []
    app.do_paste = lambda: calls.append("paste")

    app._on_single_click(SimpleNamespace(y=4))

    assert app.tree.selected == (str(folder),)
    assert calls == []
