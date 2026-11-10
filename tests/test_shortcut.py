"""Uji pemasangan pintasan global (tanpa menyentuh gsettings asli)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paste_teks import shortcut

CB = ("/org/gnome/settings-daemon/plugins/media-keys/"
      "custom-keybindings/paste-teks/")


class FakeGsettings:
    """Tiruan gsettings: menyimpan custom-keybindings seperti aslinya."""

    def __init__(self, existing=()):
        self.paths = list(existing)
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        verb = args[0]
        if verb == "get" and args[1] == shortcut.MEDIA_KEYS:
            if not self.paths:
                return 0, "@as []", ""
            body = ", ".join(f"'{p}'" for p in self.paths)
            return 0, f"[{body}]", ""
        if verb == "get" and args[1] == shortcut.SCHEMA:
            if args[2] == "binding":
                return 0, "'<Control><Alt>v'", ""
            return 0, "'paste-teks --paste-foreground'", ""
        if verb == "set" and args[1] == shortcut.MEDIA_KEYS:
            raw = args[3]
            body = raw.strip().strip("[]").strip()
            self.paths = [p.strip().strip("'") for p in body.split(",")
                          if p.strip().strip("'").startswith("/")]
            return 0, "", ""
        if verb == "set":
            return 0, "", ""
        return 0, "", ""


def test_install_adds_entry(monkeypatch):
    monkeypatch.setattr(shortcut.shutil, "which", lambda n: "/usr/bin/gsettings")
    fake = FakeGsettings()
    ok, message = shortcut.install(runner=fake)
    assert ok is True and "dipasang" in message
    assert fake.paths == [CB]


def test_install_is_idempotent(monkeypatch):
    monkeypatch.setattr(shortcut.shutil, "which", lambda n: "/usr/bin/gsettings")
    fake = FakeGsettings()
    shortcut.install(runner=fake)
    shortcut.install(runner=fake)
    assert fake.paths == [CB]          # tidak menambah duplikat


def test_install_preserves_other_bindings(monkeypatch):
    monkeypatch.setattr(shortcut.shutil, "which", lambda n: "/usr/bin/gsettings")
    other = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/other/"
    fake = FakeGsettings(existing=[other])
    shortcut.install(runner=fake)
    assert fake.paths == [other, CB]   # pintasan pengguna lain tetap utuh


def test_install_sets_command_to_cli(monkeypatch):
    monkeypatch.setattr(shortcut.shutil, "which", lambda n: "/usr/bin/gsettings")
    fake = FakeGsettings()
    shortcut.install(runner=fake)
    commands = [c for c in fake.calls if c[0] == "set" and c[2] == "command"]
    assert commands and "paste-teks --paste-foreground" in commands[0][3]


def test_install_sets_binding(monkeypatch):
    monkeypatch.setattr(shortcut.shutil, "which", lambda n: "/usr/bin/gsettings")
    fake = FakeGsettings()
    shortcut.install(binding="<Super>p", runner=fake)
    binds = [c for c in fake.calls if c[0] == "set" and c[2] == "binding"]
    assert binds and binds[0][3] == "'<Super>p'"


def test_status_reports_installed(monkeypatch):
    monkeypatch.setattr(shortcut.shutil, "which", lambda n: "/usr/bin/gsettings")
    fake = FakeGsettings()
    assert shortcut.status(runner=fake).installed is False
    shortcut.install(runner=fake)
    st = shortcut.status(runner=fake)
    assert st.installed is True and st.supported is True
    assert st.command == "paste-teks --paste-foreground"


def test_status_empty_gsettings_value(monkeypatch):
    """Nilai '@as []' tidak boleh menghasilkan entri sampah."""
    monkeypatch.setattr(shortcut.shutil, "which", lambda n: "/usr/bin/gsettings")
    calls = []

    def runner(args):
        calls.append(args)
        return 0, "@as []", ""

    st = shortcut.status(runner=runner)
    assert st.installed is False


def test_remove_clears_entry(monkeypatch):
    monkeypatch.setattr(shortcut.shutil, "which", lambda n: "/usr/bin/gsettings")
    fake = FakeGsettings()
    shortcut.install(runner=fake)
    ok, _ = shortcut.remove(runner=fake)
    assert ok is True and fake.paths == []


def test_remove_keeps_other_bindings(monkeypatch):
    monkeypatch.setattr(shortcut.shutil, "which", lambda n: "/usr/bin/gsettings")
    other = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/other/"
    fake = FakeGsettings(existing=[CB, other])
    shortcut.remove(runner=fake)
    assert fake.paths == [other]


def test_install_without_gsettings():
    real_which = shortcut.shutil.which
    shortcut.shutil.which = lambda n: None
    try:
        ok, message = shortcut.install()
    finally:
        shortcut.shutil.which = real_which
    assert ok is False and "gsettings" in message


def test_supported_without_gsettings(monkeypatch):
    monkeypatch.setattr(shortcut.shutil, "which", lambda n: None)
    assert shortcut.supported() is False


def test_install_reports_gsettings_failure(monkeypatch):
    monkeypatch.setattr(shortcut.shutil, "which", lambda n: "/usr/bin/gsettings")

    def failing(args):
        return 1, "", "dconf error"

    ok, message = shortcut.install(runner=failing)
    assert ok is False and "dconf" in message


def test_hint_default_when_not_installed(monkeypatch):
    monkeypatch.setattr(shortcut.shutil, "which", lambda n: None)
    assert shortcut.hint() == shortcut.DEFAULT_BINDING


def test_desktop_is_gnome():
    import os
    old = os.environ.get("XDG_CURRENT_DESKTOP")
    try:
        os.environ["XDG_CURRENT_DESKTOP"] = "ubuntu:GNOME"
        assert shortcut.desktop_is_gnome() is True
        os.environ["XDG_CURRENT_DESKTOP"] = "KDE"
        assert shortcut.desktop_is_gnome() is False
    finally:
        if old is None:
            os.environ.pop("XDG_CURRENT_DESKTOP", None)
        else:
            os.environ["XDG_CURRENT_DESKTOP"] = old
