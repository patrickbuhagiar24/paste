"""Uji logika hierarki snippets (tanpa GUI)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paste_teks.snippets import (
    create_category,
    create_snippet,
    delete_path,
    ensure_txt_suffix,
    flatten_texts,
    read_text,
    scan_dir,
    scan_roots,
    search,
    write_text,
)


def _make_tree(tmp: Path) -> Path:
    root = tmp / "snippets"
    (root / "Umum").mkdir(parents=True)
    (root / "Email").mkdir(parents=True)
    write_text(root / "Umum" / "Sapaan.txt", "Halo, apa kabar?")
    write_text(root / "Email" / "Balas.txt", "Terima kasih atas lamaran Anda.")
    write_text(root / "Email" / "abaikan.md", "bukan txt")
    write_text(root / ".tersembunyi.txt", "jangan tampil")
    return root


def test_scan_hierarchy(tmp_path):
    root = _make_tree(tmp_path)
    tree = scan_dir(root)
    assert tree.is_dir and tree.name == "snippets"
    names = [c.name for c in tree.children]
    assert names == ["Email", "Umum"]  # folder dulu, alfabetis
    email = next(c for c in tree.children if c.name == "Email")
    assert [c.name for c in email.children] == ["Balas"]
    assert email.children[0].is_text
    assert tree.count_texts() == 2  # .md dan dotfile dilewati


def test_scan_roots_and_flatten(tmp_path):
    root = _make_tree(tmp_path)
    nodes = scan_roots([root])
    texts = flatten_texts(nodes)
    assert {t.name for t in texts} == {"Sapaan", "Balas"}


def test_search_by_name_and_content(tmp_path):
    root = _make_tree(tmp_path)
    nodes = scan_roots([root])
    assert [n.name for n in search(nodes, "sapaan")] == ["Sapaan"]
    assert [n.name for n in search(nodes, "lamaran")] == ["Balas"]
    assert search(nodes, "tidak-ada") == []


def test_crud(tmp_path):
    root = _make_tree(tmp_path)
    cat = create_category(root, "Kerja")
    assert cat.is_dir()
    snippet = create_snippet(cat, "Laporan", "isi laporan")
    assert snippet.suffix == ".txt"
    assert read_text(snippet) == "isi laporan"
    try:
        create_snippet(cat, "Laporan.txt", "duplikat")
        raise AssertionError("seharusnya FileExistsError")
    except FileExistsError:
        pass
    delete_path(snippet)
    assert not snippet.exists()
    delete_path(cat)
    assert not cat.exists()


def test_ensure_suffix_and_read_fallback(tmp_path):
    assert ensure_txt_suffix("Halo") == "Halo.txt"
    assert ensure_txt_suffix("Halo.TXT") == "Halo.TXT"
    p = tmp_path / "latin.txt"
    p.write_bytes("café".encode("latin-1"))
    assert read_text(p) == "café"


def test_new_names_cannot_escape_parent(tmp_path):
    import pytest

    root = tmp_path / "source"
    root.mkdir()
    for name in ("../keluar", "../keluar.txt", "/tmp/keluar", "a/b", r"a\b",
                 ".", "..", "x\0y"):
        with pytest.raises(ValueError):
            create_category(root, name)
        with pytest.raises(ValueError):
            create_snippet(root, name, "isi")
    assert sorted(tmp_path.iterdir()) == [root]


def test_scan_ignores_links_inside_root_including_cycles(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside", encoding="utf-8")
    (root / "link-folder").symlink_to(outside, target_is_directory=True)
    (root / "link-file.txt").symlink_to(outside / "secret.txt")
    (root / "cycle").symlink_to(root, target_is_directory=True)
    (root / "normal.txt").write_text("inside", encoding="utf-8")
    assert [node.name for node in scan_dir(root).children] == ["normal"]
    # Pengguna tetap dapat memilih folder sumber yang merupakan symlink.
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    assert [node.name for node in scan_dir(alias).children] == ["normal"]


def test_creation_never_follows_existing_symlink(tmp_path):
    import pytest

    root = tmp_path / "source"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    (root / "broken.txt").symlink_to(outside / "new.txt")
    with pytest.raises(ValueError):
        create_category(root, "link")
    with pytest.raises(FileExistsError):
        create_snippet(root, "broken", "isi")
    assert not (outside / "new.txt").exists()


def test_filter_tree_keeps_only_matching_branches_without_resolving(tmp_path, monkeypatch):
    from paste_teks.app import filter_tree

    root = _make_tree(tmp_path)
    nodes = scan_roots([root])
    hits = search(nodes, "lamaran")

    def unexpected_resolve(*args, **kwargs):
        raise AssertionError("pemfilteran tidak perlu resolve path")

    monkeypatch.setattr(Path, "resolve", unexpected_resolve)
    filtered = filter_tree(nodes, hits)
    assert len(filtered) == 1
    assert [child.name for child in filtered[0].children] == ["Email"]
    assert [child.name for child in filtered[0].children[0].children] == ["Balas"]
    assert filter_tree(nodes, []) == []
