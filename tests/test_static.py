"""Uji statis: tangkap NameError tanpa perlu menjalankan GUI.

Regresi v1.0.3: `threading` dipakai di app.py tetapi tidak pernah diimpor,
sehingga tombol Salin/Paste melempar NameError di mesin pengguna — padahal
seluruh uji lolos karena jalur GUI tidak dijalankan tanpa display.
"""
import ast
import builtins
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "paste_teks"


def _bound_names(tree: ast.AST) -> set[str]:
    """Semua nama yang diikat di modul: impor, definisi, target assign."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Name,)) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.comprehension):
            for sub in ast.walk(node.target):
                if isinstance(sub, ast.Name):
                    names.add(sub.id)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
    return names


def _used_names(tree: ast.AST) -> set[str]:
    return {node.id for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}


def test_no_undefined_names_in_package():
    known_builtins = set(dir(builtins)) | {
        "__file__", "__name__", "__doc__", "__package__", "self", "cls",
        "annotations",
    }
    problems = {}
    for path in sorted(PKG.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        missing = _used_names(tree) - _bound_names(tree) - known_builtins
        if missing:
            problems[path.name] = sorted(missing)
    assert not problems, f"nama tidak terdefinisi: {problems}"


def test_app_module_imports_threading():
    """Tombol Salin/Paste memakai thread latar."""
    from paste_teks import app
    assert hasattr(app, "threading")


def test_modules_import_cleanly_without_display(monkeypatch):
    """Semua modul harus bisa diimpor tanpa X/Wayland (CLI tetap jalan)."""
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    import importlib
    import paste_teks
    for name in ("app", "clipboard", "snippets", "uinput"):
        importlib.import_module(f"paste_teks.{name}")
    assert paste_teks.__version__
