from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from pastex import app as app_module


class FakeMenu:
    def __init__(self):
        self.items = []
        self.popup_calls = []
        self.releases = 0

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
    assert "Select" in app.menu.labels
    assert "Edit" in app.menu.labels
    assert "Delete" in app.menu.labels
    assert "↑ Up" in app.menu.labels
    assert "↓ Down" in app.menu.labels
    assert "⚡ Paste into Another App" not in app.menu.labels
    app.menu.command("Select")()
    assert app.tree.selection() == (folder,)
    assert app.tree.focus() == folder
    assert app.menu.popup_calls == [(120, 240)]
    assert app.menu.releases == 1


def test_right_click_text_menu_has_no_paste_action(gui, sample_root):
    app = gui(roots=[sample_root])
    text = str(sample_root / "General" / "Greeting.txt")
    app.tree.identify_row = lambda _y: text
    app.menu = FakeMenu()
    app.show_preview = lambda: None

    app._on_right_click(SimpleNamespace(y=5, x_root=10, y_root=20))

    assert "Select" in app.menu.labels
    assert "⚡ Paste into Another App" not in app.menu.labels


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
    assert app.settings["order"][app_module._norm_path(source)] == ["Text.txt", "Folder"]

    app.refresh_tree()
    assert app.tree.get_children("") == (str(source / "Text.txt"), folder)


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
    assert app.settings["order"][app_module._norm_path(sample_root)] == ["Email", "Work"]
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
    monkeypatch.setattr(app_module.subprocess, "Popen", lambda command: calls.append(command))
    app = gui(roots=[sample_root], settings={"source_dir": str(sample_root)})

    app.open_source_folder()

    assert calls == [["xdg-open", str(sample_root)]]


def test_old_toolbar_controls_are_not_rendered_and_ppa_is_manual():
    source = Path(app_module.__file__).read_text(encoding="utf-8")
    workflow = (Path(app_module.__file__).resolve().parent.parent
                / ".github" / "workflows" / "publish-ppa.yml").read_text(encoding="utf-8")
    assert '"📂 Open Folder"' not in source
    assert '"✏️ Edit"' not in source
    assert '"🗑 Delete"' not in source
    assert '"⊟ Collapse All"' not in source
    assert "workflow_dispatch:" in workflow
    assert "push:" not in workflow
