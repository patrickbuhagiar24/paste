"""Uji ikon: ikon jendela harus SAMA dengan ikon di menu aplikasi.

Bug asli: `PasteTeksApp` tidak pernah memanggil `iconphoto`, sehingga jendela
memakai ikon generik bawaan Tk — berbeda dengan ikon di dash/menu GNOME.
Ditambah WM_CLASS Tk ("Tk") tidak sama dengan StartupWMClass di
paste-teks.desktop, jadi GNOME bahkan tidak memasangkan jendela ke entri menu.
"""
from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from paste_teks import app as app_module

ROOT = Path(__file__).resolve().parent.parent
ICON_DIR = ROOT / "usr" / "share" / "icons" / "hicolor"
SVG = ICON_DIR / "scalable" / "apps" / "paste-teks.svg"
DESKTOP = ROOT / "usr" / "share" / "applications" / "paste-teks.desktop"
PNG_SIZES = (64, 128, 256)


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path} bukan PNG"
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _png_rgba(path: Path) -> tuple[int, int, bytearray]:
    """Baca kembali PNG RGBA 8-bit (cukup untuk berkas buatan kita)."""
    import zlib

    data = path.read_bytes()
    width, height = struct.unpack(">II", data[16:24])
    assert data[24] == 8 and data[25] == 6, "harus 8-bit RGBA"
    pos, idat = 8, b""
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if tag == b"IDAT":
            idat += body
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = width * 4
    pixels = bytearray()
    for y in range(height):
        start = y * (stride + 1)
        assert raw[start] == 0, "filter PNG harus None"
        pixels += raw[start + 1:start + 1 + stride]
    return width, height, pixels


# --- berkas ikon ---------------------------------------------------------

def test_svg_exists_and_is_square():
    assert SVG.is_file()
    root = ET.parse(SVG).getroot()
    view = root.get("viewBox").split()
    assert len(view) == 4 and view[2] == view[3]


@pytest.mark.parametrize("size", PNG_SIZES)
def test_png_exists_for_each_size(size):
    """Tk hanya bisa memuat raster: PNG wajib ada di tema hicolor."""
    path = ICON_DIR / f"{size}x{size}" / "apps" / "paste-teks.png"
    assert path.is_file(), f"ikon jendela {size}px tidak ada"
    assert _png_size(path) == (size, size)


@pytest.mark.parametrize("size", (64, 128, 256))
def test_png_actually_drawn_not_blank(size):
    """PNG tidak boleh kanvas kosong (mis. render gagal tapi tetap ditulis)."""
    path = ICON_DIR / f"{size}x{size}" / "apps" / "paste-teks.png"
    width, height, pixels = _png_rgba(path)
    opaque = sum(1 for i in range(3, len(pixels), 4) if pixels[i] > 200)
    ratio = opaque / (width * height)
    assert 0.25 < ratio < 0.95, f"proporsi piksel opak aneh: {ratio:.2f}"


def test_png_matches_svg_colors():
    """Warna PNG harus berasal dari SVG yang sama (bukan digambar ulang)."""
    svg_text = SVG.read_text(encoding="utf-8").lower()
    width, height, pixels = _png_rgba(
        ICON_DIR / "64x64" / "apps" / "paste-teks.png")
    seen = {(pixels[i], pixels[i + 1], pixels[i + 2])
            for i in range(0, len(pixels), 4) if pixels[i + 3] > 250}
    # hijau tua (latar), putih (kertas), oranye (jam)
    for name, rgb in (("hijau", (46, 125, 50)), ("putih", (255, 255, 255)),
                      ("oranye", (255, 152, 0))):
        hexa = "#%02x%02x%02x" % rgb
        assert hexa in svg_text, f"warna {name} tidak ada di SVG"
        assert rgb in seen, f"warna {name} {hexa} tidak muncul di PNG"


def test_png_is_what_the_generator_produces():
    """PNG di repo harus hasil skrip, bukan berkas usang/manual."""
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "make_icon_png", ROOT / "scripts" / "make-icon-png.py")
    module = importlib.util.module_from_spec(spec)
    # @dataclass butuh modul terdaftar di sys.modules saat dibuat.
    sys.modules["make_icon_png"] = module
    spec.loader.exec_module(module)          # type: ignore[union-attr]

    view_w, _, shapes = module.parse_svg(SVG)
    for size in (64, 256):
        expected = module.render(size, view=view_w, shapes=shapes)
        width, height, actual = _png_rgba(
            ICON_DIR / f"{size}x{size}" / "apps" / "paste-teks.png")
        assert (width, height) == (size, size)
        assert bytes(actual) == expected, f"PNG {size}px basi — jalankan ulang skrip"


# --- identitas jendela ---------------------------------------------------

def test_constants_agree_with_desktop_file():
    """Icon=/StartupWMClass= di .desktop harus sama dengan yang dipakai app."""
    entries = {}
    for line in DESKTOP.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, value = line.split("=", 1)
            entries[key.strip()] = value.strip()
    assert entries["Icon"] == app_module.ICON_NAME
    assert entries["StartupWMClass"] == app_module.WM_CLASS
    assert entries["Exec"].endswith("/paste-teks")


def test_window_class_passed_to_tk():
    """Tk harus diberi className agar WM_CLASS tidak jatuh ke "Tk"."""
    src = Path(app_module.__file__).read_text(encoding="utf-8")
    assert "super().__init__(className=WM_CLASS)" in src


def test_icon_files_prefers_installed_and_found():
    files = app_module.icon_files()
    assert files, "PNG ikon tidak ditemukan dari kode"
    assert [f.name for f in files] == ["paste-teks.png"] * len(files)
    sizes = [int(f.parent.parent.name.split("x")[0]) for f in files]
    assert sizes == sorted(sizes, reverse=True), "harus besar -> kecil"


def test_apply_window_icon_sets_photo_and_keeps_reference(monkeypatch):
    """iconphoto dipanggil (True = semua Toplevel) dan gambar tidak di-GC."""
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

    assert used, "tidak ada berkas ikon yang dipakai"
    assert window.icon is not None, "iconphoto tidak dipanggil"
    assert window.icon[0] is True, "ikon harus berlaku juga untuk Toplevel anak"
    assert len(window.icon[1]) == len(used)
    assert window.iconname == app_module.ICON_NAME
    assert window._icon_images, "referensi gambar harus disimpan"


def test_constructor_actually_applies_window_icon():
    """__init__ WAJIB memanggil apply_window_icon(self).

    Tanpa uji ini, `apply_window_icon` bisa ada dan benar tetapi tidak pernah
    dipakai — persis bug awal (ikon jendela generik).
    """
    import ast

    tree = ast.parse(Path(app_module.__file__).read_text(encoding="utf-8"))
    app_cls = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.ClassDef) and n.name == "PasteTeksApp")
    init = next(n for n in app_cls.body
                if isinstance(n, ast.FunctionDef) and n.name == "__init__")
    calls = [node for node in ast.walk(init)
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name)
             and node.func.id == "apply_window_icon"]
    assert calls, "apply_window_icon tidak dipanggil di __init__"
    assert calls[0].args and isinstance(calls[0].args[0], ast.Name)
    assert calls[0].args[0].id == "self"


def test_apply_window_icon_survives_broken_png(monkeypatch, tmp_path):
    """PNG rusak tidak boleh membuat aplikasi gagal start."""
    bad = tmp_path / "256x256" / "apps"
    bad.mkdir(parents=True)
    (bad / "paste-teks.png").write_bytes(b"bukan png")

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
    assert "Ikon aplikasi:" in out
    assert "paste-teks.png" in out
    assert "WM_CLASS 'PasteTeks'" in out
