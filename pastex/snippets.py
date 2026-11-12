"""Scan and manage hierarchical .txt snippet files.

This module contains filesystem logic only, so it can be tested and reused by
both the CLI and GUI. The folder structure defines the hierarchy:

    snippets/
      General/
        Greeting.txt
      Email/
        Application Reply.txt
        Follow Up.txt

Folders organize .txt files, which are text snippets ready to paste.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Node:
    """A node in the folder and text tree."""

    name: str
    path: Path
    is_dir: bool
    children: list["Node"] = field(default_factory=list)
    source_root: Path | None = None  # Origin root (system vs. user)

    @property
    def is_text(self) -> bool:
        return not self.is_dir

    def count_texts(self) -> int:
        """Count .txt files under this node, including the node itself if it is a file."""
        if self.is_text:
            return 1
        return sum(c.count_texts() for c in self.children)

    def find(self, target: Path) -> "Node | None":
        """Find a node by its absolute path."""
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
    """Recursively scan a folder into a Node tree.

    Only .txt files are included. Empty folders remain visible.
    Hidden files/folders (names beginning with '.') and symlinks inside the
    source folder are skipped to avoid traversing outside the source or creating
    cycles. The source folder itself may be a symlink. Sort folders first, then
    files; sort both groups alphabetically without regard to case.
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
        if entry.name.startswith(".") or entry.is_symlink():
            continue
        if entry.is_dir():
            dirs.append(scan_dir(entry, source_root=node.source_root))
        elif is_text_file(entry):
            files.append(Node(name=entry.stem, path=entry, is_dir=False,
                              source_root=node.source_root))
    node.children = dirs + files
    return node


def scan_roots(roots: list[Path]) -> list[Node]:
    """Scan multiple roots (for example, system and user folders) into trees."""
    result: list[Node] = []
    for root in roots:
        root = Path(root)
        if root.is_dir():
            result.append(scan_dir(root, source_root=root))
    return result


def flatten_texts(nodes: list[Node]) -> list[Node]:
    """Collect all .txt file nodes from a list of trees."""
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
    """Search .txt files by filename or text content.

    Unreadable files are skipped during content searches. Return matching file
    nodes without duplicates.
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
    """Read a .txt file as UTF-8, falling back to latin-1 when necessary."""
    path = Path(path)
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


def write_text(path: Path, content: str) -> Path:
    """Write text to a .txt file, creating its parent folder if needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _single_name(name: str) -> str:
    """Accept one name only, not an absolute or relative path."""
    name = name.strip()
    if not name or name in {".", ".."} or any(c in name for c in "/\\\0"):
        raise ValueError("Enter one name without a folder separator, '.', or '..'.")
    return name


def ensure_txt_suffix(name: str) -> str:
    """Validate a filename and ensure it ends in .txt."""
    name = _single_name(name)
    if not name.lower().endswith(".txt"):
        name += ".txt"
    return name


def create_folder(parent: Path, name: str) -> Path:
    """Create a folder under parent."""
    target = Path(parent) / _single_name(name)
    if target.is_symlink():
        raise ValueError("A folder cannot be a symbolic link.")
    target.mkdir(parents=True, exist_ok=True)
    return target


# Backwards-compatible internal alias for older callers and existing data tools.
create_category = create_folder


def rename_folder(path: Path, name: str) -> Path:
    """Rename a folder without replacing an existing filesystem entry."""
    path = Path(path)
    if path.is_symlink() or not path.is_dir():
        raise ValueError("Select a folder to rename.")
    target = path.with_name(_single_name(name))
    if target == path:
        return path
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"A file or folder named '{target.name}' already exists.")
    return path.rename(target)


def create_snippet(parent: Path, filename: str, content: str = "") -> Path:
    """Create a new .txt file exclusively; fail if it already exists."""
    target = Path(parent) / ensure_txt_suffix(filename)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as file:
        file.write(content)
    return target


def delete_path(path: Path) -> None:
    """Delete a .txt file or folder recursively."""
    path = Path(path)
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()
    else:
        raise FileNotFoundError(str(path))


def user_root() -> Path:
    """Return the user folder for personal snippets (always writable)."""
    data_home = Path(os.environ.get("XDG_DATA_HOME",
                                   Path.home() / ".local" / "share"))
    new_root = data_home / "pastex" / "snippets"
    legacy_root = data_home / "paste-teks" / "snippets"
    if not new_root.exists() and legacy_root.is_dir():
        try:
            shutil.copytree(legacy_root, new_root)
        except OSError:
            # Keep the old directory usable if migration is blocked by permissions.
            return legacy_root
    return new_root


def default_roots() -> list[Path]:
    """Return default roots: system (read-only), followed by the writable user root.

    The final root is the write destination for newly created or edited files.
    """
    system_root = Path("/usr/share/pastex/snippets")
    # Relatif terhadap paket (terpasang di /usr/share/pastex/pastex).
    package_root = Path(__file__).resolve().parent.parent / "snippets"
    roots: list[Path] = []
    for candidate in (system_root, package_root):
        if candidate.is_dir() and candidate not in roots:
            roots.append(candidate)
    roots.append(user_root())
    return roots


def writable_root(roots: list[Path] | None = None) -> Path:
    """Return the root where new files are stored (the final, user-writable root)."""
    if roots:
        return Path(roots[-1])
    return default_roots()[-1]


def root_label(root: Path, index: int | None = None) -> str:
    """Return a friendly GUI label for a root without exposing its full path.

    The system, package, and user folders may all be named "snippets", so use
    a label that describes each root's role. ``index`` is accepted for callers
    that pass the root position.
    """
    root = Path(root)
    system_roots = {
        Path("/usr/share/pastex/snippets"),
        Path(__file__).resolve().parent.parent / "snippets",
    }
    try:
        resolved = root.resolve()
    except OSError:  # pragma: no cover - path aneh
        resolved = root
    if resolved in {p.resolve() for p in system_roots}:
        return "Built-in samples"
    if resolved == user_root().resolve():
        return "My Texts"
    return root.name or str(root)


# --- Built-in samples ----------------------------------------------------
#
# Since 1.5.0, the repository and Debian package no longer include sample data.
# Existing samples installed at
# /usr/share/pastex/snippets remain available and are not removed.
#
# New installations do not include samples by default. The GUI asks users to
# choose a source folder first. Samples below are exported only when
# requested with `pastex --export-data [FOLDER]`.

DEFAULT_SAMPLES: dict[str, dict[str, str]] = {
    "General": {
        "Greeting": "Hello, how are you? I hope you are doing well.\n",
        "Signature": "Best regards,\nYour Name\nTitle — Company\n",
    },
    "Email": {
        "Application Reply": (
            "Thank you for your application. We will contact you "
            "when the selection process is complete.\n"),
        "Follow Up": (
            "I am following up on my previous message. Is there any update "
            "you can share?\n"),
    },
    "Work": {
        "Report Template": (
            "Weekly Report\n\nSummary:\n- \n\nChallenges:\n- \n\n"
            "Next steps:\n- \n"),
    },
}
