"""RemoteDesktop portal fallback tests with a fake dbus (no real session)."""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pastex import portal


class FakeRemote:
    """Records the portal calls instead of talking to the desktop."""

    def __init__(self, deny_at=None):
        self.calls = []
        self.deny_at = deny_at

    def _record(self, name, *args):
        self.calls.append((name, *args))

    def CreateSession(self, options):
        self._record("CreateSession", options)
        return "/org/freedesktop/portal/desktop/request/1"

    def SelectDevices(self, session, options):
        self._record("SelectDevices", session, options)
        return "/org/freedesktop/portal/desktop/request/2"

    def Start(self, session, parent, options):
        self._record("Start", session, parent, options)
        return "/org/freedesktop/portal/desktop/request/3"

    def NotifyKeyboardKeycode(self, session, options, keycode, state):
        self._record("NotifyKeyboardKeycode", session, keycode, state)


def _fake_dbus(remote, has_owner=True):
    bus = SimpleNamespace(
        name_has_owner=lambda name: has_owner,
        get_object=lambda bus_name, path: object(),
    )
    return SimpleNamespace(
        SessionBus=lambda: bus,
        Interface=lambda obj, name: remote,
    )


def test_unavailable_without_dbus(monkeypatch):
    monkeypatch.setattr(portal, "_dbus", lambda: None)

    assert portal.available() is False
    ok, detail = portal.send_ctrl_v()
    assert ok is False
    assert "python3-dbus" in detail


def test_available_reports_the_portal_owner(monkeypatch):
    monkeypatch.setattr(portal, "_dbus", lambda: _fake_dbus(FakeRemote()))
    assert portal.available() is True

    monkeypatch.setattr(
        portal,
        "_dbus",
        lambda: SimpleNamespace(
            SessionBus=lambda: SimpleNamespace(
                name_has_owner=lambda name: (_ for _ in ()).throw(OSError("no bus"))
            )
        ),
    )
    assert portal.available() is False


def test_full_sequence_presses_ctrl_v(monkeypatch):
    portal.reset_session()
    remote = FakeRemote()
    monkeypatch.setattr(portal, "_dbus", lambda: _fake_dbus(remote))
    monkeypatch.setattr(
        portal,
        "_wait_for_response",
        lambda dbus, bus, path, timeout: (0, {"session_handle": "/session/7"}),
    )

    ok, detail = portal.send_ctrl_v()

    assert ok is True
    assert "portal" in detail
    names = [call[0] for call in remote.calls]
    assert names == [
        "CreateSession",
        "SelectDevices",
        "Start",
        "NotifyKeyboardKeycode",
        "NotifyKeyboardKeycode",
        "NotifyKeyboardKeycode",
        "NotifyKeyboardKeycode",
    ]
    keycodes = [(call[2], call[3]) for call in remote.calls[3:]]
    assert keycodes == [(29, 1), (47, 1), (47, 0), (29, 0)]
    assert all(call[1] == "/session/7" for call in remote.calls[3:])
    assert remote.calls[1][2]["types"] == portal.DEVICE_KEYBOARD
    portal.reset_session()


def test_session_is_reused_so_the_prompt_appears_once(monkeypatch):
    portal.reset_session()
    remote = FakeRemote()
    monkeypatch.setattr(portal, "_dbus", lambda: _fake_dbus(remote))
    monkeypatch.setattr(
        portal,
        "_wait_for_response",
        lambda dbus, bus, path, timeout: (0, {"session_handle": "/session/9"}),
    )

    portal.send_ctrl_v()
    portal.send_ctrl_v()

    creates = [call for call in remote.calls if call[0] == "CreateSession"]
    assert len(creates) == 1, "the portal session must be cached"
    presses = [call for call in remote.calls if call[0] == "NotifyKeyboardKeycode"]
    assert len(presses) == 8
    portal.reset_session()


def test_denied_session_is_reported(monkeypatch):
    portal.reset_session()
    remote = FakeRemote()
    monkeypatch.setattr(portal, "_dbus", lambda: _fake_dbus(remote))
    monkeypatch.setattr(
        portal,
        "_wait_for_response",
        lambda dbus, bus, path, timeout: (1, {}),
    )

    ok, detail = portal.send_ctrl_v()

    assert ok is False
    assert "not granted" in detail
    assert not [c for c in remote.calls if c[0] == "NotifyKeyboardKeycode"]
    portal.reset_session()


def test_exception_from_the_portal_is_reported(monkeypatch):
    portal.reset_session()

    class Broken:
        def CreateSession(self, options):
            raise RuntimeError("portal mati")

    monkeypatch.setattr(portal, "_dbus", lambda: _fake_dbus(Broken()))
    monkeypatch.setattr(
        portal,
        "_wait_for_response",
        lambda dbus, bus, path, timeout: (0, {"session_handle": "/session/1"}),
    )

    ok, detail = portal.send_ctrl_v()

    assert ok is False
    assert "portal error" in detail
    portal.reset_session()


def test_reset_session_forgets_the_session(monkeypatch):
    portal.reset_session()
    remote = FakeRemote()
    monkeypatch.setattr(portal, "_dbus", lambda: _fake_dbus(remote))
    monkeypatch.setattr(
        portal,
        "_wait_for_response",
        lambda dbus, bus, path, timeout: (0, {"session_handle": "/session/1"}),
    )

    portal.send_ctrl_v()
    portal.reset_session()
    portal.send_ctrl_v()

    creates = [call for call in remote.calls if call[0] == "CreateSession"]
    assert len(creates) == 2
    portal.reset_session()
