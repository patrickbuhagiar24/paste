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

DEFAULT_SETTINGS = {
    "delay": 2.5,          # detik sebelum paste otomatis dikirim
    "auto_paste": True,    # double-click = paste otomatis (bukan sekadar salin)
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
        self._countdown_id: str | None = None
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
        self.search_var.trace_add("write", lambda *_: self.refresh_tree())
        ttk.Label(toolbar, text="🔍").pack(side="left")
        ttk.Entry(toolbar, textvariable=self.search_var, width=26).pack(side="left", padx=(2, 6))
        ttk.Button(toolbar, text="↻ Muat Ulang (F5)", command=self.refresh_tree).pack(side="left")
        ttk.Button(toolbar, text="📂 Buka Folder", command=self.open_folder).pack(side="left", padx=(4, 0))
        ttk.Button(toolbar, text="＋ Kategori", command=self.add_category).pack(side="left", padx=(4, 0))
        ttk.Button(toolbar, text="＋ Teks", command=self.add_text).pack(side="left", padx=(4, 0))
        ttk.Button(toolbar, text="✏️ Edit", command=self.edit_selected).pack(side="left", padx=(4, 0))
        ttk.Button(toolbar, text="🗑 Hapus", command=self.delete_selected).pack(side="left", padx=(4, 0))

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
        ttk.Button(actions, text="📋 Salin", command=self.do_copy).pack(side="left")
        ttk.Button(actions, text="⚡ Paste ke Aplikasi Lain (1 Klik)",
                   command=self.do_paste).pack(side="left", padx=(6, 0))
        self.auto_var = tk.BooleanVar(value=bool(self.settings["auto_paste"]))
        ttk.Checkbutton(actions, text="Double-click = paste otomatis",
                        variable=self.auto_var,
                        command=self._save_current_settings).pack(side="left", padx=(8, 0))

        # Baris pengaturan
        opts = ttk.Frame(self, padding=(6, 0, 6, 4))
        opts.pack(fill="x")
        ttk.Label(opts, text="Jeda sebelum paste (detik):").pack(side="left")
        self.delay_var = tk.DoubleVar(value=float(self.settings["delay"]))
        ttk.Spinbox(opts, from_=0.5, to=5.0, increment=0.5, width=5,
                    textvariable=self.delay_var,
                    command=self._save_current_settings).pack(side="left", padx=(4, 10))
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
        self.menu.add_command(label="⚡ Paste otomatis (1 klik)", command=self.do_paste)
        self.menu.add_command(label="📋 Salin", command=self.do_copy)
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
        self.bind("<Control-c>", lambda _e: self.do_copy())
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
        query = self.search_var.get().strip() if hasattr(self, "search_var") else ""
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
        try:
            text = read_text(path)
        except OSError as exc:
            text = f"(gagal membaca berkas: {exc})"
        self.preview.insert("1.0", text)
        self.preview.configure(state="disabled")
        self.info_var.set(
            f"{path.name} — {len(text)} karakter, {len(text.split())} kata\n{path}")

    # ---------- Salin & paste ----------
    def _tk_copy(self, text: str) -> None:
        """Fallback clipboard internal Tk (selalu tersedia)."""
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()

    def do_copy(self) -> None:
        path = self.selected_path()
        if path is None or path.is_dir():
            self.status_var.set("Pilih berkas .txt terlebih dahulu.")
            return
        try:
            text = read_text(path)
        except OSError as exc:
            messagebox.showerror("Gagal membaca", str(exc))
            return
        # Salin di thread latar agar GUI tidak freeze bila tool lambat.
        self.status_var.set("Menyalin...")
        threading.Thread(target=self._copy_worker, args=(text,),
                         daemon=True).start()

    def _copy_worker(self, text: str) -> None:
        result = copy_text(text)
        if not result.ok:
            self.after(0, self._tk_copy, text)
            result.message += " (disalin via clipboard internal)"
        self.after(0, self.status_var.set, f"✅ {result.message}")

    def on_activate(self) -> None:
        """Double-click / Enter: paste otomatis bila aktif, else salin."""
        if self.auto_var.get():
            self.do_paste()
        else:
            self.do_copy()

    def do_paste(self) -> None:
        """Satu klik: salin → hitung mundur terlihat → Ctrl+V otomatis."""
        path = self.selected_path()
        if path is None or path.is_dir():
            self.status_var.set("Pilih berkas .txt terlebih dahulu.")
            return
        try:
            text = read_text(path)
        except OSError as exc:
            messagebox.showerror("Gagal membaca", str(exc))
            return
        delay = float(self.delay_var.get())
        self._save_current_settings()
        self.status_var.set("Menyalin...")
        threading.Thread(target=self._paste_worker, args=(text, delay),
                         daemon=True).start()

    def _paste_worker(self, text: str, delay: float) -> None:
        result = copy_text(text)
        if not result.ok:
            self.after(0, self._tk_copy, text)
            result.message += " (disalin via clipboard internal)"
        self.after(0, self._start_countdown, text, delay, result.message)

    def _start_countdown(self, text: str, delay: float, copy_msg: str) -> None:
        """Jendela hitung mundur kecil (selalu di atas) agar terlihat."""
        self.status_var.set(f"✅ {copy_msg}")
        win = tk.Toplevel(self)
        win.title("Paste otomatis")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        label = ttk.Label(win, padding=12)
        label.pack()
        cancelled = False

        def cancel() -> None:
            nonlocal cancelled
            cancelled = True
            win.destroy()
            self.status_var.set("Dibatalkan. Teks sudah disalin — tekan Ctrl+V.")

        ttk.Button(win, text="Batal", command=cancel).pack(pady=(0, 8))
        win.protocol("WM_DELETE_WINDOW", cancel)
        win.update_idletasks()
        x = (win.winfo_screenwidth() - win.winfo_width()) // 2
        y = (win.winfo_screenheight() - win.winfo_height()) // 3
        win.geometry(f"+{x}+{y}")

        remaining = [delay]

        def tick() -> None:
            if cancelled or not win.winfo_exists():
                return
            if remaining[0] <= 0:
                win.destroy()
                self._finish_paste()
                return
            label.configure(
                text=f"Paste dalam {remaining[0]:.1f} detik...\n"
                     "Klik aplikasi tujuan sekarang (Ctrl+V akan terkirim).")
            remaining[0] -= 0.1
            win.after(100, tick)

        tick()

    def _finish_paste(self) -> None:
        # Kecilkan jendela agar aplikasi tujuan mendapat fokus, lalu kirim Ctrl+V.
        self.iconify()
        threading.Thread(target=self._paste_sender, daemon=True).start()

    def _paste_sender(self) -> None:
        result = simulate_paste()
        self.after(0, self._paste_done, result)

    def _paste_done(self, result) -> None:
        self.deiconify()
        self.lift()
        if result.ok:
            self.status_var.set(f"⚡ {result.message}")
        else:
            self.status_var.set(f"📋 Teks sudah disalin. {result.message}")
            self._show_paste_failure(result.message)

    # ---------- izin paste otomatis (grup paste-teks) ----------
    def _show_paste_failure(self, message: str) -> None:
        """Dialog kegagalan + tombol untuk memperbaiki izin /dev/uinput."""
        st = uinput.status()
        text = (f"{message}\n\nTeks sudah ada di clipboard — "
                "tekan Ctrl+V di aplikasi tujuan.")
        if st.ready:
            messagebox.showinfo("Paste otomatis tidak tersedia", text)
            return
        text += "\n\nDiagnosis:\n" + "\n".join(st.summary()) + \
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
        threading.Thread(target=self._enable_worker, args=(st.user,),
                         daemon=True).start()

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
                + "\n".join(st.summary())
                + "\n\n" + uinput.explain(st))
        if uinput.can_self_enable(st):
            if messagebox.askyesno("Paste otomatis",
                                   text + "\n\nAktifkan sekarang?"):
                self.enable_auto_paste()
        else:
            messagebox.showinfo("Paste otomatis", text)

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
        self.settings.update({
            "delay": float(self.delay_var.get()),
            "auto_paste": bool(self.auto_var.get()),
            "always_on_top": bool(self.top_var.get()),
        })
        save_settings(self.settings)

    def _apply_settings(self) -> None:
        self.attributes("-topmost", bool(self.top_var.get()))
        self._save_current_settings()


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
        for line in st.summary():
            print(f"  {line}")
        print(f"  {uinput.explain(st)}")
    except Exception as exc:  # pragma: no cover - jangan gagalkan --check
        print(f"Izin paste otomatis: tidak dapat diperiksa ({exc})")
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
