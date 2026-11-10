"""Clipboard + paste otomatis (satu klik ke aplikasi lain).

Urutan prioritas disesuaikan Ubuntu 26.10 (GNOME, Wayland default):

  Salin  : wl-copy (Wayland) -> xclip -> xsel
  Verifikasi : wl-paste (Wayland) -> xclip -o -> xsel -o  (maks 0,3 detik)
  Paste  : ydotool (Wayland) -> xdotool (X11)

Semua pemanggilan proses lewat `runner` yang bisa diganti saat pengujian,
sehingga modul ini bisa diuji tanpa clipboard asli.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


# Tool clipboard yang mem-fork lalu tetap hidup untuk memegang seleksi.
# Bila output-nya ditangkap, pipe tidak pernah EOF -> subprocess menunggu
# sampai timeout dan melaporkan GAGAL padahal clipboard sudah terisi.
_FORKING_TOOLS = frozenset({"wl-copy", "xclip", "xsel"})

# Batas waktu untuk MEMBACA clipboard. Tool pembaca dapat menunggu seleksi
# muncul; bila tidak ada, jangan sampai menahan GUI. Dulu nilai ini mengikuti
# timeout umum (5 s) sehingga verifikasi 5 percobaan bisa mencapai ~25 detik.
READ_TIMEOUT = 0.3

# Tool pembaca clipboard (dipakai untuk verifikasi & diagnosis).
READER_TOOLS = ("wl-paste", "xclip", "xsel")


def _default_runner(*args, **kwargs) -> subprocess.CompletedProcess[str]:
    cmd = args[0] if args else kwargs.get("args")
    tool = cmd[0] if isinstance(cmd, (list, tuple)) and cmd else str(cmd or "")
    kwargs.setdefault("input", None)
    kwargs.setdefault("text", True)
    kwargs.setdefault("timeout", 5)
    if tool in _FORKING_TOOLS:
        # Jangan tunggu proses selesai: wl-copy/xclip sengaja tetap hidup.
        # Tulis masukan, tutup stdin, beri tenggang singkat, lalu anggap
        # berhasil bila proses masih hidup (itu memang perilaku normalnya).
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
            code = 0  # masih memegang seleksi — normal, bukan gagal
        return subprocess.CompletedProcess(cmd, code, "", "")
    kwargs.setdefault("capture_output", True)
    if tool in READER_TOOLS:
        # Pembacaan clipboard tidak boleh menggantung; kegagalan cepat lebih
        # baik daripada GUI tampak membeku.
        if kwargs.get("timeout", 5) > READ_TIMEOUT:
            kwargs["timeout"] = READ_TIMEOUT
    return subprocess.run(*args, **kwargs)


@dataclass
class ClipboardResult:
    ok: bool
    tool: str = ""
    message: str = ""
    verified: bool = False


@dataclass
class PasteResult:
    ok: bool
    tool: str = ""
    message: str = ""
    # False bila perintah berhasil tetapi efeknya tidak dapat dipastikan
    # (mis. xdotool di sesi Wayland: hanya menjangkau jendela XWayland,
    # aplikasi Wayland asli tidak menerima Ctrl+V walau exit code 0).
    confident: bool = True


def detect_session() -> str:
    """Deteksi sesi grafis: 'wayland', 'x11', atau 'unknown'."""
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "unknown"


def available_tools() -> dict[str, bool]:
    """Cek ketersediaan tool clipboard/paste di PATH."""
    return {name: shutil.which(name) is not None
            for name in ("wl-copy", "xclip", "xsel", "ydotool", "xdotool")}


def verify_clipboard(expected: str, runner: Runner = _default_runner,
                     *, attempts: int = 2, delay: float = 0.05) -> bool:
    """Pastikan clipboard benar-benar berisi ``expected``.

    Tool clipboard di Wayland (wl-copy) mem-fork, jadi isi clipboard bisa
    belum siap tepat setelah perintah kembali. Dicoba beberapa kali.
    Mengembalikan False bila pembacaan tidak didukung sama sekali.

    Seluruh pemeriksaan dibatasi ±0,5 detik: verifikasi adalah pelengkap,
    bukan tujuan. Sebelumnya 5 percobaan × timeout 5 detik membuat satu
    klik terasa seperti hang (terukur 7 detik di mesin pengguna).
    """
    target = expected.rstrip("\n")
    for attempt in range(attempts):
        current = read_text_from_clipboard(runner=runner)
        if current is not None and current.rstrip("\n") == target:
            return True
        if attempt + 1 < attempts and _sleep is not None:
            _sleep(delay)
    return False


def copy_text(text: str, runner: Runner = _default_runner) -> ClipboardResult:
    """Salin teks ke clipboard sistem. Kembalikan tool yang berhasil."""
    session = detect_session()
    # Urutan kandidat: Wayland dulu bila sesi Wayland, sebaliknya X11 dulu.
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
    unverified = None
    for name, cmd in candidates:
        if shutil.which(cmd[0]) is None:
            continue
        try:
            proc = runner(cmd, input=text)
            if proc.returncode != 0:
                detail = " ".join(filter(None, [
                    (getattr(proc, "stderr", "") or "").strip(),
                    (getattr(proc, "stdout", "") or "").strip()]))
                errors.append(f"{name}: {detail or proc.returncode}")
                continue
            # Exit code 0 belum tentu berarti clipboard benar-benar terisi
            # (mis. wl-copy tanpa Wayland yang valid). Baca kembali isinya.
            if verify_clipboard(text, runner=runner):
                return ClipboardResult(ok=True, tool=name, verified=True,
                                       message=f"Teks disalin dengan {name}.")
            if unverified is None:
                unverified = name
            errors.append(f"{name}: tersalin tetapi isi clipboard tidak cocok")
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f"{name}: {exc}")
    if unverified is not None:
        # Salinan "berhasil" menurut tool walau verifikasi tak bisa dilakukan.
        return ClipboardResult(
            ok=True, tool=unverified, verified=False,
            message=(f"Teks disalin dengan {unverified} (belum terverifikasi — "
                     "pasang wl-paste/xclip untuk pemeriksaan)."))
    detail = "; ".join(errors) if errors else "tidak ada tool clipboard ditemukan"
    return ClipboardResult(
        ok=False, tool="",
        message=(f"Gagal menyalin ({detail}). Pasang wl-clipboard/xclip, "
                 "atau gunakan tombol Salin di aplikasi (fallback internal)."))


def ensure_ydotoold(runner: Runner = _default_runner) -> bool:
    """Nyalakan daemon ydotoold bila belum jalan. True bila tampak siap.

    Dipakai otomatis saat `ydotool key` gagal, sehingga pengguna tidak perlu
    menyiapkan apa pun lebih dulu. Tidak fatal bila systemd tidak tersedia.
    """
    try:
        proc = runner(["systemctl", "--user", "start", "ydotoold.service"])
    except (OSError, subprocess.SubprocessError):
        return False
    if getattr(proc, "returncode", 1) == 0:
        # Beri kesempatan socket muncul sebelum percobaan berikutnya.
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
    for path in (f"{runtime}/ydotool.sock", "/run/ydotoold.sock",
                 "/tmp/ydotool.sock"):
        if os.path.exists(path):
            return True
    return False


try:  # mempermudah pengujian: waktu bisa dipalsukan
    from time import sleep as _sleep
except ImportError:  # pragma: no cover
    _sleep = None


def read_text_from_clipboard(runner: Runner = _default_runner) -> str | None:
    """Baca isi clipboard (untuk diagnosis). None bila tidak didukung."""
    session = detect_session()
    if session == "wayland":
        candidates = [["wl-paste", "--no-newline"],
                      ["xclip", "-selection", "clipboard", "-o"]]
    else:
        candidates = [["xclip", "-selection", "clipboard", "-o"],
                      ["wl-paste", "--no-newline"]]
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
    """Ketik teks apa adanya lewat ydotool (untuk diagnosis).

    Berbeda dari Ctrl+V: tidak bergantung pada clipboard sama sekali.
    """
    if shutil.which("ydotool") is None:
        return PasteResult(ok=False, tool="",
                          message="ydotool tidak terpasang.")
    try:
        proc = runner(["ydotool", "type", text])
        if proc.returncode == 0:
            return PasteResult(ok=True, tool="ydotool",
                               message=f"Teks uji terkirim via ydotool: {text!r}")
        detail = " ".join(filter(None, [
            (getattr(proc, "stderr", "") or "").strip(),
            (getattr(proc, "stdout", "") or "").strip()]))
        return PasteResult(ok=False, tool="ydotool",
                          message=f"ydotool type gagal: {detail or proc.returncode}")
    except (OSError, subprocess.SubprocessError) as exc:
        return PasteResult(ok=False, tool="", message=str(exc))


def simulate_paste(runner: Runner = _default_runner) -> PasteResult:
    """Kirim Ctrl+V ke aplikasi yang sedang fokus (paste otomatis)."""
    session = detect_session()
    # ydotool >= 1.0 (breaking change): `key` HANYA menerima keycode, ditulis
    # sebagai <keycode>:<tekan/lepas>. Linux: 29=LeftCtrl, 47=V, 42=Shift.
    # Urutan: tekan Ctrl, tekan V, lepas V, lepas Ctrl.
    KEYCODE_CTRL_V = ["ydotool", "key", "29:1", "47:1", "47:0", "29:0"]
    # ydotool 0.1.x memakai nama kunci; tetap dicoba sebagai cadangan.
    NAME_CTRL_V = ["ydotool", "key", "ctrl+v"]
    if session == "wayland":
        # xdotool SENGAJA tidak dipakai di Wayland: ia memakai XTEST sehingga
        # hanya menjangkau jendela XWayland, dan tetap mengembalikan kode 0
        # walau aplikasi Wayland asli tidak menerima apa pun. Melaporkan
        # "berhasil" di kondisi itu menyesatkan (persis jenis kebohongan yang
        # menyulitkan diagnosis versi sebelumnya).
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
            # ydotool butuh daemon; bila gagal, nyalakan sekali lalu ulangi.
            if proc.returncode != 0 and name == "ydotool" and not daemon_tried:
                daemon_tried = True
                if ensure_ydotoold(runner=runner):
                    proc = runner(cmd)
            if proc.returncode == 0:
                return PasteResult(
                    ok=True, tool=name,
                    message=f"Paste otomatis terkirim via {name}.",
                    confident=(name != "xdotool"))
            detail = " ".join(filter(None, [
                (getattr(proc, "stderr", "") or "").strip(),
                (getattr(proc, "stdout", "") or "").strip(),
            ]))
            errors.append(f"{name}: {detail or proc.returncode}")
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f"{name}: {exc}")
    if errors:
        detail = "; ".join(errors)
    elif session == "wayland":
        detail = ("ydotool tidak ditemukan; di GNOME Wayland hanya ydotool "
                  "yang dapat mengirim Ctrl+V ke aplikasi Wayland")
    else:
        detail = "tidak ada tool paste otomatis ditemukan"
    return PasteResult(
        ok=False, tool="",
        message=(f"Paste otomatis tidak tersedia ({detail}). "
                 "Teks sudah disalin — tekan Ctrl+V di aplikasi tujuan. "
                 "Jalankan `paste-teks --check` untuk diagnosis."))
