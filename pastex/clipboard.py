"""Clipboard and automatic paste into another app with one click.

Tool priority on Linux Mint (X11 by default, Wayland in the Cinnamon session):

  Copy   : xclip (X11) / wl-copy (Wayland) -> xsel
  Paste  : xdotool (X11) / ydotool (Wayland)
  Check  : clipboard read-back is reserved for explicit diagnostics

All subprocess calls use an injectable ``runner``, allowing tests to run
without access to a real clipboard.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

Runner = Callable[..., "subprocess.CompletedProcess[str]"]

# Set by the GUI once the opt-in Wayland clipboard owner has proved that it can
# serve the selection (see pastex/wlowner.py). None means "use the tools".
_OWNER = None
_UNSET = object()


def set_clipboard_owner(owner) -> None:
    """Use ``owner`` for copies; pass None to go back to wl-copy/xclip/xsel."""
    global _OWNER
    _OWNER = owner


def clipboard_owner():
    """The clipboard owner in use, or None."""
    return _OWNER


# Clipboard tools that fork and remain alive to own the selection.
# Capturing their output keeps the pipe open, so the subprocess waits
# until timeout and reports failure even though the clipboard is populated.
_FORKING_TOOLS = frozenset({"wl-copy", "xclip", "xsel"})

# Clipboard read timeout for explicit diagnostics; never let a read hang the GUI.
READ_TIMEOUT = 0.3

# A keystroke must be quick: if `ydotool key` has not returned by then the
# daemon is unreachable or wedged, and waiting longer only delays the restore
# of the Pastex window. Two candidates x 2 s stays inside the GUI deadline.
PASTE_KEY_TIMEOUT = 2.0
# `systemctl --user start ydotoold.service` must answer immediately.
DAEMON_START_TIMEOUT = 3.0
# Total portal budget. The portal is a fallback; a consent dialog the user
# never answers must not keep the window minimized.
PORTAL_TIMEOUT = 6.0

# Ctrl+V as ydotool >= 1.0 keycodes: 29=LeftCtrl, 47=V.
# Order: press Ctrl, press V, release V, release Ctrl.
KEYCODE_CTRL_V = ["ydotool", "key", "29:1", "47:1", "47:0", "29:0"]
# ydotool 0.1.x uses key names; try those as a fallback.
NAME_CTRL_V = ["ydotool", "key", "ctrl+v"]
XDOTOOL_CTRL_V = ["xdotool", "key", "ctrl+v"]

# Clipboard reader tools used for explicit diagnostics.
READER_TOOLS = ("wl-paste", "xclip", "xsel")


def _run_with_timeout(runner: Runner, cmd: list[str], timeout: float):
    """Run ``cmd`` with ``timeout``, tolerating runners without kwargs."""
    try:
        return runner(cmd, timeout=timeout)
    except TypeError:  # pragma: no cover - only simple test doubles
        return runner(cmd)


def _default_runner(*args, **kwargs) -> subprocess.CompletedProcess[str]:
    cmd = args[0] if args else kwargs.get("args")
    tool = cmd[0] if isinstance(cmd, (list, tuple)) and cmd else str(cmd or "")
    kwargs.setdefault("input", None)
    kwargs.setdefault("text", True)
    kwargs.setdefault("timeout", 5)
    if tool in _FORKING_TOOLS:
        # Do not wait for wl-copy/xclip to exit; they intentionally remain alive.
        # Write the input, close stdin, wait briefly, then consider it successful
        # if the process is still alive (this is normal behavior).
        text = kwargs.pop("input", None)
        timeout = kwargs.pop("timeout", 5) or 5
        kwargs.pop("capture_output", None)
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE if text else subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=True,
        )
        if text:
            try:
                proc.stdin.write(text)
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        try:
            code = proc.wait(timeout=min(1.0, timeout))
        except subprocess.TimeoutExpired:
            code = 0  # The process still owns the selection; this is normal.
        return subprocess.CompletedProcess(cmd, code, "", "")
    kwargs.setdefault("capture_output", True)
    if tool in READER_TOOLS and kwargs.get("timeout", 5) > READ_TIMEOUT:
        # Clipboard reads must not hang; a quick failure is better
        # than making the GUI appear frozen.
        kwargs["timeout"] = READ_TIMEOUT
    return subprocess.run(*args, check=False, **kwargs)


@dataclass
class ClipboardResult:
    ok: bool
    tool: str = ""
    message: str = ""


@dataclass
class PasteResult:
    ok: bool
    tool: str = ""
    message: str = ""
    # False when the command succeeds but its effect cannot be confirmed
    # (for example, xdotool on Wayland only reaches XWayland windows,
    # and native Wayland apps may not receive Ctrl+V even with exit code 0).
    confident: bool = True


def detect_session() -> str:
    """Detect the graphical session: wayland, x11, or unknown."""
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "unknown"


def available_tools() -> dict[str, bool]:
    """Check which clipboard and paste tools are available on PATH."""
    return {
        name: shutil.which(name) is not None
        for name in ("wl-copy", "xclip", "xsel", "ydotool", "xdotool")
    }


def _copy_candidates(session: str) -> list[tuple[str, list[str]]]:
    """Clipboard tools to try, best first for the detected session."""
    # Prefer Wayland tools in Wayland sessions and X11 tools otherwise.
    if session == "wayland":
        return [
            ("wl-copy", ["wl-copy"]),
            ("xclip", ["xclip", "-selection", "clipboard"]),
            ("xsel", ["xsel", "--clipboard", "--input"]),
        ]
    return [
        ("xclip", ["xclip", "-selection", "clipboard"]),
        ("xsel", ["xsel", "--clipboard", "--input"]),
        ("wl-copy", ["wl-copy"]),
    ]


def _paste_candidates(session: str) -> list[tuple[str, list[str]]]:
    """Keystroke tools to try, best first for the detected session.

    ydotool >= 1.0 (breaking change): ``key`` accepts only keycodes, written
    as <keycode>:<press/release>. Linux: 29=LeftCtrl, 47=V, 42=Shift.
    """
    if session == "wayland":
        # Do not use xdotool on Wayland: it uses XTEST and only reaches
        # XWayland windows, while still returning exit code 0 even when
        # native Wayland apps receive nothing.
        return [
            ("ydotool", KEYCODE_CTRL_V),
            ("ydotool", NAME_CTRL_V),
        ]
    return [
        ("xdotool", [*XDOTOOL_CTRL_V]),
        ("ydotool", KEYCODE_CTRL_V),
        ("ydotool", NAME_CTRL_V),
    ]


@dataclass
class ToolPlan:
    """Tool candidates that exist on this machine, resolved against PATH once.

    ``shutil.which`` stats every PATH entry, so resolving the same candidates
    on every click cost a few milliseconds that a click-to-paste cannot
    afford. :func:`prewarm` resolves both plans in a background thread at
    startup and the GUI passes them back in through ``plan=``.
    """

    session: str
    candidates: list[tuple[str, list[str]]]

    def __bool__(self) -> bool:
        return bool(self.candidates)

    def tool_names(self) -> list[str]:
        """Unique tool names in priority order, for logging."""
        names: list[str] = []
        for name, _cmd in self.candidates:
            if name not in names:
                names.append(name)
        return names


def resolve_copy_plan(session: str | None = None) -> ToolPlan:
    """Resolve the available clipboard tools now, so pastes can skip PATH."""
    session = session or detect_session()
    return ToolPlan(
        session,
        [
            (name, cmd)
            for name, cmd in _copy_candidates(session)
            if shutil.which(cmd[0]) is not None
        ],
    )


def resolve_paste_plan(session: str | None = None) -> ToolPlan:
    """Resolve the available keystroke tools now, so pastes can skip PATH."""
    session = session or detect_session()
    return ToolPlan(
        session,
        [
            (name, [*cmd])
            for name, cmd in _paste_candidates(session)
            if shutil.which(cmd[0]) is not None
        ],
    )


def prewarm() -> tuple[ToolPlan, ToolPlan]:
    """Resolve both plans and start ydotool's daemon before the first click.

    Without this, the first click pays for the PATH lookups and, on Wayland,
    for ``ydotoold`` being started by the failure path of the first
    ``ydotool key`` (which sleeps in 50 ms steps while the socket appears).
    Intended to run in a background thread; the current session is detected
    here rather than passed in.
    """
    session = detect_session()
    copy_plan = resolve_copy_plan(session)
    paste_plan = resolve_paste_plan(session)
    if session == "wayland" and paste_plan and not _ydotoold_socket_exists():
        try:
            ensure_ydotoold()
        except (OSError, subprocess.SubprocessError):  # pragma: no cover - daemon optional
            pass
    return copy_plan, paste_plan


def copy_text(
    text: str,
    runner: Runner = _default_runner,
    plan: ToolPlan | None = None,
    owner=_UNSET,
) -> ClipboardResult:
    """Copy text to the system clipboard and return the tool that succeeded.

    Pass a pre-resolved ``plan`` to skip the PATH lookups on the click path;
    without one the candidates are resolved here, as before. When a clipboard
    owner is live (opt-in, see pastex/wlowner.py) it copies without spawning a
    process; every failure falls through to the tools below.
    """
    active_owner = clipboard_owner() if owner is _UNSET else owner
    if active_owner is not None:
        try:
            if active_owner.copy(text):
                return ClipboardResult(
                    ok=True,
                    tool="wayland-owner",
                    message="Text copied by the built-in clipboard owner.",
                )
        except Exception:  # noqa: BLE001 - the tools remain a safe fallback
            pass
    plan = plan or resolve_copy_plan()
    errors: list[str] = []
    for name, cmd in plan.candidates:
        try:
            proc = runner(cmd, input=text)
            if proc.returncode == 0:
                # Trust the clipboard tool's exit status; do not read back or
                # replace the clipboard contents in the normal paste flow.
                return ClipboardResult(
                    ok=True,
                    tool=name,
                    message=f"Text copied with {name}.",
                )
            detail = " ".join(
                filter(
                    None,
                    [
                        (getattr(proc, "stderr", "") or "").strip(),
                        (getattr(proc, "stdout", "") or "").strip(),
                    ],
                )
            )
            errors.append(f"{name}: {detail or proc.returncode}")
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f"{name}: {exc}")
    detail = "; ".join(errors) if errors else "no clipboard tools found"
    return ClipboardResult(
        ok=False,
        tool="",
        message=(
            f"Could not copy ({detail}). Install wl-clipboard or xclip, "
            "or use the app’s Copy button (internal fallback)."
        ),
    )


def _runtime_dir() -> str:
    uid = getattr(os, "getuid", lambda: 0)()
    return os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"


def _ydotoold_socket_paths() -> list[str]:
    """Every place a ydotoold socket may live, most specific first."""
    runtime = _runtime_dir()
    paths = []
    env = os.environ.get("YDOTOOL_SOCKET")
    if env:
        paths.append(env)
    paths.extend(
        [
            f"{runtime}/ydotool.sock",
            f"{runtime}/.ydotool_socket",
            "/run/ydotoold.sock",
            "/tmp/ydotool.sock",
        ]
    )
    return paths


def _ydotoold_socket_exists() -> bool:
    return any(os.path.exists(path) for path in _ydotoold_socket_paths())


def _spawn_private_ydotoold() -> bool:
    """Start a ydotoold we own, with a socket in XDG_RUNTIME_DIR.

    Some distributions ship ``ydotoold.service`` as a *system* unit, so
    ``systemctl --user start ydotoold.service`` fails on a stock install and
    automatic paste looked broken. A daemon started by the user with a
    user-owned socket needs no root: it only needs write access to
    /dev/uinput, which ``pastex --enable-permissions`` sets up.
    """
    binary = shutil.which("ydotoold")
    if binary is None:
        return False
    runtime = _runtime_dir()
    socket = f"{runtime}/pastex-ydotool.sock"
    try:
        uid = os.getuid()
        gid = os.getgid()
    except AttributeError:  # pragma: no cover - non-POSIX
        return False
    cmd = [binary, "--socket-path", socket, "--socket-own", f"{uid}:{gid}"]
    try:
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - spawn failed
        return False
    # ydotool reads YDOTOOL_SOCKET, so every later call talks to our daemon.
    os.environ["YDOTOOL_SOCKET"] = socket
    for _ in range(20):
        if os.path.exists(socket):
            return True
        if _sleep is not None:
            _sleep(0.05)
    os.environ.pop("YDOTOOL_SOCKET", None)
    return False


def ensure_ydotoold(runner: Runner = _default_runner) -> bool:
    """Start ydotoold if it is not running; return True if a socket exists.

    Called after `ydotool key` fails so users do not have to configure the
    daemon in advance. Three attempts, cheapest first: an already running
    daemon, the user unit (works where the package ships one), and finally a
    daemon we start ourselves with a socket we own. The return value is the
    presence of a socket, not the exit status of systemctl: a unit that
    "starts" without creating a socket leaves ydotool just as broken.
    """
    try:
        proc = _run_with_timeout(
            runner,
            ["systemctl", "--user", "start", "ydotoold.service"],
            DAEMON_START_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        proc = None
    if getattr(proc, "returncode", 1) == 0:
        # Allow time for the socket to appear before retrying.
        for _ in range(20):
            if _ydotoold_socket_exists():
                return True
            if _sleep is not None:
                _sleep(0.05)
    # The unit may already be up (or the socket may live somewhere else).
    if _ydotoold_socket_exists():
        return True
    return _spawn_private_ydotoold()


try:  # Allow tests to replace the sleep function.
    from time import sleep as _sleep
except ImportError:  # pragma: no cover
    _sleep = None


def read_text_from_clipboard(runner: Runner = _default_runner) -> str | None:
    """Read clipboard contents for diagnostics, or return None if unsupported."""
    session = detect_session()
    if session == "wayland":
        candidates = [
            ["wl-paste", "--no-newline"],
            ["xclip", "-selection", "clipboard", "-o"],
        ]
    else:
        candidates = [
            ["xclip", "-selection", "clipboard", "-o"],
            ["wl-paste", "--no-newline"],
        ]
    for cmd in candidates:
        if shutil.which(cmd[0]) is None:
            continue
        try:
            proc = runner(cmd)
            if proc.returncode == 0:
                return getattr(proc, "stdout", "") or ""
        except (OSError, subprocess.SubprocessError):
            continue
    return None


def type_text(text: str, runner: Runner = _default_runner) -> PasteResult:
    """Type text directly through ydotool for diagnostics.

    Unlike Ctrl+V, this does not depend on the clipboard.
    """
    if shutil.which("ydotool") is None:
        return PasteResult(ok=False, tool="", message="ydotool is not installed.")
    try:
        proc = runner(["ydotool", "type", text])
        if proc.returncode == 0:
            return PasteResult(
                ok=True,
                tool="ydotool",
                message=f"Test text sent through ydotool: {text!r}",
            )
        detail = " ".join(
            filter(
                None,
                [
                    (getattr(proc, "stderr", "") or "").strip(),
                    (getattr(proc, "stdout", "") or "").strip(),
                ],
            )
        )
        return PasteResult(
            ok=False,
            tool="ydotool",
            message=f"ydotool type failed: {detail or proc.returncode}",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return PasteResult(ok=False, tool="", message=str(exc))


def _try_portal(timeout: float = PORTAL_TIMEOUT) -> tuple[bool, str] | None:
    """Ask the desktop portal to press Ctrl+V; None when it is unavailable.

    The portal needs no /dev/uinput access, so it rescues automatic paste on a
    machine where the udev rule was never installed. It is consulted only after
    every tool in the plan failed, so the normal path is unchanged. ``timeout``
    bounds the consent dialog: an unanswered prompt must not keep the Pastex
    window minimized.
    """
    try:
        from . import portal  # noqa: PLC0415 - optional dependency
    except Exception:  # noqa: BLE001 - never break a paste on an import error
        return None
    try:
        if not portal.available():
            return None
        try:
            return portal.send_ctrl_v(timeout=timeout)
        except TypeError:  # pragma: no cover - older signature or a test double
            return portal.send_ctrl_v()
    except Exception as exc:  # noqa: BLE001 - the portal is best-effort
        return False, f"portal error: {exc}"


def simulate_paste(
    runner: Runner = _default_runner,
    plan: ToolPlan | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> PasteResult:
    """Send Ctrl+V to the focused application.

    Pass a pre-resolved ``plan`` to skip the PATH lookups on the click path;
    without one the candidates are resolved here, as before. ``should_stop`` is
    checked between candidates and before the portal, so a paste that takes too
    long is abandoned instead of pressing keys after Pastex came back.
    """
    stop = should_stop or (lambda: False)
    plan = plan or resolve_paste_plan()
    session = plan.session
    errors: list[str] = []
    daemon_tried = False
    for name, cmd in plan.candidates:
        if stop():
            errors.append("aborted: the window was already restored")
            break
        try:
            proc = _run_with_timeout(runner, cmd, PASTE_KEY_TIMEOUT)
            # ydotool needs its daemon; if it fails, start it once and retry.
            if proc.returncode != 0 and name == "ydotool" and not daemon_tried:
                daemon_tried = True
                if ensure_ydotoold(runner=runner):
                    proc = _run_with_timeout(runner, cmd, PASTE_KEY_TIMEOUT)
            if proc.returncode == 0:
                return PasteResult(
                    ok=True,
                    tool=name,
                    message=f"Automatic paste sent through {name}.",
                    confident=(name != "xdotool"),
                )
            detail = " ".join(
                filter(
                    None,
                    [
                        (getattr(proc, "stderr", "") or "").strip(),
                        (getattr(proc, "stdout", "") or "").strip(),
                    ],
                )
            )
            errors.append(f"{name}: {detail or proc.returncode}")
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f"{name}: {exc}")
    # Last resort: the desktop portal injects the keystroke without
    # /dev/uinput, so a missing udev rule or group no longer blocks paste.
    portal = None if stop() else _try_portal()
    if portal is not None:
        ok, detail = portal
        if ok:
            return PasteResult(ok=True, tool="portal", message=detail, confident=True)
        errors.append(f"portal: {detail}")
    if errors:
        detail = "; ".join(errors)
    elif session == "wayland":
        detail = (
            "ydotool was not found; on Wayland, only ydotool "
            "can send Ctrl+V to native Wayland apps"
        )
    else:
        detail = "no automatic-paste tools were found"
    return PasteResult(
        ok=False,
        tool="",
        message=(
            f"Automatic paste is unavailable ({detail}). "
            "The text was copied; press Ctrl+V in the target app. "
            "Run `pastex --check` for diagnostics."
        ),
    )
