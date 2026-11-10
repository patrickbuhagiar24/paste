"""Logika pemindaian & pengelolaan berkas .txt hierarki.

Modul ini murni logika (tanpa GUI) sehingga mudah diuji dan dipakai ulang
dari CLI maupun GUI. Hierarki = struktur folder di disk:

    snippets/
      Umum/
        Sapaan.txt
      Email/
        Balas Lamaran.txt
        Tindak Lanjut.txt

Setiap folder adalah kategori, setiap berkas .txt adalah satu teks siap paste.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Node:
    """Satu simpul dalam pohon kategori/teks."""

    name: str
    path: Path
    is_dir: bool
    children: list["Node"] = field(default_factory=list)
    source_root: Path | None = None  # root asal (sistem vs pengguna)

    @property
    def is_text(self) -> bool:
        return not self.is_dir

    def count_texts(self) -> int:
        """Hitung total berkas .txt di bawah simpul ini (termasuk diri)."""
        if self.is_text:
            return 1
        return sum(c.count_texts() for c in self.children)

    def find(self, target: Path) -> "Node | None":
        """Cari simpul berdasarkan path absolut."""
        try:
            target = target.resolve()
        except OSError:
            pass
        if self.path.resolve() == target:
            return self
        for child in self.children:
            found = child.find(target)
            if found is not None:
                return found
        return None


def is_text_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() == ".txt"


def scan_dir(directory: Path, source_root: Path | None = None) -> Node:
    """Pindai satu folder secara rekursif menjadi pohon Node.

    Hanya berkas .txt yang dimasukkan. Folder kosong tetap ditampilkan
    (sebagai kategori) agar pengguna tahu strukturnya. Berkas/folder
    tersembunyi (diawali '.') dilewati. Urutan: folder dulu, lalu berkas,
    masing-masing alfabetis (case-insensitive).
    """
    directory = Path(directory)
    node = Node(name=directory.name or str(directory), path=directory,
                is_dir=True, source_root=source_root or directory)
    if not directory.is_dir():
        return node

    try:
        entries = sorted(directory.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return node

    dirs: list[Node] = []
    files: list[Node] = []
    for entry in entries:
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            dirs.append(scan_dir(entry, source_root=node.source_root))
        elif is_text_file(entry):
            files.append(Node(name=entry.stem, path=entry, is_dir=False,
                              source_root=node.source_root))
    node.children = dirs + files
    return node


def scan_roots(roots: list[Path]) -> list[Node]:
    """Pindai beberapa root (mis. sistem + pengguna) menjadi daftar pohon."""
    result: list[Node] = []
    for root in roots:
        root = Path(root)
        if root.is_dir():
            result.append(scan_dir(root, source_root=root))
    return result


def flatten_texts(nodes: list[Node]) -> list[Node]:
    """Kumpulkan semua simpul berkas .txt dari daftar pohon."""
    out: list[Node] = []

    def walk(n: Node) -> None:
        if n.is_text:
            out.append(n)
        for c in n.children:
            walk(c)

    for n in nodes:
        walk(n)
    return out


def search(nodes: list[Node], query: str) -> list[Node]:
    """Cari berkas .txt berdasarkan nama file atau isi teksnya.

    Pencarian isi membaca berkas; berkas yang gagal dibaca dilewati.
    Hasil berupa daftar simpul berkas yang cocok (tanpa duplikat).
    """
    q = query.strip().lower()
    if not q:
        return flatten_texts(nodes)
    hits: list[Node] = []
    seen: set[Path] = set()
    for node in flatten_texts(nodes):
        if node.path in seen:
            continue
        if q in node.name.lower():
            hits.append(node)
            seen.add(node.path)
            continue
        try:
            if q in read_text(node.path).lower():
                hits.append(node)
                seen.add(node.path)
        except OSError:
            continue
    return hits


def read_text(path: Path) -> str:
    """Baca berkas .txt sebagai UTF-8 (fallback latin-1 bila perlu)."""
    path = Path(path)
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


def write_text(path: Path, content: str) -> Path:
    """Tulis teks ke berkas .txt (buat folder induk bila belum ada)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def ensure_txt_suffix(name: str) -> str:
    """Pastikan nama berkas berakhiran .txt."""
    name = name.strip()
    if not name:
        raise ValueError("Nama berkas tidak boleh kosong.")
    if not name.lower().endswith(".txt"):
        name += ".txt"
    return name


def create_category(parent: Path, name: str) -> Path:
    """Buat folder kategori baru di bawah parent."""
    name = name.strip().strip("/\\")
    if not name:
        raise ValueError("Nama kategori tidak boleh kosong.")
    target = Path(parent) / name
    target.mkdir(parents=True, exist_ok=True)
    return target


def create_snippet(parent: Path, filename: str, content: str = "") -> Path:
    """Buat berkas .txt baru di bawah parent. Gagal bila sudah ada."""
    target = Path(parent) / ensure_txt_suffix(filename)
    if target.exists():
        raise FileExistsError(f"Berkas sudah ada: {target}")
    return write_text(target, content)


def delete_path(path: Path) -> None:
    """Hapus berkas .txt atau folder kategori (rekursif)."""
    path = Path(path)
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()
    else:
        raise FileNotFoundError(str(path))


def default_roots() -> list[Path]:
    """Daftar root bawaan: sistem (read-only) lalu pengguna (writable).

    Urutan penting: root terakhir adalah tempat penulisan (tambah/edit/hapus).
    """
    system_root = Path("/usr/share/paste-teks/snippets")
    # Relatif terhadap paket (terpasang di /usr/share/paste-teks/paste_teks).
    package_root = Path(__file__).resolve().parent.parent / "snippets"
    user_root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "paste-teks" / "snippets"
    # Di lingkungan dev (repo), pakai data/snippets bila ada.
    repo_root = Path(__file__).resolve().parent.parent / "data" / "snippets"
    roots: list[Path] = []
    for candidate in (system_root, package_root, repo_root):
        if candidate.is_dir() and candidate not in roots:
            roots.append(candidate)
    roots.append(user_root)
    return roots


def writable_root(roots: list[Path] | None = None) -> Path:
    """Root tempat berkas baru disimpan (root terakhir / folder pengguna)."""
    if roots:
        return Path(roots[-1])
    return default_roots()[-1]
