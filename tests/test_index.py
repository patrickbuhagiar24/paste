"""Uji cache index: pemindaian sekali, teks di-cache, invalidate memaksa ulang."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pastex import index as index_module
from pastex.index import SnippetIndex


def _make_tree(root: Path) -> Path:
    folder = root / "General"
    folder.mkdir(parents=True)
    (folder / "Greeting.txt").write_text("Halo dunia", encoding="utf-8")
    (folder / "Receipt.txt").write_text("Total 42", encoding="utf-8")
    (root / "Notes.txt").write_text("catatan", encoding="utf-8")
    return root


def test_nodes_are_scanned_once_until_invalidated(tmp_path):
    index = SnippetIndex()
    index.set_roots([_make_tree(tmp_path / "snippets")])

    first = index.nodes()
    second = index.nodes()

    assert first is second, "pohon yang sama harus dipakai ulang"
    assert index.scans == 1

    index.invalidate()
    index.nodes()

    assert index.scans == 2


def test_force_rescans(tmp_path):
    index = SnippetIndex()
    index.set_roots([_make_tree(tmp_path / "snippets")])
    index.nodes()

    index.nodes(force=True)

    assert index.scans == 2


def test_set_roots_reports_changes(tmp_path):
    snippets = _make_tree(tmp_path / "snippets")
    index = SnippetIndex()
    index.set_roots([snippets])
    index.nodes()

    assert index.set_roots([snippets]) is False
    assert index.scans == 1

    other = tmp_path / "other"
    other.mkdir()
    assert index.set_roots([other]) is True
    # New roots must not answer from the previous tree.
    assert [node.path for node in index.nodes()] == [other]
    assert index.scans == 2


def test_search_matches_names_and_contents(tmp_path):
    index = SnippetIndex()
    index.set_roots([_make_tree(tmp_path / "snippets")])
    index.nodes()

    assert [node.name for node in index.search("greet")] == ["Greeting"]
    assert [node.name for node in index.search("hAlo")] == ["Greeting"]
    assert [node.name for node in index.search("total")] == ["Receipt"]
    assert index.search("") != []
    assert index.search("tidak-ada") == []


def test_contents_are_read_once_across_searches(tmp_path, monkeypatch):
    index = SnippetIndex()
    index.set_roots([_make_tree(tmp_path / "snippets")])
    index.nodes()

    reads = []
    real_read = index_module.read_text

    def counting_read(path):
        reads.append(Path(path).name)
        return real_read(path)

    monkeypatch.setattr(index_module, "read_text", counting_read)

    index.search("halo")
    index.search("halo")
    index.search("total")
    index.search("greet")  # already matched by name, must not read files

    assert sorted(reads) == ["Greeting.txt", "Notes.txt", "Receipt.txt"]


def test_changed_file_is_read_again(tmp_path, monkeypatch):
    root = _make_tree(tmp_path / "snippets")
    index = SnippetIndex()
    index.set_roots([root])
    index.nodes()

    reads = []
    real_read = index_module.read_text
    monkeypatch.setattr(
        index_module,
        "read_text",
        lambda path: reads.append(Path(path).name) or real_read(path),
    )

    target = root / "Notes.txt"
    index.search("catatan")
    assert reads.count("Notes.txt") == 1

    target.write_text("catatan baru yang lebih panjang", encoding="utf-8")
    # Different size guarantees the cache notices even on coarse mtime clocks.
    index.search("baru")
    assert reads.count("Notes.txt") == 2
    assert [node.name for node in index.search("baru")] == ["Notes"]


def test_forget_drops_one_cached_file(tmp_path, monkeypatch):
    root = _make_tree(tmp_path / "snippets")
    index = SnippetIndex()
    index.set_roots([root])
    index.nodes()

    reads = []
    real_read = index_module.read_text
    monkeypatch.setattr(
        index_module,
        "read_text",
        lambda path: reads.append(Path(path).name) or real_read(path),
    )

    index.search("catatan")
    index.forget(root / "Notes.txt")
    index.search("catatan")

    assert reads.count("Notes.txt") == 2


def test_missing_file_is_skipped(tmp_path):
    root = _make_tree(tmp_path / "snippets")
    index = SnippetIndex()
    index.set_roots([root])
    index.nodes()
    (root / "Notes.txt").unlink()

    # The cached tree still lists it, but reading must not raise out of search.
    assert [node.name for node in index.search("catatan")] == []
