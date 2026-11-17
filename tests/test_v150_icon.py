"""Icon tests: the window icon must MATCH the application menu icon.

Original bug: `PastexApp` never called `iconphoto`, so the window used the
generic Tk icon instead of the one in the menu. On top of that, Tk's WM_CLASS
("Tk") did not match StartupWMClass in pastex.desktop, so the desktop did not
even associate the window with its menu entry.
"""

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from pastex import app as app_module

ROOT = Path(__file__).resolve().parent.parent
ICON_DIR = ROOT / "usr" / "share" / "icons" / "hicolor"
SVG = ICON_DIR / "scalable" / "apps" / "pastex.svg"
DESKTOP = ROOT / "usr" / "share" / "applications" / "pastex.desktop"
PNG_SIZES = (64, 128, 256)


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path} bukan PNG"
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _png_rgba(path: Path) -> tuple[int, int, bytearray]:
    """Read back an 8-bit RGBA PNG (enough for the files we generate)."""
    import zlib

    data = path.read_bytes()
    width, height = struct.unpack(">II", data[16:24])
    assert data[24] == 8 and data[25] == 6, "must be 8-bit RGBA"
    pos, idat = 8, b""
    while pos < len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        tag = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        if tag == b"IDAT":
            idat += body
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = width * 4
    pixels = bytearray()
    for y in range(height):
        start = y * (stride + 1)
        assert raw[start] == 0, "the PNG filter must be None"
        pixels += raw[start + 1 : start + 1 + stride]
    return width, height, pixels


# --- icon files ----------------------------------------------------------


def test_svg_exists_and_is_square():
    assert SVG.is_file()
    root = ET.parse(SVG).getroot()
    view = root.get("viewBox").split()
    assert len(view) == 4 and view[2] == view[3]


@pytest.mark.parametrize("size", PNG_SIZES)
def test_png_exists_for_each_size(size):
    """Tk can only load raster images: the PNG must exist in the hicolor theme."""
    path = ICON_DIR / f"{size}x{size}" / "apps" / "pastex.png"
    assert path.is_file(), f"the {size}px window icon is missing"
    assert _png_size(path) == (size, size)


@pytest.mark.parametrize("size", (64, 128, 256))
def test_png_actually_drawn_not_blank(size):
    """The PNG must not be a blank canvas (a failed render still writes a file)."""
    path = ICON_DIR / f"{size}x{size}" / "apps" / "pastex.png"
    width, height, pixels = _png_rgba(path)
    opaque = sum(1 for i in range(3, len(pixels), 4) if pixels[i] > 200)
    ratio = opaque / (width * height)
    assert 0.25 < ratio < 0.95, f"proporsi piksel opak aneh: {ratio:.2f}"


def test_png_matches_svg_colors():
    """PNG colours must come from the same SVG (not redrawn by hand)."""
    svg_text = SVG.read_text(encoding="utf-8").lower()
    _, _, pixels = _png_rgba(ICON_DIR / "64x64" / "apps" / "pastex.png")
    seen = {
        (pixels[i], pixels[i + 1], pixels[i + 2])
        for i in range(0, len(pixels), 4)
        if pixels[i + 3] > 250
    }
    # hijau tua (latar), putih (dokumen), oranye (panah paste)
    for name, rgb in (
        ("hijau", (46, 125, 50)),
        ("putih", (255, 255, 255)),
        ("oranye", (255, 152, 0)),
    ):
        hexa = f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"
        assert hexa in svg_text, f"colour {name} is missing from the SVG"
        assert rgb in seen, f"colour {name} {hexa} does not appear in the PNG"


def test_png_is_what_the_generator_produces():
    """The PNGs in the repository must come from the script, not from hand edits."""
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "make_icon_png", ROOT / "scripts" / "make-icon-png.py"
    )
    module = importlib.util.module_from_spec(spec)
    # @dataclass needs the module registered in sys.modules when it is created.
    sys.modules["make_icon_png"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]

    view_w, _, shapes = module.parse_svg(SVG)
    for size in (64, 256):
        expected = module.render(size, view=view_w, shapes=shapes)
        width, height, actual = _png_rgba(
            ICON_DIR / f"{size}x{size}" / "apps" / "pastex.png"
        )
        assert (width, height) == (size, size)
        assert bytes(actual) == expected, f"stale {size}px PNG — rerun the script"


# --- window identity -----------------------------------------------------


def test_constants_agree_with_desktop_file():
    """Icon=/StartupWMClass= in .desktop must match what the app uses."""
    # Only the [Desktop Entry] group: the file also carries a [Desktop Action]
    # section for the snippet picker.
    entries = {}
    group = ""
    for line in DESKTOP.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            group = stripped
            continue
        if group != "[Desktop Entry]":
            continue
        if "=" in line and not stripped.startswith("#"):
            key, value = line.split("=", 1)
            entries[key.strip()] = value.strip()
    assert entries["Icon"] == app_module.ICON_NAME
    assert entries["StartupWMClass"] == app_module.WM_CLASS
    assert entries["Exec"].endswith("/pastex")
    assert "Pick" in entries.get("Actions", ""), "the picker action must be registered"


def test_desktop_action_opens_the_picker():
    text = DESKTOP.read_text(encoding="utf-8")
    action = text.split("[Desktop Action Pick]", 1)[1]
    assert "Exec=/usr/bin/pastex --pick" in action


def test_window_class_passed_to_tk():
    """Tk must be given a className so WM_CLASS does not fall back to "Tk"."""
    src = Path(app_module.__file__).read_text(encoding="utf-8")
    assert "super().__init__(className=TK_CLASS_NAME)" in src


def test_icon_files_prefers_installed_and_found():
    files = app_module.icon_files()
    assert files, "no icon PNG was found from the code"
    assert [f.name for f in files] == ["pastex.png"] * len(files)
    sizes = [int(f.parent.parent.name.split("x")[0]) for f in files]
    assert sizes == sorted(sizes, reverse=True), "must be largest to smallest"


def test_apply_window_icon_sets_photo_and_keeps_reference(monkeypatch):
    """iconphoto is called (True = every Toplevel) and the images are not garbage collected."""
    loaded = []

    class FakePhoto:
        def __init__(self, file=None):
            loaded.append(file)

    class FakeTk:
        PhotoImage = FakePhoto

        def __init__(self):
            self.icon = None
            self.iconname = None

        def iconphoto(self, default, *images):
            self.icon = (default, images)

        def wm_iconname(self, name):
            self.iconname = name

    monkeypatch.setattr(app_module, "tk", FakeTk)
    window = FakeTk()
    used = app_module.apply_window_icon(window)

    assert used, "no icon file was used"
    assert window.icon is not None, "iconphoto was not called"
    assert window.icon[0] is True, "the icon must also apply to child Toplevels"
    assert len(window.icon[1]) == len(used)
    assert window.iconname == app_module.ICON_NAME
    assert window._icon_images, "the image references must be kept"


def test_constructor_actually_applies_window_icon():
    """__init__ MUST call apply_window_icon(self).

    Without this test, `apply_window_icon` could exist and be correct yet never
    be called — exactly the original bug (a generic window icon).
    """
    import ast

    tree = ast.parse(Path(app_module.__file__).read_text(encoding="utf-8"))
    app_cls = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "PastexApp"
    )
    init = next(
        n
        for n in app_cls.body
        if isinstance(n, ast.FunctionDef) and n.name == "__init__"
    )
    calls = [
        node
        for node in ast.walk(init)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "apply_window_icon"
    ]
    assert calls, "apply_window_icon is not called from __init__"
    assert calls[0].args and isinstance(calls[0].args[0], ast.Name)
    assert calls[0].args[0].id == "self"


def test_apply_window_icon_survives_broken_png(monkeypatch, tmp_path):
    """A corrupt PNG must not stop the application from starting."""
    bad = tmp_path / "256x256" / "apps"
    bad.mkdir(parents=True)
    (bad / "pastex.png").write_bytes(b"bukan png")

    class FakeTk:
        class PhotoImage:
            def __init__(self, file=None):
                raise FakeTk.TclError("cannot read")

        class TclError(Exception):
            pass

    monkeypatch.setattr(app_module, "tk", FakeTk)
    monkeypatch.setattr(app_module, "icon_dirs", lambda: [tmp_path])
    assert app_module.apply_window_icon(FakeTk()) == []


def test_check_reports_icon_status(capsys):
    assert app_module.run_cli(["--check"]) == 0
    out = capsys.readouterr().out
    assert "Application icon:" in out
    assert "pastex.png" in out
    assert "(expected WM_CLASS 'Pastex')" in out


def test_real_window_wm_class_matches_desktop(tmp_path):
    """Run the real launcher under Xvfb and check WM_CLASS through xprop.

    This test also runs on XWayland, because Tk speaks the X11 protocol; the
    desktop's own icon matching still has to be verified on a real desktop.
    """
    import os
    import shutil
    import subprocess
    import sys

    required = os.environ.get("PASTE_REQUIRE_X11_TEST") == "1"
    missing = [
        tool for tool in ("xvfb-run", "xauth", "xprop") if not shutil.which(tool)
    ]
    if app_module.tk is None:
        missing.append("tkinter")
    if missing:
        if required:
            pytest.fail("X11 test dependencies are missing: " + ", ".join(missing))
        pytest.skip("X11 test dependencies are missing: " + ", ".join(missing))

    script = r"""
import os, subprocess, sys, time
launcher, folder, expected = sys.argv[1:]
p = subprocess.Popen([launcher, "--dir", folder], stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE, text=True)
try:
    title = "Pastex v" + __import__("pastex").__version__ + " — One-Click Paste"
    for _ in range(100):
        result = subprocess.run(["xprop", "-name", title, "WM_CLASS"],
                                capture_output=True, text=True)
        if result.returncode == 0 and "WM_CLASS(STRING)" in result.stdout:
            print(result.stdout.strip())
            if result.stdout.strip().split('"')[-2] != expected:
                raise SystemExit("the actual WM_CLASS differs from StartupWMClass")
            break
        if p.poll() is not None:
            raise SystemExit("Aplikasi berhenti: " + p.stderr.read())
        time.sleep(0.1)
    else:
        raise SystemExit("xprop did not find the window")
finally:
    p.terminate()
    p.wait(timeout=10)
"""
    env = os.environ.copy()
    env["XDG_CONFIG_HOME"] = str(tmp_path / "config")
    result = subprocess.run(
        [
            "xvfb-run",
            "-a",
            sys.executable,
            "-c",
            script,
            str(ROOT / "bin" / "pastex"),
            str(tmp_path),
            app_module.WM_CLASS,
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
