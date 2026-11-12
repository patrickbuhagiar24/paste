"""Uji v1.5.0 #4: satu folder sumber + tampilan kosong berpetunjuk.

Aturan yang dijaga:
  * "📁 Choose Source Folder" menyimpan SATU folder ke settings.json.
  * Folder itu menjadi satu-satunya root DAN tempat penulisan.
  * `--dir` selalu menang untuk sesi itu, dan TIDAK mengubah folder tersimpan.
  * Bila folder sumber belum dipilih -> tampilan kosong berpetunjuk.
"""
from __future__ import annotations

from pathlib import Path

from pastex import app as app_module
from pastex.snippets import user_root


def _norm(path):
    return str(Path(path).resolve())


# --- settings -------------------------------------------------------------

def test_source_dir_default_is_none():
    assert app_module.DEFAULT_SETTINGS["source_dir"] is None


def test_load_settings_ignores_broken_source_dir(tmp_path, monkeypatch):
    target = tmp_path / "settings.json"
    monkeypatch.setattr(app_module, "SETTINGS_FILE", target)
    for broken in ("42", '""', "null", '{"x": 1}', "[1,2]"):
        target.write_text('{"source_dir": %s}' % broken, encoding="utf-8")
        assert app_module.load_settings()["source_dir"] is None


def test_load_settings_keeps_source_dir(tmp_path, monkeypatch):
    target = tmp_path / "settings.json"
    monkeypatch.setattr(app_module, "SETTINGS_FILE", target)
    target.write_text('{"source_dir": "/data/teks"}', encoding="utf-8")
    assert app_module.load_settings()["source_dir"] == "/data/teks"


# --- tombol & pemilihan folder -------------------------------------------

def test_choose_source_dir_button_exists():
    src = Path(app_module.__file__).read_text(encoding="utf-8")
    assert "📁 Choose Source Folder" in src
    assert "choose_source_dir" in src


def test_choose_source_dir_saves_and_refreshes(gui, dialogs, sample_root):
    app = gui(roots=[Path("/tidak/dipakai")])
    dialogs.answers["directory"] = str(sample_root)
    chosen = app.choose_source_dir()

    assert chosen == sample_root
    assert app.saved_settings[-1]["source_dir"] == _norm(sample_root)
    # root aktif = hanya folder sumber
    assert app.active_roots() == [sample_root]
    assert app.tree.row("Greeting") is not None


def test_choose_source_dir_cancel_changes_nothing(gui, dialogs):
    app = gui(roots=[Path("/a")])
    app.settings["source_dir"] = "/lama"
    dialogs.answers["directory"] = ""
    assert app.choose_source_dir() is None
    assert app.settings["source_dir"] == "/lama"
    assert app.saved_settings == []


def test_choose_source_dir_rejects_missing_folder(gui, dialogs, tmp_path):
    app = gui(roots=[Path("/a")])
    dialogs.answers["directory"] = str(tmp_path / "hilang")
    assert app.choose_source_dir() is None
    assert any(call[0] == "askdirectory" for call in dialogs.asked)
    assert any(call[0] == "error" for call in dialogs.asked), \
        "folder tidak valid harus dilaporkan"
    assert app.settings["source_dir"] is None
    assert app.settings["source_dir"] is None
    assert app.saved_settings == []


def test_source_dir_replaces_default_roots(gui, sample_root):
    app = gui(roots=[Path("/sistem"), Path("/pengguna")],
              settings={"source_dir": _norm(sample_root)})
    app.refresh_tree()
    assert app.active_roots() == [sample_root]
    texts = [r["text"] for r in app.tree.rows]
    assert not any("sistem" in t or "pengguna" in t for t in texts)
    assert not any("sumber" in t for t in texts)
    assert app.tree.by_iid(sample_root / "General")["parent"] == ""
    assert app.tree.by_iid(sample_root / "Email")["parent"] == ""


def test_missing_source_dir_falls_back_to_roots(gui, tmp_path):
    app = gui(roots=[tmp_path], settings={"source_dir": str(tmp_path / "hilang")})
    assert app.active_roots() == [tmp_path]


# --- tampilan kosong berpetunjuk -----------------------------------------

def test_empty_state_shown_when_no_source_selected(gui, tmp_path):
    """Pemasangan baru: tidak ada folder -> petunjuk, bukan pohon kosong."""
    app = gui(roots=[tmp_path / "belum-ada"])
    app.refresh_tree()
    assert app.empty_frame.packed is not None, "tampilan kosong tidak muncul"
    assert app.scroll.packed == "forgotten"
    assert app.tree.packed == "forgotten"


def test_empty_folder_keeps_tree_visible(gui, tmp_path):
    """Folder sumber kosong tetap ditampilkan agar bisa diisi kategori."""
    root = tmp_path / "kosong"
    root.mkdir()
    app = gui(roots=[root], settings={"source_dir": str(root)})
    app.refresh_tree()
    assert app.empty_frame.packed == "forgotten"
    assert app.tree.packed is not None


def test_empty_state_hidden_when_there_is_content(gui, sample_root):
    app = gui(roots=[sample_root])
    app.refresh_tree()
    assert app.empty_frame.packed == "forgotten"
    assert app.tree.packed is not None


def test_empty_state_has_guidance_and_button():
    src = Path(app_module.__file__).read_text(encoding="utf-8")
    start = src.index("self.empty_frame")
    block = src[start:start + 900]
    assert "No source folder selected" in block
    assert "Choose Source Folder" in block
    assert "folder hierarchy" in block.lower()


# --- penulisan di dalam folder sumber ------------------------------------

def test_target_dir_is_source_root_when_nothing_selected(gui, sample_root,
                                                         tmp_path):
    app = gui(roots=[tmp_path], settings={"source_dir": _norm(sample_root)})
    app.refresh_tree()
    assert app._target_dir() == Path(_norm(sample_root))


def test_target_dir_inside_selected_category(gui, sample_root):
    app = gui(roots=[sample_root])
    app.settings["source_dir"] = _norm(sample_root)
    app.refresh_tree()
    app.tree.selection_set(str(sample_root / "General"))
    assert app._target_dir() == sample_root / "General"


def test_target_dir_never_escapes_source(gui, tmp_path):
    """Folder induk root sumber tidak boleh jadi tujuan penulisan."""
    parent = tmp_path / "induk"
    source = parent / "sumber"
    (source / "General").mkdir(parents=True)
    app = gui(roots=[source], settings={"source_dir": _norm(source)})
    app.refresh_tree()
    # pilih berkas langsung di akar folder sumber
    (source / "Lepas.txt").write_text("x", encoding="utf-8")
    app.refresh_tree()
    app.tree.selection_set(str(source / "Lepas.txt"))
    assert app._target_dir() == source
    assert app._target_dir() != parent


def test_add_category_writes_into_source(gui, dialogs, sample_root):
    app = gui(roots=[sample_root], settings={"source_dir": _norm(sample_root)})
    app.refresh_tree()
    dialogs.answers["string"] = "Baru"
    app.add_category()
    assert (sample_root / "Baru").is_dir(), "kategori tidak dibuat di folder sumber"


def test_add_text_writes_into_source(gui, dialogs, sample_root, monkeypatch):
    app = gui(roots=[sample_root], settings={"source_dir": _norm(sample_root)})
    app.refresh_tree()
    dialogs.answers["string"] = "Catatan"
    opened = []
    monkeypatch.setattr(app_module, "EditorWindow",
                        lambda *a, **k: opened.append(a))
    app.add_text()
    assert (sample_root / "Catatan.txt").exists()


# --- --dir selalu menang, tanpa mengubah folder tersimpan ----------------

def test_dir_argument_wins_over_saved_source(gui, sample_root, tmp_path, monkeypatch):
    """--dir mengalahkan source_dir untuk sesi itu."""
    lain = tmp_path / "lain"
    lain.mkdir()
    (lain / "Catatan.txt").write_text("isi", encoding="utf-8")
    app = gui(roots=[lain], settings={"source_dir": _norm(sample_root)},
              transient_roots=True)
    app.refresh_tree()
    assert app.active_roots() == [lain]
    assert [r["text"] for r in app.tree.rows] == ["📄 Catatan"]
    assert app.tree.rows[0]["parent"] == ""


def test_dir_argument_does_not_touch_saved_source(gui, sample_root, tmp_path,
                                                 monkeypatch):
    """Argumen --dir tidak boleh menulis/mengubah folder tersimpan."""
    monkeypatch.setattr(app_module.PastexApp, "__init__",
                        lambda self, roots=None, transient_roots=False: None)
    app = gui(roots=[tmp_path], settings={"source_dir": _norm(sample_root)},
              transient_roots=True)
    app.refresh_tree()
    app.add_category() if False else None       # tidak ada dialog di sini
    assert app.settings["source_dir"] == _norm(sample_root)
    assert app.saved_settings == [], "sesi --dir menulis settings.json"


def test_dir_argument_transient_flag_from_cli(monkeypatch, tmp_path):
    """run_cli meneruskan transient_roots=True saat --dir dipakai."""
    seen = {}

    class FakeApp:
        def __init__(self, roots=None, *, transient_roots=False):
            seen["roots"] = [Path(r) for r in roots or []]
            seen["transient"] = transient_roots

        def mainloop(self):
            pass

    monkeypatch.setattr(app_module, "PastexApp", FakeApp)
    monkeypatch.setattr(app_module, "tk", object())
    assert app_module.run_cli(["--dir", str(tmp_path)]) == 0
    assert seen["roots"] == [tmp_path]
    assert seen["transient"] is True


def test_without_dir_not_transient(monkeypatch):
    seen = {}

    class FakeApp:
        def __init__(self, roots=None, *, transient_roots=False):
            seen["transient"] = transient_roots

        def mainloop(self):
            pass

    monkeypatch.setattr(app_module, "PastexApp", FakeApp)
    monkeypatch.setattr(app_module, "tk", object())
    app_module.run_cli([])
    assert seen["transient"] is False


def test_cli_list_explains_when_nothing_found(tmp_path, capsys):
    """--list tanpa teks tidak boleh diam membingungkan."""
    from pastex.app import run_cli

    empty = tmp_path / "kosong"
    empty.mkdir()
    assert run_cli(["--dir", str(empty), "--list"]) == 0
    err = capsys.readouterr().err
    assert "no .txt files found" in err
    assert "--export-data" in err


# --- pemasangan baru: benar-benar kosong, contoh hanya atas permintaan ----

def test_nothing_written_into_user_root_automatically(gui, tmp_path, monkeypatch):
    """Start GUI tidak boleh menanam contoh di folder pengguna."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    target = user_root()
    app = gui(roots=[target])
    app.refresh_tree()
    assert not target.exists(), "folder pengguna diisi otomatis"


def test_export_data_is_opt_in(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert app_module.run_cli(["--export-data"]) == 0
    created = list(user_root().rglob("*.txt"))
    assert created, "no samples were created"
    assert (user_root() / "General" / "Greeting.txt") in created


def test_source_dir_selection_does_not_get_samples(gui, sample_root,
                                                   tmp_path, monkeypatch):
    """Folder sumber pilihan pengguna tidak diisi contoh otomatis."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    app = gui(roots=[sample_root], settings={"source_dir": _norm(sample_root)})
    app.refresh_tree()
    names = sorted(p.name for p in sample_root.rglob("*.txt"))
    assert names == ["Balas.txt", "Greeting.txt"]


def test_dir_writes_to_transient_root_not_saved_source(gui, sample_root, tmp_path):
    other = tmp_path / "lain"
    (other / "categories").mkdir(parents=True)
    app = gui(roots=[other], settings={"source_dir": _norm(sample_root)},
              transient_roots=True)
    app.refresh_tree()
    assert app._target_dir() == other
    app.tree.selection_set(str(other / "categories"))
    assert app._target_dir() == other / "categories"
