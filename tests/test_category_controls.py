from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from pastex import app as app_module


def test_collapse_all_collapses_and_persists_nested_categories(gui, tmp_path):
    source = tmp_path / "source"
    nested = source / "General" / "Nested"
    nested.mkdir(parents=True)
    (nested / "Greeting.txt").write_text("Hello", encoding="utf-8")
    (source / "Email").mkdir()
    (source / "Email" / "Reply.txt").write_text("Thanks", encoding="utf-8")

    app = gui(roots=[source])
    app.refresh_tree()
    app.collapse_all_categories()

    expected = {
        app_module._norm_path(source / "General"),
        app_module._norm_path(nested),
        app_module._norm_path(source / "Email"),
    }
    assert app.collapsed == expected
    assert set(app.saved_settings[-1]["collapsed"]) == expected
    assert all(row["open"] is False for row in app.tree.rows if "dir" in row["tags"])
    assert app.status_var.get() == "All categories collapsed."


def test_collapse_all_in_search_still_remembers_hidden_categories(gui, tmp_path):
    source = tmp_path / "source"
    (source / "General").mkdir(parents=True)
    (source / "General" / "Greeting.txt").write_text("hello", encoding="utf-8")
    (source / "Email").mkdir()
    (source / "Email" / "Reply.txt").write_text("thanks", encoding="utf-8")

    app = gui(roots=[source])
    app.search_var.set("Greeting")
    app.refresh_tree()
    assert not any("Email" in row["text"] for row in app.tree.rows)

    app.collapse_all_categories()

    assert app_module._norm_path(source / "Email") in app.collapsed


def test_right_click_selects_row_and_uses_popup_menu(gui, sample_root):
    app = gui(roots=[sample_root])
    target = str(sample_root / "General" / "Greeting.txt")
    popup_calls = []
    releases = []

    class FakeMenu:
        def tk_popup(self, x, y):
            popup_calls.append((x, y))

        def grab_release(self):
            releases.append(True)

    app.tree.identify_row = lambda _y: target
    app.menu = FakeMenu()
    app.show_preview = lambda: None

    result = app._on_right_click(SimpleNamespace(y=3, x_root=120, y_root=240))

    assert result == "break"
    assert app.tree.selection() == (target,)
    assert app.tree.focus() == target
    assert popup_calls == [(120, 240)]
    assert releases == [True]


def test_right_click_menu_supports_common_button_bindings():
    source = Path(app_module.__file__).read_text(encoding="utf-8")
    assert 'self.tree.bind("<Button-3>", self._on_right_click)' in source
    assert 'self.tree.bind("<Button-2>", self._on_right_click)' in source
    assert 'self.tree.bind("<Control-Button-1>", self._on_right_click)' in source
