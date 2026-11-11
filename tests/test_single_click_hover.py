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


def test_single_click_selects_and_schedules_text_paste(tmp_path):
    snippet = tmp_path / "snippet.txt"
    snippet.write_text("hello", encoding="utf-8")
    app = PastexApp.__new__(PastexApp)
    app.tree = FakeTree(str(snippet))
    scheduled = []
    app._click_after_id = None
    app.after = lambda delay, callback: scheduled.append((delay, callback)) or "timer-1"
    app.after_cancel = lambda _timer_id: None

    app._on_single_click(SimpleNamespace(y=4))

    assert app.tree.selected == (str(snippet),)
    assert app.tree.focused == str(snippet)
    assert len(scheduled) == 1
    assert scheduled[0][0] == 250
    assert scheduled[0][1].__self__ is app
    assert scheduled[0][1].__func__ is PastexApp._activate_single_click


def test_double_click_is_debounced_to_one_paste(tmp_path):
    snippet = tmp_path / "snippet.txt"
    snippet.write_text("hello", encoding="utf-8")
    app = PastexApp.__new__(PastexApp)
    app.tree = FakeTree(str(snippet))
    scheduled = []
    cancelled = []
    app._click_after_id = None
    app.after = lambda delay, callback: scheduled.append((delay, callback)) or f"timer-{len(scheduled)}"
    app.after_cancel = cancelled.append

    app._on_single_click(SimpleNamespace(y=4))
    app._on_single_click(SimpleNamespace(y=4))

    assert cancelled == ["timer-1"]
    assert len(scheduled) == 2
    assert app._click_after_id == "timer-2"


def test_single_click_on_folder_does_not_schedule_paste(tmp_path):
    folder = tmp_path / "category"
    folder.mkdir()
    app = PastexApp.__new__(PastexApp)
    app.tree = FakeTree(str(folder))
    scheduled = []
    app._click_after_id = None
    app.after = lambda delay, callback: scheduled.append((delay, callback))

    app._on_single_click(SimpleNamespace(y=4))

    assert app.tree.selected == (str(folder),)
    assert scheduled == []
