"""Pastex GUI — a hierarchy of folders containing .txt files.

Launch: pastex (no arguments). Click or press Enter on a .txt file to copy
and paste it into another app.

"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

from . import __version__, uinput
from .clipboard import (
    ClipboardResult,
    PasteResult,
    available_tools,
    copy_text,
    detect_session,
    force_minimize_window,
    prewarm as prewarm_clipboard,
    read_text_from_clipboard,
    set_clipboard_owner,
    activate_window,
    focus_the_window_below,
    focus_window,
    normalize_window_id,
    x11_active_window,
    restore_window,
    simulate_paste,
    can_type_text,
    type_text_x11,
    verify_copy as verify_clipboard,
    window_manager,
    x11_focus_moved,
    x11_window_unmapped,
    x11_wm_state,
)
from .index import SnippetIndex
from .instance import SingleInstance, send_command
from .picker import open_picker
from .watcher import DirectoryWatcher, last_error as watcher_module_last_error
from .snippets import (
    Node,
    create_folder,
    create_snippet,
    default_roots,
    delete_path,
    read_text,
    rename_folder,
    writable_root,
    write_text,
)
from .snippets import (
    search as search_nodes,
)

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog, ttk
except ImportError:  # pragma: no cover - handled at runtime
    tk = None  # type: ignore[assignment]
    filedialog = messagebox = simpledialog = ttk = None  # type: ignore[assignment]

# Use object as the GUI base when tkinter is unavailable so the module still
# imports (for tests) without a display.
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
# on Linux Mint (verified with xprop) is "Pastex", not the supplied className.
# The desktop StartupWMClass must match the actual value.
APP_ID = "pastex"
TK_CLASS_NAME = "Pastex"
WM_CLASS = "Pastex"
ICON_NAME = APP_ID
# Time given to the window manager to move focus to the previous application
# after Pastex hides itself, before Ctrl+V is sent.
FOCUS_SETTLE_MS = 80

# iconify() only *asks* the window manager to minimize; the window is still
# reported as "normal" for a while (see the 1.8.1 log line "did not minimize
# (state=normal)"), so the check is retried instead of taken once.
# Cinnamon animates the minimize, so the window can take a few hundred ms to
# report "iconic". 600 ms keeps three times that headroom; giving up earlier
# made the code decide "never hidden" and skip Ctrl+V entirely.
MINIMIZE_WAIT_MS = 900
MINIMIZE_RETRY_MS = 20
# After this much waiting, ask the X server who holds the input focus. The
# paste only needs the focus to leave Pastex, and WM_STATE (which Tk's
# ``state()`` reports) is not updated by every window manager, so polling the
# focus is what actually decides the paste.
MINIMIZE_FOCUS_CHECK_AT_MS = 120
MINIMIZE_FOCUS_RETRY_MS = 100
# Restoring is checked and repeated as well: deiconify() is dropped when it
# lands while the minimize animation is still running, which left the window
# minimized for good.
RESTORE_WAIT_MS = 1200
RESTORE_RETRY_MS = 60
# deiconify() is asked again for this long whatever the state says: Tk's
# "wm state" reported "normal" for a window that was still minimized, so the
# repeat must not depend on it.
RESTORE_REPEAT_MS = 400
# If the window is still not back by then, ask the window manager directly.
RESTORE_ACTIVATE_AT_MS = 240
# Some compositors need a second request before they hide the window.
MINIMIZE_REHIDE_AT_MS = 120
# Some window managers ignore every minimize they are sent -- Muffin did, and
# the click then sat for 900 ms and gave up with nothing pasted. After this
# long with the focus still on Pastex, the focus is handed to the window behind
# it instead: that cannot be refused, and the text then goes where it belongs.
MINIMIZE_FOCUS_TRANSFER_AT_MS = 200

# Hard deadline for the "send Ctrl+V" half of a click. Every tool call has its
# own timeout, but a wedged daemon or an unanswered portal consent dialog could
# still keep the window minimized for a minute; when this fires the window is
# restored and the remaining tools are abandoned.
PASTE_DEADLINE_MS = 9000

# Shown when the window could not be minimized: the text is on the
# clipboard, but Pastex keeps focus so the synthetic Ctrl+V is skipped.
PASTE_MANUAL_HINT = "📋 Text copied — press Ctrl+V in the target app."


def log_error(message: str) -> None:
    """Append an error message to ~/.config/pastex/error.log for diagnostics."""
    try:
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        with ERROR_LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {message}\n")
    except OSError:
        pass


def _xdotool_available() -> bool:
    """True when the window manager can be asked directly (xdotool is present)."""
    try:
        return shutil.which("xdotool") is not None
    except Exception:  # noqa: BLE001 - never break a click on PATH oddities
        return False


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
    if tk is None:  # pragma: no cover - no tkinter
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
        window.wm_iconname(ICON_NAME)  # fallback: the themed icon name
    except (tk.TclError, AttributeError):  # pragma: no cover - optional WM icon name
        return used
    return used


# Restore delays shipped in earlier versions. They are migrated to the current
# default at load time, while a value the user chose deliberately is kept.
PREVIOUS_RESTORE_DELAYS = (0.1, 0.05)

DEFAULT_SETTINGS = {
    # Keep Pastex hidden briefly after Ctrl+V is sent before restoring its
    # window. The text is already pasted by then, so this only delays the
    # window coming back; 30 ms is enough for the target app to keep focus.
    "restore_delay": 0.03,
    # User-selected source folder. If set, only this folder is displayed
    # and used for writes.
    "source_dir": None,
    # Folders currently collapsed in the tree (absolute paths).
    "collapsed": [],
    # Optional per-folder child ordering, keyed by absolute parent path.
    "order": {},
    "setup_assistant_seen": False,
    # Opt-in (1.8.0): own the Wayland selection from a helper process instead
    # of spawning wl-copy for every copy. Off until the user asks for it,
    # because the protocol glue cannot be exercised without a compositor.
    "clipboard_owner": False,
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
    # Restores now happen sooner: migrate the earlier defaults, but keep any
    # value the user chose deliberately.
    try:
        restore_delay = float(settings.get("restore_delay", 0))
        if any(
            abs(restore_delay - previous) < 1e-9
            for previous in PREVIOUS_RESTORE_DELAYS
        ):
            settings["restore_delay"] = DEFAULT_SETTINGS["restore_delay"]
    except (TypeError, ValueError):
        settings["restore_delay"] = DEFAULT_SETTINGS["restore_delay"]
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

        ``transient_roots=True`` marks these roots as session-only: they apply
        only while the process is running and are never saved to settings, so
        the user's selected source folder remains unchanged.
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
        # False only between clicking a snippet and the clipboard owning the
        # text, so Ctrl+V never runs against the previous clipboard contents.
        self._paste_clipboard_ready = False
        # Tool plans resolved once in the background (see _prewarm_paste).
        self._copy_plan = None
        self._paste_plan = None
        self._search_after_id: str | None = None
        self._select_mode = False
        self.collapsed: set[str] = set()
        # Snippet tree and file contents are cached so repaints and searches
        # stop walking the disk (see pastex/index.py).
        self.index = SnippetIndex()
        self.watcher = None
        self._build_ui()
        self.index.set_roots(self.active_roots())
        self.refresh_tree(force=True)
        # Ensure the window is visible and focused (important when launched from a menu).
        self.deiconify()
        self.lift()
        self.focus_force()
        self._center_window()
        # Resolve clipboard and paste tools off the click path.
        self._prewarm_paste()
        # Follow the snippet folders instead of rescanning them.
        self._start_watcher()
        if not self.settings.get("setup_assistant_seen", False):
            # Check in the background so system permission probes never delay startup.
            self.after(250, self._start_first_run_setup_check)

    def _prewarm_paste(self) -> None:
        """Resolve the clipboard and paste tools before the first click.

        ``shutil.which`` walks PATH once per candidate and, on Wayland,
        ``ydotoold`` has to be running before the first ``ydotool key``.
        Both live in a daemon thread so startup stays instant.
        """
        _run_in_thread(self._prewarm_paste_worker)

    def _prewarm_paste_worker(self) -> None:
        copy_plan, paste_plan = prewarm_clipboard()
        self._copy_plan = copy_plan
        self._paste_plan = paste_plan
        try:
            daemon = "running" if uinput.ydotoold_running() else "not running"
        except Exception:  # noqa: BLE001 - diagnostics must never break startup
            daemon = "?"
        try:
            wm = window_manager() or "none (nothing can minimize the window)"
        except Exception:  # noqa: BLE001 - diagnostics must never break startup
            wm = "?"
        log_error(
            "[paste] ready: "
            f"copy={','.join(copy_plan.tool_names()) or '-'} "
            f"paste={','.join(paste_plan.tool_names()) or '-'} "
            f"ydotoold={daemon} wm={wm} "
            f"session={os.environ.get('XDG_SESSION_TYPE', '-')}/"
            f"{os.environ.get('XDG_CURRENT_DESKTOP', '-')}"
        )
        if self.__dict__.get("settings", {}).get("clipboard_owner"):
            self._start_clipboard_owner()

    def _start_clipboard_owner(self) -> bool:
        """Own the Wayland selection instead of spawning wl-copy (opt-in).

        Runs in the prewarm thread. The helper process only becomes the copy
        path once it has proved that the compositor really serves what it
        offers; otherwise ``wl-copy`` keeps being used and nothing changes.
        """
        from . import wlowner  # noqa: PLC0415 - optional feature

        if not wlowner.available():
            log_error("[owner] no Wayland session; keeping the clipboard tools")
            return False
        owner = wlowner.OwnerClipboard()
        if not owner.start():
            log_error(f"[owner] helper did not start: {owner.error}")
            return False
        ok, detail = wlowner.verify(
            owner, read_text_from_clipboard, restore=copy_text
        )
        if not ok:
            log_error(f"[owner] self-test failed ({detail}); keeping the tools")
            owner.stop()
            return False
        self._clipboard_owner = owner
        set_clipboard_owner(owner)
        log_error("[owner] owning the Wayland selection; wl-copy stays the fallback")
        return True

    def _release_clipboard_owner(self) -> None:
        """Stop the helper and go back to the clipboard tools."""
        owner = self.__dict__.get("_clipboard_owner")
        if owner is None:
            return
        self._clipboard_owner = None
        # Stop routing copies through it before it exits, so a click during
        # shutdown cannot write into a dead pipe.
        set_clipboard_owner(None)
        try:
            owner.stop()
        except OSError:  # pragma: no cover - already gone
            pass

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
                message += f"\n\nSee the log at {ERROR_LOG} for details."
                messagebox.showinfo("Setup Requested", message)
                return
            message = err or out or f"The setup helper exited with code {code}."
            messagebox.showwarning(
                "Setup Not Completed",
                f"Pastex could not configure automatic paste.\n\n{message}\n\n"
                f"See the log at {ERROR_LOG} for details.",
            )
            return
        messagebox.showwarning(
            "Automatic Paste Setup",
            f"Pastex checked its clipboard and paste prerequisites before use.\n\n"
            f"{details}\n\nSee the log at {ERROR_LOG} for a full report.",
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
        # /dev/uinput is configured through the first-run setup dialog and
        # the paste-failure dialog; status is shown there and in the log.

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
        # The window manager hides Pastex within a few milliseconds of
        # iconify(); sending Ctrl+V on that signal is much faster than a
        # fixed delay, with FOCUS_SETTLE_MS kept only as a fallback.
        self.bind("<Unmap>", self._on_window_unmap, add="+")
        self.bind("<F5>", lambda _e: self.refresh_tree(force=True))
        self.bind("<Escape>", lambda _e: self._exit_select_mode())
        self.bind(
            "<Control-f>", lambda _e: self.search_var.set("") or self._focus_search()
        )

    def _on_search_changed(self) -> None:
        """Debounce searches by 300 ms to avoid rescanning on every keystroke."""
        if self._search_after_id is not None:
            self.after_cancel(self._search_after_id)
        # Filter the cached tree: no disk walk while the user types.
        self._search_after_id = self.after(
            180, lambda: self.refresh_tree(force=False)
        )

    def _focus_search(self) -> None:
        if getattr(self, "search_entry", None) is not None:
            self.search_entry.focus_set()

    def search_now(self) -> str:
        """Run the current search immediately instead of waiting for debounce."""
        if self._search_after_id is not None:
            self.after_cancel(self._search_after_id)
            self._search_after_id = None
        self.refresh_tree(force=False)
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
        if after_id is None or not PastexApp._tk_ready(self):
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
            # Session-only roots take precedence and do not alter the saved folder.
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

    def refresh_tree(self, force: bool = True) -> None:
        """Repaint the tree from the in-memory index.

        ``force`` (the default) re-walks the folders first, so a caller that
        just changed something on disk always sees the result. Search and
        repaint paths pass ``force=False`` to reuse the cached tree instead of
        touching the disk on every keystroke.
        """
        started = time.monotonic()
        try:
            PastexApp._refresh_tree_now(self, force)
        finally:
            elapsed_ms = (time.monotonic() - started) * 1000
            if elapsed_ms >= 300:
                # A slow first paint is fine; a slow repaint blocks the click
                # handler behind it, which used to look like a slow paste.
                log_error(
                    f"[index] the refresh took {elapsed_ms:.0f} ms "
                    f"(force={force})"
                )

    def _refresh_tree_now(self, force: bool = True) -> None:
        previous_highlight = self.__dict__.get("_context_highlight_iid", "")
        self._hover_iid = ""
        self._context_highlight_iid = ""
        roots = self.active_roots()
        if self.index.set_roots(roots):
            force = True
        nodes = self.index.nodes(force=force)
        self._apply_saved_order(nodes)
        has_roots = bool(nodes)
        query = self.search_var.get().strip()
        if query:
            nodes = filter_tree(nodes, self.index.search(query))
        # Expand all folders during search so results are visible. Otherwise,
        # restore the saved collapsed state.
        self.collapsed = set() if query else self._collapsed_paths()
        total = 0
        top_level: list[Node] = []
        for root_node in nodes:
            # The source folder is a container; show its contents
            # directly at the top level, including files at the root.
            top_level.extend(root_node.children)
            total += root_node.count_texts()
        # Patch the existing rows instead of deleting and rebuilding the tree:
        # item ids survive, so the scroll position, the expanded folders and the
        # selection stay put, and unchanged rows cost a comparison instead of an
        # insert. This is the only remaining per-refresh cost on the GUI side.
        self._sync_nodes("", top_level, claimed=set())
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

    def _node_iid(self, node: Node, claimed: set[str]) -> str:
        """Item id for ``node``, stable across refreshes.

        The path is the id, which is what lets a refresh reuse the existing row.
        ``claimed`` catches the (rare) case of two roots sharing a path, as with
        a development checkout and the system copy: the second one gets a suffix.
        """
        iid = str(node.path)
        if iid in claimed:
            iid = f"{iid}#{node.source_root}"
        claimed.add(iid)
        return iid

    def _row_values(self, node: Node, label: str | None = None) -> dict:
        icon = "📁 " if node.is_dir else "📄 "
        collapsed = getattr(self, "collapsed", set())
        return {
            "text": f"{icon}{label if label is not None else node.name}",
            "open": node.is_dir and str(node.path) not in collapsed,
            "tags": ("dir" if node.is_dir else "file"),
        }

    def _insert_row(self, parent: str, node: Node, iid: str, label=None) -> None:
        self.tree.insert(parent, "end", iid=iid, **self._row_values(node, label))

    def _insert_children(
        self, iid: str, children: list[Node], claimed: set[str], label=None
    ) -> None:
        for child in children:
            child_iid = self._node_iid(child, claimed)
            self._insert_row(iid, child, child_iid, label)
            self._insert_children(child_iid, child.children, claimed, label)

    def _insert_node(
        self,
        parent: str,
        node: Node,
        *,
        label: str | None = None,
        claimed: set[str] | None = None,
        iid: str | None = None,
    ) -> str:
        """Insert ``node`` and its children under ``parent`` (new rows only)."""
        if claimed is None:
            claimed = set()
        if iid is None:
            iid = self._node_iid(node, claimed)
        self._insert_row(parent, node, iid, label)
        self._insert_children(iid, node.children, claimed, label)
        return iid

    @staticmethod
    def _as_tags(value) -> tuple[str, ...]:
        """Normalise ``-tags``: Tk may hand back a string or a tuple."""
        if isinstance(value, str):
            return tuple(part for part in value.split() if part)
        return tuple(value or ())

    @staticmethod
    def _as_flag(value) -> bool:
        """Normalise ``-open``: Tk may hand back 1/0 or "1"/"0"."""
        if isinstance(value, str):
            try:
                return bool(int(value))
            except ValueError:
                return bool(value)
        return bool(value)

    def _update_row(self, iid: str, node: Node) -> None:
        """Rewrite a row in place, but only when a value actually changed."""
        values = self._row_values(node)
        current = {
            "text": self.tree.item(iid, "text"),
            "open": self._as_flag(self.tree.item(iid, "open")),
            "tags": self._as_tags(self.tree.item(iid, "tags")),
        }
        desired = {
            "text": values["text"],
            "open": bool(values["open"]),
            "tags": self._as_tags(values["tags"]),
        }
        changes = {
            key: values[key] for key, want in desired.items() if current[key] != want
        }
        if changes:
            self.tree.item(iid, **changes)

    def _order_children(self, parent: str, wanted: list[str]) -> None:
        """Delete rows that left the tree and put the rest in ``wanted`` order."""
        current = list(self.tree.get_children(parent))
        if current == wanted:
            return
        keep = set(wanted)
        for iid in current:
            if iid not in keep:
                self.tree.delete(iid)
        for index, iid in enumerate(wanted):
            self.tree.move(iid, parent, index)

    def _sync_nodes(self, parent: str, nodes: list[Node], *, claimed: set[str]) -> None:
        """Make the rows under ``parent`` match ``nodes``, touching only changes."""
        wanted: list[str] = []
        for node in nodes:
            iid = self._node_iid(node, claimed)
            if self.tree.exists(iid):
                self._update_row(iid, node)
                self._sync_nodes(iid, node.children, claimed=claimed)
            else:
                self._insert_node(parent, node, claimed=claimed, iid=iid)
            wanted.append(iid)
        self._order_children(parent, wanted)

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
        self.refresh_tree(force=True)
        if self.watcher is not None:
            self.watcher.set_roots(self.active_roots())
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
            text = self.preview_text(path)
        except OSError as exc:
            text = f"(Could not read file: {exc})"
        self.preview.insert("1.0", text)
        self.preview.configure(state="disabled")
        self.info_var.set(
            f"{path.name} — {len(text)} characters, {len(text.split())} words\n{path}"
        )

    # Both caches are properties so partially built test doubles (and code
    # paths that run before __init__ finishes) still work.
    @property
    def index(self) -> SnippetIndex:
        """Cached snippet tree and file contents."""
        index = self.__dict__.get("_index")
        if index is None:
            index = SnippetIndex()
            self._index = index
        return index

    @index.setter
    def index(self, value: SnippetIndex) -> None:
        self._index = value

    @property
    def watcher(self):
        """inotify watcher for the snippet folders, when it could start."""
        return self.__dict__.get("_watcher")

    @watcher.setter
    def watcher(self, value) -> None:
        self._watcher = value

    def preview_text(self, path) -> str:
        """Text of ``path`` from the index cache, refreshed when it changed."""
        return self.index.text(path)

    # ---------- Picker and single instance ----------
    def open_picker(self) -> None:
        """Show the snippet picker popup (see pastex/picker.py)."""
        popup = open_picker(self)
        if popup is None:
            self.status_var.set("The picker needs a display.")

    def paste_path(self, path, hide=None) -> None:
        """Copy ``path`` and send Ctrl+V; ``hide`` is the popup to close first."""
        try:
            text = self.preview_text(path)
        except OSError as exc:
            self.status_var.set(f"Could not read {path.name}: {exc}")
            return
        self._paste_text(text, hide=hide)

    def _handle_instance_command(self, command: str) -> None:
        """Runs on the instance socket thread: hand the command to Tk."""
        try:
            self.after(0, self._run_instance_command, command)
        except TK_ERRORS:  # pragma: no cover - window already gone
            pass

    def _run_instance_command(self, command: str) -> None:
        """Act on a command sent by a later ``pastex`` launch."""
        if command == "pick":
            self.open_picker()
        elif command == "quit":
            self.destroy()
        else:
            self.show_window()

    def show_window(self) -> None:
        """Bring the existing window back (used instead of a second process)."""
        self.deiconify()
        self.lift()
        self.focus_force()

    # ---------- Live refresh ----------
    def _start_watcher(self) -> None:
        """Watch the snippet folders so edits appear without pressing F5."""
        if self.watcher is not None:
            return
        watcher = DirectoryWatcher(self.active_roots(), self._on_folders_changed)
        self.watcher = watcher
        if not watcher.start():
            log_error("[index] no folder watcher available; manual refresh only")
        elif getattr(watcher, "poll_only", False):
            log_error(
                "[index] inotify unavailable "
                f"(errno {watcher_module_last_error()}); polling the folders "
                "every 2 s instead"
            )

    def _on_folders_changed(self) -> None:
        """Watcher thread callback: repaint on the Tk thread."""
        try:
            self.after(0, self._refresh_from_disk)
        except TK_ERRORS:  # pragma: no cover - shutting down
            pass

    def _refresh_from_disk(self) -> None:
        """Re-walk the folders once the change settles (and not mid-paste)."""
        if getattr(self, "_pasting", False):
            self.after(150, self._refresh_from_disk)
            return
        self.refresh_tree(force=True)

    # ---------- Copy and paste ----------
    def on_activate(self) -> None:
        """Click once or press Enter to paste into another app."""
        self.do_paste()

    def do_paste(self) -> None:
        """Click once to paste text into another app.

        The two slow halves overlap instead of running one after the other:

          1. Minimize the window right away; minimizing keeps the panel entry,
             and the window manager then needs a few milliseconds to hand
             focus back to the previous application. The window is never
             unmapped, so the icon cannot disappear.
          2. Copy the text in a worker thread at the same time: the clipboard
             tool only has to own the selection, not the focus.
          3. Send Ctrl+V as soon as both are done. The ``<Unmap>`` signal
             normally wins the race, and ``_after_copy()`` releases the send
             when the clipboard tool is the slower half.
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
            # Usually already read for the preview, so a click does not touch
            # the disk at all.
            text = self.preview_text(path)
        except OSError as exc:
            messagebox.showerror("Read failed", str(exc))
            return
        self._paste_text(text)

    def _paste_text(self, text: str, hide=None) -> None:
        """Copy ``text`` and send Ctrl+V.

        ``hide`` is the window to get out of the way: ``None`` means the main
        window, which is minimized (never unmapped) and restored afterwards.
        The picker passes its own popup, which is destroyed instead, leaving
        the main window and the panel entry untouched.
        """
        self._pasting = True
        self._paste_finished = False
        # True once the sender has been launched for this attempt, so a late
        # copy result does not mistake a running paste for a skipped one.
        self._paste_started = False
        # Cleared so the watchdog can abandon a paste that stalls; the sender
        # checks it between tools and before the portal.
        self._paste_aborted = False
        self.status_var.set("Preparing to paste...")
        # Ctrl+V may only be sent once the clipboard owns the text, so the
        # send stays armed until _after_copy() reports success. When the
        # window manager refuses to minimize nothing is armed: keeping the
        # panel icon matters more than pasting, and Ctrl+V would land in Pastex.
        self._paste_clipboard_ready = False
        self._paste_hide_widget = hide
        # The text of this attempt, and how it will be delivered: Ctrl+V from
        # the clipboard, or typed with xdotool when the clipboard cannot hold it.
        self._paste_body = text
        self._paste_by_typing = False
        # Every paste log line carries the offset from the click, so a slow
        # click can be read from the log instead of guessed at.
        self._paste_clicked_at = time.monotonic()
        # Only restore the window that was actually hidden.
        self._paste_restore_window = hide is None
        # Reset the hide diagnostics of the previous attempt.
        self._window_unmapped = False
        self._x11_hide_checked = False
        self._x11_checked_ms = None
        self._focus_state = None
        self._focus_elsewhere = False
        self._focus_moved_to = None
        self._focus_transfer_started = False
        self._hide_signal = None
        self._force_minimize_tried = False
        self._paste_hide_via_x11 = False
        if hide is None and not PastexApp._paste_tools_ready(self):
            # A previous click (or startup) already showed that Ctrl+V cannot
            # be sent here, so there is nothing to minimize for: copy the text
            # and leave the window alone instead of hiding and bouncing back.
            log_error("[paste] Ctrl+V is unavailable here; copying the text only")
            self._paste_restore_window = False
            _run_in_thread(self._paste_worker, text)
            return
        if hide is None:
            hidden = self._hide_for_paste()
        else:
            hidden = self._hide_widget(hide)
        if hidden:
            self._send_paste()
            PastexApp._arm_paste_watchdog(self)
        _run_in_thread(self._paste_worker, text)

    def _since_click(self) -> str:
        """Return the time since the click that started this paste, as text."""
        started = self.__dict__.get("_paste_clicked_at")
        if started is None:
            return "+? ms"
        return f"+{(time.monotonic() - started) * 1000:.0f} ms"

    def _paste_tools_ready(self) -> bool:
        """False only when Ctrl+V is known to be impossible on this machine.

        Unknown counts as ready: a plan that has not been resolved yet must not
        stop the first click from trying. Once a paste fails the flag is
        cleared, so later clicks copy the text without minimizing the window.
        """
        if self.__dict__.get("_paste_ready") is False:
            return False
        plan = self.__dict__.get("_paste_plan")
        if plan is not None and not getattr(plan, "candidates", None):
            return False
        return True

    def _arm_paste_watchdog(self) -> None:
        """Guarantee the window comes back even if the sender never finishes."""
        if not PastexApp._tk_ready(self):
            return
        PastexApp._cancel_paste_watchdog(self)
        try:
            self._paste_watchdog_id = self.after(
                PASTE_DEADLINE_MS, PastexApp._paste_watchdog
            )
        except TK_ERRORS:  # pragma: no cover - interpreter already gone
            self._paste_watchdog_id = None

    def _cancel_paste_watchdog(self) -> None:
        after_id = self.__dict__.get("_paste_watchdog_id")
        self._paste_watchdog_id = None
        if after_id is None or not self._tk_ready():
            return
        try:
            self.after_cancel(after_id)
        except TK_ERRORS:  # pragma: no cover - already fired
            pass

    def _paste_watchdog(self) -> None:
        """Restore the window when the send stage overruns its deadline.

        The sender runs in a thread; when it is stuck inside a tool that never
        answers, no result would ever arrive and the window would stay
        minimized forever. Abandoning the rest of the plan keeps later keys
        from being pressed after the window is back.
        """
        self._paste_watchdog_id = None
        if self.__dict__.get("_paste_finished", True):
            return
        self._paste_aborted = True
        log_error("[paste] Ctrl+V deadline exceeded; restoring the window")
        PastexApp._finish_paste(
            self,
            PasteResult(
                ok=False,
                tool="",
                message=(
                    "Ctrl+V was not delivered before the deadline, so the "
                    "window was restored and the text is still on the clipboard."
                ),
            ),
        )

    def _hide_widget(self, widget) -> bool:
        """Destroy a helper window so focus returns to the target application.

        Only popups use this: they carry no panel entry, so unmapping them
        cannot remove the Pastex icon, and the main window stays visible.
        """
        try:
            widget.destroy()
        except TK_ERRORS:
            return False
        return True

    def _paste_worker(self, text: str) -> None:
        started = time.monotonic()
        try:
            # __dict__ on purpose: a partially built app (tests use __new__)
            # would make tkinter's Misc.__getattr__ recurse instead of
            # returning a default.
            result = copy_text(text, plan=self.__dict__.get("_copy_plan"))
        except Exception as exc:  # noqa: BLE001 - always report back to the GUI
            log_error(f"[paste] copy failed: {exc}")
            result = ClipboardResult(ok=False, message=f"copy error: {exc}")
        elapsed_ms = (time.monotonic() - started) * 1000
        log_error(
            f"[paste] copy: ok={result.ok} tool={result.tool} "
            f"({elapsed_ms:.0f} ms, {PastexApp._since_click(self)})"
        )
        empty = False
        if result.ok:
            # xclip exits 0 as soon as it handed the text to the process it
            # forks, so a successful exit does not prove the clipboard holds
            # anything: verify it before Ctrl+V pastes an empty clipboard.
            empty = not PastexApp._clipboard_holds(self, text, result)
            if empty and can_type_text(text):
                # The clipboard is unusable here, but the text still has to
                # reach the application: type it instead of pressing Ctrl+V on
                # an empty clipboard.
                self._paste_by_typing = True
                log_error(
                    "[paste] typing the text with xdotool instead of Ctrl+V "
                    "(the clipboard does not hold it)"
                )
                result = ClipboardResult(
                    ok=True, tool=result.tool, message="Typed with xdotool."
                )
                empty = False
            elif empty:
                result = ClipboardResult(
                    ok=False,
                    tool=result.tool,
                    message=(
                        f"{result.tool} reported success, but the clipboard does "
                        "not hold the text, and it cannot be typed either"
                    ),
                )
        if not result.ok:
            # Resolve the tools again next time: a missing tool may have been
            # installed while Pastex was running.
            self._copy_plan = None
        self.after(0, self._after_copy, result, empty)

    def _clipboard_holds(self, text: str, result) -> bool:
        """Make the clipboard hold the text, and say whether it does.

        Runs in the copy worker. Tk claims the CLIPBOARD selection first: that
        is in-process, instant, and no subprocess can lose it, so Ctrl+V has
        something to paste even when the tool's forked owner does not survive.
        Only then is the clipboard read back once (with one short retry) to
        decide whether Ctrl+V or typing delivers the text -- reading first cost
        a second per click on a machine where both readers are slow.
        """
        if self.__dict__.get("_clipboard_unreliable", False):
            # This machine already proved it cannot be read back; the check
            # costs a round trip through the GUI thread and two reads, so do
            # not pay for it on every click.
            log_error(
                f"[paste] the clipboard was unusable on an earlier click "
                f"({PastexApp._since_click(self)}); typing again"
            )
            return False
        started = time.monotonic()
        claimed = PastexApp._claim_the_clipboard(self, text)
        try:
            verdict = verify_clipboard(text)
        except Exception as exc:  # noqa: BLE001 - diagnostics must not block
            log_error(f"[paste] clipboard verification failed: {exc}")
            return True
        took_ms = (time.monotonic() - started) * 1000
        if verdict is True:
            log_error(
                f"[paste] clipboard verified in {took_ms:.0f} ms "
                f"(tool={result.tool}, tk_owner={claimed}, "
                f"{PastexApp._since_click(self)})"
            )
            return True
        if verdict is None:
            log_error(
                f"[paste] the clipboard cannot be read back "
                f"({took_ms:.0f} ms, tk_owner={claimed}); trusting {result.tool}"
            )
            return True
        log_error(
            f"[paste] the clipboard is empty or different after {took_ms:.0f} ms "
            f"(tool={result.tool}, tk_owner={claimed}, "
            f"{PastexApp._since_click(self)}); claiming the selection with Tk "
            "once more"
        )
        if not claimed and PastexApp._claim_the_clipboard(self, text):
            if verify_clipboard(text) is not False:
                log_error("[paste] the clipboard now holds the text (Tk owner)")
                return True
        self._clipboard_unreliable = True
        log_error(
            "[paste] the clipboard still does not hold the text "
            f"({PastexApp._since_click(self)}); "
            + (
                "it will be typed instead"
                if can_type_text(text)
                else "Ctrl+V would paste nothing"
            )
        )
        return False

    def _claim_the_clipboard(self, text: str) -> bool:
        """Make Tk own the CLIPBOARD selection (Tk calls must run on its thread).

        Called from the copy worker, so the call itself has to be marshalled
        through ``after()``. The wait is short on purpose: when the GUI thread
        is busy the paste must not sit here for seconds.
        """
        outcome: dict[str, bool] = {}
        done = threading.Event()

        def claim() -> None:
            try:
                self.clipboard_clear()
                self.clipboard_append(text)
                # Tk takes the selection when it next idles.
                self.update_idletasks()
                outcome["ok"] = True
            except Exception:  # noqa: BLE001 - never kill the copy worker
                outcome["ok"] = False
            finally:
                done.set()

        try:
            self.after(0, claim)
        except TK_ERRORS:
            return False
        return done.wait(0.4) and outcome.get("ok", False)

    def _after_copy(self, result, clipboard_empty: bool = False) -> None:
        """Release the armed Ctrl+V once the clipboard holds the copied text."""
        if not result.ok:
            if clipboard_empty:
                self._copy_failed(result.message)
            else:
                self._paste_failed(result.message)
            return
        self._paste_clipboard_ready = True
        if self.__dict__.get("_paste_started", False):
            # The <Unmap> signal already released the send and the sender is
            # running; it reports back through _paste_done. Touching the status
            # here used to replace the result with a "press Ctrl+V" hint.
            return
        if not self.__dict__.get("_paste_pending", False):
            # The window could not be minimized, so no Ctrl+V was armed: keep
            # the icon and let the user paste manually.
            self._pasting = False
            self.status_var.set(PASTE_MANUAL_HINT)
            log_error("[paste] kept the window visible; Ctrl+V not sent")
            return
        # The window is normally hidden by now (the <Unmap> signal arrives
        # first); this call covers the opposite order and the fallback timer.
        self._send_paste_now()

    def _window_state(self) -> str:
        """Return the window state as text, tolerating missing Tk support."""
        try:
            return str(self.state())
        except TK_ERRORS:
            return ""

    def _restore_window_via_x11(self, window_id: str) -> None:
        """Show the window through the window manager, off the GUI thread."""
        log_error(
            f"[paste] asking the window manager to show {window_id} "
            f"({PastexApp._since_click(self)})"
        )
        _run_in_thread(PastexApp._restore_window_worker, self, window_id)

    def _restore_window_worker(self, window_id: str) -> None:
        started = time.monotonic()
        ok = restore_window(window_id)
        took = int((time.monotonic() - started) * 1000)
        log_error(
            "[paste] the window manager "
            + ("showed the window again" if ok else "did not show the window")
            + f" in {took} ms ({PastexApp._since_click(self)})"
        )
        if ok:
            PastexApp._take_the_focus_back(self, window_id)
            return
        # The window manager kept the request: hand it back to Tk, which retries
        # deiconify() every RESTORE_RETRY_MS. This is the 1.10.8 path that did
        # bring the window back; it is only slow when the request is dropped.
        log_error("[paste] falling back to Tk to show the window again")
        try:
            self.after(0, lambda app=self: PastexApp._restore_window_via_tk(app))
        except TK_ERRORS:  # pragma: no cover - interpreter already gone
            pass

    def _restore_window(self, *, raise_window: bool = False) -> None:
        """Bring the window back and confirm that the WM really did it.

        Two things made the window stay minimized after a successful paste:
        ``deiconify()`` is dropped when it arrives while Cinnamon is still
        animating the minimize, and ``wm state`` reported "normal" for a window
        that was still minimized, so a check that trusted it never asked again.
        The request is now repeated every ``RESTORE_RETRY_MS`` for at least
        ``RESTORE_REPEAT_MS`` whatever the state says, and after
        ``RESTORE_ACTIVATE_AT_MS`` the window manager itself is asked to
        activate the window through xdotool.
        """
        if self.__dict__.get("_paste_hide_via_x11", False):
            # The window was minimized through the window manager, so bring it
            # back the same way: deiconify() would block the GUI thread for up
            # to two seconds waiting for a state change that may never come.
            window_id = PastexApp._window_id(self)
            if window_id is not None:
                PastexApp._restore_window_via_x11(self, window_id)
                return
        PastexApp._restore_window_via_tk(self, raise_window=raise_window)

    def _restore_window_via_tk(self, *, raise_window: bool = False) -> None:
        """The pre-1.10.9 restore: Tk's deiconify(), retried, plus a focus ask."""
        if raise_window:
            try:
                self.lift()
            except TK_ERRORS:
                pass
        try:
            self.deiconify()
        except TK_ERRORS:  # test doubles without Tk, or interpreter gone
            return
        if self.__dict__.pop("_paste_was_zoomed", False):
            # Bring back the maximized window the user had before the paste.
            try:
                self.state("zoomed")
            except TK_ERRORS:
                pass
        self._restore_waited_ms = 0
        self._restore_activated = False
        PastexApp._wait_for_the_restore(self)

    def _wait_for_the_restore(self) -> bool:
        """Schedule the next restore check; True when one was scheduled."""
        waited = self.__dict__.get("_restore_waited_ms", 0)
        if waited >= RESTORE_WAIT_MS or not PastexApp._tk_ready(self):
            return False
        try:
            self._restore_check_id = self.after(
                RESTORE_RETRY_MS, lambda app=self: PastexApp._restore_check(app)
            )
        except TK_ERRORS:  # pragma: no cover - interpreter already gone
            return False
        return True

    def _restore_check(self) -> None:
        """Repeat deiconify() until the window is really back.

        The state is only a hint: Muffin reported "normal" while the window was
        still minimized, which used to cut this loop short and leave the window
        hidden for good.
        """
        self._restore_check_id = None
        waited = self.__dict__.get("_restore_waited_ms", 0) + RESTORE_RETRY_MS
        self._restore_waited_ms = waited
        state = PastexApp._window_state(self)
        if state not in {"iconic", "withdrawn"} and waited > RESTORE_REPEAT_MS:
            log_error(f"[paste] window restored after {waited} ms (state={state})")
            return
        if state in {"iconic", "withdrawn"}:
            log_error(
                "[paste] the window manager kept the window hidden; "
                f"restoring again ({waited} ms, state={state})"
            )
        try:
            self.deiconify()
        except TK_ERRORS:  # pragma: no cover - interpreter already gone
            return
        if waited >= RESTORE_ACTIVATE_AT_MS and not self.__dict__.get(
            "_restore_activated", False
        ):
            # Ask the window manager directly: EWMH activation un-minimizes a
            # window even when the deiconify request was dropped.
            PastexApp._activate_the_window(self)
        PastexApp._wait_for_the_restore(self)

    def _activate_the_window(self) -> None:
        self._restore_activated = True
        window_id = PastexApp._window_id(self)
        if window_id is None:
            return
        log_error(f"[paste] asking the window manager to show {window_id} again")
        _run_in_thread(activate_window, window_id)

    def _hide_worker(self, window_id: str) -> None:
        """Minimize through the window manager and report whether it obeyed."""
        started = time.monotonic()
        ok = force_minimize_window(window_id)
        took = int((time.monotonic() - started) * 1000)
        log_error(
            "[paste] the window manager "
            + ("minimized the window" if ok else "did not minimize the window")
            + f" in {took} ms ({PastexApp._since_click(self)})"
        )

    def _transfer_the_focus(self) -> None:
        """Hand the focus to the app behind Pastex when minimizing is ignored.

        A window manager that will not minimize still has to give another
        window the focus: ``xdotool windowfocus`` is a plain XSetInputFocus, so
        the text is typed where the user meant it to go instead of being lost.
        """
        if self.__dict__.get("_focus_transfer_started", False):
            return
        self._focus_transfer_started = True
        window_id = PastexApp._window_id(self)
        if window_id is None:
            return
        log_error(
            "[paste] the window is still focused; handing the focus to the "
            f"window behind it ({PastexApp._since_click(self)})"
        )
        _run_in_thread(PastexApp._transfer_the_focus_worker, self, window_id)

    def _transfer_the_focus_worker(self, window_id: str) -> None:
        target = focus_the_window_below(window_id)
        if target is None:
            log_error(
                f"[paste] there is no window behind Pastex to hand the focus to "
                f"({PastexApp._since_click(self)})"
            )
            return
        self._focus_moved_to = target
        self._focus_elsewhere = True
        log_error(
            f"[paste] the focus went to {target}; the text goes there "
            f"({PastexApp._since_click(self)})"
        )
        try:
            self.after(0, lambda app=self: PastexApp._send_paste_now(app))
        except TK_ERRORS:  # pragma: no cover - shutting down
            pass

    def _take_the_focus_back(self, window_id: str) -> None:
        """Give Pastex the focus again, but only if it is still on the target."""
        target = self.__dict__.get("_focus_moved_to")
        if target is None:
            return
        if x11_active_window() != normalize_window_id(target):
            return
        if focus_window(window_id):
            log_error("[paste] the focus is back on Pastex")

    def _hide_for_paste(self) -> bool:
        """Minimize Pastex, keeping its panel entry, and report whether it worked.

        ``withdraw()`` unmaps the only window, so the window list drops an
        unpinned app and only shows it again once a click makes the window
        active — the icon appeared to vanish on one click and come back on the
        next. Minimizing keeps the window mapped, so the panel entry survives
        while focus returns to the previous application, and it is the only
        thing done here: when the window manager refuses to minimize, the
        window simply stays visible and the caller skips the synthetic Ctrl+V
        instead of unmapping it.

        Returns True when the window is hidden and the paste may go ahead.
        """
        self._minimize_waited_ms = 0
        self._rehide_done = False
        self._hide_logged = False
        self._paste_was_zoomed = False
        # Ask the window manager first: Tk's iconify() blocks the GUI thread for
        # up to two seconds when the window manager does not report the state
        # change (Tk waits for the unmap), which was where three seconds of a
        # click went on a Cinnamon session. EWMH minimize returns immediately
        # and is sent from a worker thread.
        window_id = PastexApp._window_id(self)
        if window_id is not None and _xdotool_available():
            self._paste_hide_via_x11 = True
            log_error(
                f"[paste] minimizing through the window manager ({window_id}, "
                f"{PastexApp._since_click(self)})"
            )
            _run_in_thread(PastexApp._hide_worker, self, window_id)
            return True
        self._paste_hide_via_x11 = False
        # Tk/X11 drops iconify() for a maximized window, so the fallback (no
        # xdotool) has to unzoom first and put the zoom back on restore.
        try:
            if str(self.state()) == "zoomed":
                self._paste_was_zoomed = True
                log_error("[paste] unmaximizing before the paste; the zoom is restored")
                self.state("normal")
                self.update_idletasks()
        except TK_ERRORS:
            self._paste_was_zoomed = False
        try:
            self.iconify()
            self.update_idletasks()
        except TK_ERRORS:
            log_error("[paste] minimize failed; the window stays visible")
            return False
        # Whether the window actually hid is decided in _send_paste_now: the
        # compositor answers asynchronously and reported "normal" here even
        # when the minimize worked a few milliseconds later.
        return True

    def _paste_window_hidden(self) -> bool:
        """True when the window is off screen (or nothing can be read).

        A picker popup is destroyed instead of minimized and the main window
        stays visible on purpose, so its state must not be consulted there.
        """
        if self.__dict__.get("_paste_hide_widget") is not None:
            return True
        if self.__dict__.get("_focus_elsewhere", False):
            # The window manager refused to minimize, so the focus was handed
            # to the app behind Pastex: the text goes to the same place.
            return True
        if self.__dict__.get("_window_unmapped", False):
            return True
        try:
            state = str(self.state())
        except TK_ERRORS:  # test doubles: keep the previous behaviour
            return True
        if state in {"iconic", "withdrawn"}:
            if not self.__dict__.get("_hide_logged", False):
                self._hide_logged = True
                log_error("[paste] window minimized before Ctrl+V; panel entry kept")
            return True
        return False

    def _window_id(self) -> str | None:
        """Return the X11 window id as a hex string, if Tk can provide it."""
        try:
            return hex(int(self.winfo_id()))
        except TK_ERRORS:
            return None

    def _ask_the_wm_to_minimize_again(self) -> None:
        """Repeat the EWMH minimize, without touching Tk."""
        if self.__dict__.get("_rehide_asked", False):
            return
        self._rehide_asked = True
        window_id = PastexApp._window_id(self)
        if window_id is None:
            return
        log_error(
            f"[paste] the window is still in the foreground; asking the window "
            f"manager to minimize it again ({PastexApp._since_click(self)})"
        )
        _run_in_thread(force_minimize_window, window_id)

    def _force_minimize(self) -> None:
        """Ask the window manager to minimize, for when iconify() is ignored."""
        if self.__dict__.get("_force_minimize_tried", False):
            return
        self._force_minimize_tried = True
        window_id = PastexApp._window_id(self)
        if window_id is None:
            return
        log_error(
            f"[paste] iconify() did not hide the window; asking the WM ({window_id})"
        )
        _run_in_thread(force_minimize_window, window_id)

    def _x11_confirm_hidden(self, waited_ms: int = 0) -> None:
        """Ask the X server whether the window really left the foreground."""
        self._x11_hide_checked = True
        self._x11_checked_ms = waited_ms
        window_id = PastexApp._window_id(self)
        if window_id is None:
            return
        _run_in_thread(
            PastexApp._x11_confirm_hidden_worker, self, window_id, waited_ms
        )

    def _x11_confirm_hidden_worker(self, window_id: str, waited_ms: int = 0) -> None:
        """Decide the hide from the X focus, falling back to the WM's own state.

        ``wm state`` (Tk) and ``WM_STATE`` are updated late or not at all by
        some window managers -- Muffin reported ``normal`` for the whole budget
        on a real Cinnamon session -- while the input focus has to leave an
        iconified window. That makes the focus the honest signal.
        """
        if self.__dict__.get("_window_unmapped", False):
            return
        moved = x11_focus_moved(window_id, WM_CLASS)
        self._focus_state = {True: "moved", False: "ours"}.get(moved, "unknown")
        if moved is None:
            # No xdotool or no focus information: ask the window manager.
            if x11_wm_state(window_id) != "iconic" and not x11_window_unmapped(
                window_id
            ):
                return
            signal = "wm-state"
        elif not moved:
            return
        else:
            signal = "focus"
        self._window_unmapped = True
        self._hide_signal = signal
        log_error(
            f"[paste] the window left the foreground after {waited_ms} ms "
            f"({signal}); sending Ctrl+V"
        )
        try:
            self.after(0, PastexApp._send_paste_now, self)
        except TK_ERRORS:  # pragma: no cover - shutting down
            pass

    def _wait_for_the_window_hiding(self) -> bool:
        """Retry the minimize check; True when another attempt was scheduled."""
        waited = self.__dict__.get("_minimize_waited_ms", 0)
        if waited >= MINIMIZE_WAIT_MS or not PastexApp._tk_ready(self):
            return False
        waited += MINIMIZE_RETRY_MS
        self._minimize_waited_ms = waited
        if waited >= MINIMIZE_REHIDE_AT_MS and not self.__dict__.get("_rehide_done"):
            # Ask once more: some window managers ignore the first request, and
            # a maximized window ignores iconify() outright. xdotool sends the
            # EWMH request directly, which the WM cannot mistake for a Tk no-op.
            self._rehide_done = True
            if self.__dict__.get("_paste_hide_via_x11", False):
                # iconify() here cost two seconds of every click on a Cinnamon
                # session: Tk waits that long for an unmap the window manager
                # never reports, for a window it has already minimized.
                PastexApp._ask_the_wm_to_minimize_again(self)
            else:
                try:
                    self.iconify()
                except TK_ERRORS:  # pragma: no cover - interpreter already gone
                    pass
                PastexApp._force_minimize(self)
        if (
            waited >= MINIMIZE_FOCUS_TRANSFER_AT_MS
            and self.__dict__.get("_focus_state") == "ours"
            and not self.__dict__.get("_focus_elsewhere", False)
        ):
            PastexApp._transfer_the_focus(self)
        checked = self.__dict__.get("_x11_checked_ms")
        if waited >= MINIMIZE_FOCUS_CHECK_AT_MS and (
            checked is None or waited - checked >= MINIMIZE_FOCUS_RETRY_MS
        ):
            # Keep asking: the window manager animates the minimize, so the
            # focus can move a couple of hundred milliseconds after the click.
            PastexApp._x11_confirm_hidden(self, waited)
        try:
            # Bind explicitly: Tk calls the callback with no arguments, and
            # test doubles have no instance method to pass along.
            self._paste_send_id = self.after(
                MINIMIZE_RETRY_MS, lambda app=self: PastexApp._send_paste_now(app)
            )
        except TK_ERRORS:  # pragma: no cover - interpreter already gone
            return False
        return True

    def _send_paste(self) -> None:
        """Arm the paste, which fires as soon as the window is hidden.

        ``<Unmap>`` arrives once the window manager has actually taken the
        window off screen, which is also when focus moves to the previous
        application. That avoids a fixed delay, so the paste starts as soon as
        the window manager allows; ``FOCUS_SETTLE_MS`` remains a fallback for
        window managers that never report the unmap.
        """
        if FOCUS_SETTLE_MS <= 0 or not self._tk_ready():
            self._paste_pending = True
            self._send_paste_now()
            return
        self._paste_pending = True
        try:
            self._paste_send_id = self.after(FOCUS_SETTLE_MS, self._send_paste_now)
        except TK_ERRORS:  # pragma: no cover - interpreter already gone
            self._paste_send_id = None
            self._send_paste_now()

    def _on_window_unmap(self, event=None) -> None:
        """Send Ctrl+V once the window manager has hidden the window."""
        widget = getattr(event, "widget", None)
        expected = self.__dict__.get("_paste_hide_widget", None)
        if expected is None:
            expected = self
        if widget is not None and widget is not expected:
            return
        self._send_paste_now()

    def _send_paste_now(self) -> None:
        """Run the sender once, when the window is hidden and the clipboard ready.

        Called for the unmap signal, the fallback timer and the clipboard
        result, in whichever order they arrive.
        """
        if not self.__dict__.get("_paste_pending", False):
            return
        if not self.__dict__.get("_paste_clipboard_ready", True):
            # The window is already hidden but the clipboard tool has not
            # finished yet; _after_copy() calls back here as soon as it has.
            return
        if not PastexApp._paste_window_hidden(self):
            if PastexApp._wait_for_the_window_hiding(self):
                return
            # The window manager never hid the window: keep it (the panel icon
            # matters more than pasting), leave the text on the clipboard and
            # ask the user to press Ctrl+V instead of typing into Pastex.
            self._paste_pending = False
            self._pasting = False
            self._paste_finished = True
            PastexApp._cancel_paste_watchdog(self)
            # The state read lags the window manager: the window can finish
            # minimizing right after this check gives up. Ask for it back
            # instead of leaving it minimized, as 1.10.x did.
            PastexApp._restore_window(self)
            self.status_var.set(PASTE_MANUAL_HINT)
            log_error(
                f"[paste] the window stayed in the foreground for "
                f"{MINIMIZE_WAIT_MS} ms (tk={PastexApp._window_state(self) or '?'}, "
                f"focus={self.__dict__.get('_focus_state', 'unknown')}, "
                f"forced={self.__dict__.get('_force_minimize_tried', False)}, "
                f"zoomed={self.__dict__.get('_paste_was_zoomed', False)}); Ctrl+V "
                "was not sent; the window was restored and the text stays on the "
                "clipboard"
            )
            return
        self._paste_pending = False
        self._paste_started = True
        # Explicit class call keeps partially-built test doubles working.
        PastexApp._cancel_paste_send_timeout(self)
        _run_in_thread(self._paste_sender)

    def _cancel_paste_send_timeout(self) -> None:
        after_id = self.__dict__.get("_paste_send_id")
        self._paste_send_id = None
        if after_id is None or self.__dict__.get("tk") is None:
            return
        try:
            self.after_cancel(after_id)
        except TK_ERRORS:
            pass

    def _paste_sender(self) -> None:
        typing = bool(self.__dict__.get("_paste_by_typing", False))
        log_error(
            (
                "[paste] typing the text with xdotool"
                if typing
                else "[paste] sending Ctrl+V"
            )
            + f" ({PastexApp._since_click(self)})"
        )
        if typing:
            # Typing a long snippet takes as long as it takes; the call has its
            # own timeout, so the paste deadline must not fire mid-sentence.
            PastexApp._cancel_paste_watchdog(self)
        try:
            if typing:
                typed_at = time.monotonic()
                result = type_text_x11(self.__dict__.get("_paste_body", ""))
                log_error(
                    f"[paste] typed {len(self.__dict__.get('_paste_body', ''))} "
                    f"characters in {(time.monotonic() - typed_at) * 1000:.0f} ms "
                    f"({PastexApp._since_click(self)})"
                )
            else:
                result = simulate_paste(
                    plan=self.__dict__.get("_paste_plan"),
                    should_stop=lambda: bool(
                        self.__dict__.get("_paste_aborted", False)
                    ),
                )
        except Exception as exc:  # noqa: BLE001 - always report back to the GUI
            log_error(f"[paste] sender failed: {exc}")
            result = PasteResult(ok=False, tool="", message=f"paste error: {exc}")
        log_error(
            f"[paste] result: ok={result.ok} tool={result.tool} message={result.message}"
        )
        if not result.ok:
            # Resolve the tools again next time: a missing tool may have been
            # installed while Pastex was running.
            self._paste_plan = None
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
        if self.__dict__.get("_paste_finished", False):
            # A late result from an attempt the watchdog already abandoned:
            # report it, but do not move the window or open a second dialog.
            log_error(f"[paste] late result: ok={result.ok} message={result.message}")
            if result.ok:
                self._paste_ready = True
            return
        self._paste_finished = True
        self._pasting = False
        self._paste_pending = False
        self._paste_clipboard_ready = False
        # Remember whether Ctrl+V can be sent here: a failed paste makes the
        # next click copy the text without minimizing the window first.
        self._paste_ready = bool(getattr(result, "ok", False))
        # Explicit class call keeps partially-built test doubles working.
        PastexApp._cancel_paste_send_timeout(self)
        PastexApp._cancel_paste_watchdog(self)
        # The picker never hid the main window, so leave the target app alone.
        restore = self.__dict__.get("_paste_restore_window", True)
        self._paste_hide_widget = None
        if restore:
            PastexApp._restore_window(self)
        if not result.ok:
            if restore:
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
        if restore:
            self.lift()
        self.status_var.set(
            f"⚡ {result.message} If the text does not appear, press Ctrl+V."
        )

    def _copy_failed(self, message: str) -> None:
        """Report a clipboard that does not hold the text (nothing to paste).

        Unlike :meth:`_paste_failed` this must not tell the user to press
        Ctrl+V: the clipboard is empty, so pasting would insert nothing.
        """
        self._paste_finished = True
        self._pasting = False
        self._paste_pending = False
        self._paste_clipboard_ready = False
        self._paste_ready = False
        PastexApp._cancel_paste_send_timeout(self)
        PastexApp._cancel_paste_watchdog(self)
        self.status_var.set(f"Could not copy to the clipboard: {message}")
        PastexApp._restore_window(self)
        try:
            messagebox.showerror(
                "Could not copy to the clipboard",
                f"{message}.\n\nNothing was pasted. See the log at "
                f"{ERROR_LOG}.",
            )
        except TK_ERRORS:  # pragma: no cover - no display in tests
            pass

    def _paste_failed(self, message: str) -> None:
        self._paste_finished = True
        self._pasting = False
        self._paste_pending = False
        self._paste_clipboard_ready = False
        self._paste_ready = False
        PastexApp._cancel_paste_send_timeout(self)
        PastexApp._cancel_paste_watchdog(self)
        # Keep the app visible so the clipboard failure message can be read.
        self.deiconify()
        self.lift()
        self.status_var.set(f"Could not copy to the clipboard: {message}")
        self._show_paste_failure(message)

    # ---------- Automatic-paste permissions (pastex group) ----------
    def _show_paste_failure(self, message: str) -> None:
        """Show the failure reason and the manual steps that may resolve it.

        Since 1.5.0, the GUI permission button has been removed. The dialog
        provides the diagnosis and the manual setup steps.
        """
        st = uinput.status()
        text = (
            f"{message}\n\nThe text is on the clipboard — "
            "press Ctrl+V in the target app."
        )
        if not st.needed:
            # X11: no permission is involved, so do not send the user to
            # the manual /dev/uinput setup steps.
            text += (
                "\n\nThis is an X11 session, so no /dev/uinput permission is "
                "involved: xdotool could not deliver Ctrl+V to the window that "
                "was active before Pastex."
            )
            messagebox.showinfo("Automatic paste is unavailable", text)
            return
        if st.ready:
            messagebox.showinfo("Automatic paste is unavailable", text)
            return
        text += (
            "\n\nDiagnosis:\n"
            + "\n".join(_status_lines(st))
            + "\n\n"
            + uinput.explain(st)
            + "\n\n"
            + uinput.MANUAL_HINT
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
        self.index.invalidate()
        self.refresh_tree(force=True)
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
        self.index.invalidate()
        self.refresh_tree(force=True)
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
            self.index.invalidate()
            self.refresh_tree(force=True)
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
            for path in paths:
                self.index.forget(path)
            self.index.invalidate()
        self.refresh_tree(force=True)

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
    """Return formatted permission-status lines for the GUI."""
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
            self.master.index.forget(self.path)
            self.master.refresh_tree(force=True)
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

def run_gui() -> int:
    """Run the Pastex GUI and return the exit code.

    One instance per user: a later launch asks the running one to show its
    window and exits, so it costs no startup time and cannot leave a second
    panel entry.
    """
    if tk is None:
        print(
            "Error: tkinter is unavailable. Install python3-tk "
            "(sudo apt install python3-tk).",
            file=sys.stderr,
        )
        return 1

    answer = send_command("show")
    if answer is not None and answer.get("ok"):
        return 0
    if answer is not None:
        log_error(f"[instance] command refused: {answer.get('error')}")

    try:
        app = PastexApp(roots=default_roots())
    except Exception:  # noqa: BLE001 - report unexpected failures during GUI startup
        tb = traceback.format_exc()
        log_error(f"Could not start GUI:\n{tb}")
        print(f"Error: Pastex could not start. See {ERROR_LOG}", file=sys.stderr)
        print(tb, file=sys.stderr)
        return 1
    # getattr keeps partially built test doubles working, like elsewhere here.
    handler = getattr(app, "_handle_instance_command", None)
    instance = SingleInstance(handler) if handler is not None else None
    if instance is not None:
        if instance.claim() and not instance.serve():
            log_error("[instance] could not listen; later launches start a new window")
        app.instance = instance
    try:
        app.mainloop()
    finally:
        if instance is not None:
            instance.stop()
        # Whatever ended the loop, stop owning the clipboard before exiting:
        # a helper left behind would keep serving a dead window's selection.
        release_owner = getattr(app, "_release_clipboard_owner", None)
        if callable(release_owner):
            release_owner()
    return 0


def main() -> None:
    """Entry point: launch the GUI (no command-line options)."""
    try:
        code = run_gui()
    except Exception:  # noqa: BLE001 - log unexpected failures at the boundary
        tb = traceback.format_exc()
        log_error(tb)
        print(f"Unexpected error. See {ERROR_LOG}.", file=sys.stderr)
        print(tb, file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
