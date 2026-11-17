"""One Pastex per user, plus a tiny socket a second launch can talk to.

Launching Pastex from the applications menu used to start another Tk process
(~200-400 ms) and could leave two dock entries. Now the first process takes an
``flock`` on ``$XDG_RUNTIME_DIR/pastex/pastex.lock`` and listens on a Unix
socket next to it; a later launch sends ``show`` or ``pick`` and exits right
away, which is effectively instant.

The protocol is one JSON object per line::

    {"command": "show"}       raise the main window
    {"command": "pick"}       open the snippet picker popup
    {"command": "quit"}       close the running instance

Replies are ``{"ok": true}`` or ``{"ok": false, "error": "..."}``.
"""

from __future__ import annotations

import fcntl
import json
import os
import socket
import tempfile
import threading
from pathlib import Path

SOCKET_NAME = "pastex.sock"
LOCK_NAME = "pastex.lock"

COMMANDS = ("show", "pick", "quit")


def runtime_dir() -> Path:
    """Directory holding the lock and the socket (per user, per boot)."""
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    return Path(base) / "pastex"


class SingleInstance:
    """Own the instance slot and answer commands from later launches."""

    def __init__(self, on_command, *, directory=None) -> None:
        self.directory = Path(directory) if directory is not None else runtime_dir()
        self.socket_path = self.directory / SOCKET_NAME
        self.lock_path = self.directory / LOCK_NAME
        self.on_command = on_command
        self._lock_file = None
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def owns_the_slot(self) -> bool:
        return self._lock_file is not None

    def claim(self) -> bool:
        """Try to become the running instance; False when another one holds it."""
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            handle = open(self.lock_path, "a+", encoding="utf-8")
        except OSError:
            return False
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._lock_file = handle
        return True

    def serve(self) -> bool:
        """Listen for commands in a daemon thread; False if the socket failed."""
        if self._socket is not None:
            return True
        try:
            stale = self.socket_path
            if stale.exists():
                stale.unlink()
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(str(self.socket_path))
            server.listen(8)
            server.settimeout(0.5)
        except OSError:
            return False
        self._socket = server
        self._thread = threading.Thread(
            target=self._run, name="pastex-instance", daemon=True
        )
        self._thread.start()
        return True

    def _run(self) -> None:  # pragma: no cover - exercised through the client
        while not self._stop.is_set():
            server = self._socket
            if server is None:
                return
            try:
                connection, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with connection:
                self.handle_connection(connection)

    def handle_connection(self, connection: socket.socket) -> None:
        """Read one command, hand it to the callback and answer."""
        try:
            payload = connection.recv(1024)
        except OSError:
            return
        reply = self.process(payload)
        try:
            connection.sendall(json.dumps(reply).encode() + b"\n")
        except OSError:  # pragma: no cover - client already gone
            pass

    def process(self, payload: bytes) -> dict:
        """Parse one request and run it (separate from the socket for tests)."""
        try:
            request = json.loads(payload.decode() or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"ok": False, "error": "invalid request"}
        command = str(request.get("command", ""))
        if command not in COMMANDS:
            return {"ok": False, "error": f"unknown command: {command}"}
        try:
            self.on_command(command)
        except Exception as exc:  # noqa: BLE001 - never break the server loop
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def stop(self) -> None:
        self._stop.set()
        server = self._socket
        self._socket = None
        if server is not None:
            try:
                server.close()
            except OSError:  # pragma: no cover - already closed
                pass
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=1.0)
        try:
            if self.socket_path.exists():
                self.socket_path.unlink()
        except OSError:  # pragma: no cover - read-only runtime dir
            pass
        if self._lock_file is not None:
            try:
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            except OSError:  # pragma: no cover - already released
                pass
            self._lock_file.close()
            self._lock_file = None


def send_command(command: str, *, directory=None, timeout: float = 0.6) -> dict | None:
    """Ask the running instance to run ``command``; None when it is not there."""
    if command not in COMMANDS:
        raise ValueError(f"unknown command: {command}")
    path = (Path(directory) if directory is not None else runtime_dir()) / SOCKET_NAME
    if not path.exists():
        return None
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(str(path))
        client.sendall(json.dumps({"command": command}).encode() + b"\n")
        reply = b""
        while b"\n" not in reply:
            chunk = client.recv(1024)
            if not chunk:
                break
            reply += chunk
    except OSError:
        return None
    finally:
        client.close()
    try:
        return json.loads(reply.decode() or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):  # pragma: no cover - bad peer
        return None
