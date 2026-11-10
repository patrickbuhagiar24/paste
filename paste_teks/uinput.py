"""Deteksi & aktivasi izin /dev/uinput untuk paste otomatis (ydotool).

ydotool mengirim Ctrl+V dengan menulis event ke ``/dev/uinput``. Perangkat
itu dimiliki ``root:input`` dengan mode 0660, sehingga pengguna biasa tidak
punya akses — dan inilah penyebab paling umum "paste otomatis tidak jalan".

Alih-alih memasukkan pengguna ke grup ``input`` (yang memberi akses ke
SEMUA perangkat input, setara keylogger), paket ini memakai grup khusus
``paste-teks`` + udev rule yang hanya membuka ``/dev/uinput``.

Catatan penting: keanggotaan grup **di sistem** (``/etc/group``) berbeda
dengan keanggotaan **di sesi berjalan**. Bila akun sudah ditambahkan tetapi
sesi belum dimulai ulang, proses masih memakai daftar grup yang lama — dan
itulah yang diperiksa ``session_groups()``.
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
# Letak resminya di $XDG_RUNTIME_DIR (biasanya /run/user/<uid>/ydotool.sock).
SOCKET_PATHS = ("/run/ydotoold.sock", "/tmp/ydotool.sock")

COMMON_GROUPS = ("sudo", "admin", "wheel", "users", "plugdev", "input",
                 "audio", "video", "docker", "adm", "staff", "lpadmin",
                 "netdev", "bluetooth", "scanner", "cdrom", "lp", "dialout",
                 "floppy", "games", "kvm", "render", "systemd-journal")

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
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except KeyError:  # pragma: no cover - sangat jarang
        return os.environ.get("USER") or os.environ.get("LOGNAME") or "unknown"


def user_groups(user: str | None = None, *, grouplist=None) -> set[str]:
    """Semua grup yang tercatat untuk ``user`` di sistem (NSS/LDAP)."""
    import grp

    name = user or current_user()
    try:
        entry = pwd.getpwnam(name)
    except KeyError:
        return set()
    if grouplist is None:
        grouplist = os.getgrouplist
    try:
        gids = grouplist(name, entry.pw_gid)
    except KeyError:
        # Basis grup NSS tidak bisa diakses (mis. sedang offline).
        gids = [entry.pw_gid]
    names = set()
    for gid in gids:
        try:
            names.add(grp.getgrgid(gid).gr_name)
        except KeyError:
            continue
    return names


def _proc_groups(*, path: str = "/proc/self/status") -> set[str] | None:
    """Grup yang dimiliki proses saat ini (sesi berjalan), dibaca dari /proc."""
    import grp

    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.startswith("Groups:"):
                    continue
                gids = [int(part) for part in line.split()[1:]]
                names = set()
                for gid in gids:
                    try:
                        names.add(grp.getgrgid(gid).gr_name)
                    except KeyError:
                        continue
                return names
    except (OSError, ValueError):
        return None
    return None


def session_groups() -> set[str] | None:
    """Grup yang BENAR-BENAR aktif di proses ini (bukan hanya di /etc/group)."""
    return _proc_groups()


def device_writable(*, access=None) -> bool:
    """Apakah /dev/uinput bisa ditulis proses ini."""
    if access is None:
        access = os.access
    try:
        return bool(access(DEVICE, os.W_OK))
    except OSError:  # pragma: no cover
        return False


def device_details(dev: str = DEVICE) -> tuple[int, int, str] | None:
    """(uid, gid, mode) perangkat bila ada."""
    from grp import getgrgid

    try:
        info = os.stat(dev)
    except OSError:
        return None
    try:
        group = getgrgid(info.st_gid).gr_name
    except (KeyError, ImportError):
        group = str(info.st_gid)
    return info.st_uid, info.st_gid, group


def socket_candidates() -> list[str]:
    """Semua lokasi socket yang mungkin dipakai ydotoold."""
    uid = os.getuid()
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"
    return [f"{runtime}/ydotool.sock",
            f"{runtime}/.ydotool_socket",
            *SOCKET_PATHS]


def acl_granted(dev: str = DEVICE, *, uid: int | None = None) -> bool:
    """Apakah ACL memberi izin tulis kepada pengguna ini di ``dev``?

    TAG+="uaccess" membuat logind menambahkan ACL ``user:<uid>:rw-`` pada
    perangkat. Karena itu akses bisa ada tanpa keanggotaan grup apa pun.
    """
    try:
        import subprocess

        out = subprocess.run(["getfacl", "--absolute-names", "-p", dev],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, Exception):
        return False
    want = f"user:{uid if uid is not None else os.getuid()}:"
    for line in out.splitlines():
        line = line.strip()
        if line.startswith(want) and "w" in line.rsplit(":", 1)[-1]:
            return True
    return False


def ydotoold_socket() -> str | None:
    """Lokasi socket ydotoold bila ada (untuk ydotool >= 1.0)."""
    for path in socket_candidates():
        if os.path.exists(path):
            return path
    return None


def ydotoold_running() -> bool:
    """Daemon berjalan — terlihat dari socket atau prosesnya."""
    if ydotoold_socket() is not None:
        return True
    for pid in ("/proc",):
        try:
            entries = os.listdir(pid)
        except OSError:  # pragma: no cover
            continue
        for entry in entries:
            if not entry.isdigit():
                continue
            try:
                with open(f"/proc/{entry}/comm", encoding="utf-8",
                          errors="replace") as handle:
                    if handle.read().strip() == "ydotoold":
                        return True
            except OSError:
                continue
    return False


def udev_rule_installed(rule: str = UDEV_RULE) -> bool:
    return os.path.exists(rule)


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
    session: set[str] | None = None
    daemon: bool = False
    rule: bool = False
    acl: bool = False

    # --- turunan -------------------------------------------------------
    @property
    def in_session(self) -> bool:
        """Grup paste-teks aktif di sesi berjalan (bukan hanya di sistem)."""
        if self.session is None:
            return self.device_writable
        return GROUP in self.session

    @property
    def account_ready(self) -> bool:
        """Akun sudah tercatat di grup (mungkin sesi belum memuat)."""
        return self.in_group or self.in_session

    @property
    def acl_granted(self) -> bool:
        """Akses berasal dari ACL uaccess (tanpa perlu grup / logout)."""
        return self.device_writable and self.acl

    @property
    def needs_relogin(self) -> bool:
        """Sudah jadi anggota di sistem, tetapi sesi belum memuatnya."""
        return self.account_ready and not self.in_session

    @property
    def daemon_ready(self) -> bool:
        return self.daemon or self.socket is not None

    @property
    def ready(self) -> bool:
        """Semua yang dibutuhkan untuk mengirim Ctrl+V sudah tersedia?"""
        if self.device_writable:
            if not self.ydotoold_binary:
                return True
            return self.daemon_ready
        return self.ydotoold_binary and self.daemon_ready

    @property
    def daemon_missing(self) -> bool:
        return self.ydotoold_binary and not self.daemon_ready

    def summary(self, prefix: str = "") -> list[tuple[str, str, str]]:
        """(tanda, label, keterangan) untuk ditampilkan berbaris rapi."""
        def state(ok: bool) -> str:
            return "✅" if ok else "❌"

        rows: list[tuple[str, str, str]] = []

        if self.device_writable and self.account_ready:
            group_detail = "anggota, aktif di sesi ini"
        elif self.needs_relogin:
            group_detail = "anggota di sistem — sesi belum memuat"
        elif self.in_group or self.in_session:
            group_detail = "anggota, aktif di sesi ini"
        elif self.group_exists:
            group_detail = "belum jadi anggota"
        else:
            group_detail = "grup belum ada — paket belum terpasang benar?"

        if self.acl_granted and not self.account_ready:
            rows.append(("✅", f"{prefix}akses {DEVICE}",
                         "ACL sesi aktif (tanpa grup, tanpa logout)"))
        else:
            # Bila perangkat sudah bisa ditulis, akses pasti aktif — /proc
            # mungkin tidak terbaca (container, NSS khusus), jadi jangan
            # salah memberi tanda.
            group_ok = self.account_ready and (self.in_session
                                               or self.device_writable)
            rows.append((state(group_ok), f"{prefix}grup '{GROUP}'",
                         group_detail))

        if self.device_writable:
            device_detail = f"{DEVICE} bisa ditulis"
        elif not self.rule:
            device_detail = f"{DEVICE} tidak bisa ditulis — udev rule belum ada"
        elif self.needs_relogin:
            device_detail = (f"{DEVICE} tidak bisa ditulis — muncul setelah "
                             "login ulang")
        else:
            device_detail = f"{DEVICE} tidak bisa ditulis"
        rows.append((state(self.device_writable), f"{prefix}tulis {DEVICE}",
                     device_detail))

        if self.ydotoold_binary:
            if self.daemon_ready:
                where = self.socket or "proses ydotoold"
                rows.append(("✅", f"{prefix}daemon ydotoold",
                             f"berjalan ({where})"))
            else:
                rows.append(("❌", f"{prefix}daemon ydotoold",
                             "belum berjalan"))
        return rows


def status(user: str | None = None) -> UinputStatus:
    name = user or current_user()
    groups = user_groups(name)
    try:
        import grp

        grp.getgrnam(GROUP)
        group_exists = True
    except (KeyError, ImportError):
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
        session=session_groups(),
        daemon=ydotoold_running(),
        rule=udev_rule_installed(),
        acl=acl_granted(),
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


def daemon_command() -> list[str]:
    return ["systemctl", "--user", "start", "ydotoold"]


def start_daemon(*, runner: Callable[..., object] | None = None,
                 timeout: int = 20) -> tuple[int, str, str]:
    """Nyalakan ydotoold (systemd --user). Kembalikan (kode, out, err)."""
    if runner is None:
        import subprocess

        def runner(cmd, **kw):
            return subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=timeout, **kw)
    try:
        proc = runner(daemon_command())
        return (getattr(proc, "returncode", -1),
                (getattr(proc, "stdout", "") or "").strip(),
                (getattr(proc, "stderr", "") or "").strip())
    except (OSError, Exception) as exc:
        return -1, "", str(exc)


def explain(st: UinputStatus | None = None) -> str:
    """Langkah perbaikan berikutnya, dalam bahasa Indonesia.

    Urutan sengaja: login ulang dulu (persyaratan sesi), baru daemon, lalu
    aktivasi grup. Bila semuanya siap, kembalikan pesan positif.
    """
    st = st or status()
    if st.ready:
        return "Paste otomatis siap digunakan."

    steps: list[str] = []
    if not st.account_ready:
        steps.append(
            f"Akun '{st.user}' belum menjadi anggota grup '{GROUP}'. Klik "
            "\"Aktifkan sekarang\" (atau pakai perintah manual di README).")
    if st.needs_relogin:
        steps.append(
            f"Akun '{st.user}' sudah menjadi anggota grup '{GROUP}' di "
            "sistem, tetapi SESI INI belum memuatnya. Logout lalu login "
            "ulang (atau reboot) — langkah ini wajib.")
    if st.daemon_missing:
        steps.append(
            "ydotoold belum berjalan. Jalankan `systemctl --user start "
            "ydotoold` (atau `ydotoold &`) lalu coba lagi.")
    if st.in_session and not st.device_writable and not st.needs_relogin:
        if not st.rule:
            steps.append(
                "udev rule grup 'paste-teks' tidak ditemukan. Pasang ulang "
                "paket: `sudo apt install --reinstall paste-teks`.")
        else:
            steps.append(
                f"Izin {DEVICE} belum berlaku. Muat ulang aturan udev:\n"
                "sudo udevadm control --reload-rules && "
                "sudo udevadm trigger --sysname-match=uinput")
    if not steps:  # pragma: no cover - jaring pengaman
        steps.append("Periksa `paste-teks --check` untuk detail.")
    return "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))
