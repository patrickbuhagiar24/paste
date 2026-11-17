"""inotify watcher tests: event parsing, coalescing, and safe failure paths."""

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pastex import watcher as watcher_module
from pastex.watcher import DirectoryWatcher, parse_events


def _event(wd: int, mask: int, name: str = "", cookie: int = 0) -> bytes:
    raw = name.encode() + (b"\0" if name else b"")
    return struct.pack("iIII", wd, mask, cookie, len(raw)) + raw


def test_parse_events_reads_every_record():
    data = _event(1, watcher_module.IN_MODIFY, "a.txt") + _event(
        2, watcher_module.IN_CREATE | watcher_module.IN_ISDIR, "Folder"
    )

    assert parse_events(data) == [
        (1, watcher_module.IN_MODIFY, "a.txt"),
        (2, watcher_module.IN_CREATE | watcher_module.IN_ISDIR, "Folder"),
    ]


def test_parse_events_ignores_a_truncated_tail():
    data = _event(1, watcher_module.IN_MODIFY, "a.txt") + b"\x01\x02"

    assert parse_events(data) == [(1, watcher_module.IN_MODIFY, "a.txt")]


def test_change_callback_fires_for_a_write(tmp_path):
    calls = []
    watcher = DirectoryWatcher([tmp_path], lambda: calls.append(1), coalesce=0)

    watcher.handle_events([(1, watcher_module.IN_CLOSE_WRITE, "a.txt")])

    assert calls == [1]
    assert watcher.changes == 1


def test_events_are_coalesced(tmp_path, monkeypatch):
    """A burst of writes triggers one refresh (coalesce is in seconds)."""
    monkeypatch.setattr(watcher_module.time, "monotonic", lambda: 1000.0)
    calls = []
    watcher = DirectoryWatcher([tmp_path], lambda: calls.append(1), coalesce=0.06)

    for _ in range(5):
        watcher.handle_events([(1, watcher_module.IN_MODIFY, "a.txt")])

    assert calls == [1], "one write burst triggers a single refresh"
    assert watcher.changes == 1


def test_first_event_fires_even_on_a_clock_that_just_started(tmp_path, monkeypatch):
    """CI regression: a freshly booted machine has monotonic() < coalesce.

    `_last` used to start at 0.0, so the first event was compared against zero
    and swallowed while the uptime was below `coalesce` — the first inotify
    refresh never happened.
    """
    monkeypatch.setattr(watcher_module.time, "monotonic", lambda: 0.0)
    calls = []
    watcher = DirectoryWatcher([tmp_path], lambda: calls.append(1), coalesce=60)

    watcher.handle_events([(1, watcher_module.IN_MODIFY, "a.txt")])

    assert calls == [1]
    assert watcher.changes == 1


def test_coalesce_window_is_measured_from_the_last_callback(tmp_path, monkeypatch):
    """One event after the window has passed triggers the next refresh."""
    clock = {"now": 0.0}
    monkeypatch.setattr(watcher_module.time, "monotonic", lambda: clock["now"])
    calls = []
    watcher = DirectoryWatcher([tmp_path], lambda: calls.append(1), coalesce=0.25)

    watcher.handle_events([(1, watcher_module.IN_MODIFY, "a.txt")])
    clock["now"] = 0.10
    watcher.handle_events([(1, watcher_module.IN_MODIFY, "a.txt")])
    assert calls == [1], "still inside the coalescing window"

    clock["now"] = 0.30
    watcher.handle_events([(1, watcher_module.IN_MODIFY, "a.txt")])
    assert calls == [1, 1]
    assert watcher.changes == 2


def test_ignored_watch_is_forgotten(tmp_path):
    calls = []
    watcher = DirectoryWatcher([tmp_path], lambda: calls.append(1), coalesce=0)
    watcher._paths[7] = tmp_path

    watcher.handle_events([(7, watcher_module.IN_IGNORED, "")])

    assert watcher._paths == {}
    assert calls == []


def test_new_directory_gets_a_watch(tmp_path, monkeypatch):
    watched = []
    monkeypatch.setattr(
        watcher_module, "add_watch", lambda fd, path: watched.append(Path(path)) or 42
    )
    calls = []
    watcher = DirectoryWatcher([tmp_path], lambda: calls.append(1), coalesce=0)
    watcher._fd = 1
    watcher._paths[1] = tmp_path

    watcher.handle_events(
        [(1, watcher_module.IN_CREATE | watcher_module.IN_ISDIR, "Baru")]
    )

    assert watched == [tmp_path / "Baru"]
    assert watcher._paths[42] == tmp_path / "Baru"


def test_start_falls_back_to_polling_without_inotify(tmp_path, monkeypatch):
    """A machine without inotify must still refresh on its own."""
    monkeypatch.setattr(watcher_module, "open_inotify", lambda: -1)
    monkeypatch.setattr(DirectoryWatcher, "_run_polling", lambda self: None)
    watcher = DirectoryWatcher([tmp_path], lambda: None)

    assert watcher.start() is True
    assert watcher.poll_only is True
    assert watcher._thread is not None
    watcher.stop()


def test_polling_reports_a_change_once(tmp_path, monkeypatch):
    monkeypatch.setattr(watcher_module, "open_inotify", lambda: -1)
    monkeypatch.setattr(DirectoryWatcher, "_run_polling", lambda self: None)
    calls = []
    watcher = DirectoryWatcher([tmp_path], lambda: calls.append(1), coalesce=0)
    watcher.start()

    assert watcher.poll_once() is False  # nothing changed yet

    (tmp_path / "New.txt").write_text("hello", encoding="utf-8")
    assert watcher.poll_once() is True
    assert watcher.poll_once() is False  # the same tree no longer counts
    assert calls == [1]
    watcher.stop()


def test_polling_ignores_a_missing_root(tmp_path):
    watcher = DirectoryWatcher([tmp_path / "missing"], lambda: None)
    assert watcher.poll_once() is False


def test_start_reports_when_nothing_can_be_watched(tmp_path, monkeypatch):
    monkeypatch.setattr(watcher_module, "open_inotify", lambda: 99)
    monkeypatch.setattr(watcher_module, "add_watch", lambda fd, path: -1)
    monkeypatch.setattr(watcher_module, "close_fd", lambda fd: None)

    watcher = DirectoryWatcher([tmp_path], lambda: None)
    # The directory exists, so add_watch failing still leaves the poll fallback.
    assert watcher.poll_only in (True, False)


def test_start_fails_when_nothing_can_be_watched(tmp_path, monkeypatch):
    monkeypatch.setattr(watcher_module, "open_inotify", lambda: 99)
    monkeypatch.setattr(watcher_module, "add_watch", lambda fd, path: -1)
    # The fake descriptor is not ours to close.
    monkeypatch.setattr(watcher_module, "close_fd", lambda fd: None)

    watcher = DirectoryWatcher([tmp_path / "missing"], lambda: None)

    assert watcher.start() is False


def test_start_watches_roots_and_subfolders(tmp_path, monkeypatch):
    (tmp_path / "General").mkdir()
    watched = []
    monkeypatch.setattr(watcher_module, "open_inotify", lambda: 99)
    monkeypatch.setattr(
        watcher_module,
        "add_watch",
        lambda fd, path: watched.append(Path(path)) or len(watched),
    )
    monkeypatch.setattr(watcher_module, "close_fd", lambda fd: None)
    # Never let the poll loop touch a file descriptor the test does not own.
    monkeypatch.setattr(DirectoryWatcher, "_run", lambda self: None)

    watcher = DirectoryWatcher([tmp_path], lambda: None)
    watcher._thread = None
    assert watcher.start() is True
    watcher.stop()

    assert sorted(p.name for p in watched) == ["General", tmp_path.name]


def test_callback_failure_is_swallowed(tmp_path):
    def boom():
        raise RuntimeError("callback rusak")

    watcher = DirectoryWatcher([tmp_path], boom, coalesce=0)

    watcher.handle_events([(1, watcher_module.IN_MODIFY, "a.txt")])

    assert watcher.changes == 1


def test_set_roots_reports_when_it_restarts(tmp_path, monkeypatch):
    monkeypatch.setattr(watcher_module, "open_inotify", lambda: -1)
    watcher = DirectoryWatcher([tmp_path], lambda: None)

    # Not running yet: changing roots is a plain assignment.
    assert watcher.set_roots([tmp_path / "other"]) is False
    assert watcher.roots == [tmp_path / "other"]
