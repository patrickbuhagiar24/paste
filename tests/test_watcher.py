"""Uji watcher inotify: parsing event, koalisi, dan jalur gagal yang aman."""

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


def test_events_are_coalesced(tmp_path):
    calls = []
    watcher = DirectoryWatcher([tmp_path], lambda: calls.append(1), coalesce=60)

    for _ in range(5):
        watcher.handle_events([(1, watcher_module.IN_MODIFY, "a.txt")])

    assert calls == [1], "satu burst tulisan hanya memicu satu refresh"
    assert watcher.changes == 1


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


def test_start_fails_without_inotify(tmp_path, monkeypatch):
    monkeypatch.setattr(watcher_module, "open_inotify", lambda: -1)
    watcher = DirectoryWatcher([tmp_path], lambda: None)

    assert watcher.start() is False
    assert watcher._thread is None


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
