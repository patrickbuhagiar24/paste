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
