"""Own the Wayland clipboard from a separate helper process (opt-in).

``wl-copy`` forks, connects to the compositor, writes the data and stays alive:
5-20 ms for every copy, on the click path. The data-control protocol
(``ext-data-control-v1``, implemented by mutter/GNOME 47+) lets a client own the
selection directly, so a copy becomes a couple of marshalled messages.

This module is the *helper*: a tiny process that connects to the compositor,
keeps the clipboard served, and answers copy requests on stdin::

    {"copy": {"text/plain;charset=utf-8": "halo"}}   ->  {"ok": true, "id": 3}
    {"quit": true}                                    ->  exits

It is deliberately separate from the GUI. The protocol glue below talks to
libwayland-client through ctypes, which cannot be exercised in CI (no
compositor) and can fail in ways Python cannot catch: a wrong listener array, a
crashed helper. Running it out of process means the worst case is a dead helper:
:mod:`pastex.wlowner` sees EOF, stops offering itself, and the app falls back to
``wl-copy`` exactly as before.

The protocol tables were written against wayland-protocols' generated code for
``ext_data_control_v1``: manager requests ``create_data_source``(0),
``get_data_device``(1), ``destroy``(2); source requests ``offer``(0),
``destroy``(1) and events ``send``(0, ``sh``), ``cancelled``(1); device requests
``set_selection``(0), ``destroy``(1), ``set_primary_selection``(2) and events
``data_offer``(0), ``selection``(1), ``finished``(2), ``primary_selection``(3).
"""

from __future__ import annotations

import ctypes
import json
import os
import select
import sys
import threading
import traceback

# MIME types offered for plain text; GTK apps ask for the first one.
TEXT_MIMES = ("text/plain;charset=utf-8", "text/plain", "UTF8_STRING")

MANAGER_INTERFACE = "ext_data_control_manager_v1"
DEVICE_INTERFACE = "ext_data_control_device_v1"
SOURCE_INTERFACE = "ext_data_control_source_v1"
OFFER_INTERFACE = "ext_data_control_offer_v1"
SEAT_INTERFACE = "wl_seat"
REGISTRY_INTERFACE = "wl_registry"

# Opcodes, in the order the protocol XML declares them.
MANAGER_CREATE_DATA_SOURCE = 0
MANAGER_GET_DATA_DEVICE = 1
SOURCE_OFFER = 0
SOURCE_DESTROY = 1
DEVICE_SET_SELECTION = 0
DISPLAY_GET_REGISTRY = 1
REGISTRY_BIND = 0


class OwnerError(RuntimeError):
    """The helper could not own the selection."""


# --- ctypes bindings ------------------------------------------------------
class _WlMessage(ctypes.Structure):
    pass


class _WlInterface(ctypes.Structure):
    pass


class _WlArgument(ctypes.Union):
    _fields_ = [
        ("i", ctypes.c_int32),
        ("u", ctypes.c_uint32),
        ("f", ctypes.c_int32),
        ("s", ctypes.c_char_p),
        ("o", ctypes.c_void_p),
        ("n", ctypes.c_uint32),
        ("a", ctypes.c_void_p),
        ("h", ctypes.c_int32),
    ]


_InterfacePtr = ctypes.POINTER(_WlInterface)
_ArgumentPtr = ctypes.POINTER(_WlArgument)

_WlMessage._fields_ = [
    ("name", ctypes.c_char_p),
    ("signature", ctypes.c_char_p),
    ("types", ctypes.POINTER(_InterfacePtr)),
]
_WlInterface._fields_ = [
    ("name", ctypes.c_char_p),
    ("version", ctypes.c_int),
    ("method_count", ctypes.c_int),
    ("methods", ctypes.POINTER(_WlMessage)),
    ("event_count", ctypes.c_int),
    ("events", ctypes.POINTER(_WlMessage)),
]

# One entry per argument: the interface pointer for objects, NULL otherwise.
_SPECS: dict[str, dict] = {
    REGISTRY_INTERFACE: {
        "version": 1,
        "methods": [("bind", "usun", (None, None, None, None))],
        "events": [
            ("global", "usu", (None, None, None)),
            ("global_remove", "u", (None,)),
        ],
    },
    SEAT_INTERFACE: {
        "version": 1,
        "methods": [
            ("get_pointer", "n", (None,)),
            ("get_keyboard", "n", (None,)),
            ("get_touch", "n", (None,)),
            ("release", "", ()),
        ],
        # Bound at version 1, so only these two events can arrive. Both need a
        # listener: libwayland refuses to dispatch a NULL one.
        "events": [("capabilities", "u", (None,)), ("name", "s", (None,))],
    },
    MANAGER_INTERFACE: {
        "version": 1,
        "methods": [
            ("create_data_source", "n", (SOURCE_INTERFACE,)),
            ("get_data_device", "no", (DEVICE_INTERFACE, SEAT_INTERFACE)),
            ("destroy", "", ()),
        ],
        "events": [],
    },
    SOURCE_INTERFACE: {
        "version": 1,
        "methods": [("offer", "s", (None,)), ("destroy", "", ())],
        "events": [("send", "sh", (None, None)), ("cancelled", "", ())],
    },
    DEVICE_INTERFACE: {
        "version": 1,
        "methods": [
            ("set_selection", "?o", (SOURCE_INTERFACE,)),
            ("destroy", "", ()),
            ("set_primary_selection", "?o", (SOURCE_INTERFACE,)),
        ],
        "events": [
            ("data_offer", "n", (OFFER_INTERFACE,)),
            ("selection", "?o", (OFFER_INTERFACE,)),
            ("finished", "", ()),
            ("primary_selection", "?o", (OFFER_INTERFACE,)),
        ],
    },
    OFFER_INTERFACE: {
        "version": 1,
        "methods": [("receive", "sh", (None, None)), ("destroy", "", ())],
        "events": [("offer", "s", (None,))],
    },
}


def _fill_message(message: _WlMessage, spec, interfaces, keep: list) -> None:
    """Populate one ``wl_message`` and the ``types`` array it points at."""
    label, signature, argument_interfaces = spec
    message.name = label.encode()
    message.signature = signature.encode()
    keep.extend([message.name, message.signature])
    if not signature:
        message.types = ctypes.cast(None, ctypes.POINTER(_InterfacePtr))
        return
    entries = (_InterfacePtr * len(signature))()
    for index, interface_name in enumerate(argument_interfaces):
        if interface_name is None:
            entries[index] = ctypes.cast(None, _InterfacePtr)
        else:
            pointer = ctypes.pointer(interfaces[interface_name])
            keep.append(pointer)
            entries[index] = pointer
    keep.append(entries)
    message.types = ctypes.cast(entries, ctypes.POINTER(_InterfacePtr))


def build_interfaces(keep: list) -> dict[str, _WlInterface]:
    """Build every ``wl_interface`` this helper needs, cross-linked."""
    interfaces: dict[str, _WlInterface] = {}
    for name, spec in _SPECS.items():
        iface = _WlInterface()
        iface.name = name.encode()
        iface.version = spec["version"]
        keep.append(iface.name)
        interfaces[name] = iface
    for name, spec in _SPECS.items():
        iface = interfaces[name]
        methods = (_WlMessage * max(1, len(spec["methods"])))()
        events = (_WlMessage * max(1, len(spec["events"])))()
        for index, method in enumerate(spec["methods"]):
            _fill_message(methods[index], method, interfaces, keep)
        for index, event in enumerate(spec["events"]):
            _fill_message(events[index], event, interfaces, keep)
        iface.method_count = len(spec["methods"])
        iface.methods = ctypes.cast(methods, ctypes.POINTER(_WlMessage))
        iface.event_count = len(spec["events"])
        iface.events = ctypes.cast(events, ctypes.POINTER(_WlMessage))
        keep.extend([methods, events])
    return interfaces


class Library:
    """The libwayland-client entry points this helper needs, typed once."""

    NAMES = ("libwayland-client.so.0", "libwayland-client.so")

    def __init__(self, path: str | None = None):
        loaded = None
        for candidate in ((path,) if path else self.NAMES):
            try:
                loaded = ctypes.CDLL(candidate)
                break
            except OSError:
                continue
        if loaded is None:
            raise OwnerError("libwayland-client is not installed")
        self._declare(loaded)
        self.lib = loaded

    def _declare(self, lib) -> None:
        lib.wl_display_connect.argtypes = [ctypes.c_char_p]
        lib.wl_display_connect.restype = ctypes.c_void_p
        lib.wl_display_disconnect.argtypes = [ctypes.c_void_p]
        lib.wl_display_disconnect.restype = None
        lib.wl_display_get_fd.argtypes = [ctypes.c_void_p]
        lib.wl_display_get_fd.restype = ctypes.c_int
        for name in (
            "wl_display_dispatch_pending",
            "wl_display_roundtrip",
            "wl_display_flush",
            "wl_display_prepare_read",
            "wl_display_read_events",
        ):
            function = getattr(lib, name)
            function.argtypes = [ctypes.c_void_p]
            function.restype = ctypes.c_int
        lib.wl_proxy_add_listener.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p,
        ]
        lib.wl_proxy_add_listener.restype = ctypes.c_int
        lib.wl_proxy_marshal_array.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            _ArgumentPtr,
        ]
        lib.wl_proxy_marshal_array.restype = None
        lib.wl_proxy_marshal_array_constructor.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            _ArgumentPtr,
            _InterfacePtr,
        ]
        lib.wl_proxy_marshal_array_constructor.restype = ctypes.c_void_p

    def __getattr__(self, item):
        return getattr(self.lib, item)


# Callback prototypes. Kept alive by Connection: a garbage-collected ctypes
# callback becomes a dangling pointer the next time the compositor calls it.
_GLOBAL_CB = ctypes.CFUNCTYPE(
    None,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_uint32,
    ctypes.c_char_p,
    ctypes.c_uint32,
)
_GLOBAL_REMOVE_CB = ctypes.CFUNCTYPE(
    None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32
)
_SEAT_CAPS_CB = ctypes.CFUNCTYPE(
    None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32
)
_SEAT_NAME_CB = ctypes.CFUNCTYPE(
    None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p
)
_SEND_CB = ctypes.CFUNCTYPE(
    None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int
)
_CANCELLED_CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)
_NEW_OFFER_CB = ctypes.CFUNCTYPE(
    None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
)
_OFFER_CB = ctypes.CFUNCTYPE(
    None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
)
_FINISHED_CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)


class Connection:
    """A Wayland connection that owns one selectable data source."""

    def __init__(self, library: Library | None = None):
        self.lib = library or Library()
        self.display = 0
        self.registry = 0
        self.manager = 0
        self.device = 0
        self.seat = 0
        self.source = 0
        self.fd = -1
        self.payload: dict[str, str] = {}
        self.served = 0
        self._keep: list = []
        self._callbacks: list = []
        self._interfaces = build_interfaces(self._keep)
        self._manager_global = 0
        self._seat_global = 0
        self._wake_read, self._wake_write = os.pipe()
        os.set_blocking(self._wake_read, False)

    # -- marshalling --------------------------------------------------
    @staticmethod
    def _tokens(signature: str) -> list[str]:
        """Split a Wayland signature: ``?`` only marks the next type nullable."""
        tokens = []
        for char in signature:
            if char != "?":
                tokens.append(char)
        return tokens

    def _request(self, proxy: int, opcode: int, signature: str, values) -> int:
        """Marshal one request; return the new object id for constructors."""
        tokens = self._tokens(signature)
        args = (_WlArgument * max(1, len(tokens)))()
        remaining = list(values)
        for index, token in enumerate(tokens):
            if token == "n":  # new_id: filled in by the constructor call
                args[index].o = None
                continue
            value = remaining.pop(0)
            if token == "s":
                encoded = value.encode() if isinstance(value, str) else value
                self._keep.append(encoded)
                args[index].s = encoded
            elif token == "u":
                args[index].u = int(value)
            elif token in "o?":
                args[index].o = ctypes.c_void_p(value) if value else None
            else:  # pragma: no cover - no other kinds are marshalled
                raise OwnerError(f"unsupported argument type {token!r}")

        if "n" not in tokens:
            self.lib.wl_proxy_marshal_array(
                ctypes.c_void_p(proxy), opcode, ctypes.cast(args, _ArgumentPtr)
            )
            return 0

        interface = self._pending_interface
        self._pending_interface = None
        if interface is None:  # pragma: no cover - programming error
            raise OwnerError("constructor without an interface")
        iface_ptr = ctypes.pointer(self._interfaces[interface])
        self._keep.append(iface_ptr)
        new_proxy = self.lib.wl_proxy_marshal_array_constructor(
            ctypes.c_void_p(proxy),
            opcode,
            ctypes.cast(args, _ArgumentPtr),
            ctypes.cast(iface_ptr, _InterfacePtr),
        )
        if not new_proxy:
            raise OwnerError(f"could not create {interface}")
        return int(new_proxy)

    _pending_interface: str | None = None

    def _construct(self, proxy: int, opcode: int, signature: str, values, interface):
        self._pending_interface = interface
        try:
            return self._request(proxy, opcode, signature, values)
        finally:
            self._pending_interface = None

    def _listen(self, proxy: int, callbacks) -> None:
        self._callbacks.extend(callbacks)
        implementation = (ctypes.c_void_p * len(callbacks))()
        for index, callback in enumerate(callbacks):
            implementation[index] = ctypes.cast(callback, ctypes.c_void_p)
        self._keep.append(implementation)
        if self.lib.wl_proxy_add_listener(ctypes.c_void_p(proxy), implementation, None) != 0:
            raise OwnerError("could not install a listener")

    # -- display helpers ----------------------------------------------
    def _roundtrip(self) -> None:
        if self.lib.wl_display_roundtrip(ctypes.c_void_p(self.display)) < 0:
            raise OwnerError("the compositor connection was lost")

    def _flush(self) -> None:
        self.lib.wl_display_flush(ctypes.c_void_p(self.display))

    # -- setup --------------------------------------------------------
    def open(self) -> None:
        """Connect, find the globals, and create the data device."""
        display = self.lib.wl_display_connect(None)
        if not display:
            raise OwnerError("could not connect to the Wayland compositor")
        self.display = int(display)
        self.fd = int(self.lib.wl_display_get_fd(ctypes.c_void_p(self.display)))
        self.registry = self._construct(
            self.display, DISPLAY_GET_REGISTRY, "n", [], REGISTRY_INTERFACE
        )
        self._listen(
            self.registry,
            [_GLOBAL_CB(self._on_global), _GLOBAL_REMOVE_CB(self._on_global_remove)],
        )
        self._roundtrip()
        if not self._manager_global:
            raise OwnerError(f"{MANAGER_INTERFACE} is not supported by the compositor")
        if not self._seat_global:
            raise OwnerError("the compositor offers no wl_seat")

        self.manager = self._construct(
            self.registry, REGISTRY_BIND, "usun", [
                self._manager_global,
                MANAGER_INTERFACE,
                self._interfaces[MANAGER_INTERFACE].version,
            ],
            MANAGER_INTERFACE,
        )
        self.seat = self._construct(
            self.registry, REGISTRY_BIND, "usun", [
                self._seat_global,
                SEAT_INTERFACE,
                self._interfaces[SEAT_INTERFACE].version,
            ],
            SEAT_INTERFACE,
        )
        self._listen(
            self.seat,
            [_SEAT_CAPS_CB(self._ignore), _SEAT_NAME_CB(self._ignore)],
        )
        self._roundtrip()

        self.device = self._construct(
            self.manager,
            MANAGER_GET_DATA_DEVICE,
            "no",
            [self.seat],
            DEVICE_INTERFACE,
        )
        self._listen(
            self.device,
            [
                _NEW_OFFER_CB(self._ignore),
                _OFFER_CB(self._ignore),
                _FINISHED_CB(self._ignore),
                _OFFER_CB(self._ignore),
            ],
        )
        self._flush()
        self._roundtrip()

    # -- listeners ----------------------------------------------------
    def _on_global(self, _data, _registry, name, interface, version) -> None:
        if not interface:
            return
        label = interface.decode("utf-8", "replace")
        if label == MANAGER_INTERFACE and version >= 1:
            self._manager_global = int(name)
        elif label == SEAT_INTERFACE and version >= 1:
            self._seat_global = int(name)

    def _on_global_remove(self, _data, _registry, _name) -> None:
        return

    def _ignore(self, *_args) -> None:
        return

    def _on_send(self, _data, _source, mime, fd) -> None:
        """Answer a paste request: write the payload and close the pipe."""
        try:
            label = mime.decode("utf-8", "replace") if mime else ""
            text = self.payload.get(label)
            if text is None:
                text = next(
                    (self.payload[key] for key in TEXT_MIMES if key in self.payload),
                    "",
                )
            data = text.encode("utf-8")
            while data:
                written = os.write(fd, data)
                if written <= 0:  # pragma: no cover - reader vanished
                    break
                data = data[written:]
            self.served += 1
        except OSError:  # pragma: no cover - reader closed the pipe
            pass
        finally:
            try:
                os.close(fd)
            except OSError:  # pragma: no cover - already closed
                pass

    # -- serving ------------------------------------------------------
    def copy(self, types: dict[str, str]) -> None:
        """Replace the selection with ``types`` (mime -> text)."""
        self.payload = {
            mime: value for mime, value in (types or {}).items() if isinstance(value, str)
        }
        if not self.payload:
            self.payload = {TEXT_MIMES[0]: ""}
        previous = self.source
        self.source = self._construct(
            self.manager, MANAGER_CREATE_DATA_SOURCE, "n", [], SOURCE_INTERFACE
        )
        self._listen(
            self.source, [_SEND_CB(self._on_send), _CANCELLED_CB(self._ignore)]
        )
        for mime in list(self.payload):
            self._request(self.source, SOURCE_OFFER, "s", [mime])
        self._request(self.device, DEVICE_SET_SELECTION, "?o", [self.source or None])
        self._flush()
        if previous:
            self._request(previous, SOURCE_DESTROY, "", [])
        self._flush()

    def wait(self, timeout: float = 0.2, *, commands=None) -> None:
        """Pump the connection once, then run any queued commands."""
        if self.lib.wl_display_prepare_read(ctypes.c_void_p(self.display)) != 0:
            self.lib.wl_display_dispatch_pending(ctypes.c_void_p(self.display))
            self._run_commands(commands)
            return
        self._flush()
        try:
            ready, _, _ = select.select(
                [self.fd, self._wake_read], [], [], timeout
            )
        except (OSError, ValueError):  # pragma: no cover - fd gone
            ready = []
        if self._wake_read in ready:
            try:
                os.read(self._wake_read, 4096)
            except OSError:  # pragma: no cover - non-blocking pipe
                pass
        if self.fd in ready:
            self.lib.wl_display_read_events(ctypes.c_void_p(self.display))
        else:
            self.lib.wl_display_cancel_read(ctypes.c_void_p(self.display))
        self.lib.wl_display_dispatch_pending(ctypes.c_void_p(self.display))
        self._run_commands(commands)

    def _run_commands(self, commands) -> None:
        if commands is None:
            return
        while True:
            try:
                command, reply = commands.get_nowait()
            except Exception:  # noqa: BLE001 - queue.Empty
                return
            try:
                if command is not None:
                    self.copy(command)
                reply({"ok": True, "id": self.served})
            except Exception as exc:  # noqa: BLE001 - never die silently
                reply({"ok": False, "error": str(exc)})

    def wake(self) -> None:
        try:
            os.write(self._wake_write, b"x")
        except OSError:  # pragma: no cover - pipe full or closed
            pass

    def close(self) -> None:
        try:
            if self.display:
                self.lib.wl_display_disconnect(ctypes.c_void_p(self.display))
        except OSError:  # pragma: no cover - already gone
            pass
        for fd in (self._wake_read, self._wake_write):
            try:
                os.close(fd)
            except OSError:  # pragma: no cover - already closed
                pass


# --- helper process -------------------------------------------------------
def serve(stdin=None, stdout=None, connection_factory=Connection) -> int:
    """Run the helper: own the selection and answer commands until EOF."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    import queue

    try:
        connection = connection_factory()
        connection.open()
    except Exception as exc:  # noqa: BLE001 - report, never traceback at the user
        _reply(stdout, {"ready": False, "error": f"{type(exc).__name__}: {exc}"})
        return 1
    _reply(stdout, {"ready": True})

    commands: "queue.Queue" = queue.Queue()
    stopped = threading.Event()

    def pump() -> None:
        while not stopped.is_set():
            try:
                connection.wait(0.2, commands=commands)
            except Exception as exc:  # noqa: BLE001 - a dead link ends the helper
                _reply(stdout, {"ok": False, "error": f"lost: {type(exc).__name__}: {exc}"})
                stopped.set()
                return

    thread = threading.Thread(target=pump, name="pastex-wlserver", daemon=True)
    thread.start()
    try:
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("quit"):
                break
            if "copy" in payload:
                done = threading.Event()
                outcome: dict = {}

                def reply(result, done=done, outcome=outcome):
                    outcome.update(result)
                    done.set()

                commands.put((payload["copy"], reply))
                connection.wake()
                done.wait(2.0)
                _reply(stdout, outcome or {"ok": False, "error": "timeout"})
    finally:
        stopped.set()
        connection.wake()
        thread.join(timeout=1.0)
        connection.close()
    return 0


def _reply(stdout, payload: dict) -> None:
    try:
        stdout.write(json.dumps(payload) + "\n")
        stdout.flush()
    except (OSError, ValueError):  # pragma: no cover - parent went away
        pass


def main(argv=None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if "--serve" not in argv:
        print("usage: python3 -m pastex.wlserver --serve", file=sys.stderr)
        return 2
    try:
        return serve()
    except Exception:  # noqa: BLE001 - the parent must still get a clean exit
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
