"""Client side of the opt-in Wayland clipboard owner.

``pastex.wlserver`` owns the selection in its own process; this module talks to
it over a pipe:

    app  --{"copy": {...}}-->  helper
    app  <--{"ok": true}-----  helper

Everything here is best-effort. ``start()`` returns False when the helper
cannot come up (no compositor support, no libwayland-client, a crash while
loading), ``copy()`` returns False when the helper does not answer in time, and
the caller keeps using ``wl-copy``. The GUI never blocks on it: the helper is
started in a background thread and every wait has a timeout.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid

from .wlserver import TEXT_MIMES

# How long to wait for the helper to report ready / acknowledge a copy.
START_TIMEOUT = 3.0
COPY_TIMEOUT = 0.6
# Verifying the owner: read back the selection a few times before giving up.
VERIFY_ATTEMPTS = 5
VERIFY_DELAY = 0.05

_EOF = object()


def available() -> bool:
    """Cheap check: a Wayland session and a helper worth spawning."""
    if os.environ.get("WAYLAND_DISPLAY"):
        return True
    return os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"


def helper_command() -> list[str]:
    """Command line for the helper.

    The script path is used instead of ``-m pastex.wlserver`` because a child
    process does not inherit the ``sys.path`` entry that ``bin/pastex`` adds for
    the Debian layout (``/usr/share/pastex``); the module itself only needs the
    standard library.
    """
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wlserver.py")
    return [sys.executable, script, "--serve"]


def spawn_helper() -> "subprocess.Popen[str]":
    """Start the owner helper; stdin/stdout carry the JSON protocol."""
    return subprocess.Popen(
        helper_command(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )


class OwnerClipboard:
    """A running helper, addressed with one request/response per copy."""

    def __init__(
        self,
        spawn=spawn_helper,
        *,
        start_timeout: float = START_TIMEOUT,
        copy_timeout: float = COPY_TIMEOUT,
    ) -> None:
        self._spawn = spawn
        self.start_timeout = start_timeout
        self.copy_timeout = copy_timeout
        self.process = None
        self.error = ""
        self.copies = 0
        self._lines: "queue.Queue" = queue.Queue()
        self._reader: threading.Thread | None = None
        self._lock = threading.Lock()

    # -- lifecycle ----------------------------------------------------
    def start(self) -> bool:
        """Spawn the helper and wait for its ready line."""
        if self.process is not None:
            return True
        try:
            self.process = self._spawn()
        except (OSError, subprocess.SubprocessError) as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self.process = None
            return False
        self._reader = threading.Thread(
            target=self._read_lines, name="pastex-owner-reader", daemon=True
        )
        self._reader.start()
        ready = self._next_line(self.start_timeout)
        if ready is None or ready is _EOF or not ready.get("ready"):
            detail = "" if ready in (None, _EOF) else str(ready.get("error", ""))
            self.error = detail or "helper did not report ready"
            self.stop()
            return False
        return True

    def stop(self) -> None:
        process = self.process
        self.process = None
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.write(json.dumps({"quit": True}) + "\n")
                process.stdin.flush()
        except (OSError, ValueError):  # pragma: no cover - helper already gone
            pass
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:  # pragma: no cover - stubborn helper
            try:
                process.terminate()
                process.wait(timeout=1.0)
            except (OSError, subprocess.TimeoutExpired):
                pass
        except OSError:  # pragma: no cover - already reaped
            pass

    @property
    def running(self) -> bool:
        process = self.process
        return process is not None and process.poll() is None

    # -- copying ------------------------------------------------------
    def copy(self, text: str, mimes: tuple[str, ...] = TEXT_MIMES) -> bool:
        """Hand ``text`` to the helper; True once it owns the selection."""
        if not self.running:
            return False
        payload = {mime: text for mime in mimes}
        with self._lock:
            try:
                self.process.stdin.write(json.dumps({"copy": payload}) + "\n")
                self.process.stdin.flush()
            except (OSError, ValueError) as exc:
                self.error = f"{type(exc).__name__}: {exc}"
                return False
            reply = self._next_line(self.copy_timeout)
        if reply is None or reply is _EOF or not reply.get("ok"):
            if reply is _EOF:
                self.error = "the helper exited"
            elif isinstance(reply, dict):
                self.error = str(reply.get("error", "copy failed"))
            return False
        self.copies += 1
        return True

    # -- plumbing -----------------------------------------------------
    def _read_lines(self) -> None:
        stream = self.process.stdout if self.process is not None else None
        try:
            while stream is not None:
                line = stream.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    self._lines.put(json.loads(line))
                except ValueError:
                    continue
        except (OSError, ValueError):  # pragma: no cover - helper died
            pass
        finally:
            self._lines.put(_EOF)

    def _next_line(self, timeout: float):
        """Next protocol message, ``None`` on timeout, ``_EOF`` when it exits."""
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            remaining = deadline - time.monotonic()
            try:
                return self._lines.get(timeout=max(0.0, remaining))
            except queue.Empty:
                return None


def verify(
    owner: OwnerClipboard,
    reader,
    restore=None,
    *,
    attempts: int = VERIFY_ATTEMPTS,
    delay: float = VERIFY_DELAY,
    nonce_factory=None,
) -> tuple[bool, str]:
    """Prove the owner really serves the selection before trusting it.

    Owning the clipboard is not the same as serving it: a compositor that
    rejects the source, or a broken protocol table, leaves an owner that looks
    healthy and pastes nothing. Copy a nonce, read it back with the normal
    reader, then put the user's clipboard back.
    """
    previous = None
    try:
        previous = reader()
    except Exception:  # noqa: BLE001 - a failed pre-read must not block the test
        previous = None
    nonce = f"pastex-owner-{(nonce_factory or uuid.uuid4)().hex}"
    if not owner.copy(nonce):
        return False, owner.error or "the helper refused the copy"
    seen = None
    for _ in range(max(1, attempts)):
        try:
            seen = reader()
        except Exception:  # noqa: BLE001 - keep trying until the deadline
            seen = None
        if seen == nonce:
            break
        time.sleep(delay)
    ok = seen == nonce
    if previous:
        try:
            (restore or (lambda _text: None))(previous)
        except Exception:  # noqa: BLE001 - best effort
            pass
    return ok, "" if ok else "the selection was not readable back"
