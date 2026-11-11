#!/usr/bin/env python3
"""Render usr/share/icons/.../paste-teks.svg menjadi PNG untuk ikon jendela.

Kenapa perlu: ikon menu dibaca GNOME dari tema (SVG, tajam di semua ukuran),
sedangkan ikon JENDELA harus gambar raster yang bisa dimuat Tk
(`PhotoImage`/`iconphoto`). Tanpa PNG, jendela menampilkan ikon generik
sehingga berbeda dengan ikon di menu aplikasi.

Supaya keduanya dijamin sama, PNG di sini dirender dari BERKAS SVG YANG SAMA
— bukan digambar ulang dengan tangan. Skrip ini hanya mendukung subset SVG
yang dipakai ikon kita (rect, circle, path garis lurus, fill/stroke), tanpa
dependensi luar (tidak butuh cairo/rsvg/ImageMagick).

Pakai:
    python3 scripts/make-icon-png.py            # tulis 64/128/256 px
    python3 scripts/make-icon-png.py --sizes 64 --out /tmp/uji
"""
from __future__ import annotations

import argparse
import re
import struct
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "usr" / "share" / "icons" / "hicolor" / "scalable" / "apps" / "paste-teks.svg"
ICON_DIR = ROOT / "usr" / "share" / "icons" / "hicolor"
SIZES = (64, 128, 256)


@dataclass
class Shape:
    """Satu bentuk sederhana hasil pembacaan SVG."""

    kind: str                      # "rect" | "circle" | "line"
    fill: tuple[int, int, int, int] | None = None
    stroke: tuple[int, int, int, int] | None = None
    width: float = 0.0             # lebar stroke
    # rect / line
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0
    r: float = 0.0                 # radius sudut rect / radius circle
    points: list[tuple[float, float]] = field(default_factory=list)


def _color(value: str | None) -> tuple[int, int, int, int] | None:
    if not value or value == "none":
        return None
    value = value.strip()
    if value.startswith("#"):
        hexa = value[1:]
        if len(hexa) == 3:
            hexa = "".join(c * 2 for c in hexa)
        if len(hexa) >= 6:
            return (int(hexa[0:2], 16), int(hexa[2:4], 16),
                    int(hexa[4:6], 16), 255)
    return None  # warna bernama tidak dipakai ikon ini


_PATH_TOKEN = re.compile(r"[MmLlHhVvZz]|-?\d*\.?\d+(?:[eE][-+]?\d+)?")


def _path_points(d: str) -> list[tuple[float, float]]:
    """Ubah path garis lurus menjadi polilin (subset: M L H V Z)."""
    tokens = _PATH_TOKEN.findall(d)
    points: list[tuple[float, float]] = []
    i = 0
    cmd = "M"
    x = y = 0.0
    start = (0.0, 0.0)
    while i < len(tokens):
        token = tokens[i]
        if token.isalpha():
            cmd = token
            i += 1
            if cmd in "Zz":
                if points:
                    points.append(start)
                continue
        if cmd in "Mm":
            nx, ny = float(tokens[i]), float(tokens[i + 1])
            i += 2
            x, y = (nx, ny) if cmd == "M" else (x + nx, y + ny)
            start = (x, y)
            points.append((x, y))
            cmd = "L" if cmd == "M" else "l"
        elif cmd in "Ll":
            nx, ny = float(tokens[i]), float(tokens[i + 1])
            i += 2
            x, y = (nx, ny) if cmd == "L" else (x + nx, y + ny)
            points.append((x, y))
        elif cmd in "Hh":
            nx = float(tokens[i])
            i += 1
            x = nx if cmd == "H" else x + nx
            points.append((x, y))
        elif cmd in "Vv":
            ny = float(tokens[i])
            i += 1
            y = ny if cmd == "V" else y + ny
            points.append((x, y))
        else:  # pragma: no cover - perintah tidak didukung
            raise ValueError(f"perintah path tidak didukung: {cmd!r}")
    return points


def parse_svg(path: Path = SVG) -> tuple[float, float, list[Shape]]:
    """Baca SVG ikon -> (lebar, tinggi, daftar Shape) dalam urutan gambar."""
    root = ET.parse(path).getroot()
    vb = root.get("viewBox", "").split()
    width = float(vb[2]) if len(vb) == 4 else float(root.get("width", "64"))
    height = float(vb[3]) if len(vb) == 4 else float(root.get("height", "64"))
    shapes: list[Shape] = []
    for node in root.iter():
        tag = node.tag.split("}")[-1]
        fill = _color(node.get("fill", "#000000"))
        stroke = _color(node.get("stroke"))
        width_stroke = float(node.get("stroke-width", "1") or 1)
        if tag == "rect":
            shapes.append(Shape("rect", fill=fill, stroke=stroke,
                                width=width_stroke,
                                x=float(node.get("x", 0)), y=float(node.get("y", 0)),
                                w=float(node.get("width", 0)),
                                h=float(node.get("height", 0)),
                                r=float(node.get("rx", 0) or 0)))
        elif tag == "circle":
            shapes.append(Shape("circle", fill=fill, stroke=stroke,
                                width=width_stroke,
                                x=float(node.get("cx", 0)), y=float(node.get("cy", 0)),
                                r=float(node.get("r", 0))))
        elif tag == "path":
            points = _path_points(node.get("d", ""))
            if len(points) >= 2:
                shapes.append(Shape("line", fill=fill, stroke=stroke or fill,
                                    width=width_stroke,
                                    points=points,
                                    r=width_stroke / 2))
    return width, height, shapes


# --- rasterisasi ---------------------------------------------------------

def _inside_rect(px, py, s: Shape) -> bool:
    if not (s.x <= px <= s.x + s.w and s.y <= py <= s.y + s.h):
        return False
    r = min(s.r, s.w / 2, s.h / 2)
    if r <= 0:
        return True
    # sudut membulat: titik di luar kotak inti harus berada dalam lingkaran sudut
    cx = min(max(px, s.x + r), s.x + s.w - r)
    cy = min(max(py, s.y + r), s.y + s.h - r)
    return (px - cx) ** 2 + (py - cy) ** 2 <= r * r


def _dist_to_segments(px, py, points) -> float:
    best = float("inf")
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        dx, dy = x2 - x1, y2 - y1
        length2 = dx * dx + dy * dy
        t = 0.0 if length2 == 0 else ((px - x1) * dx + (py - y1) * dy) / length2
        t = max(0.0, min(1.0, t))
        qx, qy = x1 + t * dx, y1 + t * dy
        best = min(best, ((px - qx) ** 2 + (py - qy) ** 2) ** 0.5)
    return best


def _blend(buf: bytearray, index: int, color) -> None:
    """Composite source-over warna opak di atas buffer RGBA."""
    if color[3] >= 255:
        buf[index:index + 4] = bytes(color)
        return
    alpha = color[3] / 255
    src = color[:3] + (color[3],)
    dst = bytes(buf[index:index + 4])
    out = [
        round(src[0] * alpha + dst[0] * (1 - alpha)),
        round(src[1] * alpha + dst[1] * (1 - alpha)),
        round(src[2] * alpha + dst[2] * (1 - alpha)),
        max(dst[3], src[3]),
    ]
    buf[index:index + 4] = bytes(out)


def _bounds(s: Shape, ss: float, size: int) -> tuple[int, int, int, int]:
    pad = max(s.width, 1.0) / 2 + 1
    if s.kind == "rect":
        lo_x, hi_x = s.x - pad, s.x + s.w + pad
        lo_y, hi_y = s.y - pad, s.y + s.h + pad
    elif s.kind == "circle":
        lo_x, hi_x = s.x - s.r - pad, s.x + s.r + pad
        lo_y, hi_y = s.y - s.r - pad, s.y + s.r + pad
    else:
        xs = [p[0] for p in s.points]
        ys = [p[1] for p in s.points]
        lo_x, hi_x = min(xs) - pad, max(xs) + pad
        lo_y, hi_y = min(ys) - pad, max(ys) + pad
    x0 = max(0, int(lo_x * ss))
    y0 = max(0, int(lo_y * ss))
    x1 = min(size * int(ss) - 1, int(hi_x * ss) + 1)
    y1 = min(size * int(ss) - 1, int(hi_y * ss) + 1)
    return x0, y0, x1, y1


def render(size: int, view: float = 64.0, supersample: int = 4,
           shapes: list[Shape] | None = None) -> bytes:
    """Render bentuk menjadi RGBA (size x size) dengan anti-aliasing."""
    if shapes is None:
        _, _, shapes = parse_svg()
    ss = int(supersample)
    hi = size * ss
    scale = hi / view
    buf = bytearray(hi * hi * 4)          # transparan

    for shape in shapes:
        x0, y0, x1, y1 = _bounds(shape, scale, size)
        if x1 < x0 or y1 < y0:
            continue
        for py in range(y0, y1 + 1):
            cy = (py + 0.5) / scale
            for px in range(x0, x1 + 1):
                cx = (px + 0.5) / scale
                index = (py * hi + px) * 4
                hit_fill = hit_stroke = False
                if shape.kind == "rect":
                    hit_fill = _inside_rect(cx, cy, shape)
                elif shape.kind == "circle":
                    d = ((cx - shape.x) ** 2 + (cy - shape.y) ** 2) ** 0.5
                    hit_fill = d <= shape.r
                    if shape.stroke and shape.width > 0:
                        hit_stroke = abs(d - shape.r) <= shape.width / 2
                else:
                    if shape.stroke and shape.width > 0:
                        hit_stroke = _dist_to_segments(
                            cx, cy, shape.points) <= shape.width / 2
                if hit_stroke and shape.stroke:
                    _blend(buf, index, shape.stroke)
                elif hit_fill and shape.fill:
                    _blend(buf, index, shape.fill)

    # turunkan resolusi: rata-rata kotak ss x ss -> anti-aliasing
    out = bytearray(size * size * 4)
    area = ss * ss
    for y in range(size):
        for x in range(size):
            r = g = b = a = 0
            for sy in range(ss):
                base = ((y * ss + sy) * hi + x * ss) * 4
                for sx in range(ss):
                    i = base + sx * 4
                    alpha = buf[i + 3]
                    if alpha:
                        r += buf[i] * alpha
                        g += buf[i + 1] * alpha
                        b += buf[i + 2] * alpha
                        a += alpha
            o = (y * size + x) * 4
            if a:
                out[o:o + 4] = bytes((round(r / a), round(g / a),
                                      round(b / a), round(a / area)))
    return bytes(out)


def write_png(path: Path, size: int, rgba: bytes) -> Path:
    """Tulis PNG RGBA 8-bit tanpa dependensi (zlib + struct)."""
    raw = bytearray()
    stride = size * 4
    for y in range(size):
        raw.append(0)  # filter "None"
        raw += rgba[y * stride:(y + 1) * stride]

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
           + chunk(b"IEND", b""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)
    return path


def icon_path(size: int, base: Path = ICON_DIR) -> Path:
    return Path(base) / f"{size}x{size}" / "apps" / "paste-teks.png"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sizes", default=",".join(str(s) for s in SIZES),
                        help="daftar ukuran dipisah koma (bawaan: 64,128,256)")
    parser.add_argument("--out", default=str(ICON_DIR),
                        help="folder dasar hicolor (bawaan: usr/share/icons/hicolor)")
    args = parser.parse_args(argv)

    view_w, view_h, shapes = parse_svg()
    if view_w != view_h:  # pragma: no cover - ikon kita persegi
        print(f"peringatan: viewBox tidak persegi ({view_w}x{view_h})")
    base = Path(args.out)
    for size in (int(s) for s in args.sizes.split(",") if s.strip()):
        rgba = render(size, view=max(view_w, view_h), shapes=shapes)
        path = write_png(icon_path(size, base), size, rgba)
        print(f"ditulis: {path} ({size}x{size})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
