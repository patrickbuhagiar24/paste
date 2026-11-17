"""Pastex GUI — a hierarchy of folders containing .txt files.

Run: pastex [--dir FOLDER] [--list] [--copy FILE] [--paste FILE]
Click or press Enter on a .txt file to copy and paste it into another app.

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
from .clipboard import available_tools, copy_text, detect_session, simulate_paste
from .snippets import (
    DEFAULT_SAMPLES,
    Node,
    create_folder,
    create_snippet,
    default_roots,
    delete_path,
    flatten_texts,
    read_text,
    rename_folder,
    scan_roots,
    user_root,
    writable_root,
    write_text,
)
from .snippets import (
    search as search_nodes,
)

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog, ttk
except ImportError:  # pragma: no cover - ditangani saat runtime
    tk = None  # type: ignore[assignment]
    filedialog = messagebox = simpledialog = ttk = None  # type: ignore[assignment]

# Use object as the GUI base when tkinter is unavailable so the CLI still works.
_TkBase = tk.Tk if tk is not None else object  # type: ignore[union-attr]
_ToplevelBase = tk.Toplevel if tk is not None else object  # type: ignore[union-attr]
# Errors raised by Tk calls. Without tkinter, AttributeError is what the same
# calls raise on a test double, so both cases are handled by the same handlers.
TK_ERRORS = (
    (tk.TclError, AttributeError, RecursionError)
    if tk is not None
    else (AttributeError, RecursionError)
)


SETTINGS_DIR = (
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "pastex"
)
SETTINGS_FILE = SETTINGS_DIR / "settings.json"
ERROR_LOG = SETTINGS_DIR / "error.log"

# Tk may normalize className when creating the X11 WM_CLASS. The observed value
# for Ubuntu Tk (verified with xprop) is "Pastex", not the supplied className.
# The desktop StartupWMClass must match the actual value.
APP_ID = "pastex"
TK_CLASS_NAME = "Pastex"
WM_CLASS = "Pastex"
ICON_NAME = APP_ID
# Time given to the window manager to move focus to the previous application
# after Pastex hides itself, before Ctrl+V is sent.
FOCUS_SETTLE_MS = 80


def log_error(message: str) -> None:
    """Append an error message to ~/.config/pastex/error.log for diagnostics."""
    try:
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        with ERROR_LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {message}\n")
    except OSError:
        pass


def _run_in_thread(target, *args) -> None:
    """Run target in a daemon thread so I/O does not block the GUI."""
    threading.Thread(target=target, args=args, daemon=True).start()


def _norm_path(path) -> str:
    """Return a symlink-free absolute path for use as a stable settings key."""
    return str(Path(path).expanduser().resolve())


def _is_within(path, root) -> bool:
    """Return whether path is inside root or is root itself."""
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
    except (OSError, ValueError):
        return False
    return True


# Raster icon sizes are listed largest to smallest; Tk selects the best fit
# for the title bar, taskbar, and Alt+Tab.
ICON_SIZES = (256, 128, 64)


def icon_dirs() -> list[Path]:
    """Return available hicolor theme directories in the checkout or installation."""
    base = Path(__file__).resolve().parent.parent
    dirs: list[Path] = []
    for candidate in (
        base / "usr" / "share" / "icons" / "hicolor",
        Path("/usr/share/icons/hicolor"),
    ):
        if candidate.is_dir() and candidate not in dirs:
            dirs.append(candidate)
    return dirs


def icon_files() -> list[Path]:
    """Return discovered application PNG icons, largest first."""
    files: list[Path] = []
    for base in icon_dirs():
        for size in ICON_SIZES:
            path = base / f"{size}x{size}" / "apps" / f"{ICON_NAME}.png"
            if path.is_file() and path not in files:
                files.append(path)
    return files


def apply_window_icon(window) -> list[Path]:
    """Set the application icon on a Tk window and return the files used.

    Without this, the window uses Tk's generic icon instead of the application
    menu icon. Load PNGs rendered from the same SVG as the theme icon so they match.
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
        window.iconphoto(True, *images)  # True also applies to child Toplevels.
        window._icon_images = images  # cegah garbage-collect
    try:
        window.wm_iconname(ICON_NAME)  # cadangan: nama ikon tema
    except (tk.TclError, AttributeError):  # pragma: no cover - optional WM icon name
        return used
    return used


DEFAULT_SETTINGS = {
    # Keep Pastex hidden briefly after Ctrl+V is sent before restoring its window.
    "restore_delay": 0.1,
    # User-selected source folder. If set, only this folder is displayed
    # and used for writes. The --dir argument does not change this value.
    "source_dir": None,
    # Folders currently collapsed in the tree (absolute paths).
    "collapsed": [],
    # Optional per-folder child ordering, keyed by absolute parent path.
    "order": {},
    "setup_assistant_seen": False,
}


def startup_setup_issues(
    session: str, tools: dict[str, bool], status=None
) -> tuple[list[str], bool]:
    """Return first-launch setup issues and whether permissions can be requested."""
    issues = []
    if not any(tools.get(name, False) for name in ("wl-copy", "xclip", "xsel")):
        issues.append(
            "Clipboard tools are missing. Install them with: "
            "sudo apt install wl-clipboard xclip xsel."
        )

    use_ydotool = session == "wayland" or (
        session == "x11" and not tools.get("xdotool", False)
    )
    if session == "wayland" and not tools.get("ydotool", False):
        issues.append(
            "ydotool is missing. Automatic paste is unavailable on Wayland; "
            "install it with: sudo apt install ydotool."
        )
    elif session == "x11" and not (
        tools.get("xdotool", False) or tools.get("ydotool", False)
    ):
        issues.append(
            "No automatic-paste tool is installed. Install one with: "
            "sudo apt install xdotool ydotool."
        )
    elif session not in {"wayland", "x11"}:
        issues.append(
            "Pastex could not identify a Wayland or X11 session. "
            "Automatic paste may not be available in this environment."
        )

    can_enable = False
    if use_ydotool and tools.get("ydotool", False):
        status = status or uinput.status()
        if not status.device_writable:
            issues.append(
                "Pastex cannot access /dev/uinput, which ydotool needs to send Ctrl+V."
            )
            can_enable = uinput.can_self_enable(status)
        if status.ydotoold_binary and not status.daemon_ready:
            issues.append(
                "The ydotoold service is not running. Pastex tried to start it; "
                "if it remains unavailable, run: systemctl --user start ydotoold."
            )
    return issues, can_enable


def load_settings() -> dict:
    settings_path = SETTINGS_FILE
    legacy_path = SETTINGS_FILE.parent.parent / "paste-teks" / SETTINGS_FILE.name
    try:
        if not settings_path.exists() and legacy_path.is_file():
            settings_path = legacy_path
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_SETTINGS)
    if not isinstance(data, dict):
        return dict(DEFAULT_SETTINGS)
    settings = {**DEFAULT_SETTINGS, **data}
    # The old pre-paste focus delay has been replaced by a post-paste restore delay.
    settings.pop("focus_delay", None)
    # Normalize values to prevent malformed settings from breaking the GUI.
    collapsed = settings.get("collapsed")
    settings["collapsed"] = (
        [str(p) for p in collapsed] if isinstance(collapsed, (list, tuple)) else []
    )
    order = settings.get("order")
    settings["order"] = (
        {
            str(parent): [str(name) for name in names if isinstance(name, str)]
            for parent, names in order.items()
            if isinstance(order, dict) and isinstance(names, (list, tuple))
        }
        if isinstance(order, dict)
        else {}
    )
    source = settings.get("source_dir")
    settings["source_dir"] = str(source) if isinstance(source, str) and source else None
    if settings_path == legacy_path:
        save_settings(settings)
    return settings


def save_settings(settings: dict) -> None:
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(
            json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass


def export_data(target_dir: Path) -> list[Path]:
    """Copy built-in samples to ``target_dir`` once and return the created files.

    Used by ``--export-data``. New installations no longer bundle sample data,
    so users request it explicitly. Existing files are never overwritten.
    """
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    skipped: list[Path] = []
    for folder, files in DEFAULT_SAMPLES.items():
        for name, content in files.items():
            target = target_dir / folder / f"{name}.txt"
            if target.exists():
                skipped.append(target)
                continue
            write_text(target, content)
            created.append(target)
    for path in skipped:
        print(f"Skipped (already exists): {path}")
    for path in created:
        print(f"Created: {path}")
    print(f"{len(created)} files created, {len(skipped)} skipped in {target_dir}")
    return created


def filter_tree(nodes: list[Node], hits: list[Node]) -> list[Node]:
    """Build a filtered tree using search results from the same tree."""
    # search_nodes returns the original Nodes, so resolve() is unnecessary
    # for every file (which would perform redundant filesystem access).
    hit_paths = {h.path for h in hits}

    def keep(node: Node) -> Node | None:
        if node.is_text:
            return node if node.path in hit_paths else None
        kept = [k for child in node.children if (k := keep(child)) is not None]
        if not kept:
            return None
        clone = Node(
            name=node.name,
            path=node.path,
            is_dir=True,
            children=kept,
            source_root=node.source_root,
        )
        return clone

    return [k for n in nodes if (k := keep(n)) is not None]


class PastexApp(_TkBase):  # type: ignore[misc,valid-type]
    """The main Pastex window."""

    def __init__(
        self, roots: list[Path] | None = None, *, transient_roots: bool = False
    ):
        """Use ``roots`` instead of the default list for this session only.

        ``transient_roots=True`` is used by the CLI ``--dir`` option. These roots
        apply only while the process is running and are never saved to settings,
        so the user's selected source folder remains unchanged.
        """
        if tk is None:
            raise RuntimeError(
                "tkinter is unavailable. Install python3-tk "
                "(sudo apt install python3-tk)."
            )
        try:
            # Tk normalizes className into WM_CLASS; the integration test compares
            # xprop output with the desktop StartupWMClass.
            super().__init__(className=TK_CLASS_NAME)
        except tk.TclError as exc:
            raise RuntimeError(
                f"Could not open window (is a display available?). Detail: {exc}"
            ) from exc
        self.title(f"Pastex v{__version__} — One-Click Paste")
        # Use the same icon as the application menu.
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
        self._select_mode = False
        self.collapsed: set[str] = set()
        self._build_ui()
        self.refresh_tree()
        # Ensure the window is visible and focused (important when launched from a menu).
        self.deiconify()
        self.lift()
        self.focus_force()
        self._center_window()
        if not self.settings.get("setup_assistant_seen", False):
            # Check in the background so system permission probes never delay startup.
            self.after(250, self._start_first_run_setup_check)

    def _start_first_run_setup_check(self) -> None:
        """Inspect paste prerequisites once, before the user clicks a snippet."""
        if getattr(self, "_setup_check_started", False):
            return
        self._setup_check_started = True
        threading.Thread(
            target=self._first_run_setup_worker,
            name="pastex-setup-check",
            daemon=True,
        ).start()

    def _first_run_setup_worker(self) -> None:
        try:
            session = detect_session()
            tools = available_tools()
            use_ydotool = session == "wayland" or (
                session == "x11" and not tools.get("xdotool", False)
            )
            status = None
            if use_ydotool and tools.get("ydotool", False):
                status = uinput.status()
                if status.ydotoold_binary and not status.daemon_ready:
                    uinput.start_daemon()
                    status = uinput.status()
            issues, can_enable = startup_setup_issues(session, tools, status)
        except Exception as exc:  # noqa: BLE001 - setup diagnostics must not stop the GUI
            issues = [f"Pastex could not complete its setup check: {exc}"]
            can_enable = False
        self.after(0, self._finish_first_run_setup_check, issues, can_enable)

    def _finish_first_run_setup_check(
        self, issues: list[str], can_enable: bool
    ) -> None:
        """Record the one-time check and show only actionable setup guidance."""
        self.settings["setup_assistant_seen"] = True
        save_settings(self.settings)
        if not issues:
            self.status_var.set("Automatic paste is ready.")
            return
        details = "\n\n".join(f"• {issue}" for issue in issues)
        if can_enable and messagebox.askyesno(
            "Set Up Automatic Paste",
            f"Pastex found setup items to review:\n\n{details}\n\n"
            "Would you like to authorize Pastex to configure the required "
            "uinput access now? Your administrator password may be requested.",
        ):
            status = uinput.status()
            code, out, err = uinput.enable_for_user(status.user)
            if code == 0:
                remaining = [issue for issue in issues if "/dev/uinput" not in issue]
                message = (
                    "Permission setup completed. If your account was newly added "
                    "to the pastex group, log out and back in before automatic "
                    "paste can use it."
                )
                if remaining:
                    message += (
                        "\n\nOther setup items still need attention:\n"
                        + "\n".join(f"• {issue}" for issue in remaining)
                    )
                message += "\n\nRun `pastex --check` to review the environment."
                messagebox.showinfo("Setup Requested", message)
                return
            message = err or out or f"The setup helper exited with code {code}."
            messagebox.showwarning(
                "Setup Not Completed",
                f"Pastex could not configure automatic paste.\n\n{message}\n\n"
                "You can run `pastex --check` for details.",
            )
            return
        messagebox.showwarning(
            "Automatic Paste Setup",
            f"Pastex checked its clipboard and paste prerequisites before use.\n\n"
            f"{details}\n\nRun `pastex --check` for a full diagnostic report.",
        )

    def _center_window(self) -> None:
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        x = max(0, (self.winfo_screenwidth() - w) // 2)
        y = max(0, (self.winfo_screenheight() - h) // 2)
        self.geometry(f"{w}x{h}+{x}+{y}")

    # ---------- UI ----------
    def _build_ui(self) -> None:
        # Top toolbar
        toolbar = ttk.Frame(self, padding=6)
        toolbar.pack(fill="x")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._on_search_changed())
        search_box = ttk.Frame(toolbar)
        search_box.pack(side="left", padx=(0, 6))
        self.search_entry = ttk.Entry(
            search_box, textvariable=self.search_var, width=26
        )
        self.search_entry.pack(side="left")
        self.clear_search_button = ttk.Button(
            search_box, text="×", width=2, command=self.clear_search
        )
        self.clear_search_button.pack(side="left")
        self.search_entry.bind("<Return>", lambda _event: self.search_now())
        buttons = (
            (self.refresh_tree, "↻ Refresh (F5)", (0, 0)),
            (self.add_folder, "＋ Folder", (4, 0)),
            (self.add_text, "＋ Text", (4, 0)),
        )
        for command, label, padx in buttons:
            ttk.Button(toolbar, text=label, command=command).pack(
                side="left", padx=padx
            )
        # Choosing a source folder stays separate from opening its location.
        self.source_button = ttk.Button(
            toolbar, text="📁 Choose Source Folder", command=self.choose_source_dir
        )
        self.source_button.pack(side="right")
        self.source_var = tk.StringVar(value="")
        self.source_label = ttk.Label(
            toolbar, textvariable=self.source_var, foreground="gray", cursor="hand2"
        )
        self.source_label.pack(side="right", padx=(0, 6))
        self.source_label.bind("<Button-1>", lambda _event: self.open_source_folder())

        # Main panel: tree on the left, preview on the right.
        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        left = ttk.Frame(paned)
        ttk.Label(left, text="Folders → .txt Files").pack(anchor="w")
        # Keep one container and switch between the tree and the empty state.
        self.tree_area = ttk.Frame(left)
        self.tree_area.pack(fill="both", expand=True)
        # Selection is managed explicitly so Select mode can toggle rows without
        # the Treeview's default browse/extended click behavior.
        self.tree = ttk.Treeview(self.tree_area, show="tree", selectmode="none")
        self.tree.tag_configure("hover", background="#86bfff", foreground="#102a43")
        self.tree.tag_configure("context", background="#102a43", foreground="#ffffff")
        self._hover_iid = ""
        self._context_highlight_iid = ""
        self.scroll = ttk.Scrollbar(
            self.tree_area, orient="vertical", command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=self.scroll.set)
        # Guided empty state shown when no source folder is selected.
        self.empty_frame = ttk.Frame(self.tree_area)
        ttk.Label(self.empty_frame, text="📁", font=("", 34)).pack(pady=(28, 4))
        ttk.Label(
            self.empty_frame,
            text="No source folder selected.\n\nChoose a folder containing .txt files —\n"
            "its folder hierarchy will appear here, and new files will be saved there.",
            justify="center",
            foreground="gray",
        ).pack()
        ttk.Button(
            self.empty_frame,
            text="📁 Choose Source Folder",
            command=self.choose_source_dir,
        ).pack(pady=12)
        self._show_empty_state(True)
        paned.add(left, weight=2)

        right = ttk.Frame(paned)
        ttk.Label(right, text="Text Preview:").pack(anchor="w")
        self.preview = tk.Text(
            right, wrap="word", font=("Monospace", 11), state="disabled", height=16
        )
        self.preview.pack(fill="both", expand=True)
        self.info_var = tk.StringVar(value="Select a .txt file on the left.")
        ttk.Label(
            right, textvariable=self.info_var, wraplength=420, foreground="gray"
        ).pack(anchor="w", pady=(4, 0))

        actions = ttk.Frame(right, padding=(0, 6, 0, 0))
        actions.pack(fill="x")
        ttk.Button(
            actions, text="⚡ Paste into Another App", command=self.do_paste
        ).pack(side="left")

        # Note: the automatic-paste permission button was removed in 1.5.0.
        # Configure /dev/uinput through the CLI (`pastex --permissions`, `--enable-permissions`).
        # Status information remains available in `--check` and on paste failures.

        # Status bar
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(
            self, textvariable=self.status_var, relief="sunken", anchor="w", padding=4
        ).pack(fill="x", side="bottom")

        # Context menu is populated for each right-clicked text or folder.
        self.menu = tk.Menu(self, tearoff=0)
        self._context_iid = ""
        self._context_menu_active = False
        self._suppress_next_release = False
        self.menu.bind("<Unmap>", self._context_menu_unmapped, add="+")
        # Switching to another window must never leave the popup hovering over
        # that other application; FocusOut on the app or the menu triggers it.
        self.menu.bind("<FocusOut>", self._on_window_focus_out, add="+")
        self.bind("<FocusOut>", self._on_window_focus_out, add="+")
        # Run dismissal before widget/class bindings: Treeview and Menu class
        # handlers can consume outside clicks before a bind_all callback sees them.
        self._context_dismiss_bindtag = f"PastexContextDismiss{id(self)}"
        for sequence in ("<ButtonPress-1>", "<ButtonPress-2>", "<ButtonPress-3>"):
            self.bind_class(
                self._context_dismiss_bindtag,
                sequence,
                self._dismiss_context_menu_outside,
                add="+",
            )
        self.bind_class(
            self._context_dismiss_bindtag,
            "<ButtonRelease-1>",
            self._consume_suppressed_release,
            add="+",
        )

        def install_dismiss_bindtag(widget) -> None:
            tags = widget.bindtags()
            if self._context_dismiss_bindtag not in tags:
                widget.bindtags((self._context_dismiss_bindtag, *tags))
            for child in widget.winfo_children():
                install_dismiss_bindtag(child)

        install_dismiss_bindtag(self)
        self.bind("<Button-1>", self._exit_select_mode_outside_tree, add="+")
        self.bind("<Button-3>", self._exit_select_mode_outside_tree, add="+")
        self.bind("<Control-Button-1>", self._exit_select_mode_outside_tree, add="+")
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<Button-2>", self._on_right_click)
        self.tree.bind("<Control-Button-1>", self._on_right_click)
        self.tree.bind("<Motion>", self._on_tree_motion)
        self.tree.bind("<Leave>", self._clear_tree_hover)
        self.tree.bind("<ButtonRelease-1>", self._on_single_click)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.show_preview())
        # Save expanded/collapsed folder state so it survives refreshes
        # and future sessions (settings.json -> "collapsed").
        self.tree.bind(
            "<<TreeviewOpen>>", lambda e: self._toggle_collapsed(self._event_iid(e))
        )
        self.tree.bind(
            "<<TreeviewClose>>", lambda e: self._toggle_collapsed(self._event_iid(e))
        )
        # Arrow keys walk the visible rows and move the darker current-row
        # highlight; Ctrl+Up/Ctrl+Down reorder the current row like Up/Down
        # in the context menu.
        self.tree.bind("<Up>", lambda _e: self._on_tree_arrow(-1))
        self.tree.bind("<Down>", lambda _e: self._on_tree_arrow(1))
        self.tree.bind("<Control-Up>", lambda _e: self._on_tree_move_shortcut(-1))
        self.tree.bind("<Control-Down>", lambda _e: self._on_tree_move_shortcut(1))
        self.tree.bind("<Return>", lambda _e: self.on_activate())
        self.tree.bind("<Delete>", lambda _e: self.delete_selected())
        self.tree.bind("<F2>", lambda _e: self.edit_selected())
        self.bind("<F5>", lambda _e: self.refresh_tree())
        self.bind("<Escape>", lambda _e: self._exit_select_mode())
        self.bind(
            "<Control-f>", lambda _e: self.search_var.set("") or self._focus_search()
        )

    def _on_search_changed(self) -> None:
        """Debounce searches by 300 ms to avoid rescanning on every keystroke."""
        if self._search_after_id is not None:
            self.after_cancel(self._search_after_id)
        self._search_after_id = self.after(300, self.refresh_tree)

    def _focus_search(self) -> None:
        if getattr(self, "search_entry", None) is not None:
            self.search_entry.focus_set()

    def search_now(self) -> str:
        """Run the current search immediately instead of waiting for debounce."""
        if self._search_after_id is not None:
            self.after_cancel(self._search_after_id)
            self._search_after_id = None
        self.refresh_tree()
        return "break"

    def clear_search(self) -> None:
        """Clear the search query and return focus to the input."""
        self.search_var.set("")
        self.search_now()
        self._focus_search()

    def _item_tags(self, iid: str) -> tuple[str, ...]:
        """Return item tags normalized as a tuple across Tk and test doubles."""
        raw = self.tree.item(iid, "tags")
        if isinstance(raw, str):
            return (raw,) if raw else ()
        return tuple(raw or ())

    def _on_tree_motion(self, event) -> None:
        """Highlight the row currently under the pointer."""
        row = self.tree.identify_row(event.y)
        if row == self._hover_iid:
            return
        if self._hover_iid and self.tree.exists(self._hover_iid):
            tags = tuple(tag for tag in self._item_tags(self._hover_iid) if tag != "hover")
            self.tree.item(self._hover_iid, tags=tags)
        self._hover_iid = row
        if row:
            tags = self._item_tags(row)
            if "hover" not in tags:
                self.tree.item(row, tags=(*tags, "hover"))

    def _clear_tree_hover(self, _event=None) -> None:
        # Avoid tkinter.Misc.__getattr__ on uninitialized Tk instances (the
        # test suite often constructs PastexApp with __new__ and no Tcl interpreter).
        hover_iid = self.__dict__.get("_hover_iid", "")
        if hover_iid and self.tree.exists(hover_iid):
            tags = tuple(tag for tag in self._item_tags(hover_iid) if tag != "hover")
            self.tree.item(hover_iid, tags=tags)
        self._hover_iid = ""

    def _clear_context_highlight(self) -> None:
        """Remove the darker current-row highlight tag from the previous row."""
        iid = self.__dict__.get("_context_highlight_iid", "")
        if iid and self.tree.exists(iid):
            tags = tuple(tag for tag in self._item_tags(iid) if tag != "context")
            self.tree.item(iid, tags=tags)
        self._context_highlight_iid = ""

    def _set_context_highlight(self, iid: str) -> None:
        """Move the darker highlight to ``iid`` and keep it on that row.

        Every interaction that highlights a row — left click, right click,
        arrow keys, and editing — moves the highlight, except a right click
        while Select mode is active.
        """
        if not iid or not self.tree.exists(iid):
            return
        previous = self.__dict__.get("_context_highlight_iid", "")
        if previous and previous != iid and self.tree.exists(previous):
            tags = tuple(tag for tag in self._item_tags(previous) if tag != "context")
            self.tree.item(previous, tags=tags)
        tags = self._item_tags(iid)
        if "context" not in tags:
            self.tree.item(iid, tags=(*tags, "context"))
        self._context_highlight_iid = iid

    def _visible_rows(self) -> list[str]:
        """Return the visible tree rows top to bottom, skipping collapsed folders."""
        rows: list[str] = []

        def walk(parent: str) -> None:
            for child in self.tree.get_children(parent):
                rows.append(child)
                if self.tree.item(child, "open"):
                    walk(child)

        walk("")
        return rows

    def _current_row_iid(self) -> str:
        """Return the row that currently carries the darker highlight."""
        iid = self.__dict__.get("_context_highlight_iid", "")
        if iid and self.tree.exists(iid):
            return iid
        selection = self.tree.selection()
        if selection:
            return str(selection[0])
        try:
            return str(self.tree.focus() or "")
        except TK_ERRORS:  # pragma: no cover - tree unavailable during teardown
            return ""

    def _move_tree_highlight(self, delta: int) -> str:
        """Select and highlight the row ``delta`` positions away and preview it."""
        rows = self._visible_rows()
        if not rows:
            return ""
        current = self._current_row_iid()
        if current in rows:
            index = min(max(rows.index(current) + delta, 0), len(rows) - 1)
        else:
            index = 0 if delta >= 0 else len(rows) - 1
        iid = rows[index]
        self.tree.focus(iid)
        self._set_context_highlight(iid)
        see = getattr(self.tree, "see", None)
        if callable(see):
            see(iid)
        # Select mode keeps its toggled rows: arrows only move the highlight.
        if not self._select_mode:
            self.tree.selection_set(iid)
            if "preview" in self.__dict__:
                self.show_preview()
        return iid

    def _on_tree_arrow(self, delta: int) -> str:
        """Handle Up/Down: move the highlight without pasting."""
        self._move_tree_highlight(delta)
        return "break"

    def _on_tree_move_shortcut(self, direction: int) -> str:
        """Handle Ctrl+Up/Ctrl+Down as the context menu's Up/Down actions."""
        current = self._current_row_iid()
        if current and self.tree.exists(current):
            self.move_selected(direction, item_ids=[current])
        else:
            self.move_selected(direction)
        return "break"

    def _exit_select_mode(self) -> None:
        """Exit Select mode, clear remaining selections, and update the status bar."""
        if not self._select_mode:
            return
        self._select_mode = False
        self.tree.selection_set()
        if "preview" in self.__dict__:
            self.show_preview()
        self.status_var.set("Selection mode ended.")

    def _on_single_click(self, event) -> None:
        """Paste normally; in Select mode, left-click toggles each row."""
        if self.__dict__.get("_suppress_next_release", False):
            self._suppress_next_release = False
            return "break"
        self._clear_tree_hover()
        row = self.tree.identify_row(event.y)
        if not row:
            if self._select_mode:
                self._exit_select_mode()
            return "break"

        identify_element = getattr(self.tree, "identify_element", None)
        if callable(identify_element) and hasattr(event, "x"):
            element = str(identify_element(event.x, event.y) or "")
            if "indicator" in element.lower():
                return "break"

        self.tree.focus(row)
        self._set_context_highlight(row)
        if self._select_mode:
            if row in self.tree.selection():
                self.tree.selection_remove(row)
            else:
                self.tree.selection_add(row)
            self.show_preview()
            self.status_var.set(f"Selected {len(self.tree.selection())} item(s).")
            return "break"

        self.tree.selection_set(row)
        path = Path(row.split("#", 1)[0])
        if path.is_file():
            # Start immediately. do_paste() guards against another activation
            # while the current copy-and-paste operation is still in progress.
            self.do_paste()
        return "break"

    def _exit_select_mode_outside_tree(self, event) -> None:
        """Clear hover and leave Select mode after a click outside the list."""
        if event.widget is self.tree:
            return
        self._clear_tree_hover()
        if not self._select_mode:
            return
        menu_path = str(self.menu)
        widget_path = str(event.widget)
        if widget_path == menu_path or widget_path.startswith(menu_path + "."):
            return
        self._exit_select_mode()

    def _context_menu_unmapped(self, _event=None) -> None:
        self._context_menu_active = False
        self._cancel_menu_focus_watch()

    def _unpost_context_menu(self) -> None:
        """Close the popup menu and release its grab."""
        self._context_menu_active = False
        self._cancel_menu_focus_watch()
        try:
            self.menu.unpost()
        except TK_ERRORS:
            pass
        try:
            self.menu.grab_release()
        except TK_ERRORS:
            pass

    def _tk_ready(self) -> bool:
        """True only for instances that really own a Tcl interpreter.

        Test doubles are built with ``PastexApp.__new__``: they have no ``tk``
        attribute, so any tkinter method such as ``after()`` recurses through
        ``Misc.__getattr__`` instead of raising a catchable error.
        """
        return self.__dict__.get("tk") is not None

    def _display_focus(self):
        """Return the widget that owns the display focus, or None if unknown."""
        if not self._tk_ready() and "focus_displayof" not in self.__dict__:
            return None
        try:
            return self.focus_displayof()
        except TK_ERRORS:  # pragma: no cover - display/interpreter gone
            return None

    def _on_window_focus_out(self, _event=None) -> None:
        """Dismiss the popup when another application takes focus."""
        if not self.__dict__.get("_context_menu_active", False):
            return
        if self._tk_ready():
            try:
                self.after_idle(self._dismiss_menu_when_unfocused)
                return
            except TK_ERRORS:  # pragma: no cover - interpreter already gone
                pass
        self._dismiss_menu_when_unfocused()

    def _dismiss_menu_when_unfocused(self) -> None:
        """Unpost the menu once Tk reports that no Pastex widget has focus."""
        if not self.__dict__.get("_context_menu_active", False):
            return
        focused = self._display_focus()
        if focused is not None:
            return
        self._unpost_context_menu()

    def _cancel_menu_focus_watch(self) -> None:
        after_id = self.__dict__.get("_menu_focus_watch_id")
        self._menu_focus_watch_id = None
        if after_id is None or not self._tk_ready():
            return
        try:
            self.after_cancel(after_id)
        except TK_ERRORS:
            pass

    def _watch_menu_focus(self) -> None:
        """Poll for lost focus while the popup is posted.

        Some window managers never deliver FocusOut to an override-redirect
        popup, so a lightweight poll keeps the menu from lingering over other
        applications.
        """
        self._menu_focus_watch_id = None
        if not self.__dict__.get("_context_menu_active", False):
            return
        if self._display_focus() is None:
            self._unpost_context_menu()
            return
        self._start_menu_focus_watch()

    def _start_menu_focus_watch(self) -> None:
        self._cancel_menu_focus_watch()
        if not self._tk_ready():
            return
        try:
            self._menu_focus_watch_id = self.after(250, self._watch_menu_focus)
        except TK_ERRORS:  # pragma: no cover - interpreter already gone
            self._menu_focus_watch_id = None

    def _consume_suppressed_release(self, _event=None):
        """Swallow the left-button release of a click that dismissed the popup menu."""
        if not self.__dict__.get("_suppress_next_release", False):
            return None
        self._suppress_next_release = False
        return "break"

    def _dismiss_context_menu_outside(self, event):
        """Clear hover on mouse press and dismiss popups clicked outside."""
        self._clear_tree_hover()
        if not self._context_menu_active:
            return
        try:
            x, y = event.x_root, event.y_root
            left, top = self.menu.winfo_rootx(), self.menu.winfo_rooty()
            right = left + self.menu.winfo_width()
            bottom = top + self.menu.winfo_height()
        except (AttributeError, tk.TclError):
            # If the popup geometry cannot be queried, fail closed and dismiss it.
            x = y = left = top = right = bottom = 0
        if left <= x < right and top <= y < bottom:
            return

        self._context_menu_active = False
        self._cancel_menu_focus_watch()
        if getattr(event, "num", 1) == 1:
            self._suppress_next_release = True
        try:
            self.menu.unpost()
        except tk.TclError:
            pass
        try:
            self.menu.grab_release()
        except tk.TclError:
            pass
        return "break"

    def _on_right_click(self, event):
        """Post a context menu for a row, or dismiss selection mode on blank space."""
        self._clear_tree_hover()
        row = self.tree.identify_row(event.y)
        if not row:
            if self._select_mode:
                self._exit_select_mode()
            else:
                self._select_mode = False
            self._context_menu_active = False
            return "break"
        self._context_iid = row
        self.tree.focus(row)
        # A right click in Select mode keeps the bulk selection untouched, so
        # the darker current-row highlight is only moved in normal mode.
        if not self._select_mode:
            self._set_context_highlight(row)
        self.menu.delete(0, "end")

        if self._select_mode:
            self.menu.add_command(
                label="Delete",
                command=lambda: self.delete_selected(self._context_target_iids()),
            )
            self.menu.add_command(
                label="↑ Up",
                command=lambda: self.move_selected(
                    -1, item_ids=self._context_target_iids()
                ),
            )
            self.menu.add_command(
                label="↓ Down",
                command=lambda: self.move_selected(
                    1, item_ids=self._context_target_iids()
                ),
            )
        else:
            self.menu.add_command(label="Select", command=self.select_context_item)
            self.menu.add_command(label="Edit", command=self.edit_context_item)
            self.menu.add_command(
                label="Delete",
                command=lambda: self.delete_selected(self._context_target_iids()),
            )
            self.menu.add_separator()
            self.menu.add_command(
                label="↑ Up",
                command=lambda: self.move_selected(
                    -1, item_ids=self._context_target_iids()
                ),
            )
            self.menu.add_command(
                label="↓ Down",
                command=lambda: self.move_selected(
                    1, item_ids=self._context_target_iids()
                ),
            )
            self.menu.add_separator()
            self.menu.add_command(label="Collapse All", command=self.collapse_all)
        try:
            self._context_menu_active = True
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()
        self._start_menu_focus_watch()
        return "break"

    def _context_target_iids(self) -> list[str]:
        """Resolve normal context rows or the active multi-selection."""
        selection = list(self.tree.selection())
        iid = getattr(self, "_context_iid", "")
        if self._select_mode:
            if selection:
                return selection
            return [iid] if iid and self.tree.exists(iid) else []
        if iid and self.tree.exists(iid):
            return selection if iid in selection else [iid]
        return selection

    def select_context_item(self) -> None:
        """Enter Select mode with only the row that opened the menu selected."""
        iid = getattr(self, "_context_iid", "")
        if not iid or not self.tree.exists(iid):
            return
        self._select_mode = True
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        self.show_preview()
        self.status_var.set(
            "Select mode: click rows to add or remove them; click outside the list to finish."
        )

    def edit_context_item(self) -> None:
        """Edit only the row under the pointer, leaving multi-edit unsupported."""
        iid = getattr(self, "_context_iid", "")
        if not iid or not self.tree.exists(iid):
            return
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        self.edit_selected()

    def _event_iid(self, event) -> str:
        """Get a Treeview item id from an event, with focus/row lookup as fallback."""
        item = getattr(event, "iid", None) if event is not None else None
        if item:
            return str(item)
        try:
            focused = self.tree.focus()
        except tk.TclError:  # pragma: no cover - tree unavailable during teardown
            return ""
        return str(focused or "")

    # ---------- Tree ----------
    def active_roots(self) -> list[Path]:
        """Roots currently in use: the source folder if selected, otherwise the defaults.

        A selected source folder replaces the defaults and is the only folder
        displayed and used as a write destination.
        """
        roots = list(getattr(self, "roots", []) or [])
        if getattr(self, "transient_roots", False) and roots:
            # --dir takes precedence for this session and does not alter the saved folder.
            return roots
        source = getattr(self, "settings", {}).get("source_dir")
        if source:
            path = Path(source)
            if path.is_dir():
                return [path]
            log_error(f"[source] source folder not found: {path}")
        return roots or default_roots()

    def _show_empty_state(self, empty: bool) -> None:
        """Show the tree or the guided empty state."""
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

    def _apply_saved_order(self, nodes: list[Node]) -> None:
        """Apply each folder's persisted sibling order, keeping new items visible."""
        order_map = self.settings.get("order") or {}
        for node in nodes:
            if not node.is_dir:
                continue
            preferred = order_map.get(_norm_path(node.path), [])
            rank = {name: index for index, name in enumerate(preferred)}
            default_index = {
                child.path.name: index for index, child in enumerate(node.children)
            }
            node.children.sort(
                key=lambda child: rank.get(
                    child.path.name, len(rank) + default_index[child.path.name]
                )
            )
            self._apply_saved_order(node.children)

    def refresh_tree(self) -> None:
        previous_highlight = self.__dict__.get("_context_highlight_iid", "")
        self._hover_iid = ""
        self._context_highlight_iid = ""
        self.tree.delete(*self.tree.get_children())
        roots = self.active_roots()
        nodes = scan_roots(roots)
        self._apply_saved_order(nodes)
        has_roots = bool(nodes)
        query = self.search_var.get().strip()
        if query:
            nodes = filter_tree(nodes, search_nodes(nodes, query))
        # Expand all folders during search so results are visible. Otherwise,
        # restore the saved collapsed state.
        self.collapsed = set() if query else self._collapsed_paths()
        total = 0
        for root_node in nodes:
            # The source folder is a container; show its contents
            # directly at the top level, including files at the root.
            for child in root_node.children:
                self._insert_node("", child)
            total += root_node.count_texts()
        self._show_empty_state(not nodes)
        if query and has_roots:
            self._show_empty_state(False)
        if query:
            self.status_var.set(
                f"Search results for '{query}': {total} matching files."
            )
        else:
            self.status_var.set(
                f"{total} .txt files in {len(roots)} folders "
                f"({', '.join(str(r) for r in roots)})"
            )
        self._update_source_label()
        # Row ids survive a refresh, so keep the darker highlight on the same row.
        if previous_highlight:
            self._set_context_highlight(previous_highlight)
        if "preview" in self.__dict__:
            self.show_preview()

    def _update_source_label(self) -> None:
        var = getattr(self, "source_var", None)
        if var is None:  # pragma: no cover - UI minimal
            return
        source = getattr(self, "settings", {}).get("source_dir")
        if source:
            var.set(f"📁 {Path(source).name} ↗")
        else:
            var.set("")

    def _insert_node(self, parent: str, node: Node, *, label: str | None = None) -> str:
        icon = "📁 " if node.is_dir else "📄 "
        iid = str(node.path)
        # Avoid duplicate item ids if two roots share a path (development vs system).
        if self.tree.exists(iid):
            iid = f"{iid}#{node.source_root}"
        collapsed = getattr(self, "collapsed", set())
        is_open = node.is_dir and str(node.path) not in collapsed
        self.tree.insert(
            parent,
            "end",
            iid=iid,
            text=f"{icon}{label if label is not None else node.name}",
            open=is_open,
            tags=("dir" if node.is_dir else "file"),
        )
        for child in node.children:
            self._insert_node(iid, child)
        return iid

    def move_selected(
        self, direction: int, *, item_ids: list[str] | None = None
    ) -> None:
        """Move selected siblings one position while preserving their relative order."""
        if self.search_var.get().strip():
            self.status_var.set("Clear the search before changing item order.")
            return
        selection = list(item_ids if item_ids is not None else self.tree.selection())
        selection = [iid for iid in selection if self.tree.exists(iid)]
        if not selection:
            return
        groups: dict[str, list[str]] = {}
        for iid in selection:
            groups.setdefault(self.tree.parent(iid), []).append(iid)

        step = -1 if direction < 0 else 1
        changed = 0
        order = dict(self.settings.get("order") or {})
        for parent_iid, group in groups.items():
            siblings = list(self.tree.get_children(parent_iid))
            selected = set(group)
            reordered = siblings.copy()
            indices = (
                range(1, len(reordered))
                if step < 0
                else range(len(reordered) - 2, -1, -1)
            )
            for index in indices:
                neighbor = index - 1 if step < 0 else index + 1
                if reordered[index] in selected and reordered[neighbor] not in selected:
                    reordered[index], reordered[neighbor] = (
                        reordered[neighbor],
                        reordered[index],
                    )
            if reordered == siblings:
                continue
            for index, iid in enumerate(reordered):
                self.tree.move(iid, parent_iid, index)
            parent_path = Path(group[0].split("#", 1)[0]).parent
            order[_norm_path(parent_path)] = [
                Path(iid.split("#", 1)[0]).name for iid in reordered
            ]
            changed += len(group)

        if not changed:
            label = "top" if step < 0 else "bottom"
            self.status_var.set(
                f"Selected items are already at the {label} of their folders."
            )
            return
        self.settings["order"] = order
        if not getattr(self, "transient_roots", False):
            save_settings(self.settings)
        verb = "up" if step < 0 else "down"
        self.status_var.set(f"Moved {changed} selected item(s) {verb}.")

    # ---------- Source folder and collapsed folders ----------
    def choose_source_dir(self) -> Path | None:
        """Choose one source folder, which will also be the write destination.

        The choice is saved in settings.json and restored in the next session.
        Return the chosen path, or None if canceled, for easier testing.
        """
        current = self.settings.get("source_dir")
        if not current or not Path(current).is_dir():
            current = str(Path.home())
        chosen = filedialog.askdirectory(
            title="Choose Source Folder (.txt files)",
            initialdir=current,
            mustexist=True,
        )
        if not chosen:
            return None
        path = Path(chosen).expanduser()
        if not path.is_dir():
            messagebox.showerror("Invalid folder", f"Not a folder: {path}")
            return None
        self.settings["source_dir"] = _norm_path(path)
        self.writable = path
        save_settings(self.settings)
        self.refresh_tree()
        self.status_var.set(f"📁 Source folder: {path}")
        return path

    def _collapsed_paths(self) -> set[str]:
        """Return the saved collapsed folders as normalized paths."""
        stored = self.settings.get("collapsed") or []
        return {_norm_path(p) for p in stored if str(p).strip()}

    def _save_collapsed(self, paths) -> None:
        """Save the collapsed folder state to settings.json."""
        unique = sorted({_norm_path(p) for p in paths if str(p).strip()})
        self.settings["collapsed"] = unique
        save_settings(self.settings)

    def _toggle_collapsed(self, iid: str) -> None:
        """Record whether a folder was opened or closed, then save the state.

        Called by the TreeviewOpen and TreeviewClose events.
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

    def collapse_all(self) -> None:
        """Collapse every visible folder and persist its state."""
        collapsed = set()

        def visit(parent: str) -> None:
            for iid in self.tree.get_children(parent):
                path = Path(str(iid).split("#", 1)[0])
                if not path.is_dir():
                    continue
                collapsed.add(_norm_path(path))
                self.tree.item(iid, open=False)
                visit(iid)

        visit("")
        self.collapsed = collapsed
        self._save_collapsed(collapsed)
        self.status_var.set("All folders collapsed.")

    def _prune_collapsed(self, path: Path) -> None:
        """Remove collapsed-state entries for ``path`` and its descendants."""
        self._prune_collapsed_paths([path])

    def _prune_collapsed_paths(self, paths: list[Path]) -> None:
        """Prune several deleted trees and save the remaining state once."""
        removed_roots = [Path(_norm_path(path)) for path in paths]
        current = self._collapsed_paths()
        remaining = {
            value
            for value in current
            if not any(_is_within(value, root) for root in removed_roots)
        }
        if remaining != current:
            self._save_collapsed(remaining)

    def selected_path(self) -> Path | None:
        sel = self.tree.selection()
        if not sel:
            return None
        # An item id may have a #root suffix; keep only its path.
        raw = sel[0].split("#")[0]
        return Path(raw)

    def show_preview(self) -> None:
        path = self.selected_path()
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        if path is None or path.is_dir() or not path.exists():
            self.current_file = None
            self.preview.insert(
                "1.0",
                "Select a .txt file to preview its contents.\n\n"
                "Click once or press Enter to paste into another app.",
            )
            self.info_var.set("Select a .txt file on the left.")
            self.preview.configure(state="disabled")
            return
        self.current_file = path
        try:
            text = read_text(path)
        except OSError as exc:
            text = f"(Could not read file: {exc})"
        self.preview.insert("1.0", text)
        self.preview.configure(state="disabled")
        self.info_var.set(
            f"{path.name} — {len(text)} characters, {len(text.split())} words\n{path}"
        )

    # ---------- Copy and paste ----------
    def on_activate(self) -> None:
        """Click once or press Enter to paste into another app."""
        self.do_paste()

    def do_paste(self) -> None:
        """Click once to paste text into another app.

        The flow is:

          1. Copy the text while Pastex remains visible.
          2. Minimize the window after copying so the target app receives focus;
             minimizing keeps the dock entry, unlike withdrawing the window.
          3. Send Ctrl+V to the focused app.
          4. Wait briefly, then restore Pastex with deiconify().
        """
        if self._pasting:
            self.status_var.set("Paste in progress...")
            return
        path = self.selected_path()
        if path is None or path.is_dir():
            self.status_var.set("Select a .txt file first.")
            return
        try:
            text = read_text(path)
        except OSError as exc:
            messagebox.showerror("Read failed", str(exc))
            return
        self._pasting = True
        self.status_var.set("Preparing to paste...")
        # Leave Pastex visible while setting the clipboard. Once copying succeeds,
        # _after_copy() withdraws it immediately before sending Ctrl+V.
        _run_in_thread(self._paste_worker, text)

    def _paste_worker(self, text: str) -> None:
        started = time.monotonic()
        result = copy_text(text)
        elapsed_ms = (time.monotonic() - started) * 1000
        log_error(
            f"[paste] salin: ok={result.ok} tool={result.tool} ({elapsed_ms:.0f} ms)"
        )
        self.after(0, self._after_copy, result)

    def _after_copy(self, result) -> None:
        """Send Ctrl+V immediately after the clipboard tool accepts the text."""
        if not result.ok:
            self._paste_failed(result.message)
            return
        self._hide_for_paste()
        self._send_paste()

    def _hide_for_paste(self) -> None:
        """Take Pastex out of the way while keeping its dock entry alive.

        ``withdraw()`` unmaps the only window, so GNOME drops an unpinned app
        from the dock and only shows it again once a click makes the window
        active — which looked like the icon vanishing on one click and coming
        back on the next. Minimizing keeps the window mapped, so the dock
        entry survives while focus returns to the previous application.
        """
        try:
            self.iconify()
            self.update_idletasks()
        except TK_ERRORS:
            try:
                self.withdraw()
            except TK_ERRORS:  # pragma: no cover - window already gone
                pass
            return
        try:
            state = str(self.state())
        except TK_ERRORS:  # pragma: no cover - state unavailable in test doubles
            state = "iconic"
        if state not in {"iconic", "withdrawn"}:
            # The window manager refused to minimize: fall back to withdrawing
            # so the target application still receives Ctrl+V.
            try:
                self.withdraw()
            except TK_ERRORS:  # pragma: no cover - window already gone
                pass
            return
        log_error("[paste] window minimized before Ctrl+V; dock entry kept")

    def _send_paste(self) -> None:
        """Wait for the window manager to hand focus over, then send Ctrl+V."""
        if FOCUS_SETTLE_MS <= 0 or not self._tk_ready():
            _run_in_thread(self._paste_sender)
            return
        try:
            self.after(
                FOCUS_SETTLE_MS, lambda: _run_in_thread(self._paste_sender)
            )
        except TK_ERRORS:  # pragma: no cover - interpreter already gone
            _run_in_thread(self._paste_sender)

    def _paste_sender(self) -> None:
        log_error("[paste] sending Ctrl+V")
        result = simulate_paste()
        log_error(
            f"[paste] hasil: ok={result.ok} tool={result.tool} pesan={result.message}"
        )
        self.after(0, self._paste_done, result)

    def _paste_done(self, result) -> None:
        """Wait after sending Ctrl+V, then restore the Pastex window."""
        delay_ms = int(float(self.settings.get("restore_delay", 0.1)) * 1000)
        if delay_ms > 0:
            log_error(f"[paste] waiting {delay_ms} ms before restoring the window")
            self.after(delay_ms, lambda: PastexApp._finish_paste(self, result))
        else:
            PastexApp._finish_paste(self, result)

    def _finish_paste(self, result) -> None:
        self._pasting = False
        self.deiconify()
        if not result.ok:
            self.lift()
            self.status_var.set(f"📋 Text copied. {result.message}")
            self._show_paste_failure(result.message)
            return
        if getattr(result, "confident", True):
            log_error("[paste] succeeded; window restored")
            self.status_var.set(f"⚡ {result.message}")
            return
        # The command succeeded but its effect is uncertain (for example, xdotool
        # on Wayland). Do not claim success; ask the user to verify.
        log_error("[paste] sent; success is not yet confirmed")
        self.lift()
        self.status_var.set(
            f"⚡ {result.message} If the text does not appear, press Ctrl+V."
        )

    def _paste_failed(self, message: str) -> None:
        self._pasting = False
        # Keep the app visible so the clipboard failure message can be read.
        self.deiconify()
        self.lift()
        self.status_var.set(f"Could not copy to the clipboard: {message}")
        self._show_paste_failure(message)

    # ---------- Automatic-paste permissions (pastex group) ----------
    def _show_paste_failure(self, message: str) -> None:
        """Show the failure reason and CLI commands that may resolve it.

        Since 1.5.0, the GUI permission button has been removed. Configure
        /dev/uinput permissions with `pastex --enable-permissions`
        and inspected with `--permissions`. The dialog provides instructions
        and the command to run in a terminal.
        """
        st = uinput.status()
        text = (
            f"{message}\n\nThe text is on the clipboard — "
            "press Ctrl+V in the target app."
        )
        if st.ready:
            messagebox.showinfo("Automatic paste is unavailable", text)
            return
        text += (
            "\n\nDiagnosis:\n"
            + "\n".join(_status_lines(st))
            + "\n\n"
            + uinput.explain(st)
            + "\n\n"
            + uinput.CLI_HINT
        )
        messagebox.showinfo("Automatic paste is unavailable", text)

    # ---------- Create, edit, and delete ----------
    def _target_dir(self) -> Path:
        """Return the folder where new folders and snippets should be created.

        When a source folder is selected, all writes must remain inside it;
        the parent root is outside the selected source and cannot be used.
        """
        if getattr(self, "transient_roots", False):
            path = self.selected_path()
            for root in self.active_roots():
                if path is not None and _is_within(path, root):
                    return path if path.is_dir() else path.parent
            return self.writable
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

    def _expand_parent_if_collapsed(self, parent: Path) -> None:
        """Ensure a newly created item's parent folder is expanded."""
        collapsed = self._collapsed_paths()
        key = _norm_path(parent)
        if key in collapsed:
            collapsed.discard(key)
            if getattr(self, "transient_roots", False):
                self.settings["collapsed"] = sorted(collapsed)
            else:
                self._save_collapsed(collapsed)

    def add_folder(self) -> None:
        name = simpledialog.askstring("New Folder", "Folder name:")
        if not name:
            return
        try:
            created = create_folder(self._target_dir(), name)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not create folder", str(exc))
            return
        self._expand_parent_if_collapsed(created.parent)
        self.refresh_tree()
        iid = str(created)
        if self.tree.exists(iid):
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            self._set_context_highlight(iid)

    def add_text(self) -> None:
        name = simpledialog.askstring(
            "New text", "File name (the .txt extension is optional):"
        )
        if not name:
            return
        try:
            created = create_snippet(self._target_dir(), name, "")
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not create text", str(exc))
            return
        self._expand_parent_if_collapsed(created.parent)
        self.refresh_tree()
        iid = str(created)
        if self.tree.exists(iid):
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            self._set_context_highlight(iid)
            if "preview" in self.__dict__:
                self.show_preview()
            self.edit_selected()
        else:
            editor = EditorWindow(self, created)
            if "tk" in self.__dict__:
                self.wait_window(editor)

    def _remap_folder_settings(self, old_path: Path, new_path: Path) -> None:
        """Keep collapsed and ordering preferences attached after a folder rename."""
        old = old_path.resolve()
        new = new_path.resolve()

        def remap(value: str) -> str:
            path = Path(value)
            try:
                relative = path.resolve().relative_to(old)
            except (OSError, ValueError):
                return value
            return str(new / relative)

        self.settings["collapsed"] = [
            remap(value) for value in self.settings.get("collapsed", [])
        ]
        old_parent = str(old.parent)
        order = self.settings.get("order") or {}
        remapped_order = {}
        for parent, names in order.items():
            mapped_parent = remap(parent)
            mapped_names = list(names)
            if str(Path(parent).resolve()) == old_parent:
                mapped_names = [
                    new.name if name == old.name else name for name in mapped_names
                ]
            remapped_order[mapped_parent] = mapped_names
        self.settings["order"] = remapped_order
        save_settings(self.settings)

    def edit_selected(self) -> None:
        path = self.selected_path()
        # Editing also moves the darker current-row highlight to the edited row.
        selection = self.tree.selection()
        if selection:
            self._set_context_highlight(str(selection[0]))
        if path is None:
            self.status_var.set("Select a text file or folder to edit.")
            return
        if path.is_dir():
            name = simpledialog.askstring(
                "Rename Folder", "New folder name:", initialvalue=path.name
            )
            if not name:
                return
            try:
                renamed = rename_folder(path, name)
            except (OSError, ValueError) as exc:
                messagebox.showerror("Could not rename folder", str(exc))
                return
            self._remap_folder_settings(path, renamed)
            self.refresh_tree()
            iid = str(renamed)
            if self.tree.exists(iid):
                self.tree.selection_set(iid)
                self.tree.focus(iid)
                self._set_context_highlight(iid)
                self.show_preview()
            self.status_var.set(f"Renamed folder to '{renamed.name}'.")
            return
        editor = EditorWindow(self, path)
        if "tk" in self.__dict__:
            self.wait_window(editor)

    def delete_selected(self, item_ids: list[str] | None = None) -> None:
        """Delete selected files and folders together after one confirmation."""
        selection = list(item_ids if item_ids is not None else self.tree.selection())
        paths = list(
            dict.fromkeys(
                Path(iid.split("#", 1)[0]) for iid in selection if self.tree.exists(iid)
            )
        )
        if not paths:
            return
        selected_folders = [path for path in paths if path.is_dir()]
        paths = [
            path
            for path in paths
            if not any(
                path != folder and _is_within(path, folder)
                for folder in selected_folders
            )
        ]
        if len(paths) == 1:
            path = paths[0]
            prompt = f"Delete '{path.name}'?\n{path}"
        else:
            details = "\n".join(str(path) for path in paths)
            prompt = f"Delete these {len(paths)} selected items?\n{details}"
        if not messagebox.askyesno("Delete", prompt):
            return

        try:
            for path in paths:
                delete_path(path)
        except OSError as exc:
            messagebox.showerror("Could not delete", str(exc))
        else:
            # Remove collapsed-state entries for deleted folders in one save.
            self._prune_collapsed_paths(paths)
            self._select_mode = False
        self.refresh_tree()

    def open_source_folder(self) -> None:
        """Open the chosen source folder when its clickable name is activated."""
        source = self.settings.get("source_dir")
        if not source or not Path(source).is_dir():
            self.status_var.set("Choose a source folder first.")
            return
        try:
            subprocess.Popen(["xdg-open", str(Path(source))])
        except OSError as exc:
            messagebox.showerror("Could not open source folder", str(exc))


def _status_lines(st: uinput.UinputStatus, *, prefix: str = "") -> list[str]:
    """Return formatted permission-status lines for the GUI and --check."""
    return [
        f"{mark} {label}: {detail}" for mark, label, detail in st.summary(prefix=prefix)
    ]


class EditorWindow(_ToplevelBase):  # type: ignore[misc,valid-type]
    """Built-in editor for a single .txt file."""

    def __init__(self, master, path: Path):
        if tk is None:
            raise RuntimeError("tkinter is unavailable.")
        super().__init__(master)
        self.title(f"Edit — {path.name}")
        self.geometry("640x460")
        self.path = path
        try:
            content = read_text(path)
        except OSError as exc:
            content = f"(Could not read: {exc})"
        self.text = tk.Text(self, wrap="word", font=("Monospace", 11))
        self.text.pack(fill="both", expand=True, padx=8, pady=8)
        self.text.insert("1.0", content)
        bar = ttk.Frame(self, padding=8)
        bar.pack(fill="x")
        ttk.Button(bar, text="💾 Save (Ctrl+S)", command=self.save).pack(side="left")
        ttk.Button(bar, text="⚡ Save & Paste", command=self.save_and_paste).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(bar, text="Close", command=self.destroy).pack(side="right")
        self.bind("<Control-s>", lambda _e: self.save())

    def save(self) -> bool:
        try:
            write_text(self.path, self.text.get("1.0", "end-1c"))
        except OSError as exc:
            messagebox.showerror("Could not save", str(exc))
            return False
        if isinstance(self.master, PastexApp):
            self.master.refresh_tree()
            self.master.status_var.set(f"💾 Saved: {self.path}")
        return True

    def save_and_paste(self) -> None:
        if not self.save():
            return
        if isinstance(self.master, PastexApp):
            iid = str(self.path)
            if self.master.tree.exists(iid):
                self.master.tree.selection_set(iid)
                self.master.tree.focus(iid)
                self.master.show_preview()
            self.destroy()
            if self.master.tree.exists(iid):
                self.master.do_paste()


def run_cli(argv: list[str] | None = None) -> int:
    """Non-GUI CLI options for listing, copying, and pasting files."""
    parser = argparse.ArgumentParser(
        prog="pastex",
        description="Paste text snippets from a .txt folder hierarchy (GUI + CLI).",
    )
    parser.add_argument(
        "--dir",
        action="append",
        default=[],
        help="Additional snippet folder (may be repeated).",
    )
    parser.add_argument(
        "--list", action="store_true", help="List all text files and exit."
    )
    parser.add_argument(
        "--copy", metavar="FILE", help="Copy FILE contents to the clipboard."
    )
    parser.add_argument(
        "--paste", metavar="FILE", help="Copy FILE and send Ctrl+V automatically."
    )
    parser.add_argument("--version", action="store_true", help="Show the version.")
    parser.add_argument(
        "--test-paste",
        action="store_true",
        help="Send test text through ydotool (diagnostics).",
    )
    parser.add_argument(
        "--ctrl-v",
        action="store_true",
        help="With --test-paste, send Ctrl+V instead of text.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check the environment (tkinter, display, tools) and exit.",
    )
    # /dev/uinput permissions are managed through the CLI (since 1.5.0).
    parser.add_argument(
        "--permissions",
        action="store_true",
        help="Show automatic-paste permissions (/dev/uinput).",
    )
    parser.add_argument(
        "--enable-permissions",
        action="store_true",
        help="Request /dev/uinput access (the 'pastex' group via pkexec).",
    )
    parser.add_argument(
        "--export-data",
        metavar="FOLDER",
        nargs="?",
        const="",
        default=None,
        help="Export sample snippets to FOLDER (default: the user data folder).",
    )
    args = parser.parse_args(argv)

    if args.version:
        print(f"pastex {__version__}")
        return 0

    roots = [Path(d) for d in args.dir] or default_roots()
    if args.check:
        return run_check(roots)
    if args.permissions:
        return run_permission_info()
    if args.enable_permissions:
        return run_enable_permission()
    if args.export_data is not None:
        target = Path(args.export_data) if args.export_data else user_root()
        export_data(target)
        return 0
    if args.list:
        nodes = scan_roots(roots)
        for node in nodes:
            _print_tree(node, prefix="")
        if not flatten_texts(nodes):
            print("(no .txt files found)", file=sys.stderr)
            print(
                "Choose a source folder in the GUI, use --dir FOLDER, or "
                "run --export-data.",
                file=sys.stderr,
            )
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
        print(
            "Error: tkinter is unavailable. Install python3-tk "
            "(sudo apt install python3-tk).",
            file=sys.stderr,
        )
        return 1

    try:
        # --dir applies only to this session; it does not change the saved source
        # folder.
        app = PastexApp(roots=roots, transient_roots=bool(args.dir))
    except Exception:  # noqa: BLE001 - report unexpected failures during GUI startup
        tb = traceback.format_exc()
        log_error(f"Could not start GUI:\n{tb}")
        print(f"Error: Pastex could not start. See {ERROR_LOG}", file=sys.stderr)
        print(tb, file=sys.stderr)
        return 1
    app.mainloop()
    return 0


TEST_PASTE_TEXT = "pastex-ok "


def run_test_paste(ctrl_v: bool = False) -> int:
    """Type test text or send Ctrl+V to the active window for diagnostics.

    If the test text appears in the target app, /dev/uinput permissions,
    the daemon, and ydotool are working. Any remaining issue is with clipboard
    access or the Ctrl+V key event.
    """
    from .clipboard import detect_session, ensure_ydotoold, type_text

    print(f"Paste test — graphical session: {detect_session()}")
    if ctrl_v:
        print("Sending Ctrl+V to the active window in 3 seconds...")
    else:
        print(f"Sending test text {TEST_PASTE_TEXT!r} in 3 seconds...")
    print("Click the target window NOW (for example, a comment box or chat).")
    time.sleep(3)

    if ctrl_v:
        return _full_paste_test()

    result = type_text(TEST_PASTE_TEXT)
    if not result.ok:
        print("Typing failed; starting ydotoold and retrying...")
        ensure_ydotoold()
        result = type_text(TEST_PASTE_TEXT)
    if not result.ok:
        print(f"❌ {result.message}")
        print(f"Check {ERROR_LOG} and run `pastex --check`.")
        return 2
    print(f"✅ {result.message}")
    print(
        "If the test text APPEARS in the target window, ydotool, permissions, and the daemon "
        "are working correctly."
    )
    print(
        "If it does NOT appear, the compositor may be ignoring the uinput device "
        "(check `lsinput` or the system log)."
    )
    print("Test the complete flow (clipboard + Ctrl+V): pastex --test-paste --ctrl-v")
    return 0


def _full_paste_test() -> int:
    """Test the actual paste flow: copy, verify, then send Ctrl+V."""
    from .clipboard import (
        copy_text,
        ensure_ydotoold,
        read_text_from_clipboard,
        simulate_paste,
    )

    probe = f"{TEST_PASTE_TEXT}{os.getpid()}"
    print(f"Copying test text {probe!r}...")
    copied = copy_text(probe)
    print(("✅ " if copied.ok else "❌ ") + copied.message)
    back = read_text_from_clipboard()
    if back is None:
        print("⚠️ Could not read the clipboard back; verification skipped.")
    elif back.rstrip("\n") == probe:
        print("✅ Clipboard contents verified")
    else:
        print(f"❌ Clipboard contents differ: {back.strip()[:60]!r}")
        print(
            "   The text was not copied correctly; Ctrl+V will paste "
            "the previous contents."
        )
        return 2

    print("Sending Ctrl+V in 1 second — click the target window NOW.")
    time.sleep(1)
    paste = simulate_paste()
    if not paste.ok:
        ensure_ydotoold()
        paste = simulate_paste()
    if not paste.ok:
        print(f"❌ {paste.message}")
        return 2
    print(f"✅ {paste.message}")
    print(
        f"If {probe!r} appears in the target window, automatic paste is working "
        "correctly."
    )
    return 0


def permission_report() -> str:
    """Build an automatic-paste permission report for --permissions and --check."""
    st = uinput.status()
    lines = "\n".join(_status_lines(st))
    return (
        f"Automatic-paste permissions (group '{uinput.GROUP}', account '{st.user}'):\n"
        f"{lines}\n\nNext steps:\n{uinput.explain(st)}"
    )


def run_permission_info() -> int:
    """Print /dev/uinput permission status without opening the GUI."""
    print(permission_report())
    if not uinput.status().ready:
        print(f"\n{uinput.CLI_HINT}")
    return 0


def run_enable_permission() -> int:
    """Request /dev/uinput permissions; replaces the removed GUI button."""
    st = uinput.status()
    if st.device_writable:
        print("Automatic paste permissions are already enabled.")
        return 0
    if not uinput.can_self_enable(st):
        print("Cannot request administrator privileges from here.")
        print("Run the following command in a terminal:\n")
        print(uinput.MANUAL_COMMAND)
        return 2
    print(f"Requesting /dev/uinput access for account '{st.user}'...")
    code, out, err = uinput.enable_for_user(st.user)
    for line in (out, err):
        if line:
            print(line)
    if code == 0:
        print("\n✅ Permissions enabled.")
        print(
            "Log out and back in (or reboot) to activate the new group; "
            "automatic paste should then work."
        )
        return 0
    if code in (126, 127):
        print("\n❌ Password request cancelled.")
        return 1
    print(f"\n❌ Activation failed (exit code {code}).")
    print("\nManual alternative in a terminal:\n")
    print(uinput.MANUAL_COMMAND)
    return 1


def clipboard_status() -> list[str]:
    """Test writing to and reading back from the system clipboard.

    wl-copy and xclip fork and own the selection. A failure here (for example,
    a pipe hang causing the tool to be reported as failed) can make automatic
    paste send the previous clipboard contents even when Ctrl+V is delivered.
    """
    from .clipboard import (
        available_tools,
        copy_text,
        detect_session,
        read_text_from_clipboard,
    )

    tools = available_tools()
    lines = [
        "tool: "
        + ", ".join(
            f"{k}={'available' if v else 'not available'}"
            for k, v in tools.items()
            if k in ("wl-copy", "xclip", "xsel")
        )
    ]
    if detect_session() == "unknown":
        lines.append("No graphical session detected — clipboard test skipped")
        return lines

    probe = f"pastex-{os.getpid()}"
    result = copy_text(probe)
    if not result.ok:
        lines.append(f"❌ copy: {result.message}")
        return lines
    lines.append(f"✅ copied through {result.tool}")

    back = read_text_from_clipboard()
    if back is None:
        lines.append("(read-back not supported — install wl-paste or xclip)")
    elif back.strip() == probe:
        lines.append("✅ read-back matches")
    else:
        lines.append(f"⚠️ read-back differs: {back.strip()[:40]!r}")
    return lines


def run_check(roots: list[Path]) -> int:
    """Print environment diagnostics for troubleshooting."""
    from .clipboard import available_tools, detect_session

    print(f"pastex {__version__}")
    print(f"Python: {sys.version.split()[0]} ({sys.executable})")
    print(
        f"Graphical session: {detect_session()} "
        f"(DISPLAY={os.environ.get('DISPLAY', '-')}, "
        f"WAYLAND_DISPLAY={os.environ.get('WAYLAND_DISPLAY', '-')})"
    )
    print(
        f"tkinter: {'available' if tk is not None else 'NOT available — install python3-tk'}"
    )
    tools = available_tools()
    print(
        "Clipboard/paste tools: "
        + ", ".join(
            f"{k}={'available' if v else 'not available'}" for k, v in tools.items()
        )
    )
    print(f"Application icon: {ICON_NAME} (expected WM_CLASS '{WM_CLASS}')")
    found_icons = icon_files()
    if found_icons:
        for path in found_icons:
            print(f"  ✅ {path}")
    else:
        print(
            "  ❌ not found — the window will use the default icon; "
            "reinstall the package or run scripts/make-icon-png.py"
        )
    try:
        st = uinput.status()
        print(
            f"Automatic-paste permissions (group '{uinput.GROUP}', account '{st.user}'):"
        )
        for line in _status_lines(st):
            print(f"  {line}")
        print("\nNext steps:")
        for line in uinput.explain(st).splitlines():
            print(f"  {line}")
        if not st.ready:
            # Keep permission information in --check even though the GUI button was removed.
            print(f"\n{uinput.CLI_HINT}")
    except Exception as exc:  # noqa: BLE001 - diagnostics should continue after optional checks fail
        print(f"Automatic-paste permissions: could not be checked ({exc})")
    clip = clipboard_status()
    print("Clipboard:")
    for line in clip:
        print(f"  {line}")
    settings = load_settings()
    source = settings.get("source_dir")
    print("Source folder:")
    if source:
        print(f"  - {source} ({'exists' if Path(source).is_dir() else 'MISSING'})")
    else:
        print("  (not selected — using the default folders below)")
    print("Snippet folders:")
    for r in roots:
        print(f"  - {r} ({'exists' if r.is_dir() else 'not found'})")
    print(f"Settings: {SETTINGS_FILE}")
    print(f"Error log: {ERROR_LOG}")
    return 0


def _print_tree(node: Node, prefix: str) -> None:
    label = node.name + ("/" if node.is_dir else ".txt")
    print(f"{prefix}{label}")
    for child in node.children:
        _print_tree(child, prefix + "  ")


def main() -> None:
    try:
        code = run_cli()
    except Exception:  # noqa: BLE001 - log unexpected failures at the CLI boundary
        tb = traceback.format_exc()
        log_error(tb)
        print(f"Unexpected error. See {ERROR_LOG}.", file=sys.stderr)
        print(tb, file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
