"""Refresh the snippet tree from inotify events instead of constant rescans.

The GUI used to discover changes only through F5 or its own edits. Watching
the source folders means a file dropped in by an editor or a sync client
shows up immediately, and the tree no longer has to be
re-walked "just in case".

Everything here is best-effort: if inotify is unavailable (a low
``fs.inotify.max_user_instances``, a container, or a non-Linux platform)
:func:`open_inotify` returns ``-1`` and the watcher falls back to polling the
folders every couple of seconds, so the tree still refreshes by itself. No
exception ever escapes the watcher thread.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os
import select
import struct
import threading
import time
from pathlib import Path

# inotify event masks (linux/inotify.h).
IN_MODIFY = 0x00000002
IN_ATTRIB = 0x00000004
IN_CLOSE_WRITE = 0x00000008
IN_MOVED_FROM = 0x00000040
IN_MOVED_TO = 0x00000080
IN_CREATE = 0x00000100
IN_DELETE = 0x00000200
IN_DELETE_SELF = 0x00000400
IN_MOVE_SELF = 0x00000800
IN_IGNORED = 0x00008000
IN_ISDIR = 0x40000000
IN_Q_OVERFLOW = 0x00004000

# Writes, renames, creations and deletions inside the watched folders.
WATCH_MASK = (
    IN_MODIFY
    | IN_ATTRIB
    | IN_CLOSE_WRITE
    | IN_MOVED_FROM
    | IN_MOVED_TO
    | IN_CREATE
    | IN_DELETE
    | IN_DELETE_SELF
    | IN_MOVE_SELF
)

_EVENT_HEADER = struct.Struct("iIII")

# Polling fallback when inotify cannot be opened.
POLL_INTERVAL = 2.0
_LAST_ERRNO = 0


def last_error() -> int:
    """Return the errno of the last failed :func:`open_inotify` (0 if none)."""
    return _LAST_ERRNO

try:  # Linux only; elsewhere the watcher simply stays unavailable.
    _LIBC = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
except OSError:  # pragma: no cover - depends on the platform
    _LIBC = None


def available() -> bool:
    """True when this platform can watch folders with inotify."""
    return _LIBC is not None and hasattr(_LIBC, "inotify_init1")


def open_inotify() -> int:
    """Open a non-blocking inotify descriptor, or return -1."""
    global _LAST_ERRNO
    if not available():  # pragma: no cover - platform dependent
        _LAST_ERRNO = errno.ENOSYS
        return -1
    fd = _LIBC.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
    if fd < 0:  # pragma: no cover - resource limits
        _LAST_ERRNO = ctypes.get_errno() or errno.EMFILE
        return -1
    _LAST_ERRNO = 0
    return int(fd)


def add_watch(fd: int, path) -> int:
    """Watch one directory recursively-by-caller; return the watch id or -1."""
    if fd < 0 or _LIBC is None:  # pragma: no cover - guarded by callers
        return -1
    wd = _LIBC.inotify_add_watch(fd, str(path).encode(), WATCH_MASK)
    return int(wd) if wd >= 0 else -1


def close_fd(fd: int) -> None:
    if fd < 0 or _LIBC is None:  # pragma: no cover - guarded by callers
        return
    try:
        _LIBC.close(fd)
    except OSError:  # pragma: no cover - already closed
        pass


def parse_events(data: bytes) -> list[tuple[int, int, str]]:
    """Split the buffer inotify returns into ``(wd, mask, name)`` tuples."""
    events: list[tuple[int, int, str]] = []
    offset = 0
    size = _EVENT_HEADER.size
    while offset + size <= len(data):
        wd, mask, _cookie, length = _EVENT_HEADER.unpack_from(data, offset)
        offset += size
        raw = data[offset : offset + length]
        offset += length
        name = raw.split(b"\0", 1)[0].decode("utf-8", "replace")
        events.append((int(wd), int(mask), name))
    return events


def tree_signature(roots) -> tuple:
    """Return (path, mtime, size, is_dir) for everything under ``roots``.

    Used by the polling fallback: cheap to compute, and different as soon as a
    file is created, edited, renamed or deleted.
    """
    items: list[tuple[str, int, int, bool]] = []
    for root in roots:
        try:
            paths = sorted(Path(root).rglob("*"))
        except OSError:  # pragma: no cover - vanished mid-walk
            continue
        for path in paths:
            try:
                info = path.stat()
            except OSError:
                continue
            items.append((str(path), info.st_mtime_ns, info.st_size, path.is_dir()))
    return tuple(items)


class DirectoryWatcher:
    """Watch snippet folders in a daemon thread and call ``on_change``.

    ``on_change`` may be called from the watcher thread, so the GUI marshals it
    with ``after()``. Repeat events are coalesced: a burst of writes (an editor
    saving a file produces several) triggers one callback.
    """

    def __init__(
        self,
        roots,
        on_change,
        *,
        coalesce: float = 0.25,
        poll_timeout: float = 0.5,
    ) -> None:
        """``coalesce`` is the minimum gap between callbacks, in seconds."""
        self.roots = [Path(root) for root in roots]
        self.on_change = on_change
        self.coalesce = max(0.0, float(coalesce))
        self.poll_timeout = poll_timeout
        self._fd = -1
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._last: float | None = None
        self._paths: dict[int, Path] = {}
        self.changes = 0
        # True when inotify was unavailable and the folders are polled instead.
        self.poll_only = False
        self._signature: tuple | None = None

    # -- lifecycle -----------------------------------------------------
    def start(self) -> bool:
        """Start watching; return False when nothing could be watched.

        Without inotify the watcher polls instead, so a machine where the
        inotify instances are exhausted still refreshes the tree by itself.
        """
        if self._thread is not None:
            return True
        self._fd = open_inotify()
        if self._fd < 0:
            self.poll_only = True
            self._signature = tree_signature(self.roots)
            self._thread = threading.Thread(
                target=self._run_polling, name="pastex-watcher-poll", daemon=True
            )
            self._thread.start()
            return True
        self.poll_only = False
        self._watch_tree()
        if not self._paths:
            self.stop()
            return False
        self._thread = threading.Thread(
            target=self._run, name="pastex-watcher", daemon=True
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=1.0)
        close_fd(self._fd)
        self._fd = -1
        self._paths.clear()
        self.poll_only = False
        self._signature = None

    def set_roots(self, roots) -> bool:
        """Point the watcher at new folders; return True when it restarted."""
        normalised = [Path(root) for root in roots]
        if normalised == self.roots:
            return False
        was_running = self._thread is not None
        self.stop()
        self.roots = normalised
        self._stop = threading.Event()
        if was_running:
            return self.start()
        return False

    # -- internals -----------------------------------------------------
    def _watch_tree(self) -> None:
        for root in self.roots:
            if not root.is_dir():
                continue
            self._watch_dir(root)
            try:
                directories = [p for p in root.rglob("*") if p.is_dir()]
            except OSError:  # pragma: no cover - vanished mid-walk
                directories = []
            for directory in directories:
                self._watch_dir(directory)

    def _watch_dir(self, path: Path) -> None:
        wd = add_watch(self._fd, path)
        if wd >= 0 and wd not in self._paths:
            self._paths[wd] = path

    def _run(self) -> None:  # pragma: no cover - exercised through fakes in tests
        while not self._stop.is_set():
            fd = self._fd
            if fd < 0:
                return
            try:
                ready, _, _ = select.select([fd], [], [], self.poll_timeout)
            except (OSError, ValueError):
                return
            if not ready:
                continue
            try:
                data = os.read(fd, 65536)
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EINTR):
                    continue
                return
            if not data:
                continue
            self.handle_events(parse_events(data))

    def _run_polling(self) -> None:
        """Fallback loop: compare the tree signature every ``POLL_INTERVAL``."""
        while not self._stop.is_set():
            if self._stop.wait(POLL_INTERVAL):
                return
            try:
                self.poll_once()
            except Exception:  # noqa: BLE001 - a watcher must never die
                continue

    def poll_once(self) -> bool:
        """Rescan once; return True when something changed."""
        signature = tree_signature(self.roots)
        if self._signature is None:
            # First scan: record the baseline instead of crying wolf.
            self._signature = signature
            return False
        if signature == self._signature:
            return False
        self._signature = signature
        self._trigger()
        return True

    def handle_events(self, events) -> None:
        """React to parsed inotify events (public so it can be unit-tested)."""
        interesting = False
        for wd, mask, name in events:
            if mask & IN_Q_OVERFLOW:
                interesting = True
                continue
            if mask & IN_IGNORED:
                self._paths.pop(wd, None)
                continue
            if mask & (IN_CREATE | IN_MOVED_TO) and mask & IN_ISDIR:
                parent = self._paths.get(wd)
                if parent is not None and name:
                    self._watch_dir(parent / name)
            if mask & WATCH_MASK:
                interesting = True
        if interesting:
            self._trigger()

    def _trigger(self) -> None:
        now = time.monotonic()
        # `_last` is None until the first event. Comparing against 0.0 instead
        # would swallow that first event whenever the monotonic clock (uptime)
        # is younger than `coalesce` seconds -- e.g. on a freshly booted VM.
        if self._last is not None and now - self._last < self.coalesce:
            return
        self._last = now
        self.changes += 1
        try:
            self.on_change()
        except Exception:  # noqa: BLE001 - a watcher must never kill the app
            pass
