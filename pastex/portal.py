"""Send Ctrl+V through the desktop portal instead of /dev/uinput.

Automatic paste normally goes through ``ydotool``, which needs write access to
``/dev/uinput`` (the ``pastex`` group and its udev rule) or root. The
``org.freedesktop.portal.RemoteDesktop`` interface can inject the same
keystroke with **no root and no device permissions**: the desktop asks the user
once per session and then tells the compositor to press the keys.

This module is a last-resort fallback used only when ``ydotool`` failed (for
example on a machine where the udev rule was never installed), so a broken
portal can never make the normal path worse:

    session = CreateSession()          -> request handle
    SelectDevices(session, KEYBOARD)   -> request handle
    Start(session)                     -> request handle, user consent here
    NotifyKeyboardKeycode(session, 29, 1)  # Ctrl press
    NotifyKeyboardKeycode(session, 47, 1)  # V press
    NotifyKeyboardKeycode(session, 47, 0)  # V release
    NotifyKeyboardKeycode(session, 29, 0)  # Ctrl release

Keycodes are Linux evdev codes (29 = LEFTCTRL, 47 = V), the same numbers
``ydotool key`` takes. The session is cached for the lifetime of the process,
so the consent prompt appears once and later clicks are silent.
"""

from __future__ import annotations

import os

BUS_NAME = "org.freedesktop.portal.Desktop"
OBJECT_PATH = "/org/freedesktop/portal/desktop"
REMOTE_DESKTOP = "org.freedesktop.portal.RemoteDesktop"
REQUEST = "org.freedesktop.portal.Request"

DEVICE_KEYBOARD = 1
CTRL = 29
V = 47
PRESSED = 1
RELEASED = 0

_sequence = [
    (CTRL, PRESSED),
    (V, PRESSED),
    (V, RELEASED),
    (CTRL, RELEASED),
]

_session_handle: str | None = None
_token = 0


def reset_session() -> None:
    """Forget the cached session (used by tests and by ``--check``)."""
    global _session_handle, _token
    _session_handle = None
    _token = 0


def _next_token() -> str:
    global _token
    _token += 1
    return f"pastex{os.getpid()}_{_token}"


def _dbus():
    """Return the dbus module, or None when python3-dbus is not installed."""
    try:
        import dbus  # noqa: PLC0415 - optional dependency
    except ImportError:
        return None
    return dbus


def available() -> bool:
    """True when python3-dbus is installed and a session bus is reachable."""
    dbus = _dbus()
    if dbus is None:
        return False
    try:
        bus = dbus.SessionBus()
        return bool(bus.name_has_owner(BUS_NAME))
    except Exception:  # noqa: BLE001 - any bus problem means "not available"
        return False


def _wait_for_response(dbus, bus, request_path, timeout: float):
    """Wait for the portal's Response signal and return (code, results)."""
    received: list[tuple[int, dict]] = []

    def on_response(code, results):
        received.append((int(code), dict(results)))

    bus.add_signal_receiver(
        on_response,
        signal_name="Response",
        dbus_interface=REQUEST,
        path=request_path,
    )
    try:
        from dbus.mainloop.glib import DBusGMainLoop  # noqa: PLC0415 - optional
        from gi.repository import GLib  # noqa: PLC0415 - optional
    except ImportError:
        return None
    DBusGMainLoop(set_as_default=True)
    loop = GLib.MainLoop()
    deadline = GLib.timeout_add(int(timeout * 1000), loop.quit)
    GLib.timeout_add(10, _poll(received, loop))
    loop.run()
    GLib.source_remove(deadline)
    return received[0] if received else None


def _poll(received, loop):
    def check() -> bool:
        if received:
            loop.quit()
            return False
        return True

    return check


def _call_and_wait(dbus, bus, method, *args, timeout: float = 30.0):
    """Call a portal method that answers with a Request object path."""
    request_path = str(method(*args))
    return _wait_for_response(dbus, bus, request_path, timeout)


def _create_session(dbus, bus, remote) -> str | None:
    global _session_handle
    if _session_handle is not None:
        return _session_handle
    options = {
        "handle_token": _next_token(),
        "session_handle_token": _next_token(),
    }
    reply = _call_and_wait(dbus, bus, remote.CreateSession, options)
    if not reply or reply[0] != 0:  # pragma: no cover - needs a real portal
        return None
    session = reply[1].get("session_handle")
    if not session:  # pragma: no cover - defensive
        return None
    devices = {"types": DEVICE_KEYBOARD, "handle_token": _next_token()}
    reply = _call_and_wait(dbus, bus, remote.SelectDevices, session, devices)
    if not reply or reply[0] != 0:  # pragma: no cover - needs a real portal
        return None
    reply = _call_and_wait(
        dbus, bus, remote.Start, session, "", {"handle_token": _next_token()}
    )
    if not reply or reply[0] != 0:  # pragma: no cover - user dismissed the prompt
        return None
    _session_handle = session
    return session


def send_ctrl_v(timeout: float = 30.0) -> tuple[bool, str]:
    """Press Ctrl+V through the portal; return ``(ok, detail)``."""
    dbus = _dbus()
    if dbus is None:
        return False, "python3-dbus is not installed"
    try:
        bus = dbus.SessionBus()
        portal = bus.get_object(BUS_NAME, OBJECT_PATH)
        remote = dbus.Interface(portal, REMOTE_DESKTOP)
        session = _create_session(dbus, bus, remote)
        if not session:
            return False, "the desktop portal session was not granted"
        for keycode, state in _sequence:
            remote.NotifyKeyboardKeycode(
                session, {"handle_token": _next_token()}, keycode, state
            )
    except Exception as exc:  # noqa: BLE001 - the portal is a fallback
        return False, f"portal error: {exc}"
    return True, "Automatic paste sent through the desktop portal."
