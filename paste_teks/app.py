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
    DEFAULT_SAMPLES,
    Node,
    create_category,
    create_snippet,
    default_roots,
    delete_path,
    flatten_texts,
    read_text,
    root_label,
    scan_roots,
    search as search_nodes,
    user_root,
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

# Nama ikon tema (usr/share/icons/hicolor/*/apps/<nama>.png / .svg) sekaligus
# kelas jendela. Nilainya WAJIB sama dengan "Icon=" dan "StartupWMClass=" di
# paste-teks.desktop; kalau berbeda, GNOME menganggap jendela yang sedang
# berjalan sebagai aplikasi lain dan menampilkan ikon generik.
APP_ID = "paste-teks"
WM_CLASS = "PasteTeks"
ICON_NAME = APP_ID


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


def _norm_path(path) -> str:
    """Path absolut tanpa symlink, sebagai kunci stabil di settings.json."""
    return str(Path(path).expanduser().resolve())


def _is_within(path, root) -> bool:
    """Apakah ``path`` berada di dalam ``root`` (atau sama dengannya)?"""
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
    except (OSError, ValueError):
        return False
    return True


# Ukuran ikon raster yang dipasang pada jendela (besar -> kecil; Tk memilih
# yang paling cocok untuk judul, taskbar, dan Alt+Tab).
ICON_SIZES = (256, 128, 64)


def icon_dirs() -> list[Path]:
    """Folder dasar tema hicolor yang tersedia (repo maupun .deb terpasang)."""
    base = Path(__file__).resolve().parent.parent
    dirs: list[Path] = []
    for candidate in (base / "usr" / "share" / "icons" / "hicolor",
                      Path("/usr/share/icons/hicolor")):
        if candidate.is_dir() and candidate not in dirs:
            dirs.append(candidate)
    return dirs


def icon_files() -> list[Path]:
    """Berkas PNG ikon aplikasi yang ditemukan, terbesar lebih dulu."""
    files: list[Path] = []
    for base in icon_dirs():
        for size in ICON_SIZES:
            path = base / f"{size}x{size}" / "apps" / f"{ICON_NAME}.png"
            if path.is_file() and path not in files:
                files.append(path)
    return files


def apply_window_icon(window) -> list[Path]:
    """Pasang ikon aplikasi pada jendela Tk; kembalikan berkas yang dipakai.

    Tanpa ini jendela memakai ikon generik bawaan Tk sehingga berbeda dengan
    ikon di menu aplikasi (mis. dash GNOME). Ikon diambil dari PNG yang
    dirender dari SVG yang sama dengan ikon tema, jadi keduanya identik.
    """
    if tk is None:  # pragma: no cover - CLI tanpa tkinter
        return []
    images = []
    used: list[Path] = []
    for path in icon_files():
        try:
            image = tk.PhotoImage(file=str(path))
        except (tk.TclError, OSError):
            continue
        images.append(image)
        used.append(path)
    if images:
        window.iconphoto(True, *images)   # True: juga untuk Toplevel anak
        window._icon_images = images      # cegah garbage-collect
    try:
        window.wm_iconname(ICON_NAME)     # cadangan: nama ikon tema
    except Exception:  # pragma: no cover - WM tanpa dukungan iconname
        pass
    return used


DEFAULT_SETTINGS = {
    # Jeda setelah jendela dilepas, agar compositor sempat memindahkan fokus
    # sebelum Ctrl+V dikirim. 0,6 detik cukup di GNOME Wayland.
    "focus_delay": 0.6,
    "always_on_top": False,
    # Folder sumber pilihan pengguna. Bila ada, HANYA folder ini yang tampil
    # dan menjadi tempat penulisan. Argumen --dir tidak mengubah nilai ini.
    "source_dir": None,
    # Kategori yang sedang diciutkan di pohon (path absolut).
    "collapsed": [],
}

def load_settings() -> dict:
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_SETTINGS)
    if not isinstance(data, dict):
        return dict(DEFAULT_SETTINGS)
    settings = {**DEFAULT_SETTINGS, **data}
    # Normalisasi agar nilai rusak tidak membuat GUI meledak.
    collapsed = settings.get("collapsed")
    settings["collapsed"] = [str(p) for p in collapsed] if isinstance(
        collapsed, (list, tuple)) else []
    source = settings.get("source_dir")
    settings["source_dir"] = str(source) if isinstance(source, str) and source else None
    return settings


def save_settings(settings: dict) -> None:
    try:
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(settings, indent=2, ensure_ascii=False),
                                 encoding="utf-8")
    except OSError:
        pass


def export_data(target_dir: Path) -> list[Path]:
    """Salin contoh bawaan ke ``target_dir`` (sekali), kembalikan berkas baru.

    Dipakai ``--ekspor-data``. Tujuannya untuk pemasangan baru: repo dan .deb
    tidak lagi menyertakan contoh, jadi pengguna yang ingin contoh memintanya
    secara eksplisit. Berkas yang sudah ada tidak pernah ditimpa.
    """
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    skipped: list[Path] = []
    for category, files in DEFAULT_SAMPLES.items():
        for name, content in files.items():
            target = target_dir / category / f"{name}.txt"
            if target.exists():
                skipped.append(target)
                continue
            write_text(target, content)
            created.append(target)
    for path in skipped:
        print(f"lewati (sudah ada): {path}")
    for path in created:
        print(f"dibuat: {path}")
    print(f"{len(created)} berkas dibuat, {len(skipped)} dilewati di {target_dir}")
    return created


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

    def __init__(self, roots: list[Path] | None = None,
                 *, transient_roots: bool = False):
        """``roots`` menggantikan daftar bawaan untuk sesi ini saja.

        ``transient_roots=True`` dipakai untuk argumen CLI ``--dir``: daftar itu
        berlaku selama proses ini hidup dan TIDAK PERNAH ditulis ke
        settings.json, sehingga folder sumber pilihan pengguna tetap utuh.
        """
        if tk is None:
            raise RuntimeError("tkinter tidak tersedia. Pasang python3-tk "
                               "(sudo apt install python3-tk).")
        try:
            # className menentukan WM_CLASS jendela. Harus sama dengan
            # StartupWMClass di paste-teks.desktop agar GNOME memasangkan
            # jendela dengan entri menu (ikon & pengelompokan taskbar benar).
            super().__init__(className=WM_CLASS)
        except Exception as exc:
            raise RuntimeError(
                "Gagal membuka jendela (display tidak tersedia?). "
                f"Detail: {exc}") from exc
        self.title(f"Paste Teks v{__version__} — Tempel Cepat Satu Klik")
        # Ikon jendela = ikon tema yang sama dengan ikon di menu aplikasi.
        self.icon_files_used = apply_window_icon(self)
        self.geometry("980x620")
        self.minsize(720, 440)
        self.settings = load_settings()
        given = [Path(r) for r in (roots or [])]
        self.roots: list[Path] = given or default_roots()
        self.transient_roots = bool(transient_roots and given)
        self.writable = writable_root(self.roots)
        self.current_file: Path | None = None
        self._pasting = False
        self._search_after_id: str | None = None
        self.collapsed: set[str] = set()
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
        self.search_entry = ttk.Entry(toolbar, textvariable=self.search_var,
                                      width=26)
        self.search_entry.pack(side="left", padx=(2, 6))
        buttons = (
            (self.refresh_tree, "↻ Muat Ulang (F5)", (0, 0)),
            (self.open_folder, "📂 Buka Folder", (4, 0)),
            (self.add_category, "＋ Kategori", (4, 0)),
            (self.add_text, "＋ Teks", (4, 0)),
            (self.edit_selected, "✏️ Edit", (4, 0)),
            (self.delete_selected, "🗑 Hapus", (4, 0)),
        )
        for command, label, padx in buttons:
            ttk.Button(toolbar, text=label, command=command).pack(
                side="left", padx=padx)
        # Tombol folder sumber: satu folder saja, dan folder itulah tempat
        # penulisan. Ditaruh paling kanan agar alur utama tetap di kiri.
        self.source_button = ttk.Button(toolbar, text="📁 Pilih Folder Sumber",
                                        command=self.choose_source_dir)
        self.source_button.pack(side="right")
        self.source_var = tk.StringVar(value="")
        ttk.Label(toolbar, textvariable=self.source_var,
                  foreground="gray").pack(side="right", padx=(0, 6))

        # Panel utama: kiri pohon, kanan pratinjau
        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=6)

        left = ttk.Frame(paned)
        ttk.Label(left, text="Kategori / Folder → Berkas .txt").pack(anchor="w")
        # Wadah tetap; isinya berganti antara pohon dan tampilan kosong.
        self.tree_area = ttk.Frame(left)
        self.tree_area.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(self.tree_area, show="tree", selectmode="browse")
        self.scroll = ttk.Scrollbar(self.tree_area, orient="vertical",
                                    command=self.tree.yview)
        self.tree.configure(yscrollcommand=self.scroll.set)
        # Tampilan kosong berpetunjuk (dipakai bila belum ada folder sumber).
        self.empty_frame = ttk.Frame(self.tree_area)
        ttk.Label(self.empty_frame, text="📁", font=("", 34)).pack(pady=(28, 4))
        ttk.Label(
            self.empty_frame,
            text="Belum ada folder sumber.\n\nPilih satu folder berisi berkas "
                 ".txt —\nhierarki folder di dalamnya menjadi kategori,\ndan "
                 "berkas baru akan disimpan di folder itu.",
            justify="center", foreground="gray").pack()
        ttk.Button(self.empty_frame, text="📁 Pilih Folder Sumber",
                   command=self.choose_source_dir).pack(pady=12)
        self._show_empty_state(True)
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
        # Catatan: tombol "Izin Paste Otomatis" DIHAPUS di 1.5.0. Aktivasi izin
        # /dev/uinput kini lewat CLI (`paste-teks --izin`, `--aktifkan-izin`);
        # informasi statusnya tetap muncul di `--check` dan saat paste gagal.

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
        # Kategori diciutkan/dibuka: catat agar pulih pada refresh berikutnya
        # dan pada sesi berikutnya (settings.json -> "collapsed").
        self.tree.bind("<<TreeviewOpen>>",
                       lambda e: self._toggle_collapsed(self._event_iid(e)))
        self.tree.bind("<<TreeviewClose>>",
                       lambda e: self._toggle_collapsed(self._event_iid(e)))
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
        if getattr(self, "search_entry", None) is not None:
            self.search_entry.focus_set()

    def _on_right_click(self, event) -> None:
        row = self.tree.identify_row(event.y)
        if row:
            self.tree.selection_set(row)
            self.show_preview()
        self.menu.post(event.x_root, event.y_root)

    def _event_iid(self, event) -> str:
        """iid dari event Treeview (``focus()``/``identify_row`` sebagai cadangan)."""
        item = getattr(event, "iid", None) if event is not None else None
        if item:
            return str(item)
        try:
            focused = self.tree.focus()
        except Exception:  # pragma: no cover - treeview tiruan
            return ""
        return str(focused or "")

    # ---------- Pohon ----------
    def active_roots(self) -> list[Path]:
        """Root yang dipakai sekarang: folder sumber bila ada, selain itu bawaan.

        Folder sumber menggantikan daftar bawaan sepenuhnya: hanya folder itu
        yang tampil dan hanya folder itu yang ditulisi.
        """
        roots = list(getattr(self, "roots", []) or [])
        if getattr(self, "transient_roots", False) and roots:
            # --dir menang untuk sesi ini; folder tersimpan tidak diubah.
            return roots
        source = getattr(self, "settings", {}).get("source_dir")
        if source:
            path = Path(source)
            if path.is_dir():
                return [path]
            log_error(f"[sumber] folder sumber tidak ditemukan: {path}")
        return roots or default_roots()

    def _show_empty_state(self, empty: bool) -> None:
        """Tampilkan pohon atau tampilan kosong berpetunjuk."""
        tree_area = getattr(self, "tree_area", None)
        if tree_area is None:  # pragma: no cover - UI minimal
            return
        if empty:
            self.tree.pack_forget()
            self.scroll.pack_forget()
            self.empty_frame.pack(fill="both", expand=True)
        else:
            self.empty_frame.pack_forget()
            self.tree.pack(side="left", fill="both", expand=True)
            self.scroll.pack(side="right", fill="y")

    def refresh_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        roots = self.active_roots()
        nodes = scan_roots(roots)
        query = self.search_var.get().strip()
        if query:
            nodes = filter_tree(nodes, search_nodes(nodes, query))
        # Saat mencari, semua kategori dibuka agar hasil terlihat. Saat tidak
        # mencari, status ciut yang tersimpan dipulihkan.
        self.collapsed = set() if query else self._collapsed_paths()
        total = 0
        for root_node in nodes:
            self._insert_node(
                "", root_node, label=self._root_label(root_node.path))
            total += root_node.count_texts()
        self._show_empty_state(not nodes)
        if query:
            self.status_var.set(f"Hasil pencarian '{query}': {total} berkas cocok.")
        else:
            self.status_var.set(
                f"{total} berkas .txt dari {len(roots)} folder "
                f"({', '.join(str(r) for r in roots)})")
        self._update_source_label()

    def _root_label(self, path: Path) -> str:
        """Label akar: nama folder sumber, selain itu label peran root."""
        source = getattr(self, "settings", {}).get("source_dir")
        if source and Path(source) == Path(path):
            return Path(path).name or str(path)
        return root_label(path)

    def _update_source_label(self) -> None:
        var = getattr(self, "source_var", None)
        if var is None:  # pragma: no cover - UI minimal
            return
        source = getattr(self, "settings", {}).get("source_dir")
        if source:
            var.set(f"📁 {Path(source).name}")
        else:
            var.set("")

    def _insert_node(self, parent: str, node: Node, *, label: str | None = None) -> str:
        icon = "📁 " if node.is_dir else "📄 "
        iid = str(node.path)
        # Hindari iid duplikat bila dua root punya path sama (dev vs sistem).
        if self.tree.exists(iid):
            iid = f"{iid}#{node.source_root}"
        collapsed = getattr(self, "collapsed", set())
        is_open = node.is_dir and str(node.path) not in collapsed
        self.tree.insert(parent, "end", iid=iid,
                         text=f"{icon}{label if label is not None else node.name}",
                         open=is_open,
                         tags=("dir" if node.is_dir else "file"))
        for child in node.children:
            self._insert_node(iid, child)
        return iid

    # ---------- Folder sumber & kategori ciut ----------
    def choose_source_dir(self) -> Path | None:
        """Pilih SATU folder sumber; folder itu menjadi tempat penulisan.

        Pilihan disimpan di settings.json dan dipulihkan pada sesi berikutnya.
        Kembalikan path terpilih (None bila dibatalkan) agar mudah diuji.
        """
        current = self.settings.get("source_dir") or str(Path.home())
        chosen = filedialog.askdirectory(
            title="Pilih Folder Sumber (berkas .txt)", initialdir=current,
            mustexist=True)
        if not chosen:
            return None
        path = Path(chosen).expanduser()
        if not path.is_dir():
            messagebox.showerror("Folder tidak valid",
                                 f"Bukan folder: {path}")
            return None
        self.settings["source_dir"] = _norm_path(path)
        self.writable = path
        save_settings(self.settings)
        self.refresh_tree()
        self.status_var.set(f"📁 Folder sumber: {path}")
        return path

    def _collapsed_paths(self) -> set[str]:
        """Kategori yang tersimpan dalam keadaan ciut (path ternormalisasi)."""
        stored = self.settings.get("collapsed") or []
        return {_norm_path(p) for p in stored if str(p).strip()}

    def _save_collapsed(self, paths) -> None:
        """Simpan status ciut kategori ke settings.json."""
        unique = sorted({_norm_path(p) for p in paths if str(p).strip()})
        self.settings["collapsed"] = unique
        save_settings(self.settings)

    def _toggle_collapsed(self, iid: str) -> None:
        """Catat buka/tutup satu kategori lalu simpan.

        Dipanggil dari event <<TreeviewOpen>>/<<TreeviewClose>> Treeview.
        """
        path = str(iid).split("#")[0]
        if not Path(path).is_dir():
            return
        collapsed = self._collapsed_paths()
        key = _norm_path(path)
        if key in collapsed:
            collapsed.discard(key)
        else:
            collapsed.add(key)
        self._save_collapsed(collapsed)
        self.collapsed = collapsed

    def _prune_collapsed(self, path: Path) -> None:
        """Buang catatan ciut untuk ``path`` (mis. setelah kategori dihapus)."""
        key = _norm_path(path)
        collapsed = {p for p in self._collapsed_paths()
                     if p != key and not p.startswith(key + str(Path("/")))}
        if collapsed != self._collapsed_paths():
            self._save_collapsed(collapsed)

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
        """Dialog kegagalan: sebutkan penyebab + perintah CLI untuk memperbaiki.

        Pada 1.5.0 tombol "Izin Paste Otomatis" dihapus dari GUI; aktivasi
        izin /dev/uinput dipindah ke `paste-teks --aktifkan-izin` (dan
        `--izin` untuk melihat statusnya). Dialog tetap memberi informasi dan
        menyebut perintah yang harus dijalankan di terminal.
        """
        st = uinput.status()
        text = (f"{message}\n\nTeks sudah ada di clipboard — "
                "tekan Ctrl+V di aplikasi tujuan.")
        if st.ready:
            messagebox.showinfo("Paste otomatis tidak tersedia", text)
            return
        text += ("\n\nDiagnosis:\n" + "\n".join(_status_lines(st))
                 + "\n\n" + uinput.explain(st)
                 + "\n\n" + uinput.CLI_HINT)
        messagebox.showinfo("Paste otomatis tidak tersedia", text)

    # ---------- CRUD ----------
    def _target_dir(self) -> Path:
        """Folder tempat kategori/teks baru dibuat.

        Bila folder sumber dipilih, penulisan SELALU di dalamnya: folder induk
        root tidak boleh dipakai karena itu berada di luar folder sumber.
        """
        source = self.settings.get("source_dir")
        if source and Path(source).is_dir():
            source_path = Path(source)
            path = self.selected_path()
            if path is not None and path.is_dir() and _is_within(path, source_path):
                return path
            if path is not None and _is_within(path.parent, source_path):
                return path.parent
            return source_path
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
        # Jangan biarkan settings.json menyimpan path yang sudah tiada.
        self._prune_collapsed(path)
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
    # Izin /dev/uinput: dulu tombol di GUI, sejak 1.5.0 hanya lewat CLI.
    parser.add_argument("--izin", action="store_true",
                        help="Tampilkan status izin paste otomatis (/dev/uinput).")
    parser.add_argument("--aktifkan-izin", action="store_true",
                        help="Minta akses /dev/uinput (grup 'paste-teks' lewat pkexec).")
    parser.add_argument("--ekspor-data", metavar="FOLDER", nargs="?",
                        const="", default=None,
                        help="Salin contoh bawaan ke FOLDER (bawaan: folder pengguna).")
    args = parser.parse_args(argv)

    if args.version:
        print(f"paste-teks {__version__}")
        return 0

    roots = [Path(d) for d in args.dir] or default_roots()
    if args.check:
        return run_check(roots)
    if args.izin:
        return run_permission_info()
    if args.aktifkan_izin:
        return run_enable_permission()
    if args.ekspor_data is not None:
        target = Path(args.ekspor_data) if args.ekspor_data else user_root()
        export_data(target)
        return 0
    if args.list:
        nodes = scan_roots(roots)
        for node in nodes:
            _print_tree(node, prefix="")
        if not flatten_texts(nodes):
            print("(belum ada berkas .txt)", file=sys.stderr)
            print("Pilih folder sumber di GUI, pakai --dir FOLDER, atau "
                  "jalankan --ekspor-data.", file=sys.stderr)
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
        # --dir berlaku untuk sesi ini saja; folder sumber tersimpan tidak
        # pernah diubah oleh argumen CLI.
        app = PasteTeksApp(roots=roots, transient_roots=bool(args.dir))
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


def permission_report() -> str:
    """Teks laporan status izin paste otomatis (dipakai --izin dan --check)."""
    st = uinput.status()
    lines = "\n".join(_status_lines(st))
    return (f"Izin paste otomatis (grup '{uinput.GROUP}', akun '{st.user}'):\n"
            f"{lines}\n\nLangkah berikutnya:\n{uinput.explain(st)}")


def run_permission_info() -> int:
    """``--izin``: cetak status izin /dev/uinput, tanpa membuka GUI."""
    print(permission_report())
    if not uinput.status().ready:
        print(f"\n{uinput.CLI_HINT}")
    return 0


def run_enable_permission() -> int:
    """``--aktifkan-izin``: minta akses /dev/uinput (pengganti tombol GUI)."""
    st = uinput.status()
    if st.device_writable:
        print("Izin paste otomatis sudah aktif.")
        return 0
    if not uinput.can_self_enable(st):
        print("Tidak dapat meminta hak administrator dari sini.")
        print("Jalankan perintah berikut di terminal:\n")
        print(uinput.MANUAL_COMMAND)
        return 2
    print(f"Meminta akses /dev/uinput untuk akun '{st.user}' ...")
    code, out, err = uinput.enable_for_user(st.user)
    for line in (out, err):
        if line:
            print(line)
    if code == 0:
        print("\n✅ Izin diaktifkan.")
        print("Logout lalu login ulang (atau reboot) agar grup baru aktif; "
              "setelah itu paste otomatis akan bekerja.")
        return 0
    if code in (126, 127):
        print("\n❌ Permintaan kata sandi dibatalkan.")
        return 1
    print(f"\n❌ Aktivasi gagal (kode keluar {code}).")
    print("\nAlternatif manual di terminal:\n")
    print(uinput.MANUAL_COMMAND)
    return 1


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
    print(f"Ikon aplikasi: {ICON_NAME} (WM_CLASS '{WM_CLASS}')")
    found_icons = icon_files()
    if found_icons:
        for path in found_icons:
            print(f"  ✅ {path}")
    else:
        print("  ❌ tidak ditemukan — jendela akan memakai ikon generik; "
              "pasang ulang paket atau jalankan scripts/make-icon-png.py")
    try:
        st = uinput.status()
        print(f"Izin paste otomatis (grup '{uinput.GROUP}', akun '{st.user}'):")
        for line in _status_lines(st):
            print(f"  {line}")
        print("\nLangkah berikutnya:")
        for line in uinput.explain(st).splitlines():
            print(f"  {line}")
        if not st.ready:
            # Info izin tetap ada di --check walau tombolnya sudah dihapus.
            print(f"\n{uinput.CLI_HINT}")
    except Exception as exc:  # pragma: no cover - jangan gagalkan --check
        print(f"Izin paste otomatis: tidak dapat diperiksa ({exc})")
    clip = clipboard_status()
    print("Clipboard:")
    for line in clip:
        print(f"  {line}")
    settings = load_settings()
    source = settings.get("source_dir")
    print("Folder sumber:")
    if source:
        print(f"  - {source} ({'ada' if Path(source).is_dir() else 'TIDAK ADA'})")
    else:
        print("  (belum dipilih — memakai folder bawaan di bawah)")
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
