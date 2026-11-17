"""v1.5.0 #3: collapsed categories are stored and restored.

Bug guarded here: the GUI opened every category on each refresh_tree
(open=True), so a category the user had just collapsed expanded again — and
the state was lost every time the application was closed.
"""

from __future__ import annotations

import json
from pathlib import Path

from pastex import app as app_module


def _norm(path):
    return str(path.resolve())


def test_collapsed_default_is_empty_list():
    assert app_module.DEFAULT_SETTINGS["collapsed"] == []


def test_load_settings_normalises_broken_collapsed(tmp_path, monkeypatch):
    target = tmp_path / "settings.json"
    monkeypatch.setattr(app_module, "SETTINGS_FILE", target)
    for broken in ('"bukan list"', "42", "null"):
        target.write_text(f'{{"collapsed": {broken}}}', encoding="utf-8")
        assert app_module.load_settings()["collapsed"] == []


def test_load_settings_keeps_saved_collapsed(tmp_path, monkeypatch):
    target = tmp_path / "settings.json"
    monkeypatch.setattr(app_module, "SETTINGS_FILE", target)
    target.write_text(json.dumps({"collapsed": ["/a/b", "/c"]}), encoding="utf-8")
    assert app_module.load_settings()["collapsed"] == ["/a/b", "/c"]


def test_open_treeview_event_marks_folder_collapsed(gui, sample_root):
    """Collapsing a category must be written to settings.json."""
    app = gui(roots=[sample_root])
    app.refresh_tree()
    assert app.tree.row("General")["open"] is True

    app._toggle_collapsed(str(sample_root / "General"))

    assert app.saved_settings, "settings.json was not written"
    assert app.saved_settings[-1]["collapsed"] == [_norm(sample_root / "General")]
    # .txt files are not recorded even if a stray event arrives
    app._toggle_collapsed(str(sample_root / "General" / "Greeting.txt"))
    assert app.saved_settings[-1]["collapsed"] == [_norm(sample_root / "General")]


def test_refresh_tree_restores_collapsed_state(gui, sample_root):
    """refresh_tree (F5) must honour the collapsed state instead of opening everything."""
    saved = _norm(sample_root / "Email")
    app = gui(roots=[sample_root], settings={"collapsed": [saved]})
    app.refresh_tree()
    assert app.tree.row("General")["open"] is True
    assert app.tree.row("Email")["open"] is False


def test_collapsed_survives_new_session(sample_root, tmp_path, monkeypatch):
    """A new session (settings.json read again) restores collapsed categories."""
    from conftest import make_app

    config_dir = tmp_path / "config"
    monkeypatch.setattr(app_module, "SETTINGS_DIR", config_dir)
    monkeypatch.setattr(app_module, "SETTINGS_FILE", config_dir / "settings.json")

    first = make_app(roots=[sample_root], settings=app_module.load_settings())
    first.refresh_tree()
    first._toggle_collapsed(str(sample_root / "Email"))
    assert (config_dir / "settings.json").exists(), "settings were not written"

    # Second session: load from disk, not from the old object.
    second = make_app(roots=[sample_root], settings=app_module.load_settings())
    second.refresh_tree()
    assert second.tree.row("Email")["open"] is False
    assert second.tree.row("General")["open"] is True


def test_opening_again_removes_from_collapsed(gui, sample_root):
    app = gui(
        roots=[sample_root], settings={"collapsed": [_norm(sample_root / "Email")]}
    )
    app.refresh_tree()
    app._toggle_collapsed(str(sample_root / "Email"))  # opened again
    assert app.saved_settings[-1]["collapsed"] == []
    app.refresh_tree()
    assert app.tree.row("Email")["open"] is True


def test_search_expands_everything_and_does_not_overwrite_state(gui, sample_root):
    """Search opens everything, but the stored collapsed state is not dropped."""
    saved = _norm(sample_root / "Email")
    app = gui(roots=[sample_root], settings={"collapsed": [saved]})
    app.search_var.set("Greeting")
    app.refresh_tree()
    dirs = [r for r in app.tree.rows if r["tags"] == "dir"]
    assert dirs, "the search found no categories"
    assert all(r["open"] is True for r in dirs), "categories must be open while searching"
    assert app.settings["collapsed"] == [saved], "the collapsed state was dropped during search"
    assert app.saved_settings == [], "searching must not write settings"

    app.search_var.set("")
    app.refresh_tree()
    assert app.tree.row("Email")["open"] is False


def test_deleting_folder_prunes_collapsed(gui, sample_root, monkeypatch):
    monkeypatch.setattr(
        app_module,
        "messagebox",
        type(
            "B",
            (),
            {
                "askyesno": staticmethod(lambda *a, **k: True),
                "showerror": staticmethod(lambda *a, **k: None),
            },
        ),
    )
    app = gui(
        roots=[sample_root],
        settings={
            "collapsed": [
                _norm(sample_root / "Email"),
                _norm(sample_root / "Email" / "Dalam"),
                _norm(sample_root / "General"),
            ]
        },
    )
    app.refresh_tree()
    app.tree.selection_set(str(sample_root / "Email"))
    app.delete_selected()
    assert not (sample_root / "Email").exists()
    assert app.saved_settings[-1]["collapsed"] == [_norm(sample_root / "General")]


def test_prune_only_removes_subtree(gui, sample_root):
    other = sample_root / "Emailku"
    other.mkdir()
    app = gui(
        roots=[sample_root],
        settings={"collapsed": [_norm(sample_root / "Email"), _norm(other)]},
    )
    app._prune_collapsed(sample_root / "Email")
    assert app.saved_settings[-1]["collapsed"] == [_norm(other)]


def test_treeview_open_close_bound_to_handler():
    """The <<TreeviewOpen>>/<<TreeviewClose>> events must be bound.

    Without these bindings a category can be collapsed while the state is
    never recorded — the original symptom fixed in 1.5.0.
    """
    text = Path(app_module.__file__).read_text(encoding="utf-8")
    for event in ("<<TreeviewOpen>>", "<<TreeviewClose>>"):
        idx = text.index(event)
        snippet = text[idx : idx + 260]
        assert "_toggle_collapsed" in snippet, f"{event} is not bound:\n{snippet}"


def test_event_iid_prefers_event_attribute(gui):
    app = gui(roots=[])
    event = type("E", (), {"iid": "/tmp/kategori"})()
    assert app._event_iid(event) == "/tmp/kategori"
    app.tree._focus = "/tmp/other"
    assert app._event_iid(object()) == "/tmp/other"
