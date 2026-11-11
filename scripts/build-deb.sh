#!/usr/bin/env bash
# Bangun paket .deb paste-teks tanpa debhelper (hanya butuh dpkg-deb).
# Hasil: dist/paste-teks_<versi>_all.deb
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="$(python3 -c 'from paste_teks import __version__; print(__version__)')"
PKG="paste-teks"
ARCH="all"
STAGE="build/deb"
OUT="dist/${PKG}_${VERSION}-1_${ARCH}.deb"

rm -rf "$STAGE"
ICON_SIZES="64 128 256"
mkdir -p "$STAGE/DEBIAN" "$STAGE/usr/bin" \
  "$STAGE/usr/share/paste-teks" \
  "$STAGE/usr/share/applications" \
  "$STAGE/usr/share/icons/hicolor/scalable/apps" \
  "$STAGE/usr/lib/paste-teks" \
  "$STAGE/usr/lib/udev/rules.d" \
  "$STAGE/usr/lib/systemd/user" \
  "$STAGE/usr/share/doc/$PKG"

install -m 755 bin/paste-teks "$STAGE/usr/bin/paste-teks"
cp -r paste_teks "$STAGE/usr/share/paste-teks/"
find "$STAGE/usr/share/paste-teks" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find "$STAGE/usr/share/paste-teks" -name "*.pyc" -delete 2>/dev/null || true
# Sejak 1.5.0 paket TIDAK lagi menyalin contoh bawaan (data/snippets/ sudah
# dihapus dari repo). Contoh yang sudah terpasang di mesin pengguna tetap
# dibiarkan; pemasangan baru bisa meminta contoh lewat `--ekspor-data`.
install -m 644 usr/share/applications/paste-teks.desktop "$STAGE/usr/share/applications/"
install -m 644 usr/share/icons/hicolor/scalable/apps/paste-teks.svg "$STAGE/usr/share/icons/hicolor/scalable/apps/"
# PNG: ikon JENDELA (Tk iconphoto tidak bisa memuat SVG). Dirender dari SVG
# yang sama oleh scripts/make-icon-png.py agar ikon jendela = ikon menu.
for size in $ICON_SIZES; do
  install -d "$STAGE/usr/share/icons/hicolor/${size}x${size}/apps"
  install -m 644 "usr/share/icons/hicolor/${size}x${size}/apps/paste-teks.png" \
    "$STAGE/usr/share/icons/hicolor/${size}x${size}/apps/"
done
install -m 644 README.md "$STAGE/usr/share/doc/$PKG/README.md"
install -m 644 LICENSE "$STAGE/usr/share/doc/$PKG/copyright"

# Helper root (dipanggil lewat pkexec) + udev rule grup paste-teks.
install -m 755 usr/lib/paste-teks/paste-teks-uinput-group "$STAGE/usr/lib/paste-teks/"
install -m 644 usr/lib/udev/rules.d/70-paste-teks-uinput.rules "$STAGE/usr/lib/udev/rules.d/"
install -m 644 usr/lib/systemd/user/ydotoold.service "$STAGE/usr/lib/systemd/user/"
install -m 755 debian/postinst "$STAGE/DEBIAN/postinst"
install -m 755 debian/postrm "$STAGE/DEBIAN/postrm"
gzip -9 -n "$STAGE/usr/share/doc/$PKG/README.md" 2>/dev/null || true

cat > "$STAGE/DEBIAN/control" <<EOF
Package: $PKG
Version: ${VERSION}-1
Section: utils
Priority: optional
Architecture: $ARCH
Maintainer: Patrick Buhagiar <patrickbuhagiar24@users.noreply.github.com>
Depends: python3 (>= 3.10), python3-tk
Recommends: wl-clipboard, xclip, xsel, xdotool, ydotool
Description: GUI paste teks hierarki dari berkas .txt — tempel ke aplikasi lain satu klik
 Paste Teks menampilkan berkas .txt dalam hierarki kategori/folder (GUI).
 Double-click atau tombol "Paste ke Aplikasi Lain" menyalin teks ke clipboard
 dan mengirim Ctrl+V otomatis ke aplikasi yang sedang fokus.
 .
 Mendukung Ubuntu 26.10 (GNOME Wayland & X11): clipboard via
 wl-copy/xclip/xsel, paste otomatis via ydotool/xdotool.
 .
 Paket tidak lagi membawa contoh bawaan; pilih satu folder sumber lewat
 tombol "Pilih Folder Sumber" di GUI, atau minta contoh dengan
 paste-teks --ekspor-data. Teks pribadi disimpan di
 ~/.local/share/paste-teks/snippets dan bisa ditambah/diedit dari GUI.
Homepage: https://github.com/patrickbuhagiar24/paste
EOF

mkdir -p dist
dpkg-deb --build --root-owner-group "$STAGE" "$OUT"
echo "OK: $OUT"
dpkg-deb --info "$OUT" | head -n 20
