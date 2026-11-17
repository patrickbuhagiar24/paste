"""v1.5.0 #2: automatic-paste permissions moved from the GUI to the CLI.

The "Automatic Paste Permissions" button was removed from the GUI. The flow:
  * `--permissions`            -> prints the status (same as the --check section)
  * `--enable-permissions`   -> requests /dev/uinput access (pkexec + helper)
The information still appears in `--check` and in the paste failure dialog.
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


# --- CLI: --permissions -----------------------------------------------------------


def test_izin_flag_reports_status(capsys, monkeypatch):
    monkeypatch.setattr(uinput, "status", lambda *a, **k: FakeStatus())
    monkeypatch.setattr(uinput, "explain", lambda st=None: "1. do X")
    assert app_module.run_cli(["--permissions"]) == 0
    out = capsys.readouterr().out
    assert "Automatic-paste permissions" in out
    assert "write access to /dev/uinput" in out
    assert "do X" in out


def test_izin_flag_points_to_activation_command(capsys, monkeypatch):
    """When not ready, --permissions must name the activation command."""
    monkeypatch.setattr(uinput, "status", lambda *a, **k: FakeStatus())
    monkeypatch.setattr(uinput, "explain", lambda st=None: "1. do X")
    app_module.run_cli(["--permissions"])
    out = capsys.readouterr().out
    assert "--enable-permissions" in out


def test_check_still_reports_permission_and_cli_hint(capsys, monkeypatch):
    """The permission info STAYS in --check, now with CLI guidance."""
    monkeypatch.setattr(uinput, "status", lambda *a, **k: FakeStatus())
    monkeypatch.setattr(uinput, "explain", lambda st=None: "1. do X")
    assert app_module.run_cli(["--check"]) == 0
    out = capsys.readouterr().out
    assert "Automatic-paste permissions" in out
    assert "--enable-permissions" in out


# --- CLI: --enable-permissions -------------------------------------------------


def test_aktifkan_izin_calls_helper(monkeypatch, capsys):
    calls = []

    def fake_enable(user=None, **kw):
        calls.append(user)
        return 0, "akun ditambahkan", ""

    monkeypatch.setattr(uinput, "status", lambda *a, **k: FakeStatus(writable=False))
    monkeypatch.setattr(uinput, "can_self_enable", lambda st=None: True)
    monkeypatch.setattr(uinput, "enable_for_user", fake_enable)
    assert app_module.run_cli(["--enable-permissions"]) == 0
    assert calls == ["tester"]
    out = capsys.readouterr().out
    assert "Permissions enabled" in out
    assert "Log out" in out or "log out" in out


def test_aktifkan_izin_noop_when_already_writable(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(uinput, "status", lambda *a, **k: FakeStatus(writable=True))
    monkeypatch.setattr(
        uinput, "enable_for_user", lambda *a, **k: calls.append(a) or (0, "", "")
    )
    assert app_module.run_cli(["--enable-permissions"]) == 0
    assert calls == []
    assert "already enabled" in capsys.readouterr().out


def test_aktifkan_izin_without_pkexec_prints_manual_command(monkeypatch, capsys):
    monkeypatch.setattr(uinput, "status", lambda *a, **k: FakeStatus(writable=False))
    monkeypatch.setattr(uinput, "can_self_enable", lambda st=None: False)
    assert app_module.run_cli(["--enable-permissions"]) == 2
    out = capsys.readouterr().out
    assert "sudo usermod -aG pastex" in out


def test_aktifkan_izin_cancelled_password(monkeypatch, capsys):
    monkeypatch.setattr(uinput, "status", lambda *a, **k: FakeStatus(writable=False))
    monkeypatch.setattr(uinput, "can_self_enable", lambda st=None: True)
    monkeypatch.setattr(
        uinput, "enable_for_user", lambda *a, **k: (126, "", "dibatalkan")
    )
    assert app_module.run_cli(["--enable-permissions"]) == 1
    assert "cancelled" in capsys.readouterr().out


# --- paste failure dialog ------------------------------------------------


def test_paste_failure_dialog_mentions_cli(monkeypatch):
    """A paste failure still explains permissions and names the CLI command."""
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
    assert "--enable-permissions" in text


def test_paste_failure_dialog_ready_case_stays_simple(monkeypatch):
    from pastex.app import PastexApp

    shown = []
    monkeypatch.setattr(uinput, "status", lambda *a, **k: FakeStatus(writable=True))
    monkeypatch.setattr(app_module, "messagebox", FakeBox(shown))
    PastexApp._show_paste_failure(type("F", (), {})(), "the clipboard is empty")
    assert "the clipboard is empty" in shown[-1][2]


def test_cli_hint_mentions_both_flags():
    assert "--permissions" in uinput.CLI_HINT
    assert "--enable-permissions" in uinput.CLI_HINT


def test_no_gui_dependency_left_in_permission_paths():
    """The permission CLI must not use tkinter (it has to run without a display)."""
    tree = ast.parse(APP_SRC)
    wanted = {"run_permission_info", "run_enable_permission", "permission_report"}
    found = {
        n.name: n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name in wanted
    }
    assert set(found) == wanted
    for name, node in found.items():
        body = ast.dump(node)
        assert "messagebox" not in body, f"{name} uses a GUI dialog"

# --- Linux Mint default session: X11 needs nothing ------------------------


def test_permissions_report_on_x11_says_not_needed(capsys, monkeypatch):
    """On X11 the report must not list the group or /dev/uinput at all."""
    monkeypatch.setattr(
        uinput,
        "status",
        lambda *a, **k: FakeStatus(needed=False, session_kind="x11"),
    )
    assert app_module.run_cli(["--permissions"]) == 0
    out = capsys.readouterr().out
    assert "not needed on X11" in out
    assert "write access to /dev/uinput" not in out
    assert "--enable-permissions" not in out


def test_check_on_x11_reports_no_permission_setup(capsys, monkeypatch):
    monkeypatch.setattr(
        uinput,
        "status",
        lambda *a, **k: FakeStatus(needed=False, session_kind="x11"),
    )
    assert app_module.run_cli(["--check"]) == 0
    out = capsys.readouterr().out
    assert "not needed on X11" in out
    assert "Automatic-paste permissions (group" not in out


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
    assert "--enable-permissions" not in text
    assert "xdotool could not deliver Ctrl+V" in text


def test_enable_permissions_notes_that_x11_needs_nothing(capsys, monkeypatch):
    monkeypatch.setattr(
        uinput,
        "status",
        lambda *a, **k: FakeStatus(needed=False, session_kind="x11"),
    )
    monkeypatch.setattr(uinput, "can_self_enable", lambda st=None: False)
    app_module.run_cli(["--enable-permissions"])
    out = capsys.readouterr().out
    assert "does not need /dev/uinput permissions" in out
