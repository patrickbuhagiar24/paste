"""In-memory index of the snippet tree.

``refresh_tree()`` used to walk the whole source folder on every repaint, and
``snippets.search()`` re-read every file for every keystroke. Both are cheap
for a handful of snippets and noticeably slow for a few hundred, so the GUI
keeps the tree here instead:

  * the folder walk runs once and is reused until something changes
    (:meth:`SnippetIndex.invalidate`, driven by :mod:`pastex.watcher`);
  * file contents are cached with their mtime and size, so a content search
    reads each file once and re-reads it only after it changes.

The cache is deliberately simple: no background refresh, no eviction. A
snippet collection is small, and "invalidate, then scan" is easy to reason
about.
"""

from __future__ import annotations

from pathlib import Path

from .snippets import Node, flatten_texts, read_text, scan_roots


class SnippetIndex:
    """Cached snippet tree plus cached file contents."""

    def __init__(self) -> None:
        self._roots: list[Path] | None = None
        self._nodes: list[Node] | None = None
        # path -> (mtime, size, text)
        self._texts: dict[Path, tuple[float, int, str]] = {}
        # Diagnostics: how many times the disk was actually walked.
        self.scans = 0

    @property
    def roots(self) -> list[Path]:
        return list(self._roots or [])

    def set_roots(self, roots) -> bool:
        """Point the index at ``roots``; return True when they changed."""
        normalised = [Path(root) for root in roots]
        if self._roots == normalised:
            return False
        self._roots = normalised
        self._nodes = None
        self._texts.clear()
        return True

    def invalidate(self) -> None:
        """Drop the cached tree so the next access walks the folders again."""
        self._nodes = None

    def nodes(self, *, force: bool = False) -> list[Node]:
        """Return the cached tree, scanning only when needed."""
        if force:
            self._nodes = None
        if self._nodes is None:
            self.scans += 1
            self._nodes = scan_roots(self._roots or [])
        return self._nodes

    def text(self, path) -> str:
        """Read ``path`` once and reuse it until mtime or size changes."""
        path = Path(path)
        stat = path.stat()
        cached = self._texts.get(path)
        if cached and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
            return cached[2]
        text = read_text(path)
        self._texts[path] = (stat.st_mtime, stat.st_size, text)
        return text

    def forget(self, path) -> None:
        """Drop one cached file (used when it is deleted or renamed)."""
        self._texts.pop(Path(path), None)

    def search(self, query: str) -> list[Node]:
        """Match snippet names first, then cached contents."""
        nodes = self.nodes()
        query = query.strip().lower()
        if not query:
            return flatten_texts(nodes)
        hits: list[Node] = []
        seen: set[Path] = set()
        for node in flatten_texts(nodes):
            if node.path in seen:
                continue
            if query in node.name.lower():
                hits.append(node)
                seen.add(node.path)
                continue
            try:
                if query in self.text(node.path).lower():
                    hits.append(node)
                    seen.add(node.path)
            except OSError:
                continue
        return hits
