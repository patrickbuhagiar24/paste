"""A small snippet picker that pastes without touching the main window.

Bind ``pastex --pick`` to a keyboard shortcut (for example Super+V) and the
list appears where the pointer is. Choosing a snippet copies it and sends
Ctrl+V to the application that kept the focus, because the picker is a
frameless popup:

  * the main window is never minimized or unmapped, so the panel icon is not
    part of the paste at all;
  * the popup carries no panel entry of its own, so hiding it cannot remove an
    icon either;
  * the application behind the popup keeps the focus when the popup is
    destroyed, which is exactly where Ctrl+V should land.

:class:`PickerModel` holds the filtering and selection logic so it can be
tested without a display; :func:`open_picker` is the thin Tk layer.
"""

from __future__ import annotations

from .snippets import Node

MAX_ROWS = 12


def label_for(node: Node) -> str:
    """Show a snippet with its folder, relative to the source root."""
    try:
        relative = node.path.relative_to(node.source_root) if node.source_root else None
    except ValueError:  # pragma: no cover - defensive
        relative = None
    if relative is not None:
        return str(relative.with_suffix(""))
    return node.name


class PickerModel:
    """Filtering and keyboard selection for the picker."""

    def __init__(self, search) -> None:
        self._search = search
        self.query = ""
        self.matches: list[Node] = list(search(""))
        self.index = 0

    def set_query(self, query: str) -> list[Node]:
        self.query = query
        self.matches = list(self._search(query))
        self.index = 0
        return self.matches

    def move(self, delta: int) -> Node | None:
        if not self.matches:
            self.index = 0
            return None
        self.index = (self.index + delta) % len(self.matches)
        return self.current()

    def set_index(self, index: int) -> Node | None:
        if not self.matches:
            self.index = 0
            return None
        self.index = index % len(self.matches)
        return self.current()

    def current(self) -> Node | None:
        if not self.matches:
            return None
        return self.matches[min(self.index, len(self.matches) - 1)]

    def labels(self, limit: int = MAX_ROWS) -> list[str]:
        return [label_for(node) for node in self.matches[:limit]]


def popup_geometry(
    pointer_x: int,
    pointer_y: int,
    width: int = 460,
    height: int = 340,
) -> str:
    """Place the popup at the pointer, kept away from the screen edges."""
    x = max(0, int(pointer_x) - 40)
    y = max(0, int(pointer_y) - 24)
    return f"{width}x{height}+{x}+{y}"


def open_picker(app):  # pragma: no cover - the Tk layer, covered by hand
    """Show the picker for ``app``; returns the popup Toplevel."""
    from . import app as app_module

    tk = app_module.tk
    if tk is None:
        return None
    model = PickerModel(app.index.search)
    try:
        pointer_x, pointer_y = app.winfo_pointerxy()
    except tk.TclError:  # pragma: no cover - pointer unavailable
        pointer_x, pointer_y = 200, 200
    popup = tk.Toplevel(app)
    popup.overrideredirect(True)
    popup.attributes("-topmost", True)
    popup.geometry(popup_geometry(pointer_x, pointer_y))
    frame = tk.Frame(popup, borderwidth=2, relief="ridge")
    frame.pack(fill="both", expand=True)
    entry = tk.Entry(frame)
    entry.pack(fill="x", padx=6, pady=(6, 3))
    listing = tk.Listbox(frame, height=MAX_ROWS, activestyle="none")
    listing.pack(fill="both", expand=True, padx=6, pady=(0, 6))

    def refresh() -> None:
        listing.delete(0, "end")
        for label in model.labels():
            listing.insert("end", label)
        if model.matches:
            listing.selection_clear(0, "end")
            listing.selection_set(min(model.index, len(model.matches) - 1))
            listing.see(min(model.index, len(model.matches) - 1))

    def choose(_event=None) -> str:
        node = model.current()
        popup.destroy()
        if node is not None:
            app.paste_path(node.path, hide=None)
        return "break"

    def on_query(_event=None) -> None:
        model.set_query(entry.get())
        refresh()

    def on_key(delta: int):
        def handler(_event=None) -> str:
            model.move(delta)
            refresh()
            return "break"

        return handler

    def on_click(_event=None) -> str:
        selection = listing.curselection()
        if selection:
            model.set_index(int(selection[0]))
        return choose()

    entry.bind("<KeyRelease>", on_query)
    entry.bind("<Return>", choose)
    entry.bind("<Down>", on_key(1))
    entry.bind("<Up>", on_key(-1))
    entry.bind("<Escape>", lambda _e: (popup.destroy(), "break")[1])
    listing.bind("<ButtonRelease-1>", on_click)
    listing.bind("<Return>", choose)
    listing.bind("<Escape>", lambda _e: (popup.destroy(), "break")[1])
    def on_focus_out(_event=None) -> None:
        """Close when another application takes the focus, not on entry focus."""
        focused = popup.focus_get()
        if focused is None or focused.winfo_toplevel() is not popup:
            popup.destroy()

    popup.bind("<FocusOut>", on_focus_out)
    refresh()
    entry.focus_force()
    return popup
