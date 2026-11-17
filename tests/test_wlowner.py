"""Uji klien pemilik clipboard Wayland (pastex/wlowner.py).

Helper-nya sendiri adalah proses terpisah; di sini subprocess diganti tiruan
supaya protokol JSON, timeout, deteksi kematian helper, dan self-test bisa
diuji tanpa kompositor.
"""

from __future__ import annotations

import io
import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pastex import wlowner


class BlockingStdout:
    """Stdout tiruan yang tidak pernah mengirim baris (untuk uji timeout)."""

    def readline(self):  # pragma: no cover - dipakai lewat thread
        time.sleep(5)
        return ""


class FakeStream(io.StringIO):
    def __init__(self, lines=(), block=False):
        super().__init__("".join(line + "\n" for line in lines))
        self.block = block

    def readline(self, *args):
        if self.block:  # pragma: no cover - dipakai lewat thread
            time.sleep(5)
            return ""
        return super().readline(*args)


class FakeProcess:
    def __init__(self, lines=(), *, alive=True, block=False):
        self.stdin = io.StringIO()
        self.stdout = FakeStream(lines, block=block)
        self._alive = alive
        self.terminated = 0
        self.waited = 0

    def written(self) -> list[dict]:
        return [json.loads(line) for line in self.stdin.getvalue().splitlines()]

    def poll(self):
        return None if self._alive else 1

    def wait(self, timeout=None):
        self.waited += 1
        return 0

    def terminate(self):  # pragma: no cover - hanya untuk helper keras kepala
        self.terminated += 1
        self._alive = False

    def kill(self):  # pragma: no cover
        self._alive = False


def owner_with(process=None, **kwargs):
    return wlowner.OwnerClipboard(spawn=lambda: process or FakeProcess(), **kwargs)


# --- lifecycle ------------------------------------------------------------
def test_start_waits_for_the_ready_line():
    process = FakeProcess([f'{{"ready": true}}'])
    owner = owner_with(process)
    assert owner.start() is True
    assert owner.running is True
    owner.stop()


def test_start_reports_the_helper_error():
    process = FakeProcess(['{"ready": false, "error": "no compositor"}'])
    owner = owner_with(process)
    assert owner.start() is False
    assert "no compositor" in owner.error
    assert owner.process is None


def test_start_reports_a_helper_that_exits_immediately():
    process = FakeProcess([])  # stdout langsung EOF
    owner = owner_with(process)
    assert owner.start() is False
    assert "ready" in owner.error


def test_start_reports_a_spawn_failure():
    def boom():
        raise FileNotFoundError("python3 hilang")

    owner = wlowner.OwnerClipboard(spawn=boom)
    assert owner.start() is False
    assert "FileNotFoundError" in owner.error


def test_start_times_out_without_a_ready_line():
    owner = owner_with(FakeProcess(block=True), start_timeout=0.05)
    assert owner.start() is False
    assert owner.process is None


def test_stop_asks_the_helper_to_quit():
    process = FakeProcess(['{"ready": true}'])
    owner = owner_with(process)
    owner.start()
    owner.stop()
    assert process.written()[-1] == {"quit": True}
    assert process.waited == 1
    assert owner.process is None


def test_running_follows_the_helper_process():
    process = FakeProcess(['{"ready": true}'])
    owner = owner_with(process)
    owner.start()
    assert owner.running is True
    process._alive = False
    assert owner.running is False


# --- copying --------------------------------------------------------------
def test_copy_sends_every_text_mime_and_counts_the_copy():
    process = FakeProcess(['{"ready": true}', '{"ok": true, "id": 1}'])
    owner = owner_with(process)
    owner.start()

    assert owner.copy("halo") is True
    request = process.written()[0]
    assert request["copy"] == {
        "text/plain;charset=utf-8": "halo",
        "text/plain": "halo",
        "UTF8_STRING": "halo",
    }
    assert owner.copies == 1
    owner.stop()


def test_copy_times_out_and_reports_failure():
    process = FakeProcess(['{"ready": true}'], block=True)
    owner = wlowner.OwnerClipboard(
        spawn=lambda: process, start_timeout=5, copy_timeout=0.05
    )
    # start() butuh baris ready; ambil dari antrean sebelum stdout memblokir.
    owner._lines.put({"ready": True})
    owner.process = process
    assert owner.copy("halo") is False
    assert owner.copies == 0


def test_copy_fails_when_the_helper_explicitly_refuses():
    process = FakeProcess(['{"ready": true}', '{"ok": false, "error": "cancelled"}'])
    owner = owner_with(process)
    owner.start()
    assert owner.copy("halo") is False
    assert owner.error == "cancelled"


def test_copy_fails_after_the_helper_died():
    process = FakeProcess(['{"ready": true}'])
    owner = owner_with(process)
    owner.start()
    process._alive = False

    assert owner.copy("halo") is False


def test_copy_reports_a_broken_pipe():
    process = FakeProcess(['{"ready": true}'])
    owner = owner_with(process)
    owner.start()

    class Broken(io.StringIO):
        def write(self, _text):
            raise BrokenPipeError("pipe")

    process.stdin = Broken()
    assert owner.copy("halo") is False
    assert "BrokenPipeError" in owner.error


# --- self-test ------------------------------------------------------------
class ScriptedOwner:
    def __init__(self, accepts=True, error=""):
        self.accepts = accepts
        self.error = error
        self.copied: list[str] = []

    def copy(self, text, mimes=None):
        self.copied.append(text)
        return self.accepts


class ServingOwner:
    """Owner tiruan yang benar-benar "menyimpan" teksnya, seperti kompositor."""

    def __init__(self, store):
        self.store = store
        self.error = ""

    def copy(self, text, mimes=None):
        self.store["value"] = text
        return True


def test_verify_accepts_an_owner_that_serves_the_nonce():
    store = {"value": ""}
    ok, detail = wlowner.verify(
        ServingOwner(store), lambda: store["value"], delay=0
    )
    assert ok is True
    assert detail == ""
    assert (store["value"] == "" or store["value"].startswith("pastex-owner-"))


def test_verify_polls_until_the_selection_is_readable():
    store = {"value": ""}
    reads = {"count": 0}

    def reader():
        reads["count"] += 1
        # Baris pertama belum siap: kompositor butuh waktu.
        return "" if reads["count"] == 1 else store["value"]

    ok, _detail = wlowner.verify(ServingOwner(store), reader, attempts=3, delay=0)
    assert ok is True
    assert reads["count"] == 2


def test_verify_rejects_an_owner_that_never_serves_it():
    owner = ScriptedOwner()
    ok, detail = wlowner.verify(owner, lambda: "teks lama", attempts=3, delay=0)
    assert ok is False
    assert "not readable" in detail


def test_verify_reports_when_the_owner_refuses_the_copy():
    owner = ScriptedOwner(accepts=False, error="compositor said no")
    ok, detail = wlowner.verify(owner, lambda: "", delay=0)
    assert ok is False
    assert detail == "compositor said no"


def test_verify_restores_the_previous_clipboard():
    owner = ScriptedOwner()
    restored: list[str] = []
    seen = {"value": "isi lama"}

    def reader():
        return seen["value"]

    def copier(text, mimes=None):
        seen["value"] = text
        return True

    owner.copy = copier
    ok, _detail = wlowner.verify(
        owner, reader, restore=restored.append, attempts=1, delay=0
    )
    assert ok is True
    assert restored == ["isi lama"]


def test_verify_does_not_restore_an_empty_clipboard():
    store = {"value": ""}
    restored: list[str] = []
    ok, _detail = wlowner.verify(
        ServingOwner(store),
        lambda: store["value"],
        restore=restored.append,
        attempts=1,
        delay=0,
    )
    assert ok is True
    assert restored == []


def test_verify_survives_a_failing_reader_and_restorer():
    owner = ScriptedOwner()

    def reader():
        raise OSError("no clipboard tool")

    ok, detail = wlowner.verify(
        owner,
        reader,
        restore=lambda _text: (_ for _ in ()).throw(OSError("nope")),
        attempts=2,
        delay=0,
    )
    assert ok is False
    assert "not readable" in detail


# --- availability ---------------------------------------------------------
def test_available_follows_the_session(monkeypatch):
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    assert wlowner.available() is False

    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    assert wlowner.available() is True

    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("XDG_SESSION_TYPE", "tty")
    assert wlowner.available() is True


def test_helper_command_runs_the_server_script():
    command = wlowner.helper_command()
    assert command[0] == sys.executable
    assert command[1].endswith("wlserver.py")
    assert Path(command[1]).is_file()
    assert command[2:] == ["--serve"]
