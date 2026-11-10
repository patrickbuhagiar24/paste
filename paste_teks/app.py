"""GUI Paste Teks — hierarki kategori/folder berisi berkas .txt.

Jalankan:  paste-teks [--dir FOLDER] [--list] [--copy FILE] [--paste FILE]
Antarmuka berbahasa Indonesia. Double-click / Enter pada berkas .txt =
salin + paste otomatis (Ctrl+V) ke aplikasi lain dalam satu klik.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

from . import __version__, uinput
from .clipboard import copy_text, simulate_paste
from .snippets import (
    Node,
    create_category,
    create_snippet,
    default_roots,
    delete_path,
    read_text,
    scan_roots,
    search as search_nodes,
    writable_root,
    write_text,
)

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog, ttk
except ImportError:  # pragma: no cover - ditangani saat runtime
    tk = None  # type: ignore[assignment]
    filedialog = messagebox = simpledialog = ttk = None  # type: ignore[assignment]

# Basis kelas GUI: object bila tkinter tidak tersedia (CLI tetap bisa dipakai).
_TkBase = tk.Tk if tk is not None else object  # type: ignore[union-attr]
_ToplevelBase = tk.Toplevel if tk is not None else object  # type: ignore[union-attr]


SETTINGS_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "paste-teks"
SETTINGS_FILE = SETTINGS_DIR / "settings.json"
ERROR_LOG = SETTINGS_DIR / "error.log"


def log_error(message: str) -> None:
    """Catat error ke ~/.config/paste-teks/error.log (untuk diagnosis)."""
    try:
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        with ERROR_LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {message}\n")
    except OSError:
        pass

def _run_in_thread(target, *args) -> None:
    """Jalankan `target` di thread daemon (pekerjaan I/O tanpa membekukan GUI)."""
    threading.Thread(target=target, args=args, daemon=True).start()


DEFAULT_SETTINGS = {
    # Jeda setelah jendela dilepas, agar compositor sempat memindahkan fokus
    # sebelum Ctrl+V dikirim. 0,6 detik cukup di GNOME Wayland.
    "focus_delay": 0.6,
    "always_on_top": False,
}


def load_settings() -> dict:
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return {**DEFAULT_SETTINGS, **data}
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_SETTINGS)


def save_settings(settings: dict) -> None:
    try:
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(settings, indent=2, ensure_ascii=False),
                                 encoding="utf-8")
    except OSError:
        pass


def filter_tree(nodes: list[Node], hits: list[Node]) -> list[Node]:
    """Bangun pohon terfilter: hanya berkas cocok + folder leluhurnya."""
    hit_paths = {h.path.resolve() for h in hits}

    def keep(node: Node) -> Node | None:
        if node.is_text:
            return node if node.path.resolve() in hit_paths else None
        kept = [k for child in node.children if (k := keep(child)) is not None]
        if not kept:
            return None
        clone = Node(name=node.name, path=node.path, is_dir=True,
                     children=kept, source_root=node.source_root)
        return clone

    return [k for n in nodes if (k := keep(n)) is not None]


class PasteTeksApp(_TkBase):  # type: ignore[misc,valid-type]
    """Jendela utama Paste Teks."""

    def __init__(self, roots: list[Path] | None = None):
        if tk is None:
            raise RuntimeError("tkinter tidak tersedia. Pasang python3-tk "
                               "(sudo apt install python3-tk).")
        try:
            super().__init__()
        except Exception as exc:
            raise RuntimeError(
                "Gagal membuka jendela (display tidak tersedia?). "
                f"Detail: {exc}") from exc
        self.title(f"Paste Teks v{__version__} — Tempel Cepat Satu Klik")
        self.geometry("980x620")
        self.minsize(720, 440)
        self.settings = load_settings()
        self.roots: list[Path] = roots or default_roots()
        self.writable = writable_root(self.roots)
        self.current_file: Path | None = None
        self._pasting = False
        self._search_after_id: str | None = None
        self._build_ui()
        self.refresh_tree()
        self._apply_settings()
        # Pastikan jendela terlihat & fokus (penting saat diluncurkan dari menu).
        self.deiconify()
        self.lift()
        self.focus_force()
        self._center_window()

    def _center_window(self) -> None:
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        x = max(0, (self.winfo_screenwidth() - w) // 2)
        y = max(0, (self.winfo_screenheight() - h) // 2)
        self.geometry(f"{w}x{h}+{x}+{y}")

    # ---------- UI ----------
    def _build_ui(self) -> None:
        # Toolbar atas
        toolbar = ttk.Frame(self, padding=6)
        toolbar.pack(fill="x")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._on_search_changed())
        ttk.Label(toolbar, text="🔍").pack(side="left")
        ttk.Entry(toolbar, textvariable=self.search_var,
                  width=26).pack(side="left", padx=(2, 6))
        buttons = (
            ("↻ Muat Ulang (F5)", self.refresh_tree, (0, 0)),
            ("📂 Buka Folder", self.open_folder, (4, 0)),
            ("＋ Kategori", self.add_category, (4, 0)),
            ("＋ Teks", self.add_text, (4, 0)),
            ("✏️ Edit", self.edit_selected, (4, 0)),
            ("🗑 Hapus", self.delete_selected, (4, 0)),
        )
        for label, command, padx in buttons:
            ttk.Button(toolbar, text=label, command=command).pack(
                side="left", padx=padx)
        # Panel utama: kiri pohon, kanan pratinjau
        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=6)

        left = ttk.Frame(paned)
        ttk.Label(left, text="Kategori / Folder → Berkas .txt").pack(anchor="w")
        self.tree = ttk.Treeview(left, show="tree", selectmode="browse")
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        paned.add(left, weight=2)

        right = ttk.Frame(paned)
        ttk.Label(right, text="Pratinjau isi teks:").pack(anchor="w")
        self.preview = tk.Text(right, wrap="word", font=("Monospace", 11),
                               state="disabled", height=16)
        self.preview.pack(fill="both", expand=True)
        self.info_var = tk.StringVar(value="Pilih berkas .txt di sebelah kiri.")
        ttk.Label(right, textvariable=self.info_var, wraplength=420,
                  foreground="gray").pack(anchor="w", pady=(4, 0))

        actions = ttk.Frame(right, padding=(0, 6, 0, 0))
        actions.pack(fill="x")
        ttk.Button(actions, text="⚡ Tempel ke Aplikasi Lain",
                   command=self.do_paste).pack(side="left")

        # Baris pengaturan
        opts = ttk.Frame(self, padding=(6, 0, 6, 4))
        opts.pack(fill="x")
        self.top_var = tk.BooleanVar(value=bool(self.settings["always_on_top"]))
        ttk.Checkbutton(opts, text="Selalu di atas", variable=self.top_var,
                        command=self._apply_settings).pack(side="left")
        ttk.Button(opts, text="🔐 Izin Paste Otomatis",
                   command=self.show_permission_info).pack(side="right")

        # Status bar
        self.status_var = tk.StringVar(value="Siap.")
        ttk.Label(self, textvariable=self.status_var, relief="sunken",
                  anchor="w", padding=4).pack(fill="x", side="bottom")

        # Menu konteks (klik kanan)
        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="⚡ Tempel ke Aplikasi Lain",
                              command=self.do_paste)
        self.menu.add_separator()
        self.menu.add_command(label="✏️ Edit", command=self.edit_selected)
        self.menu.add_command(label="🗑 Hapus", command=self.delete_selected)
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.show_preview())
        self.tree.bind("<Double-1>", lambda _e: self.on_activate())
        self.tree.bind("<Return>", lambda _e: self.on_activate())
        self.tree.bind("<Delete>", lambda _e: self.delete_selected())
        self.tree.bind("<F2>", lambda _e: self.edit_selected())
        self.bind("<F5>", lambda _e: self.refresh_tree())
        self.bind("<Control-f>", lambda _e: self.search_var.set("") or self._focus_search())

    def _on_search_changed(self) -> None:
        """Debounce pencarian 300 ms agar tidak memindai file tiap ketikan."""
        if self._search_after_id is not None:
            self.after_cancel(self._search_after_id)
        self._search_after_id = self.after(300, self.refresh_tree)

    def _focus_search(self) -> None:
        for child in self.winfo_children():
            for sub in child.winfo_children():
                if isinstance(sub, ttk.Entry):
                    sub.focus_set()
                    return

    def _on_right_click(self, event) -> None:
        row = self.tree.identify_row(event.y)
        if row:
            self.tree.selection_set(row)
            self.show_preview()
        self.menu.post(event.x_root, event.y_root)

    # ---------- Pohon ----------
    def refresh_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        nodes = scan_roots(self.roots)
        query = self.search_var.get().strip()
        if query:
            nodes = filter_tree(nodes, search_nodes(nodes, query))
        total = 0
        for root_node in nodes:
            self._insert_node("", root_node)
            total += root_node.count_texts()
        self.status_var.set(
            f"{total} berkas .txt dari {len(self.roots)} folder "
            f"({', '.join(str(r) for r in self.roots)})")
        if query:
            self.status_var.set(f"Hasil pencarian '{query}': {total} berkas cocok.")

    def _insert_node(self, parent: str, node: Node) -> str:
        icon = "📁 " if node.is_dir else "📄 "
        iid = str(node.path)
        # Hindari iid duplikat bila dua root punya path sama (dev vs sistem).
        if self.tree.exists(iid):
            iid = f"{iid}#{node.source_root}"
        self.tree.insert(parent, "end", iid=iid, text=f"{icon}{node.name}",
                         open=node.is_dir, tags=("dir" if node.is_dir else "file"))
        for child in node.children:
            self._insert_node(iid, child)
        return iid

    def selected_path(self) -> Path | None:
        sel = self.tree.selection()
        if not sel:
            return None
        # iid bisa ber-suffix #root; ambil bagian path-nya.
        raw = sel[0].split("#")[0]
        return Path(raw)

    def show_preview(self) -> None:
        path = self.selected_path()
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        if path is None or path.is_dir() or not path.exists():
            self.current_file = None
            self.preview.insert("1.0", "Pilih berkas .txt untuk melihat isinya.\n\n"
                                       "Double-click / Enter = paste otomatis ke aplikasi lain.")
            self.info_var.set("Pilih berkas .txt di sebelah kiri.")
            self.preview.configure(state="disabled")
            return
        self.current_file = path
        self._remember_selection(path)
        try:
            text = read_text(path)
        except OSError as exc:
            text = f"(gagal membaca berkas: {exc})"
        self.preview.insert("1.0", text)
        self.preview.configure(state="disabled")
        self.info_var.set(
            f"{path.name} — {len(text)} karakter, {len(text.split())} kata\n{path}")

    # ---------- Salin & paste ----------
    def on_activate(self) -> None:
        """Double-click / Enter: satu tindakan = paste ke aplikasi lain."""
        self.do_paste()

    def do_paste(self) -> None:
        """KLIK SATU KALI -> teks tertempel di aplikasi lain.

        Alur (pola yang sama dengan CopyQ/ClipClip):

          1. teks disalin ke clipboard
          2. jendela Paste Teks DILEPAS (withdraw) — bukan dikecilkan,
             bukan diturunkan. Hanya withdraw yang benar-benar menyerahkan
             fokus kembali ke aplikasi yang tadi dipakai.
          3. tunggu sesaat agar compositor memindahkan fokus
          4. kirim Ctrl+V -> tertempel di kolom input aplikasi tujuan
          5. jendela Paste Teks muncul kembali

        Riwayat bug: `iconify()` memindahkan fokus tetapi diabaikan sebagian
        compositor; `lower()` TIDAK memindahkan fokus sama sekali (jendela
        kita tetap fokus, sehingga Ctrl+V masuk ke Paste Teks sendiri).
        """
        path = self.selected_path()
        if path is None or path.is_dir():
            self.status_var.set("Pilih berkas .txt terlebih dahulu.")
            return
        try:
            text = read_text(path)
        except OSError as exc:
            messagebox.showerror("Gagal membaca", str(exc))
            return
        if self._pasting:
            self.status_var.set("Paste sedang berjalan...")
            return
        self._pasting = True
        self.status_var.set("Menyiapkan paste...")
        # LEPAS JENDELA LEBIH DULU, sebelum menyalin. Sebelumnya jendela baru
        # dilepas setelah penyalinan selesai; bila penyalinan lambat/gagal,
        # jendela tetap memegang fokus dan Ctrl+V tidak pernah terkirim
        # (gejala: log berhenti di "[paste] mulai").
        self.withdraw()
        log_error("[paste] jendela di-withdraw, menyalin di latar")
        _run_in_thread(self._paste_worker, text)

    def _paste_worker(self, text: str) -> None:
        started = time.monotonic()
        result = copy_text(text)
        elapsed_ms = (time.monotonic() - started) * 1000
        log_error(f"[paste] salin: ok={result.ok} tool={result.tool} "
                  f"verified={result.verified} ({elapsed_ms:.0f} ms)")
        self.after(0, self._after_copy, result)

    def _after_copy(self, result) -> None:
        """Setelah salinan selesai: tunggu fokus, lalu kirim Ctrl+V."""
        if not result.ok:
            self._paste_failed(result.message)
            return
        delay_ms = int(float(self.settings.get("focus_delay", 0.6)) * 1000)
        log_error(f"[paste] menunggu fokus {delay_ms} ms")
        self.after(delay_ms, self._send_paste_after_release)

    def _send_paste_after_release(self) -> None:
        _run_in_thread(self._paste_sender)

    def _paste_sender(self) -> None:
        log_error("[paste] mengirim Ctrl+V")
        result = simulate_paste()
        log_error(f"[paste] hasil: ok={result.ok} tool={result.tool} "
                  f"pesan={result.message}")
        self.after(0, self._paste_done, result)

    def _paste_done(self, result) -> None:
        self._pasting = False
        self.deiconify()
        if not result.ok:
            self.lift()
            self.status_var.set(f"📋 Teks sudah disalin. {result.message}")
            self._show_paste_failure(result.message)
            return
        if getattr(result, "confident", True):
            log_error("[paste] berhasil, jendela muncul kembali")
            self.status_var.set(f"⚡ {result.message}")
            return
        # Perintah berhasil tetapi efeknya tidak dapat dipastikan (mis. xdotool
        # di sesi Wayland). Jangan klaim berhasil — minta pengguna memeriksa.
        log_error("[paste] terkirim, keberhasilan BELUM pasti")
        self.lift()
        self.status_var.set(
            f"⚡ {result.message} Bila teks tidak muncul, tekan Ctrl+V.")

    def _paste_failed(self, message: str) -> None:
        self._pasting = False
        # Jendela sudah dilepas: WAJIB dimunculkan kembali apa pun hasilnya.
        self.deiconify()
        self.lift()
        self.status_var.set(f"Gagal menyalin ke clipboard: {message}")
        self._show_paste_failure(message)

    # ---------- izin paste otomatis (grup paste-teks) ----------
    def _show_paste_failure(self, message: str) -> None:
        """Dialog kegagalan + tombol untuk memperbaiki izin /dev/uinput."""
        st = uinput.status()
        text = (f"{message}\n\nTeks sudah ada di clipboard — "
                "tekan Ctrl+V di aplikasi tujuan.")
        if st.ready:
            messagebox.showinfo("Paste otomatis tidak tersedia", text)
            return
        text += "\n\nDiagnosis:\n" + "\n".join(_status_lines(st)) + \
            "\n\n" + uinput.explain(st)
        if uinput.can_self_enable(st):
            if messagebox.askyesno(
                    "Aktifkan paste otomatis?",
                    text + "\n\nAktifkan sekarang? Anda akan diminta "
                    "kata sandi administrator."):
                self.enable_auto_paste()
        else:
            messagebox.showinfo("Paste otomatis tidak tersedia", text)

    def enable_auto_paste(self) -> None:
        """Minta akses /dev/uinput via pkexec + helper (grup paste-teks)."""
        st = uinput.status()
        if st.device_writable:
            messagebox.showinfo("Paste otomatis", "Izin sudah aktif.")
            return
        if not uinput.can_self_enable(st):
            messagebox.showinfo(
                "Aktivasi manual diperlukan",
                "Tidak dapat meminta hak administrator dari aplikasi ini.\n\n"
                "Jalankan perintah berikut di terminal:\n\n"
                + uinput.MANUAL_COMMAND)
            return
        self.status_var.set("Meminta hak administrator...")
        _run_in_thread(self._enable_worker, st.user)

    def _enable_worker(self, user: str) -> None:
        code, out, err = uinput.enable_for_user(user)
        self.after(0, self._enable_done, user, code, out, err)

    def _enable_done(self, user: str, code: int, out: str, err: str) -> None:
        if code == 0:
            detail = out or "Akun ditambahkan ke grup 'paste-teks'."
            messagebox.showinfo(
                "Berhasil — satu langkah lagi",
                f"{detail}\n\nLogout lalu login ulang (atau reboot) agar grup "
                "baru aktif. Setelah itu paste otomatis akan bekerja.")
            self.status_var.set("✅ Izin diaktifkan — logout & login ulang.")
            return
        if code in (126, 127):
            reason = "Permintaan kata sandi dibatalkan."
        else:
            reason = (err or out or f"kode keluar {code}")
        self.status_var.set("Aktivasi izin gagal.")
        messagebox.showwarning(
            "Aktivasi gagal",
            f"{reason}\n\nAlternatif manual di terminal:\n\n"
            + uinput.MANUAL_COMMAND)

    def show_permission_info(self) -> None:
        st = uinput.status()
        text = ("Status paste otomatis\n\n"
                + "\n".join(_status_lines(st))
                + "\n\n" + uinput.explain(st))
        if st.daemon_missing:
            if messagebox.askyesno(
                    "Daemon ydotoold",
                    text + "\n\nNyalakan daemon ydotoold sekarang?"):
                self.start_ydotoold()
                return
        if uinput.can_self_enable(st):
            if messagebox.askyesno("Paste otomatis",
                                   text + "\n\nAktifkan sekarang?"):
                self.enable_auto_paste()
        else:
            messagebox.showinfo("Paste otomatis", text)

    def start_ydotoold(self) -> None:
        """Nyalakan ydotoold lewat systemd --user (tanpa hak root)."""
        self.status_var.set("Menyalakan ydotoold...")
        _run_in_thread(self._daemon_worker)

    def _daemon_worker(self) -> None:
        code, out, err = uinput.start_daemon()
        self.after(0, self._daemon_done, code, out, err)

    def _daemon_done(self, code: int, out: str, err: str) -> None:
        if code == 0 and uinput.ydotoold_running():
            self.status_var.set("✅ ydotoold berjalan.")
            messagebox.showinfo("ydotoold", "Daemon ydotoold sudah berjalan. "
                                "Silakan coba Paste Otomatis lagi.")
            return
        detail = err or out or (f"kode keluar {code}"
                                if code else "daemon belum terdeteksi")
        self.status_var.set("Gagal menyalakan ydotoold.")
        messagebox.showwarning(
            "ydotoold tidak bisa dinyalakan",
            f"{detail}\n\nCoba jalankan di terminal:\n\n  ydotoold &\n\n"
            "Atau tanpa daemon, jalankan ydotool dengan \"ydotool key "
            "ctrl+v\" — aplikasi akan memakainya sebagai cadangan.")

    # ---------- CRUD ----------
    def _target_dir(self) -> Path:
        path = self.selected_path()
        if path is not None and path.is_dir():
            return path
        if path is not None and path.parent.is_dir():
            return path.parent
        return self.writable

    def add_category(self) -> None:
        name = simpledialog.askstring("Kategori baru", "Nama folder kategori:")
        if not name:
            return
        try:
            create_category(self._target_dir(), name)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Gagal membuat kategori", str(exc))
            return
        self.refresh_tree()

    def add_text(self) -> None:
        name = simpledialog.askstring("Teks baru", "Nama berkas (tanpa .txt juga boleh):")
        if not name:
            return
        try:
            create_snippet(self._target_dir(), name, "")
        except (OSError, ValueError) as exc:
            messagebox.showerror("Gagal membuat teks", str(exc))
            return
        self.refresh_tree()
        self.edit_selected()

    def edit_selected(self) -> None:
        path = self.selected_path()
        if path is None or path.is_dir():
            self.status_var.set("Pilih berkas .txt untuk diedit.")
            return
        editor = EditorWindow(self, path)
        self.wait_window(editor)

    def delete_selected(self) -> None:
        path = self.selected_path()
        if path is None:
            return
        if not messagebox.askyesno("Hapus", f"Hapus '{path.name}'?\n{path}"):
            return
        try:
            delete_path(path)
        except OSError as exc:
            messagebox.showerror("Gagal menghapus", str(exc))
            return
        self.refresh_tree()

    def open_folder(self) -> None:
        target = self._target_dir()
        target.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.Popen(["xdg-open", str(target)])
        except OSError as exc:
            messagebox.showerror("Gagal membuka folder", str(exc))

    # ---------- Pengaturan ----------
    def _save_current_settings(self) -> None:
        self.settings["always_on_top"] = bool(self.top_var.get())
        save_settings(self.settings)

    def _apply_settings(self) -> None:
        self.attributes("-topmost", bool(self.top_var.get()))
        self._save_current_settings()


def _status_lines(st: "uinput.UinputStatus", *, prefix: str = "") -> list[str]:
    """Baris status izin siap tampil (dipakai GUI dan --check)."""
    return [f"{mark} {label}: {detail}" for mark, label, detail
            in st.summary(prefix=prefix)]


class EditorWindow(_ToplevelBase):  # type: ignore[misc,valid-type]
    """Editor teks internal untuk satu berkas .txt."""

    def __init__(self, master, path: Path):
        if tk is None:
            raise RuntimeError("tkinter tidak tersedia.")
        super().__init__(master)
        self.title(f"Edit — {path.name}")
        self.geometry("640x460")
        self.path = path
        try:
            content = read_text(path)
        except OSError as exc:
            content = f"(gagal membaca: {exc})"
        self.text = tk.Text(self, wrap="word", font=("Monospace", 11))
        self.text.pack(fill="both", expand=True, padx=8, pady=8)
        self.text.insert("1.0", content)
        bar = ttk.Frame(self, padding=8)
        bar.pack(fill="x")
        ttk.Button(bar, text="💾 Simpan (Ctrl+S)", command=self.save).pack(side="left")
        ttk.Button(bar, text="⚡ Simpan & Paste Otomatis",
                   command=self.save_and_paste).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="Tutup", command=self.destroy).pack(side="right")
        self.bind("<Control-s>", lambda _e: self.save())

    def save(self) -> None:
        try:
            write_text(self.path, self.text.get("1.0", "end-1c"))
        except OSError as exc:
            messagebox.showerror("Gagal menyimpan", str(exc))
            return
        if isinstance(self.master, PasteTeksApp):
            self.master.refresh_tree()
            self.master.status_var.set(f"💾 Tersimpan: {self.path}")

    def save_and_paste(self) -> None:
        self.save()
        if isinstance(self.master, PasteTeksApp):
            self.master.tree.selection_set(str(self.path))
            self.master.show_preview()
            self.destroy()
            self.master.do_paste()


def run_cli(argv: list[str] | None = None) -> int:
    """CLI non-GUI: --list / --copy / --paste untuk skrip & pengujian."""
    parser = argparse.ArgumentParser(
        prog="paste-teks",
        description="Paste teks hierarki dari berkas .txt (GUI + CLI).")
    parser.add_argument("--dir", action="append", default=[],
                        help="Folder snippets tambahan (boleh diulang).")
    parser.add_argument("--list", action="store_true", help="Daftar semua teks lalu keluar.")
    parser.add_argument("--copy", metavar="FILE", help="Salin isi FILE ke clipboard.")
    parser.add_argument("--paste", metavar="FILE",
                        help="Salin FILE lalu kirim Ctrl+V otomatis.")
    parser.add_argument("--version", action="store_true", help="Tampilkan versi.")
    parser.add_argument("--test-paste", action="store_true",
                        help="kirim teks uji lewat ydotool (diagnostik)")
    parser.add_argument("--ctrl-v", action="store_true",
                        help="dengan --test-paste: kirim Ctrl+V, bukan teks")
    parser.add_argument("--check", action="store_true",
                        help="Periksa lingkungan (tkinter, display, tool) lalu keluar.")
    args = parser.parse_args(argv)

    if args.version:
        print(f"paste-teks {__version__}")
        return 0

    roots = [Path(d) for d in args.dir] or default_roots()
    if args.check:
        return run_check(roots)
    if args.list:
        for node in scan_roots(roots):
            _print_tree(node, prefix="")
        return 0
    if args.test_paste:
        return run_test_paste(ctrl_v=args.ctrl_v)

    if args.copy or args.paste:
        target = Path(args.copy or args.paste or "")
        try:
            text = read_text(target)
        except OSError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        result = copy_text(text)
        print(result.message)
        if args.paste:
            paste = simulate_paste()
            print(paste.message)
            return 0 if paste.ok else 2
        return 0 if result.ok else 2

    if tk is None:
        print("Error: tkinter tidak tersedia. Pasang python3-tk "
              "(sudo apt install python3-tk).", file=sys.stderr)
        return 1

    try:
        app = PasteTeksApp(roots=roots)
    except Exception:
        tb = traceback.format_exc()
        log_error(f"Gagal memulai GUI:\n{tb}")
        print(f"Error: gagal memulai Paste Teks. Detail di {ERROR_LOG}",
              file=sys.stderr)
        print(tb, file=sys.stderr)
        return 1
    app.mainloop()
    return 0


TEST_PASTE_TEXT = "paste-teks-ok "


def run_test_paste(ctrl_v: bool = False) -> int:
    """Diagnostik: ketik teks uji (atau kirim Ctrl+V) ke jendela aktif.

    Bila teks uji muncul di aplikasi tujuan, berarti izin /dev/uinput,
    daemon, dan ydotool sudah benar — masalahnya murni di clipboard atau
    pada penekanan Ctrl+V.
    """
    from .clipboard import detect_session, ensure_ydotoold, type_text

    print(f"Tes paste — sesi grafis: {detect_session()}")
    if ctrl_v:
        print("Mengirim Ctrl+V ke jendela aktif dalam 3 detik ...")
    else:
        print(f"Mengirim teks uji {TEST_PASTE_TEXT!r} dalam 3 detik ...")
    print("Klik jendela tujuan SEKARANG (mis. kolom komentar atau chat).")
    time.sleep(3)

    if ctrl_v:
        return _full_paste_test()

    result = type_text(TEST_PASTE_TEXT)
    if not result.ok:
        print("Gagal mengetik; menyalakan ydotoold lalu mengulang ...")
        ensure_ydotoold()
        result = type_text(TEST_PASTE_TEXT)
    if not result.ok:
        print(f"❌ {result.message}")
        print(f"Periksa {ERROR_LOG} dan `paste-teks --check`.")
        return 2
    print(f"✅ {result.message}")
    print("Bila teks uji MUNCUL di jendela tujuan: ydotool + izin + daemon "
          "benar.")
    print("Bila TIDAK muncul: compositor mengabaikan perangkat uinput "
          "(cek `lsinput`/log sistem).")
    print("Uji jalur lengkap (clipboard + Ctrl+V): "
          "paste-teks --test-paste --ctrl-v")
    return 0


def _full_paste_test() -> int:
    """Uji jalur paste sebenarnya: salin -> verifikasi -> Ctrl+V."""
    from .clipboard import (copy_text, ensure_ydotoold,
                            read_text_from_clipboard, simulate_paste)

    probe = f"{TEST_PASTE_TEXT}{os.getpid()}"
    print(f"Menyalin teks uji {probe!r} ...")
    copied = copy_text(probe)
    print(("✅ " if copied.ok else "❌ ") + copied.message)
    back = read_text_from_clipboard()
    if back is None:
        print("⚠️ clipboard tidak bisa dibaca kembali — verifikasi dilewati.")
    elif back.rstrip("\n") == probe:
        print("✅ isi clipboard terverifikasi")
    else:
        print(f"❌ isi clipboard berbeda: {back.strip()[:60]!r}")
        print("   Salinan tidak benar-benar masuk; Ctrl+V akan menempelkan "
              "isi lama.")
        return 2

    print("Mengirim Ctrl+V dalam 1 detik — klik jendela tujuan SEKARANG.")
    time.sleep(1)
    paste = simulate_paste()
    if not paste.ok:
        ensure_ydotoold()
        paste = simulate_paste()
    if not paste.ok:
        print(f"❌ {paste.message}")
        return 2
    print(f"✅ {paste.message}")
    print(f"Bila muncul {probe!r} di jendela tujuan: paste otomatis berfungsi "
          "sepenuhnya.")
    return 0


def clipboard_status() -> list[str]:
    """Uji nyata: tulis & baca kembali clipboard sistem.

    wl-copy/xclip mem-fork dan memegang seleksi, jadi kegagalan di sini
    (mis. pipe menggantung sehingga tool dianggap gagal) membuat paste
    otomatis menempelkan isi lama walau Ctrl+V terkirim.
    """
    from .clipboard import (available_tools, copy_text, detect_session,
                            read_text_from_clipboard)

    tools = available_tools()
    lines = ["tool: " + ", ".join(
        f"{k}={'ada' if v else 'tidak ada'}"
        for k, v in tools.items() if k in ("wl-copy", "xclip", "xsel"))]
    if detect_session() == "unknown":
        lines.append("sesi grafis tidak terdeteksi — uji clipboard dilewati")
        return lines

    probe = f"paste-teks-{os.getpid()}"
    result = copy_text(probe)
    if not result.ok:
        lines.append(f"❌ salin: {result.message}")
        return lines
    lines.append(f"✅ salin via {result.tool}")

    back = read_text_from_clipboard()
    if back is None:
        lines.append("(baca kembali tidak didukung — pasang wl-paste/xclip)")
    elif back.strip() == probe:
        lines.append("✅ baca kembali cocok")
    else:
        lines.append(f"⚠️ baca kembali berbeda: {back.strip()[:40]!r}")
    return lines


def run_check(roots: list[Path]) -> int:
    """Cetak laporan diagnosis lingkungan (untuk pemecahan masalah)."""
    from .clipboard import available_tools, detect_session

    print(f"paste-teks {__version__}")
    print(f"Python: {sys.version.split()[0]} ({sys.executable})")
    print(f"Sesi grafis: {detect_session()} "
          f"(DISPLAY={os.environ.get('DISPLAY', '-')}, "
          f"WAYLAND_DISPLAY={os.environ.get('WAYLAND_DISPLAY', '-')})")
    print(f"tkinter: {'tersedia' if tk is not None else 'TIDAK tersedia — pasang python3-tk'}")
    tools = available_tools()
    print("Tool clipboard/paste: "
          + ", ".join(f"{k}={'ada' if v else 'tidak ada'}" for k, v in tools.items()))
    try:
        st = uinput.status()
        print(f"Izin paste otomatis (grup '{uinput.GROUP}', akun '{st.user}'):")
        for line in _status_lines(st):
            print(f"  {line}")
        print("\nLangkah berikutnya:")
        for line in uinput.explain(st).splitlines():
            print(f"  {line}")
    except Exception as exc:  # pragma: no cover - jangan gagalkan --check
        print(f"Izin paste otomatis: tidak dapat diperiksa ({exc})")
    clip = clipboard_status()
    print("Clipboard:")
    for line in clip:
        print(f"  {line}")
    print("Folder snippets:")
    for r in roots:
        print(f"  - {r} ({'ada' if r.is_dir() else 'belum ada'})")
    print(f"Pengaturan: {SETTINGS_FILE}")
    print(f"Log error: {ERROR_LOG}")
    return 0


def _print_tree(node: Node, prefix: str) -> None:
    label = node.name + ("/" if node.is_dir else ".txt")
    print(f"{prefix}{label}")
    for child in node.children:
        _print_tree(child, prefix + "  ")


def main() -> None:
    try:
        code = run_cli()
    except Exception:
        tb = traceback.format_exc()
        log_error(tb)
        print(f"Error tak terduga. Detail di {ERROR_LOG}", file=sys.stderr)
        print(tb, file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
