"""Tests for the Wayland clipboard owner helper (pastex/wlserver.py).

What is covered here needs no compositor: the protocol table (opcode plus
signature, matched against ext-data-control-v1), marshalling against a fake
libwayland, payload writes to an fd, and the helper JSON protocol. The real
ctypes calls only run on a user machine, and their failures always fall back
to `wl-copy` (see pastex/wlowner.py).
"""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pastex import wlserver


# --- tabel protokol -------------------------------------------------------
def test_interface_tables_match_ext_data_control_v1():
    """Opcodes and signatures must match the protocol XML."""
    expected = {
        wlserver.MANAGER_INTERFACE: {
            "methods": [
                ("create_data_source", "n"),
                ("get_data_device", "no"),
                ("destroy", ""),
            ],
            "events": [],
        },
        wlserver.SOURCE_INTERFACE: {
            "methods": [("offer", "s"), ("destroy", "")],
            "events": [("send", "sh"), ("cancelled", "")],
        },
        wlserver.DEVICE_INTERFACE: {
            "methods": [
                ("set_selection", "?o"),
                ("destroy", ""),
                ("set_primary_selection", "?o"),
            ],
            "events": [
                ("data_offer", "n"),
                ("selection", "?o"),
                ("finished", ""),
                ("primary_selection", "?o"),
            ],
        },
        wlserver.OFFER_INTERFACE: {
            "methods": [("receive", "sh"), ("destroy", "")],
            "events": [("offer", "s")],
        },
    }
    for name, spec in expected.items():
        actual = wlserver._SPECS[name]
        assert [(method[0], method[1]) for method in actual["methods"]] == spec["methods"]
        assert [(event[0], event[1]) for event in actual["events"]] == spec["events"]


def test_core_interface_tables_match_wayland_xml():
    """wl_registry and wl_seat: the only core protocol pieces used."""
    registry = wlserver._SPECS[wlserver.REGISTRY_INTERFACE]
    assert [method[:2] for method in registry["methods"]] == [("bind", "usun")]
    assert [event[:2] for event in registry["events"]] == [
        ("global", "usu"),
        ("global_remove", "u"),
    ]

    seat = wlserver._SPECS[wlserver.SEAT_INTERFACE]
    assert [event[:2] for event in seat["events"]] == [
        ("capabilities", "u"),
        ("name", "s"),
    ]


def test_interface_structs_are_built_and_cross_linked():
    keep: list = []
    interfaces = wlserver.build_interfaces(keep)

    manager = interfaces[wlserver.MANAGER_INTERFACE]
    assert manager.name == b"ext_data_control_manager_v1"
    assert (manager.method_count, manager.event_count) == (3, 0)

    # create_data_source(0) membawa pointer ke ext_data_control_source_v1.
    create = manager.methods[0]
    assert create.name == b"create_data_source"
    assert create.signature == b"n"
    assert create.types[0].contents.name == b"ext_data_control_source_v1"

    # get_data_device(1) = device + seat.
    get_device = manager.methods[1]
    assert get_device.signature == b"no"
    assert get_device.types[0].contents.name == b"ext_data_control_device_v1"
    assert get_device.types[1].contents.name == b"wl_seat"

    # offer("s") has no object argument -> NULL pointer.
    source = interfaces[wlserver.SOURCE_INTERFACE]
    offer = source.methods[0]
    assert not bool(offer.types[0])
    send = source.events[0]
    assert send.signature == b"sh"

    device = interfaces[wlserver.DEVICE_INTERFACE]
    assert (device.method_count, device.event_count) == (3, 4)
    assert device.methods[0].signature == b"?o"


# --- libwayland tiruan ----------------------------------------------------
class FakeLibrary:
    def __init__(self):
        self.calls: list[tuple] = []
        self.listeners: list[tuple] = []
        self._next = 100

    @staticmethod
    def _plain(value):
        return value.value if hasattr(value, "value") else value

    def wl_display_connect(self, _name):
        return 7

    def wl_display_get_fd(self, _display):
        return 9

    def wl_proxy_add_listener(self, proxy, implementation, _data):
        self.listeners.append((proxy, implementation))
        return 0

    def wl_proxy_marshal_array(self, proxy, opcode, args):
        self.calls.append(("marshal", self._plain(proxy), opcode, args))
        return None

    def wl_proxy_marshal_array_constructor(self, proxy, opcode, args, interface):
        self._next += 1
        name = interface.contents.name.decode() if interface else ""
        self.calls.append(("construct", self._plain(proxy), opcode, name))
        return self._next

    def wl_display_roundtrip(self, _display):
        return 0

    def wl_display_flush(self, _display):
        return 0

    # used by wait(); tested separately
    def wl_display_prepare_read(self, _display):
        return 0

    def wl_display_read_events(self, _display):
        return 0

    def wl_display_cancel_read(self, _display):
        return 0

    def wl_display_dispatch_pending(self, _display):
        return 0

    def wl_display_disconnect(self, _display):
        return None


def make_connection():
    connection = wlserver.Connection(FakeLibrary())
    # Seed an initial id so the marshalling table is complete without a compositor.
    connection.display = 7
    connection.registry = 50
    connection.manager = 51
    connection.seat = 52
    connection.device = 53
    return connection


def test_constructor_marshalling_names_the_new_interface():
    connection = make_connection()
    source = connection._construct(
        connection.manager,
        wlserver.MANAGER_CREATE_DATA_SOURCE,
        "n",
        [],
        wlserver.SOURCE_INTERFACE,
    )
    assert source == 101
    assert connection.lib.calls == [
        ("construct", 51, 0, "ext_data_control_source_v1")
    ]
    connection.close()


def test_offer_marshals_the_mime_string():
    connection = make_connection()
    connection.source = 60
    connection._request(connection.source, wlserver.SOURCE_OFFER, "s", ["text/plain"])
    kind, proxy, opcode, args = connection.lib.calls[-1]
    assert (kind, proxy, opcode) == ("marshal", 60, 0)
    assert args[0].s == b"text/plain"
    connection.close()


def test_set_selection_marshals_the_source_object():
    connection = make_connection()
    connection._request(connection.device, wlserver.DEVICE_SET_SELECTION, "?o", [60])
    kind, proxy, opcode, args = connection.lib.calls[-1]
    assert (kind, proxy, opcode) == ("marshal", 53, 0)
    assert args[0].o == 60
    connection.close()


def test_set_selection_accepts_null_for_clearing():
    connection = make_connection()
    connection._request(connection.device, wlserver.DEVICE_SET_SELECTION, "?o", [None])
    assert connection.lib.calls[-1][3][0].o is None
    connection.close()


def test_copy_offers_every_mime_and_sets_the_new_selection():
    connection = make_connection()
    connection.copy({"text/plain;charset=utf-8": "halo", "text/plain": "halo"})

    source = connection.source
    offers = [call for call in connection.lib.calls if call[1] == source]
    assert [call[3][0].s for call in offers] == [
        b"text/plain;charset=utf-8",
        b"text/plain",
    ]
    selection = [
        call for call in connection.lib.calls if call[1] == connection.device
    ]
    assert len(selection) == 1
    assert selection[0][2] == wlserver.DEVICE_SET_SELECTION
    assert selection[0][3][0].o == source
    assert connection.payload == {
        "text/plain;charset=utf-8": "halo",
        "text/plain": "halo",
    }

    # Next copy: the old source is destroyed once the new one is in use.
    previous = source
    connection.lib.calls.clear()
    connection.copy({"text/plain;charset=utf-8": "second"})
    destroyed = [call for call in connection.lib.calls if call[1] == previous]
    assert len(destroyed) == 1
    assert destroyed[0][2] == wlserver.SOURCE_DESTROY
    assert connection.source != previous


def test_copy_with_empty_types_still_owns_a_valid_selection():
    connection = make_connection()
    connection.copy({})
    assert connection.payload == {wlserver.TEXT_MIMES[0]: ""}
    connection.close()


def test_send_event_writes_the_payload_and_closes_the_fd():
    connection = make_connection()
    connection.payload = {"text/plain;charset=utf-8": "halo dunia"}
    read_fd, write_fd = os.pipe()

    connection._on_send(None, 60, b"text/plain;charset=utf-8", write_fd)

    assert os.read(read_fd, 4096) == b"halo dunia"
    os.close(read_fd)
    with pytest.raises(OSError):
        os.close(write_fd)  # the helper already closed it
    assert connection.served == 1
    connection.close()


def test_send_event_answers_any_offered_text_mime():
    connection = make_connection()
    connection.payload = {"text/plain": "body"}
    read_fd, write_fd = os.pipe()

    connection._on_send(None, 60, b"UTF8_STRING", write_fd)

    assert os.read(read_fd, 4096) == b"body"
    os.close(read_fd)
    connection.close()


def test_send_event_survives_a_closed_reader():
    connection = make_connection()
    connection.payload = {"text/plain;charset=utf-8": "body"}
    read_fd, write_fd = os.pipe()
    os.close(read_fd)

    connection._on_send(None, 60, b"text/plain;charset=utf-8", write_fd)  # must not raise
    connection.close()


def test_open_reports_a_compositor_without_the_manager():
    class NoManager(wlserver.Connection):
        def _roundtrip(self):
            self._on_global(None, None, 3, b"wl_seat", 1)

    connection = NoManager(FakeLibrary())
    with pytest.raises(wlserver.OwnerError, match=wlserver.MANAGER_INTERFACE):
        connection.open()
    connection.close()


def test_open_reports_a_compositor_without_a_seat():
    class NoSeat(wlserver.Connection):
        def _roundtrip(self):
            self._on_global(None, None, 4, wlserver.MANAGER_INTERFACE.encode(), 1)

    connection = NoSeat(FakeLibrary())
    with pytest.raises(wlserver.OwnerError, match="wl_seat"):
        connection.open()
    connection.close()


def test_open_collects_globals_and_binds_them():
    class OneRoundtrip(wlserver.Connection):
        def _roundtrip(self):
            if not self._manager_global:
                self._on_global(None, None, 4, wlserver.MANAGER_INTERFACE.encode(), 1)
                self._on_global(None, None, 5, b"wl_seat", 8)

    connection = OneRoundtrip(FakeLibrary())
    connection.open()
    binds = [call for call in connection.lib.calls if call[0] == "construct"]
    assert ("construct", connection.registry, 0, wlserver.MANAGER_INTERFACE) in binds
    assert ("construct", connection.registry, 0, "wl_seat") in binds
    assert connection.device == connection.lib._next  # the device is built last
    connection.close()


def test_library_reports_a_missing_libwayland():
    with pytest.raises(wlserver.OwnerError, match="libwayland-client"):
        wlserver.Library("/nonexistent/libwayland-client.so")


# --- protokol helper ------------------------------------------------------
class FakeConnection:
    def __init__(self, fail=None):
        self.fail = fail
        self.copies: list[dict] = []
        self.woken = 0
        self.closed = 0
        self.served = 0

    def open(self):
        if self.fail:
            raise wlserver.OwnerError(self.fail)

    def copy(self, payload):
        self.copies.append(payload)
        self.served += 1

    def wait(self, timeout=0.2, commands=None):
        self.commands = commands
        self._drain()

    def wake(self):
        self.woken += 1
        self._drain()

    def _drain(self):
        commands = getattr(self, "commands", None)
        if commands is None:
            return
        while True:
            try:
                payload, reply = commands.get_nowait()
            except Exception:  # noqa: BLE001 - empty queue
                return
            self.copy(payload)
            reply({"ok": True, "id": self.served})

    def close(self):
        self.closed += 1


def run_serve(lines, fail=None):
    connection = FakeConnection(fail)
    out = io.StringIO()
    code = wlserver.serve(
        io.StringIO("".join(json.dumps(line) + "\n" for line in lines)),
        out,
        connection_factory=lambda: connection,
    )
    return code, [json.loads(line) for line in out.getvalue().splitlines()], connection


def test_serve_reports_ready_and_answers_a_copy():
    code, replies, connection = run_serve(
        [{"copy": {"text/plain;charset=utf-8": "halo"}}, {"quit": True}]
    )
    assert code == 0
    assert replies[0] == {"ready": True}
    assert replies[1]["ok"] is True
    assert connection.copies == [{"text/plain;charset=utf-8": "halo"}]
    assert connection.closed == 1


def test_serve_reports_why_the_helper_cannot_start():
    code, replies, connection = run_serve([{"quit": True}], fail="no compositor")
    assert code == 1
    assert replies == [{"ready": False, "error": "OwnerError: no compositor"}]
    assert connection.closed == 0


def test_serve_ignores_garbage_and_blank_lines():
    code, replies, _connection = run_serve(["", "bukan json", {"quit": True}])
    assert code == 0
    assert replies == [{"ready": True}]


def test_main_requires_the_serve_flag(capsys):
    assert wlserver.main([]) == 2
    assert "--serve" in capsys.readouterr().err


def test_real_libwayland_reports_a_missing_compositor(monkeypatch):
    """Real ctypes calls: correct symbols and a clean failure.

    Skipped when libwayland-client is not installed (e.g. a sandbox with no
    compositor); on a user machine or CI this touches the real library.
    """
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    try:
        library = wlserver.Library()
    except wlserver.OwnerError:
        pytest.skip("libwayland-client is not installed")

    connection = wlserver.Connection(library)
    with pytest.raises(wlserver.OwnerError, match="could not connect"):
        connection.open()
    connection.close()
