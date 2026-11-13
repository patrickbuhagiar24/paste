from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from pastex import app as app_module


class FakeMenu:
    def __init__(self):
        self.items = []
        self.popup_calls = []
        self.releases = 0
        self.unposts = 0

    def delete(self, *_args):
        self.items.clear()

    def add_command(self, *, label, command, **_kwargs):
        self.items.append((label, command))

    def add_separator(self):
        self.items.append(("---", None))

    def tk_popup(self, x, y):
        self.popup_calls.append((x, y))

    def grab_release(self):
        self.releases += 1

    def unpost(self):
        self.unposts += 1

    @property
    def labels(self):
        return [label for label, _command in self.items]

    def command(self, label):
        return next(command for name, command in self.items if name == label)


def test_right_click_folder_menu_selects_without_paste(gui, sample_root):
    app = gui(roots=[sample_root])
    app.refresh_tree()
    folder = str(sample_root / "General")
    app.tree.identify_row = lambda _y: folder
    app.menu = FakeMenu()
    app.show_preview = lambda: None

    result = app._on_right_click(SimpleNamespace(y=3, x_root=120, y_root=240))

    assert result == "break"
    assert app.tree.selection() == ()
    assert app._context_iid == folder
    assert app.menu.labels[:3] == ["Select", "Edit", "Delete"]
    assert "↑ Up" in app.menu.labels
    assert "↓ Down" in app.menu.labels
    assert "Collapse All" in app.menu.labels
    assert "⚡ Paste into Another App" not in app.menu.labels
    app.menu.command("Select")()
    assert app.tree.selection() == (folder,)
    assert app.tree.focus() == folder
    assert app._select_mode is True
    assert app.menu.popup_calls == [(120, 240)]
    assert app.menu.releases == 1


def test_select_context_item_adds_to_existing_selection(gui, sample_root):
    app = gui(roots=[sample_root])
    app.refresh_tree()
    folder = str(sample_root / "General")
    text = str(sample_root / "Email" / "Balas.txt")
    app.tree.selection_set(folder)
    app._context_iid = text
    app.show_preview = lambda: None

    app.select_context_item()

    assert app.tree.selection() == (folder, text)
    assert app._select_mode is True


def test_right_click_in_select_mode_shows_only_bulk_actions(gui, sample_root):
    app = gui(roots=[sample_root])
    app.refresh_tree()
    folder = str(sample_root / "General")
    text = str(sample_root / "Email" / "Balas.txt")
    app._select_mode = True
    app.tree.selection_set(folder, text)
    app.tree.identify_row = lambda _y: folder
    app.menu = FakeMenu()

    app._on_right_click(SimpleNamespace(y=3, x_root=20, y_root=30))

    assert app.menu.labels == ["Delete", "↑ Up", "↓ Down"]


def test_click_outside_tree_exits_select_mode(gui):
    app = gui()
    app._select_mode = True
    app.menu = FakeMenu()
    app._exit_select_mode_outside_tree(SimpleNamespace(widget=object()))

    assert app._select_mode is False
    assert app.status_var.get() == "Selection mode ended."


def test_context_menu_dismisses_on_left_or_right_click_outside(gui):
    app = gui()
    app.menu = FakeMenu()
    app._context_menu_active = True
    app.winfo_containing = lambda _x, _y: "outside"

    app._dismiss_context_menu_outside(
        SimpleNamespace(x_root=400, y_root=300, widget=None, num=1)
    )
    assert app.menu.unposts == 1
    assert app._context_menu_active is False

    app._context_menu_active = True
    app._dismiss_context_menu_outside(
        SimpleNamespace(x_root=400, y_root=300, widget=None, num=3)
    )
    assert app.menu.unposts == 2
    assert app._context_menu_active is False


def test_click_inside_context_menu_keeps_it_posted(gui):
    app = gui()
    app.menu = FakeMenu()
    app._context_menu_active = True
    app.winfo_containing = lambda _x, _y: app.menu

    app._dismiss_context_menu_outside(
        SimpleNamespace(x_root=10, y_root=10, widget=app.menu)
    )

    assert app.menu.unposts == 0
    assert app._context_menu_active is True


def test_search_has_no_separate_search_button(gui):
    source = Path(app_module.__file__).read_text(encoding="utf-8")
    assert "self.search_button" not in source
    assert "self.search_entry.trace_add" not in source
    assert "self.search_var.trace_add" in source


def test_right_click_blank_dismisses_without_posting_menu(gui, sample_root):
    app = gui(roots=[sample_root])
    app._select_mode = True
    app.tree.identify_row = lambda _y: ""
    app.menu = FakeMenu()

    assert app._on_right_click(SimpleNamespace(y=300, x_root=20, y_root=30)) == "break"
    assert app.menu.popup_calls == []
    assert app._select_mode is False


def test_right_click_text_menu_has_no_paste_action(gui, sample_root):
    app = gui(roots=[sample_root])
    text = str(sample_root / "General" / "Greeting.txt")
    app.tree.identify_row = lambda _y: text
    app.menu = FakeMenu()
    app.show_preview = lambda: None

    app._on_right_click(SimpleNamespace(y=5, x_root=10, y_root=20))

    assert "Select" in app.menu.labels
    assert "⚡ Paste into Another App" not in app.menu.labels


def test_collapse_all_is_persisted_and_closes_every_folder(gui, sample_root):
    nested = sample_root / "General" / "Nested"
    nested.mkdir()
    (nested / "Inside.txt").write_text("inside", encoding="utf-8")
    app = gui(roots=[sample_root])
    app.refresh_tree()

    app.collapse_all()

    assert app.tree.by_iid(str(sample_root / "General"))["open"] is False
    assert app.tree.by_iid(str(nested))["open"] is False
    assert set(app.settings["collapsed"]) == {
        app_module._norm_path(sample_root / "General"),
        app_module._norm_path(sample_root / "Email"),
        app_module._norm_path(nested),
    }


def test_search_button_runs_query_immediately(gui):
    app = gui()
    app.search_var.set("greeting")
    app._search_after_id = "pending-search"
    canceled = []
    searches = []
    app.after_cancel = canceled.append
    app.refresh_tree = lambda: searches.append(app.search_var.get())

    assert app.search_now() == "break"

    assert canceled == ["pending-search"]
    assert searches == ["greeting"]
    assert app._search_after_id is None


def test_clear_search_button_clears_query_and_refocuses(gui):
    app = gui()
    app.search_var.set("greeting")
    app._search_after_id = None
    focused = []
    searches = []
    app.search_entry = SimpleNamespace(focus_set=lambda: focused.append(True))
    app.refresh_tree = lambda: searches.append(app.search_var.get())

    app.clear_search()

    assert app.search_var.get() == ""
    assert searches == [""]
    assert focused == [True]


def test_move_up_down_persists_mixed_folder_and_text_order(gui, tmp_path):
    source = tmp_path / "source"
    (source / "Folder").mkdir(parents=True)
    (source / "Folder" / "Inside.txt").write_text("inside", encoding="utf-8")
    (source / "Text.txt").write_text("text", encoding="utf-8")
    app = gui(roots=[source])
    app.refresh_tree()

    folder = str(source / "Folder")
    app.tree.selection_set(folder)
    app.move_selected(1)

    assert app.tree.get_children("") == (str(source / "Text.txt"), folder)
    assert app.settings["order"][app_module._norm_path(source)] == [
        "Text.txt",
        "Folder",
    ]

    app.refresh_tree()
    assert app.tree.get_children("") == (str(source / "Text.txt"), folder)


def test_move_multiple_siblings_keeps_their_order_and_persists(gui, tmp_path):
    source = tmp_path / "source"
    for name in ("Alpha", "Beta"):
        (source / name).mkdir(parents=True)
    (source / "Delta.txt").write_text("delta", encoding="utf-8")
    (source / "Gamma.txt").write_text("gamma", encoding="utf-8")
    app = gui(roots=[source])
    app.refresh_tree()

    alpha = str(source / "Alpha")
    delta = str(source / "Delta.txt")
    app.tree.selection_set(alpha)
    app.tree.selection_add(delta)
    app.move_selected(1)

    expected = (str(source / "Beta"), alpha, str(source / "Gamma.txt"), delta)
    assert app.tree.get_children("") == expected
    assert app.settings["order"][app_module._norm_path(source)] == [
        "Beta",
        "Alpha",
        "Gamma.txt",
        "Delta.txt",
    ]


def test_move_selection_from_different_folders_moves_each_group(gui, sample_root):
    app = gui(roots=[sample_root])
    extra = sample_root / "Email" / "Zulu.txt"
    extra.write_text("extra", encoding="utf-8")
    app.refresh_tree()
    folder = str(sample_root / "Email")
    text = str(sample_root / "Email" / "Balas.txt")
    app.tree.selection_set(folder)
    app.tree.selection_add(text)

    app.move_selected(1)

    assert app.tree.get_children("") == (str(sample_root / "General"), folder)
    assert app.tree.get_children(folder) == (str(extra), text)
    assert app.settings["order"][app_module._norm_path(sample_root)] == [
        "General",
        "Email",
    ]
    assert app.settings["order"][app_module._norm_path(folder)] == [
        "Zulu.txt",
        "Balas.txt",
    ]


def test_delete_multiple_files_and_folders_once(gui, dialogs, tmp_path, monkeypatch):
    source = tmp_path / "source"
    folder = source / "Folder"
    folder.mkdir(parents=True)
    nested = folder / "Nested.txt"
    nested.write_text("nested", encoding="utf-8")
    text = source / "Loose.txt"
    text.write_text("loose", encoding="utf-8")
    app = gui(roots=[source])
    app.refresh_tree()
    folder_iid, text_iid = str(folder), str(text)
    app.tree.selection_set(folder_iid)
    app.tree.selection_add(text_iid)
    confirmations = []
    monkeypatch.setattr(
        app_module.messagebox,
        "askyesno",
        lambda title, prompt: confirmations.append((title, prompt)) or True,
    )

    app.delete_selected()

    assert not folder.exists()
    assert not nested.exists()
    assert not text.exists()
    assert len(confirmations) == 1
    assert "2 selected items" in confirmations[0][1]
    assert app.tree.selection() == ()


def test_move_is_disabled_while_searching(gui, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    first = source / "First.txt"
    second = source / "Second.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    app = gui(roots=[source])
    app.refresh_tree()
    app.search_var.set("First")
    app.refresh_tree()
    app.tree.selection_set(str(first))

    app.move_selected(1)

    assert app.status_var.get() == "Clear the search before changing item order."
    assert app.settings["order"] == {}


def test_edit_on_folder_renames_and_remaps_saved_paths(gui, dialogs, sample_root):
    old = sample_root / "General"
    nested = old / "Nested"
    nested.mkdir()
    (nested / "Note.txt").write_text("note", encoding="utf-8")
    settings = {
        "source_dir": app_module._norm_path(sample_root),
        "collapsed": [app_module._norm_path(old), app_module._norm_path(nested)],
        "order": {
            app_module._norm_path(sample_root): ["Email", "General"],
            app_module._norm_path(old): ["Nested"],
        },
    }
    app = gui(roots=[sample_root], settings=settings)
    app.refresh_tree()
    app.show_preview = lambda: None
    app.tree.selection_set(str(old))
    dialogs.answers["string"] = "Work"

    app.edit_selected()

    renamed = sample_root / "Work"
    assert renamed.is_dir()
    assert not old.exists()
    assert app_module._norm_path(renamed) in app.settings["collapsed"]
    assert app_module._norm_path(renamed / "Nested") in app.settings["collapsed"]
    assert app.settings["order"][app_module._norm_path(sample_root)] == [
        "Email",
        "Work",
    ]
    assert app_module._norm_path(renamed) in app.settings["order"]
    assert app.tree.selection() == (str(renamed),)


def test_folder_rename_rejects_existing_sibling(tmp_path):
    import pytest

    from pastex.snippets import rename_folder

    source = tmp_path / "source"
    folder = source / "One"
    folder.mkdir(parents=True)
    (source / "Two").mkdir()

    with pytest.raises(FileExistsError):
        rename_folder(folder, "Two")


def test_clickable_source_label_opens_chosen_source(gui, sample_root, monkeypatch):
    calls = []
    monkeypatch.setattr(
        app_module.subprocess, "Popen", lambda command: calls.append(command)
    )
    app = gui(roots=[sample_root], settings={"source_dir": str(sample_root)})

    app.open_source_folder()

    assert calls == [["xdg-open", str(sample_root)]]


def test_old_toolbar_controls_are_not_rendered_and_ppa_is_manual():
    source = Path(app_module.__file__).read_text(encoding="utf-8")
    workflow = (
        Path(app_module.__file__).resolve().parent.parent
        / ".github"
        / "workflows"
        / "publish-ppa.yml"
    ).read_text(encoding="utf-8")
    assert '"📂 Open Folder"' not in source
    assert '"✏️ Edit"' not in source
    assert '"🗑 Delete"' not in source
    assert '"⊟ Collapse All"' not in source
    assert "workflow_dispatch:" in workflow
    assert "push:" not in workflow
