"""v1.5.0 #2: automatic-paste permissions without a GUI button.

The "Automatic Paste Permissions" button was removed from the GUI. The flow:
  * the first-run setup dialog offers to request /dev/uinput access
    (pkexec + helper) in a Wayland session;
  * the paste-failure dialog explains the diagnosis and the manual setup steps.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from pastex import app as app_module
from pastex import uinput

ROOT = Path(__file__).resolve().parent.parent
APP_SRC = (ROOT / "pastex" / "app.py").read_text(encoding="utf-8")


class FakeStatus:
    """Stand-in UinputStatus."""

    def __init__(
        self,
        *,
        writable=False,
        helper=True,
        pkexec=True,
        daemon=False,
        user="tester",
        needed=True,
        session_kind="wayland",
    ):
        self.user = user
        self.device_writable = writable
        self.helper = helper
        self.pkexec = pkexec
        self.daemon = daemon
        self.needed = needed
        self.session_kind = session_kind
        self.ready = writable or not needed
        self.daemon_missing = not daemon

    def summary(self, prefix: str = ""):
        state = "✅" if self.device_writable else "❌"
        return [
            (
                state,
                f"{prefix}write access to {uinput.DEVICE}",
                "is writable" if self.device_writable else "is not writable",
            )
        ]


class FakeBox:
    """Stand-in for tkinter.messagebox (CI has no tkinter)."""

    def __init__(self, shown):
        self.shown = shown

    def showinfo(self, title, text, **kw):
        self.shown.append(("info", title, text))

    def showwarning(self, title, text, **kw):
        self.shown.append(("warning", title, text))

    def showerror(self, title, text, **kw):
        self.shown.append(("error", title, text))

    def askyesno(self, *a, **kw):
        pytest.fail("the GUI must not offer permission activation any more")


# --- the GUI has no such button any more ---------------------------------


def _code_without_comments(source: str) -> str:
    """app.py source without comments/docstrings (comments may mention history)."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def test_permission_button_removed_from_gui_source():
    """No 'Automatic Paste Permissions' button or label may remain in the GUI."""
    code = _code_without_comments(APP_SRC)
    assert "Izin Paste Otomatis" not in code
    assert "show_permission_info" not in code


def test_gui_has_no_permission_widgets():
    """The GUI methods that opened the permission dialog are gone, not just the button."""
    for gone in (
        "enable_auto_paste",
        "_enable_worker",
        "_enable_done",
        "start_ydotoold",
        "_daemon_worker",
        "_daemon_done",
    ):
        assert not hasattr(app_module.PastexApp, gone), gone


# --- paste failure dialog ------------------------------------------------


def test_paste_failure_dialog_mentions_manual_setup(monkeypatch):
    """A paste failure still explains permissions and names the manual steps."""
    from pastex.app import PastexApp

    shown = []
    monkeypatch.setattr(uinput, "status", lambda *a, **k: FakeStatus())
    monkeypatch.setattr(uinput, "explain", lambda st=None: "1. do X")
    monkeypatch.setattr(app_module, "messagebox", FakeBox(shown))

    fake = type("F", (), {})()
    PastexApp._show_paste_failure(fake, "ydotool is not available")
    assert shown, "the failure dialog must appear"
    text = shown[-1][2]
    assert "ydotool is not available" in text
    assert "Ctrl+V" in text
    assert "pastex-uinput-group" in text


def test_paste_failure_dialog_ready_case_stays_simple(monkeypatch):
    from pastex.app import PastexApp

    shown = []
    monkeypatch.setattr(uinput, "status", lambda *a, **k: FakeStatus(writable=True))
    monkeypatch.setattr(app_module, "messagebox", FakeBox(shown))
    PastexApp._show_paste_failure(type("F", (), {})(), "the clipboard is empty")
    assert "the clipboard is empty" in shown[-1][2]


def test_manual_hint_names_the_helper_script():
    assert "pastex-uinput-group" in uinput.MANUAL_HINT


# --- Linux Mint default session: X11 needs nothing ------------------------


def test_paste_failure_on_x11_does_not_blame_uinput(monkeypatch):
    """An X11 paste failure is a focus problem, not a missing permission."""
    from pastex.app import PastexApp

    shown = []
    monkeypatch.setattr(
        uinput,
        "status",
        lambda *a, **k: FakeStatus(needed=False, session_kind="x11"),
    )
    monkeypatch.setattr(app_module, "messagebox", FakeBox(shown))

    PastexApp._show_paste_failure(type("F", (), {})(), "Ctrl+V failed")
    text = shown[-1][2]
    assert "does not need" not in text
    assert "pastex-uinput-group" not in text
    assert "xdotool could not deliver Ctrl+V" in text
