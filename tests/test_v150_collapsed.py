"""Uji v1.5.0 #3: kategori yang diciutkan disimpan & dipulihkan.

Bug yang dijaga: GUI selalu membuka semua kategori pada setiap refresh_tree
(open=True), sehingga kategori yang baru diciutkan pengguna langsung mekar
lagi — dan statusnya hilang setiap aplikasi ditutup.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from paste_teks import app as app_module


def _norm(path):
    return str(path.resolve())


def test_collapsed_default_is_empty_list():
    assert app_module.DEFAULT_SETTINGS["collapsed"] == []


def test_load_settings_normalises_broken_collapsed(tmp_path, monkeypatch):
    target = tmp_path / "settings.json"
    monkeypatch.setattr(app_module, "SETTINGS_FILE", target)
    for broken in ('"bukan list"', "42", "null"):
        target.write_text('{"collapsed": %s}' % broken, encoding="utf-8")
        assert app_module.load_settings()["collapsed"] == []


def test_load_settings_keeps_saved_collapsed(tmp_path, monkeypatch):
    target = tmp_path / "settings.json"
    monkeypatch.setattr(app_module, "SETTINGS_FILE", target)
    target.write_text(json.dumps({"collapsed": ["/a/b", "/c"]}),
                      encoding="utf-8")
    assert app_module.load_settings()["collapsed"] == ["/a/b", "/c"]


def test_open_treeview_event_marks_category_collapsed(gui, sample_root):
    """Menciutkan kategori harus tersimpan ke settings.json."""
    app = gui(roots=[sample_root])
    app.refresh_tree()
    assert app.tree.row("Umum")["open"] is True

    app._toggle_collapsed(str(sample_root / "Umum"))

    assert app.saved_settings, "settings.json tidak ditulis"
    assert app.saved_settings[-1]["collapsed"] == [
        _norm(sample_root / "Umum")]
    # berkas .txt tidak ikut dicatat walau event-nya nyasar
    app._toggle_collapsed(str(sample_root / "Umum" / "Sapaan.txt"))
    assert app.saved_settings[-1]["collapsed"] == [
        _norm(sample_root / "Umum")]


def test_refresh_tree_restores_collapsed_state(gui, sample_root):
    """refresh_tree (F5) harus menghormati status ciut, bukan membuka semua."""
    saved = _norm(sample_root / "Email")
    app = gui(roots=[sample_root], settings={"collapsed": [saved]})
    app.refresh_tree()
    assert app.tree.row("Umum")["open"] is True
    assert app.tree.row("Email")["open"] is False


def test_collapsed_survives_new_session(sample_root, tmp_path, monkeypatch):
    """Sesi baru (settings.json dibaca ulang) memulihkan kategori ciut."""
    from conftest import make_app

    config_dir = tmp_path / "config"
    monkeypatch.setattr(app_module, "SETTINGS_DIR", config_dir)
    monkeypatch.setattr(app_module, "SETTINGS_FILE", config_dir / "settings.json")

    first = make_app(roots=[sample_root], settings=app_module.load_settings())
    first.refresh_tree()
    first._toggle_collapsed(str(sample_root / "Email"))
    assert (config_dir / "settings.json").exists(), "settings tidak ditulis"

    # Sesi kedua: muat dari disk, bukan dari objek lama.
    second = make_app(roots=[sample_root], settings=app_module.load_settings())
    second.refresh_tree()
    assert second.tree.row("Email")["open"] is False
    assert second.tree.row("Umum")["open"] is True


def test_opening_again_removes_from_collapsed(gui, sample_root):
    app = gui(roots=[sample_root], settings={"collapsed": [
        _norm(sample_root / "Email")]})
    app.refresh_tree()
    app._toggle_collapsed(str(sample_root / "Email"))   # dibuka lagi
    assert app.saved_settings[-1]["collapsed"] == []
    app.refresh_tree()
    assert app.tree.row("Email")["open"] is True


def test_search_expands_everything_and_does_not_overwrite_state(gui, sample_root):
    """Saat mencari semua dibuka, tetapi status ciut tersimpan tidak dihapus."""
    saved = _norm(sample_root / "Email")
    app = gui(roots=[sample_root], settings={"collapsed": [saved]})
    app.search_var.set("Sapaan")
    app.refresh_tree()
    dirs = [r for r in app.tree.rows if r["tags"] == "dir"]
    assert dirs, "pencarian tidak menemukan kategori"
    assert all(r["open"] is True for r in dirs), "kategori harus terbuka saat cari"
    assert app.settings["collapsed"] == [saved], "status ciut terhapus saat cari"
    assert app.saved_settings == [], "pencarian tidak boleh menulis settings"

    app.search_var.set("")
    app.refresh_tree()
    assert app.tree.row("Email")["open"] is False


def test_deleting_category_prunes_collapsed(gui, sample_root, monkeypatch):
    monkeypatch.setattr(app_module, "messagebox", type("B", (), {
        "askyesno": staticmethod(lambda *a, **k: True),
        "showerror": staticmethod(lambda *a, **k: None),
    }))
    app = gui(roots=[sample_root], settings={"collapsed": [
        _norm(sample_root / "Email"), _norm(sample_root / "Email" / "Dalam"),
        _norm(sample_root / "Umum")]})
    app.refresh_tree()
    app.tree.selection_set(str(sample_root / "Email"))
    app.delete_selected()
    assert not (sample_root / "Email").exists()
    assert app.saved_settings[-1]["collapsed"] == [_norm(sample_root / "Umum")]


def test_prune_only_removes_subtree(gui, sample_root):
    other = sample_root / "Emailku"
    other.mkdir()
    app = gui(roots=[sample_root], settings={"collapsed": [
        _norm(sample_root / "Email"), _norm(other)]})
    app._prune_collapsed(sample_root / "Email")
    assert app.saved_settings[-1]["collapsed"] == [_norm(other)]


def test_treeview_open_close_bound_to_handler():
    """Event <<TreeviewOpen>>/<<TreeviewClose>> harus tersambung.

    Tanpa binding ini, kategori bisa diciutkan tetapi statusnya tidak pernah
    dicatat — gejala asli yang diperbaiki di 1.5.0.
    """
    text = Path(app_module.__file__).read_text(encoding="utf-8")
    for event in ("<<TreeviewOpen>>", "<<TreeviewClose>>"):
        idx = text.index(event)
        snippet = text[idx:idx + 260]
        assert "_toggle_collapsed" in snippet, f"{event} tidak tersambung:\n{snippet}"


def test_event_iid_prefers_event_attribute(gui):
    app = gui(roots=[])
    event = type("E", (), {"iid": "/tmp/kategori"})()
    assert app._event_iid(event) == "/tmp/kategori"
    app.tree._focus = "/tmp/lain"
    assert app._event_iid(object()) == "/tmp/lain"
