"""Clipboard + paste otomatis (satu klik ke aplikasi lain).

Urutan prioritas disesuaikan Ubuntu 26.10 (GNOME, Wayland default):

  Salin : wl-copy (Wayland) -> xclip -> xsel -> Tk clipboard (fallback GUI)
  Paste : ydotool (Wayland) -> xdotool (X11) -> salin saja + ingatkan Ctrl+V

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


def _default_runner(*args, **kwargs) -> subprocess.CompletedProcess[str]:
    kwargs.setdefault("input", None)
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    kwargs.setdefault("timeout", 5)
    return subprocess.run(*args, **kwargs)


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
    for name, cmd in candidates:
        if shutil.which(cmd[0]) is None:
            continue
        try:
            proc = runner(cmd, input=text)
            if proc.returncode == 0:
                return ClipboardResult(ok=True, tool=name,
                                       message=f"Teks disalin dengan {name}.")
            errors.append(f"{name}: {proc.stderr.strip() or proc.returncode}")
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f"{name}: {exc}")
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


def simulate_paste(runner: Runner = _default_runner) -> PasteResult:
    """Kirim Ctrl+V ke aplikasi yang sedang fokus (paste otomatis)."""
    session = detect_session()
    # Keycode Linux: 29=LeftCtrl, 47=V → Ctrl+V (42=Shift, JANGAN dipakai).
    # Satu perintah per tool: `ydotool key` hanya menerima SATU argumen
    # spesifikasi kunci, jadi "ctrl+v" (nama kunci) lebih andal daripada
    # rangkaian keycode yang bisa ditolak sebagai argumen berlebih.
    if session == "wayland":
        candidates = [
            ("ydotool", ["ydotool", "key", "ctrl+v"]),
            ("xdotool", ["xdotool", "key", "ctrl+v"]),
        ]
    else:
        candidates = [
            ("xdotool", ["xdotool", "key", "ctrl+v"]),
            ("ydotool", ["ydotool", "key", "ctrl+v"]),
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
                return PasteResult(ok=True, tool=name,
                                   message=f"Paste otomatis terkirim via {name}.")
            stderr = (getattr(proc, "stderr", "") or "").strip()
            errors.append(f"{name}: {stderr or proc.returncode}")
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f"{name}: {exc}")
    detail = "; ".join(errors) if errors else "tidak ada tool paste otomatis ditemukan"
    return PasteResult(
        ok=False, tool="",
        message=(f"Paste otomatis tidak tersedia ({detail}). "
                 "Teks sudah disalin — tekan Ctrl+V di aplikasi tujuan. "
                 "Untuk Wayland pasang ydotool dan aktifkan izin /dev/uinput "
                 "lewat menu \"Izin Paste Otomatis\" di aplikasi."))
