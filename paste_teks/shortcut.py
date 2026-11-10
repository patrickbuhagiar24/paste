"""Pintasan global Ctrl+Alt+V lewat keybinding kustom GNOME.

Kenapa ini ada: paste lewat tombol GUI bergantung pada "jendela mana yang
memegang fokus" saat Ctrl+V dikirim. Di GNOME Wayland, menutup jendela,
mengecilkan, atau sekadar menyentuh jendela Tk bisa mengembalikan fokus ke
Paste Teks — sehingga Ctrl+V masuk ke aplikasi kita, bukan ke kolom input
pengguna.

Jalur yang bebas dari masalah itu: pengguna memfokuskan aplikasi tujuan
sendiri, lalu menekan pintasan global. Handler-nya adalah proses pendek
``paste-teks --paste-foreground`` yang menyalin teks terpilih lalu mengirim
Ctrl+V — jalur yang sama dengan ``--test-paste --ctrl-v`` yang sudah
terbukti bekerja.

Implementasi memakai `gsettings` (bagian dari GNOME, tanpa dependensi
tambahan, tanpa hak root).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass

MEDIA_KEYS = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM_PATH = ("/org/gnome/settings-daemon/plugins/media-keys/"
               "custom-keybindings/paste-teks/")
SCHEMA = (MEDIA_KEYS + ".custom-keybinding:" + CUSTOM_PATH)
SHORTCUT_NAME = "Paste Teks — tempel teks terpilih"
DEFAULT_BINDING = "<Control><Alt>v"
COMMAND = "paste-teks --paste-foreground"

# Nama skema lama di beberapa distro (gnome-settings-daemon < 3.38 punya
# path yang sama; ini hanya jaring pengaman).
LEGACY_SCHEMAS = ("org.gnome.settings-daemon.plugins.media-keys",)


@dataclass
class ShortcutStatus:
    supported: bool
    installed: bool
    binding: str = ""
    command: str = ""

    def summary(self) -> str:
        if not self.supported:
            return "Pintasan global tidak didukung di sesi ini."
        if not self.installed:
            return "Pintasan global belum dipasang."
        return f"Pintasan {self.binding} aktif → {self.command}"


def _run(args: list[str], *, timeout: int = 10):
    """Jalankan gsettings; kembalikan (kode, stdout, stderr)."""
    try:
        proc = subprocess.run(args, capture_output=True, text=True,
                              timeout=timeout)
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, "", str(exc)


def _gsettings(*args: str, runner=None):
    if runner is not None:
        return runner(list(args))
    return _run(["gsettings", *args])


def supported() -> bool:
    """Apakah sesi ini GNOME dengan skema media-keys?"""
    if shutil.which("gsettings") is None:
        return False
    code, out, _ = _run(["gsettings", "list-schemas"])
    if code != 0:
        return False
    return SCHEMA.split(":")[0] in out


def _installed_paths(runner=None) -> list[str]:
    code, out, _ = _gsettings("get", MEDIA_KEYS, "custom-keybindings",
                              runner=runner)
    if code != 0 or not out:
        return []
    # Format gsettings: ['/path/a/', '/path/b/'] atau '@as []' (kosong).
    if out.startswith("@as"):
        out = out[len("@as"):].strip()
    body = out.strip().strip("[]").strip()
    paths = []
    for part in body.split(","):
        cleaned = part.strip().strip("'").strip()
        if cleaned.startswith("/"):
            paths.append(cleaned)
    return paths


def status(runner=None) -> ShortcutStatus:
    if shutil.which("gsettings") is None:
        return ShortcutStatus(supported=False, installed=False)
    paths = _installed_paths(runner=runner)
    installed = CUSTOM_PATH in paths
    binding = ""
    command = ""
    if installed:
        _, binding, _ = _gsettings("get", SCHEMA, "binding", runner=runner)
        _, command, _ = _gsettings("get", SCHEMA, "command", runner=runner)
        binding = binding.strip("'")
        command = command.strip("'")
    return ShortcutStatus(supported=True, installed=installed,
                          binding=binding or DEFAULT_BINDING,
                          command=command or COMMAND)


def install_commands(binding: str = DEFAULT_BINDING, *,
                     runner=None) -> list[list[str]]:
    """Perintah gsettings untuk memasang pintasan (idempoten).

    Pintasan milik pengguna lain WAJIB dipertahankan: kita hanya
    menambahkan entri kita sendiri ke daftar yang sudah ada.
    """
    paths = _installed_paths(runner=runner)
    if CUSTOM_PATH not in paths:
        paths.append(CUSTOM_PATH)
    value = "[" + ", ".join(f"'{p}'" for p in paths) + "]"
    return [
        ["gsettings", "set", MEDIA_KEYS, "custom-keybindings", value],
        ["gsettings", "set", SCHEMA, "name", f"'{SHORTCUT_NAME}'"],
        ["gsettings", "set", SCHEMA, "command", f"'{COMMAND}'"],
        ["gsettings", "set", SCHEMA, "binding", f"'{binding}'"],
    ]


def remove_commands(*, runner=None) -> list[list[str]]:
    paths = [p for p in _installed_paths(runner=runner) if p != CUSTOM_PATH]
    value = "[" + ", ".join(f"'{p}'" for p in paths) + "]"
    return [["gsettings", "set", MEDIA_KEYS, "custom-keybindings", value]]


def install(binding: str = DEFAULT_BINDING, *, runner=None) -> tuple[bool, str]:
    """Pasang pintasan. Kembalikan (berhasil, pesan)."""
    if shutil.which("gsettings") is None:
        return False, "gsettings tidak ditemukan (bukan sesi GNOME?)"
    errors = []
    for cmd in install_commands(binding, runner=runner):
        code, _out, err = _gsettings(*cmd[1:], runner=runner)
        if code != 0:
            errors.append(err or f"gagal: {' '.join(cmd)}")
    if errors:
        return False, "; ".join(errors)
    return True, f"Pintasan {binding} dipasang."


def remove(*, runner=None) -> tuple[bool, str]:
    errors = []
    for cmd in remove_commands(runner=runner):
        code, _out, err = _gsettings(*cmd[1:], runner=runner)
        if code != 0:
            errors.append(err or f"gagal: {' '.join(cmd)}")
    return (not errors), ("; ".join(errors) if errors
                          else "Pintasan dihapus.")


def hint() -> str:
    st = status()
    return st.binding if st.installed else DEFAULT_BINDING


def desktop_is_gnome() -> bool:
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
    return "GNOME" in desktop or "UNITY" in desktop
