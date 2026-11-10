"""Deteksi & aktivasi izin /dev/uinput untuk paste otomatis (ydotool).

ydotool mengirim Ctrl+V dengan menulis event ke ``/dev/uinput``. Perangkat
itu dimiliki ``root:input`` dengan mode 0660, sehingga pengguna biasa tidak
punya akses — dan inilah penyebab paling umum "paste otomatis tidak jalan".

Alih-alih memasukkan pengguna ke grup ``input`` (yang memberi akses ke
SEMUA perangkat input, setara keylogger), paket ini memakai grup khusus
``paste-teks`` + udev rule yang hanya membuka ``/dev/uinput``.
"""
from __future__ import annotations

import os
import pwd
import shutil
import stat
from dataclasses import dataclass, field
from typing import Callable

GROUP = "paste-teks"
HELPER = "/usr/lib/paste-teks/paste-teks-uinput-group"
UDEV_RULE = "/usr/lib/udev/rules.d/70-paste-teks-uinput.rules"
DEVICE = "/dev/uinput"

# ydotool >= 1.0 berbicara dengan daemon ydotoold lewat socket Unix.
SOCKET_PATHS = ("/run/ydotoold.sock", "/tmp/ydotool.sock")
USER_SOCKET = "/run/user/{uid}/ydotool.sock"

MANUAL_COMMAND = (
    "sudo groupadd --system paste-teks\n"
    "echo 'KERNEL==\"uinput\", SUBSYSTEM==\"misc\", GROUP=\"paste-teks\", "
    "MODE=\"0660\", OPTIONS+=\"static_node=uinput\"' \\\n"
    "  | sudo tee /usr/lib/udev/rules.d/70-paste-teks-uinput.rules\n"
    "sudo usermod -aG paste-teks $USER\n"
    "sudo udevadm control --reload-rules && sudo udevadm trigger --sysname-match=uinput\n"
    "# lalu logout & login ulang"
)


def current_user() -> str:
    """Nama pengguna yang menjalankan aplikasi (bukan root)."""
    user = os.environ.get("PKEXEC_UID") or os.environ.get("USER") \
        or os.environ.get("LOGNAME")
    if user and user.isidentifier():
        pass
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except KeyError:  # pragma: no cover - sangat jarang
        return user or "unknown"


def user_groups(user: str | None = None, *, grouplist=None) -> set[str]:
    """Semua grup efektif milik ``user`` (termasuk dari NSS/LDAP)."""
    name = user or current_user()
    if grouplist is None:
        grouplist = os.getgrouplist
    try:
        entry = pwd.getpwnam(name)
    except KeyError:
        return set()
    try:
        gids = grouplist(name, entry.pw_gid)
    except (KeyError, OSError):
        gids = [entry.pw_gid]
    import grp
    names = set()
    for gid in gids:
        try:
            names.add(grp.getgrgid(gid).gr_name)
        except KeyError:
            continue
    return names


def device_writable(*, access=None) -> bool:
    """Apakah /dev/uinput bisa ditulis proses ini."""
    if access is None:
        access = os.access
    try:
        return bool(access(DEVICE, os.W_OK))
    except OSError:  # pragma: no cover
        return False


def ydotoold_socket() -> str | None:
    """Lokasi socket ydotoold bila ada (untuk ydotool >= 1.0)."""
    candidates = list(SOCKET_PATHS) + [USER_SOCKET.format(uid=os.getuid())]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def helper_available() -> bool:
    """Apakah helper root aman dipanggil lewat pkexec?

    Hanya percaya pada berkas milik root yang tidak bisa ditulis pengguna
    lain — mencegah eskalasi hak akses lewat berkas yang bisa dimodifikasi.
    """
    try:
        info = os.stat(HELPER)
    except OSError:
        return False
    if info.st_uid != 0:
        return False
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return False
    return os.access(HELPER, os.X_OK)


@dataclass
class UinputStatus:
    user: str
    group_exists: bool
    in_group: bool
    device_writable: bool
    helper: bool
    pkexec: bool
    ydotoold_binary: bool
    socket: str | None
    groups: set[str] = field(default_factory=set)

    @property
    def ready(self) -> bool:
        """Semua yang dibutuhkan untuk mengirim Ctrl+V sudah tersedia?"""
        if self.device_writable:
            return True
        return self.ydotoold_binary and self.socket is not None

    @property
    def needs_relogin(self) -> bool:
        """Sudah masuk grup tetapi sesi belum memuatnya."""
        return self.in_group and not self.device_writable

    def summary(self) -> list[str]:
        def mark(ok: bool) -> str:
            return "✅" if ok else "❌"

        lines = [
            f"{mark(self.in_group)} grup '{GROUP}'"
            + (" (anggota)" if self.in_group else " (belum jadi anggota)"),
            f"{mark(self.device_writable)} tulis {DEVICE}",
        ]
        if self.ydotoold_binary:
            lines.append(
                f"{mark(self.socket is not None)} daemon ydotoold"
                + (f" ({self.socket})" if self.socket else " (belum berjalan)"))
        return lines


def status(user: str | None = None) -> UinputStatus:
    name = user or current_user()
    groups = user_groups(name)
    try:
        import grp
        group_exists = True
        grp.getgrnam(GROUP)
    except (KeyError, ImportError):  # pragma: no cover - dipanggil di root
        group_exists = False
    return UinputStatus(
        user=name,
        group_exists=group_exists,
        in_group=GROUP in groups,
        device_writable=device_writable(),
        helper=helper_available(),
        pkexec=shutil.which("pkexec") is not None,
        ydotoold_binary=shutil.which("ydotoold") is not None,
        socket=ydotoold_socket(),
        groups=groups,
    )


def can_self_enable(st: UinputStatus | None = None) -> bool:
    """Bisa menawarkan tombol "Aktifkan sekarang" (pkexec + helper)?"""
    st = st or status()
    return st.helper and st.pkexec and not st.device_writable


def enable_command(user: str | None = None) -> list[str]:
    """Perintah pkexec yang dijalankan untuk menambah pengguna ke grup."""
    return ["pkexec", HELPER, user or current_user()]


def enable_for_user(user: str | None = None, *,
                    runner: Callable[..., object] | None = None,
                    timeout: int = 120) -> tuple[int, str, str]:
    """Jalankan helper via pkexec. Kembalikan (kode, stdout, stderr).

    kode 126/127 = permintaan kata sandi dibatalkan / pkexec tidak ada.
    """
    if runner is None:
        import subprocess

        def runner(cmd, **kw):
            return subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=timeout, **kw)

    try:
        proc = runner(enable_command(user))
        return (getattr(proc, "returncode", -1),
                (getattr(proc, "stdout", "") or "").strip(),
                (getattr(proc, "stderr", "") or "").strip())
    except (OSError, Exception) as exc:  # SubprocessError & TimeoutExpired
        return -1, "", str(exc)


def explain(st: UinputStatus | None = None) -> str:
    """Penjelasan ramah dalam bahasa Indonesia untuk dialog aplikasi."""
    st = st or status()
    if st.ready:
        return "Paste otomatis siap digunakan."
    if st.needs_relogin:
        return (
            f"Akun '{st.user}' sudah menjadi anggota grup '{GROUP}', tetapi "
            "sesi ini belum memuatnya. Logout lalu login ulang (atau reboot) "
            "agar paste otomatis aktif."
        )
    if st.ydotoold_binary and st.socket is None:
        return (
            "ydotoold belum berjalan. Jalankan `systemctl --user start "
            "ydotoold` (atau `ydotoold &`), lalu coba lagi."
        )
    return (
        f"Akun '{st.user}' belum punya izin menulis {DEVICE}, sehingga "
        "ydotool tidak bisa mengirim Ctrl+V. Klik \"Aktifkan sekarang\" untuk "
        f"menambahkan akun ke grup '{GROUP}'."
    )
