"""Uji statis: tangkap NameError tanpa perlu menjalankan GUI.

Regresi v1.0.3: `threading` dipakai di app.py tetapi tidak pernah diimpor,
sehingga tombol Salin/Paste melempar NameError di mesin pengguna — padahal
seluruh uji lolos karena jalur GUI tidak dijalankan tanpa display.
"""

import ast
import builtins
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "pastex"


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
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }


def test_no_undefined_names_in_package():
    known_builtins = set(dir(builtins)) | {
        "__file__",
        "__name__",
        "__doc__",
        "__package__",
        "self",
        "cls",
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
    from pastex import app

    assert hasattr(app, "threading")


def test_modules_import_cleanly_without_display(monkeypatch):
    """Semua modul harus bisa diimpor tanpa X/Wayland (CLI tetap jalan)."""
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    import importlib

    import pastex

    for name in ("app", "clipboard", "snippets", "uinput"):
        importlib.import_module(f"pastex.{name}")
    assert pastex.__version__


# --- Konsistensi metadata paket -------------------------------------------
ROOT = PKG.parent


def _control_recommends(text: str) -> list[str]:
    for line in text.splitlines():
        if line.startswith("Recommends:"):
            return [p.strip() for p in line.split(":", 1)[1].split(",") if p.strip()]
    return []


def test_build_deb_recommends_matches_debian_control():
    """Dua sumber Recommends (control & build-deb.sh) tidak boleh menyimpang."""
    control = _control_recommends((ROOT / "debian" / "control").read_text())
    script = _control_recommends((ROOT / "scripts" / "build-deb.sh").read_text())
    assert control == script, f"control={control} build-deb={script}"


def test_reader_tools_declared_in_recommends():
    """Setiap tool pembaca/pembuat clipboard yang dipakai kode harus diusulkan
    sebagai paket, supaya pengguna tidak menemui fitur mati diam-diam."""
    from pastex.clipboard import READER_TOOLS

    recommends = _control_recommends((ROOT / "debian" / "control").read_text())
    # nama perintah -> paket Debian
    package_of = {
        "wl-paste": "wl-clipboard",
        "wl-copy": "wl-clipboard",
        "xclip": "xclip",
        "xsel": "xsel",
        "xdotool": "xdotool",
        "ydotool": "ydotool",
    }
    needed = {package_of[t] for t in READER_TOOLS if t in package_of}
    needed |= {"wl-clipboard", "ydotool"}  # salin Wayland + paste Wayland
    missing = needed - set(recommends)
    assert not missing, f"paket belum ada di Recommends: {sorted(missing)}"


def test_docs_do_not_promise_removed_tk_clipboard():
    """Fallback clipboard Tk dihapus di v1.4.0; dokumen tidak boleh menjanjikannya."""
    for name in ("README.md", "debian/control"):
        text = (ROOT / name).read_text().lower()
        assert "fallback clipboard" not in text, (
            f"{name} masih menjanjikan fallback clipboard"
        )


def test_package_description_matches_between_sources():
    """build-deb.sh menulis deskripsi sendiri; klaimnya tidak boleh menyimpang
    dari debian/control (regresi v1.4.2: 'fallback clipboard internal' masih
    tertinggal di .deb meski sudah dihapus dari control)."""

    def description_lines(text: str) -> list[str]:
        lines, capture = [], False
        for line in text.splitlines():
            if line.startswith("Description:"):
                capture = True
                continue
            if capture:
                if line.startswith((" ", "\t")) or not line.strip():
                    if line.strip():
                        lines.append(line.strip())
                else:
                    break
        return lines

    control = description_lines((ROOT / "debian" / "control").read_text())
    script = description_lines((ROOT / "scripts" / "build-deb.sh").read_text())
    assert control, "deskripsi control tidak terbaca"
    assert control == script, (
        "deskripsi di .deb (build-deb.sh) berbeda dari debian/control:\n"
        f"control   = {control}\nbuild-deb = {script}"
    )
