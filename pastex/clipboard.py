"""Clipboard and automatic paste into another app with one click.

Tool priority for Ubuntu 26.10 (GNOME, Wayland by default):

  Copy   : wl-copy (Wayland) -> xclip -> xsel
  Paste  : ydotool (Wayland) -> xdotool (X11)
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


# Clipboard tools that fork and remain alive to own the selection.
# Capturing their output keeps the pipe open, so the subprocess waits
# until timeout and reports failure even though the clipboard is populated.
_FORKING_TOOLS = frozenset({"wl-copy", "xclip", "xsel"})

# Clipboard read timeout for explicit diagnostics; never let a read hang the GUI.
READ_TIMEOUT = 0.3

# Clipboard reader tools used for explicit diagnostics.
READER_TOOLS = ("wl-paste", "xclip", "xsel")


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


def copy_text(text: str, runner: Runner = _default_runner) -> ClipboardResult:
    """Copy text to the system clipboard and return the tool that succeeded."""
    session = detect_session()
    # Prefer Wayland tools in Wayland sessions and X11 tools otherwise.
    if session == "wayland":
        candidates = [
            ("wl-copy", ["wl-copy"]),
            ("xclip", ["xclip", "-selection", "clipboard"]),
            ("xsel", ["xsel", "--clipboard", "--input"]),
        ]
    else:
        candidates = [
            ("xclip", ["xclip", "-selection", "clipboard"]),
            ("xsel", ["xsel", "--clipboard", "--input"]),
            ("wl-copy", ["wl-copy"]),
        ]
    errors: list[str] = []
    for name, cmd in candidates:
        if shutil.which(cmd[0]) is None:
            continue
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


def ensure_ydotoold(runner: Runner = _default_runner) -> bool:
    """Start ydotoold if it is not running; return True if it appears ready.

    This is called automatically after `ydotool key` fails, so users do not
    need to configure it in advance. Lack of systemd is not fatal.
    """
    try:
        proc = runner(["systemctl", "--user", "start", "ydotoold.service"])
    except (OSError, subprocess.SubprocessError):
        return False
    if getattr(proc, "returncode", 1) == 0:
        # Allow time for the socket to appear before retrying.
        for _ in range(20):
            if _ydotoold_socket_exists():
                return True
            if _sleep is not None:
                _sleep(0.05)
        return True
    return False


def _ydotoold_socket_exists() -> bool:
    uid = getattr(os, "getuid", lambda: 0)()
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    for path in (f"{runtime}/ydotool.sock", "/run/ydotoold.sock", "/tmp/ydotool.sock"):
        if os.path.exists(path):
            return True
    return False


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


def simulate_paste(runner: Runner = _default_runner) -> PasteResult:
    """Send Ctrl+V to the focused application."""
    session = detect_session()
    # ydotool >= 1.0 (breaking change): `key` accepts only keycodes, written
    # as <keycode>:<press/release>. Linux: 29=LeftCtrl, 47=V, 42=Shift.
    # Urutan: tekan Ctrl, tekan V, lepas V, lepas Ctrl.
    KEYCODE_CTRL_V = ["ydotool", "key", "29:1", "47:1", "47:0", "29:0"]
    # ydotool 0.1.x uses key names; try those as a fallback.
    NAME_CTRL_V = ["ydotool", "key", "ctrl+v"]
    if session == "wayland":
        # Do not use xdotool on Wayland: it uses XTEST and only reaches
        # XWayland windows, while still returning exit code 0 even when
        # native Wayland apps receive nothing. Claiming success in this case
        # would be misleading and make diagnosis harder.
        candidates = [
            ("ydotool", KEYCODE_CTRL_V),
            ("ydotool", NAME_CTRL_V),
        ]
    else:
        candidates = [
            ("xdotool", ["xdotool", "key", "ctrl+v"]),
            ("ydotool", KEYCODE_CTRL_V),
            ("ydotool", NAME_CTRL_V),
        ]
    errors: list[str] = []
    daemon_tried = False
    for name, cmd in candidates:
        if shutil.which(cmd[0]) is None:
            continue
        try:
            proc = runner(cmd)
            # ydotool needs its daemon; if it fails, start it once and retry.
            if proc.returncode != 0 and name == "ydotool" and not daemon_tried:
                daemon_tried = True
                if ensure_ydotoold(runner=runner):
                    proc = runner(cmd)
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
    if errors:
        detail = "; ".join(errors)
    elif session == "wayland":
        detail = (
            "ydotool was not found; on GNOME Wayland, only ydotool "
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
