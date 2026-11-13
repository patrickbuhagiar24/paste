"""Uji v1.5.0 #2: izin paste otomatis pindah dari GUI ke CLI.

Tombol "Izin Paste Otomatis" dihapus dari GUI. Alurnya kini:
  * `--permissions`            -> menampilkan status (sama seperti bagian --check)
  * `--enable-permissions`   -> meminta akses /dev/uinput (pkexec + helper)
Informasi tetap muncul di `--check` dan pada dialog kegagalan paste.
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
    """UinputStatus tiruan."""

    def __init__(
        self, *, writable=False, helper=True, pkexec=True, daemon=False, user="tester"
    ):
        self.user = user
        self.device_writable = writable
        self.helper = helper
        self.pkexec = pkexec
        self.daemon = daemon
        self.ready = writable
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
    """Pengganti tkinter.messagebox (CI tidak punya tkinter)."""

    def __init__(self, shown):
        self.shown = shown

    def showinfo(self, title, text, **kw):
        self.shown.append(("info", title, text))

    def showwarning(self, title, text, **kw):
        self.shown.append(("warning", title, text))

    def showerror(self, title, text, **kw):
        self.shown.append(("error", title, text))

    def askyesno(self, *a, **kw):
        pytest.fail("GUI tidak boleh menawarkan aktivasi izin lagi")


# --- GUI tidak lagi punya tombol ------------------------------------------


def _code_without_comments(source: str) -> str:
    """Source app.py tanpa komentar/docstring (komentar boleh menyebut sejarah)."""
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
    """Tidak boleh ada lagi tombol/label 'Izin Paste Otomatis' di GUI."""
    code = _code_without_comments(APP_SRC)
    assert "Izin Paste Otomatis" not in code
    assert "show_permission_info" not in code


def test_gui_has_no_permission_widgets():
    """Metode GUI pembuka izin sudah hilang, bukan hanya tombolnya."""
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
    monkeypatch.setattr(uinput, "explain", lambda st=None: "1. lakukan X")
    assert app_module.run_cli(["--permissions"]) == 0
    out = capsys.readouterr().out
    assert "Automatic-paste permissions" in out
    assert "write access to /dev/uinput" in out
    assert "lakukan X" in out


def test_izin_flag_points_to_activation_command(capsys, monkeypatch):
    """Bila belum siap, --permissions harus menyebut perintah aktivasi."""
    monkeypatch.setattr(uinput, "status", lambda *a, **k: FakeStatus())
    monkeypatch.setattr(uinput, "explain", lambda st=None: "1. lakukan X")
    app_module.run_cli(["--permissions"])
    out = capsys.readouterr().out
    assert "--enable-permissions" in out


def test_check_still_reports_permission_and_cli_hint(capsys, monkeypatch):
    """Info izin TETAP ada di --check, kini plus petunjuk CLI."""
    monkeypatch.setattr(uinput, "status", lambda *a, **k: FakeStatus())
    monkeypatch.setattr(uinput, "explain", lambda st=None: "1. lakukan X")
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


# --- Dialog kegagalan paste ----------------------------------------------


def test_paste_failure_dialog_mentions_cli(monkeypatch):
    """Kegagalan paste tetap menjelaskan izin + menyebut perintah CLI."""
    from pastex.app import PastexApp

    shown = []
    monkeypatch.setattr(uinput, "status", lambda *a, **k: FakeStatus())
    monkeypatch.setattr(uinput, "explain", lambda st=None: "1. lakukan X")
    monkeypatch.setattr(app_module, "messagebox", FakeBox(shown))

    fake = type("F", (), {})()
    PastexApp._show_paste_failure(fake, "ydotool tidak tersedia")
    assert shown, "dialog kegagalan harus muncul"
    text = shown[-1][2]
    assert "ydotool tidak tersedia" in text
    assert "Ctrl+V" in text
    assert "--enable-permissions" in text


def test_paste_failure_dialog_ready_case_stays_simple(monkeypatch):
    from pastex.app import PastexApp

    shown = []
    monkeypatch.setattr(uinput, "status", lambda *a, **k: FakeStatus(writable=True))
    monkeypatch.setattr(app_module, "messagebox", FakeBox(shown))
    PastexApp._show_paste_failure(type("F", (), {})(), "clipboard kosong")
    assert "clipboard kosong" in shown[-1][2]


def test_cli_hint_mentions_both_flags():
    assert "--permissions" in uinput.CLI_HINT
    assert "--enable-permissions" in uinput.CLI_HINT


def test_no_gui_dependency_left_in_permission_paths():
    """Fungsi CLI izin tidak boleh memakai tkinter (harus jalan tanpa display)."""
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
        assert "messagebox" not in body, f"{name} memakai dialog GUI"
