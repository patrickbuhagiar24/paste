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
import re
import shutil
import tempfile
import subprocess
import time
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
# A click must not wait on the clipboard: the verification retries once, briefly.
VERIFY_RETRY_SLEEP = 0.06

# A keystroke must be quick: if `ydotool key` has not returned by then the
# daemon is unreachable or wedged, and waiting longer only delays the restore
# of the Pastex window. Two candidates x 2 s stays inside the GUI deadline.
PASTE_KEY_TIMEOUT = 2.0
# `systemctl --user start ydotoold.service` must answer immediately.
DAEMON_START_TIMEOUT = 3.0
# X11 helpers used by the hide step: one short query, one EWMH request.
X11_QUERY_TIMEOUT = 2.0
# Restoring the window: xdotool without --sync returns at once, and the result
# is polled for about half a second. --sync would wait for the focus, which
# Muffin's focus-stealing protection likes to refuse (two wasted seconds).
RESTORE_TIMEOUT = 1.0
RESTORE_POLL_S = 0.06
RESTORE_POLLS = 8
FORCE_MINIMIZE_TIMEOUT = 2.0
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


def _window_id(value: str) -> str | None:
    """Normalise a window id (``41943043``, ``0x2800003``) to hex form."""
    text = (value or "").strip().splitlines()[-1].strip() if value else ""
    try:
        return hex(int(text, 0))
    except (TypeError, ValueError):
        return None


def x11_window_unmapped(
    window_id: str, runner: Runner = _default_runner
) -> bool | None:
    """Ask the X server whether ``window_id`` is unmapped.

    Only ``IsUnMapped`` is an answer: with a reparenting window manager
    (Muffin, Mutter, Xfwm) the *client* window stays ``IsViewable`` while the
    WM unmaps the frame around it, so a viewable client proves nothing.
    True = unmapped, None = cannot tell (or no xwininfo).
    """
    try:
        proc = _run_with_timeout(
            runner, ["xwininfo", "-id", str(window_id)], X11_QUERY_TIMEOUT
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if getattr(proc, "returncode", 1) != 0:
        return None
    out = f"{getattr(proc, 'stdout', '') or ''}"
    if "Map State: IsUnMapped" in out:
        return True
    return None


def x11_wm_state(
    window_id: str, runner: Runner = _default_runner
) -> str | None:
    """Return the WM_STATE the window manager published: 'iconic' or 'normal'."""
    if shutil.which("xprop") is None:
        return None
    try:
        proc = _run_with_timeout(
            runner,
            ["xprop", "-id", str(window_id), "-notype", "WM_STATE"],
            X11_QUERY_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if getattr(proc, "returncode", 1) != 0:
        return None
    out = f"{getattr(proc, 'stdout', '') or ''}".lower()
    if "iconic" in out:
        return "iconic"
    if "normal" in out:
        return "normal"
    return None


def x11_active_window(runner: Runner = _default_runner) -> str | None:
    """Return the window that currently has the input focus, in hex form."""
    for cmd in (
        ["xdotool", "getactivewindow"],
        ["xdotool", "getwindowfocus"],
    ):
        try:
            proc = _run_with_timeout(runner, cmd, X11_QUERY_TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            return None
        if getattr(proc, "returncode", 1) == 0:
            window = _window_id(getattr(proc, "stdout", "") or "")
            if window is not None:
                return window
    return None


def x11_window_class(
    window_id: str, runner: Runner = _default_runner
) -> str | None:
    """Return the last WM_CLASS name of a window ('Pastex' for Pastex)."""
    if shutil.which("xprop") is None:
        return None
    try:
        proc = _run_with_timeout(
            runner,
            ["xprop", "-id", str(window_id), "-notype", "WM_CLASS"],
            X11_QUERY_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if getattr(proc, "returncode", 1) != 0:
        return None
    out = f"{getattr(proc, 'stdout', '') or ''}"
    if "=" not in out:
        return None
    names = [part.strip().strip('"') for part in out.split("=", 1)[1].split(",")]
    return names[-1] if names else None


def x11_focus_moved(
    window_id: str,
    wm_class: str = "Pastex",
    runner: Runner = _default_runner,
) -> bool | None:
    """Say whether the X input focus has left Pastex's window.

    Hiding the window exists for one reason: the focus has to go back to the
    previous application before Ctrl+V is sent. Asking the X server who holds
    the focus answers that directly, while ``wm state`` and ``WM_STATE`` can
    lag behind (or never be updated by the window manager). True = the focus is
    elsewhere, False = Pastex still has it, None = cannot tell.
    """
    active = x11_active_window(runner)
    if active is None:
        return None
    own = _window_id(window_id)
    if own is not None and active == own:
        return False
    if wm_class and x11_window_class(active, runner) == wm_class:
        # A frame or a child of ours still holds the focus.
        return False
    return True


def activate_window(window_id: str, runner: Runner = _default_runner) -> bool:
    """Raise and un-minimize a window through EWMH with xdotool.

    ``deiconify()`` can be dropped while the window manager animates, and the
    WM's own ``WM_STATE`` is not reliable enough to notice; asking the window
    manager to activate the window brings it back on every desktop tested.
    """
    if shutil.which("xdotool") is None:
        return False
    try:
        proc = _run_with_timeout(
            runner,
            ["xdotool", "windowactivate", "--sync", str(window_id)],
            FORCE_MINIMIZE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return getattr(proc, "returncode", 1) == 0


def window_is_shown(window_id: str, runner: Runner = _default_runner) -> bool | None:
    """True when the window manager no longer calls ``window_id`` hidden.

    ``_NET_WM_STATE_HIDDEN`` is the property EWMH window managers (Mutter and
    Muffin included) keep current for a minimized window, while the client's
    own ``Map State`` stays ``IsViewable`` because only the frame around it is
    unmapped. None means the state cannot be read here.
    """
    if shutil.which("xprop") is None:
        return None
    try:
        proc = _run_with_timeout(
            runner, ["xprop", "-id", str(window_id), "_NET_WM_STATE"], X11_QUERY_TIMEOUT
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if getattr(proc, "returncode", 1) != 0:
        return None
    out = f"{getattr(proc, 'stdout', '') or ''}"
    if "not found" in out.lower():
        return None
    return "_NET_WM_STATE_HIDDEN" not in out


def restore_window(
    window_id: str,
    runner: Runner = _default_runner,
    sleep: Callable[[float], None] = time.sleep,
    polls: int = RESTORE_POLLS,
) -> bool:
    """Show ``window_id`` again through the window manager, without waiting.

    ``xdotool windowactivate --sync`` waits for the window to become *active*,
    and Muffin's focus-stealing protection refuses an activation that carries
    no timestamp: the request then burns its whole two-second timeout and
    reports failure even though the window itself was fine. A plain
    ``windowmap`` (XMapWindow) is answered immediately, so the window is shown
    first and only the focus is attempted; the result is then verified for
    ``RESTORE_POLLS * RESTORE_POLL_S`` seconds before giving up.
    """
    if shutil.which("xdotool") is None:
        return False
    mapped = _ask_the_window_manager(window_id, "windowmap", runner)
    _ask_the_window_manager(window_id, "windowraise", runner)
    _ask_the_window_manager(window_id, "windowactivate", runner)
    for attempt in range(max(1, polls)):
        if window_is_shown(window_id, runner) is True:
            return True
        if x11_active_window(runner) == _window_id(str(window_id)):
            return True
        if attempt + 1 < polls:
            sleep(RESTORE_POLL_S)
    # The window manager never said it showed the window; report whether the
    # request itself was accepted so the caller can decide about a fallback.
    return bool(mapped) and window_is_shown(window_id, runner) is not False


def normalize_window_id(value: str) -> str | None:
    """Return a window id in 0x-hex form, or None when it cannot be parsed."""
    return _window_id(str(value))


def focus_window(window_id: str, runner: Runner = _default_runner) -> bool:
    """Give ``window_id`` the input focus with XSetInputFocus (xdotool windowfocus).

    Unlike minimizing or activating, this does not need the window manager to
    agree: it is a direct X request, which is what makes it the fallback when a
    window manager ignores every minimize it is sent.
    """
    return _ask_the_window_manager(window_id, "windowfocus", runner)


def window_below(window_id: str, runner: Runner = _default_runner) -> str | None:
    """Return the window the user was in before Pastex, from the stacking order.

    Pastex is on top when a snippet is clicked, so the window directly below it
    in ``_NET_CLIENT_LIST_STACKING`` is the application the click is meant to
    paste into. Only a mapped window in the ``normal`` state is accepted.
    """
    if shutil.which("xprop") is None:
        return None
    try:
        proc = _run_with_timeout(
            runner,
            ["xprop", "-root", "_NET_CLIENT_LIST_STACKING"],
            X11_QUERY_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if getattr(proc, "returncode", 1) != 0:
        return None
    order = re.findall(r"0x[0-9a-fA-F]+", f"{getattr(proc, 'stdout', '') or ''}")
    ours = _window_id(str(window_id))
    below = order[: order.index(ours)] if ours in order else order
    for candidate in reversed(below):
        if candidate == ours:
            continue
        if x11_wm_state(candidate, runner) != "normal":
            continue
        if window_is_shown(candidate, runner) is False:
            continue
        return candidate
    return None


def focus_the_window_below(
    window_id: str, runner: Runner = _default_runner
) -> str | None:
    """Move the input focus off ``window_id`` to the app behind it.

    Returns the window that took the focus, or None when there is nothing to
    hand it to. This is the fallback for a window manager that ignores
    minimize requests: the text then goes to the application the user was in,
    which is what the click is for.
    """
    target = window_below(window_id, runner)
    if target is None:
        return None
    if not focus_window(target, runner):
        return None
    if x11_active_window(runner) not in {target, None}:
        # XSetInputFocus was refused or immediately overridden; ask the window
        # manager for the same thing before giving up.
        _ask_the_window_manager(target, "windowactivate", runner)
    if x11_active_window(runner) not in {target, None}:
        return None
    _ask_the_window_manager(target, "windowraise", runner)
    return target


def _ask_the_window_manager(
    window_id: str, verb: str, runner: Runner = _default_runner
) -> bool:
    """Send one xdotool request to the window manager, without ``--sync``."""
    try:
        proc = _run_with_timeout(
            runner, ["xdotool", verb, str(window_id)], RESTORE_TIMEOUT
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return getattr(proc, "returncode", 1) == 0


def force_minimize_window(window_id: str, runner: Runner = _default_runner) -> bool:
    """Minimize through EWMH with xdotool, for a WM that ignores ``iconify()``.

    Tk's ``iconify()`` is silently dropped for a maximized window on X11, and
    some window managers ignore it in other states too. This asks the window
    manager directly and waits (``--sync``) until it really unmaps the window.
    """
    if shutil.which("xdotool") is None:
        return False
    try:
        proc = _run_with_timeout(
            runner,
            ["xdotool", "windowminimize", "--sync", str(window_id)],
            FORCE_MINIMIZE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return getattr(proc, "returncode", 1) == 0


def window_manager(runner: Runner = _default_runner) -> str | None:
    """Return the window manager's name, or None when X has no EWMH WM.

    Without a window manager nothing can minimize Pastex: Tk's ``iconify()``
    is a request to the WM. Naming the WM in the log makes
    that visible instead of looking like a mystery.
    """
    if shutil.which("wmctrl") is not None:
        try:
            proc = _run_with_timeout(runner, ["wmctrl", "-m"], X11_QUERY_TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            proc = None
        if proc is not None and getattr(proc, "returncode", 1) == 0:
            for line in f"{getattr(proc, 'stdout', '') or ''}".splitlines():
                if line.startswith("Name:"):
                    name = line.split(":", 1)[1].strip()
                    return name or "unnamed"
            return None
    if shutil.which("xprop") is None:
        return None
    try:
        proc = _run_with_timeout(
            runner,
            ["xprop", "-root", "-notype", "_NET_SUPPORTING_WM_CHECK"],
            X11_QUERY_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if getattr(proc, "returncode", 1) != 0:
        return None
    out = f"{getattr(proc, 'stdout', '') or ''}"
    if "not found" in out or "no such atom" in out.lower():
        return None
    return "unnamed"


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
            f"Could not copy ({detail}). On Linux Mint install the X11 tools "
            "with: sudo apt install xclip xsel xdotool "
            "(or wl-clipboard, ydotool for a Wayland session)."
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
    /dev/uinput, which the setup helper sets up.
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
    """Read clipboard contents for diagnostics, or return None if unsupported.

    Every reader on the machine is asked before an empty answer is believed: a
    single broken tool that exits 0 with no output otherwise looks exactly like
    an empty clipboard, which used to cancel a paste that would have worked.
    """
    session = detect_session()
    if session == "wayland":
        candidates = [
            ["wl-paste", "--no-newline"],
            ["xclip", "-selection", "clipboard", "-o"],
            ["xsel", "--clipboard", "--output"],
        ]
    else:
        candidates = [
            ["xclip", "-selection", "clipboard", "-o"],
            ["xsel", "--clipboard", "--output"],
            ["wl-paste", "--no-newline"],
        ]
    empty: str | None = None
    for cmd in candidates:
        if shutil.which(cmd[0]) is None:
            continue
        try:
            proc = runner(cmd)
        except (OSError, subprocess.SubprocessError):
            continue
        if getattr(proc, "returncode", 0) != 0:
            continue
        out = f"{getattr(proc, 'stdout', '') or ''}"
        if out:
            return out
        if empty is None:
            empty = out  # every reader so far says empty; keep looking
    return empty


def _same_text(back: str, text: str) -> bool:
    """Compare clipboard contents, tolerating a trailing newline a tool adds."""
    if back == text:
        return True
    return back.rstrip("\n") == text.rstrip("\n")


def verify_copy(text: str, runner: Runner = _default_runner) -> bool | None:
    """Check that the clipboard really holds ``text``.

    ``xclip``, ``xsel`` and ``wl-copy`` fork a process that keeps the
    selection, so their exit status says nothing about the contents: a copy
    whose stdin closed early still exits 0 and then serves an empty clipboard,
    and the following Ctrl+V pastes nothing at all. True = the clipboard holds
    the text, False = it holds something else (or nothing), None = the contents
    cannot be read back here.
    """
    back = read_text_from_clipboard(runner=runner)
    if back is None:
        return None
    if _same_text(back, text):
        return True
    # The owner may take a moment to answer with the contents it just took over,
    # but this runs on the click path: one short retry, no long waits.
    time.sleep(VERIFY_RETRY_SLEEP)
    back = read_text_from_clipboard(runner=runner)
    if back is None:
        return None
    return _same_text(back, text)


# The longest text Pastex will type when the clipboard cannot hold it. Typing
# sends one key event per character, so a huge snippet would take minutes.
TYPE_TEXT_LIMIT = 20_000


def can_type_text(text: str) -> bool:
    """True when ``text`` can be delivered by typing instead of the clipboard."""
    return bool(text) and len(text) <= TYPE_TEXT_LIMIT and shutil.which("xdotool") is not None


def type_text_x11(text: str, runner: Runner = _default_runner) -> PasteResult:
    """Type ``text`` into the focused window with xdotool, without the clipboard.

    Used when the clipboard cannot be made to hold the text: Ctrl+V would paste
    nothing, while typing delivers the snippet anyway. The text goes through a
    temporary file, because ``xdotool type`` would otherwise treat a snippet
    that starts with "-" as an option.
    """
    if not can_type_text(text):
        return PasteResult(
            ok=False,
            tool="xdotool",
            message=(
                "xdotool is not installed" if shutil.which("xdotool") is None
                else f"the text is too long to type ({len(text)} characters)"
            ),
        )
    handle = None
    try:
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", suffix=".txt", prefix="pastex-type-", delete=False
        )
        handle.write(text)
        handle.close()
        proc = _run_with_timeout(
            runner,
            [
                "xdotool",
                "type",
                "--clearmodifiers",
                "--delay",
                "0",
                "--file",
                handle.name,
            ],
            # Typing sends one key event per character (about 4 ms each on a
            # real desktop), so the timeout has to grow with the text.
            max(3.0, min(60.0, len(text) * 0.006 + 2)),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return PasteResult(ok=False, tool="xdotool", message=f"typing failed: {exc}")
    finally:
        if handle is not None:
            try:
                os.unlink(handle.name)
            except OSError:
                pass
    if getattr(proc, "returncode", 1) != 0:
        detail = (getattr(proc, "stderr", "") or "").strip()
        return PasteResult(
            ok=False,
            tool="xdotool",
            message=f"xdotool could not type the text{': ' + detail if detail else ''}",
        )
    return PasteResult(
        ok=True,
        tool="xdotool",
        message="Typed the text with xdotool (the clipboard did not hold it).",
    )


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
            "See the log for diagnostics."
        ),
    )
