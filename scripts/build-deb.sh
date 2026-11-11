#!/usr/bin/env bash
# Build the Pastex Debian package without debhelper (requires dpkg-deb only).
# Output: dist/pastex_<version>_all.deb
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="$(python3 -c 'from pastex import __version__; print(__version__)')"
PKG="pastex"
ARCH="all"
STAGE="build/deb"
OUT="dist/${PKG}_${VERSION}-1_${ARCH}.deb"

rm -rf "$STAGE"
ICON_SIZES="64 128 256"
mkdir -p "$STAGE/DEBIAN" "$STAGE/usr/bin" \
  "$STAGE/usr/share/pastex" \
  "$STAGE/usr/share/applications" \
  "$STAGE/usr/share/icons/hicolor/scalable/apps" \
  "$STAGE/usr/lib/pastex" \
  "$STAGE/usr/lib/udev/rules.d" \
  "$STAGE/usr/lib/systemd/user" \
  "$STAGE/usr/share/doc/$PKG"

install -m 755 bin/pastex "$STAGE/usr/bin/pastex"
cp -r pastex "$STAGE/usr/share/pastex/"
find "$STAGE/usr/share/pastex" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find "$STAGE/usr/share/pastex" -name "*.pyc" -delete 2>/dev/null || true
# Since 1.5.0, the package no longer includes sample data (data/snippets/ was
# removed from the repository). Existing samples on user machines are kept;
# new installations can request samples with `--export-data`.
install -m 644 usr/share/applications/pastex.desktop "$STAGE/usr/share/applications/"
install -m 644 usr/share/icons/hicolor/scalable/apps/pastex.svg "$STAGE/usr/share/icons/hicolor/scalable/apps/"
# PNG: window icon (Tk iconphoto cannot load SVG). Rendered from the same SVG
# by scripts/make-icon-png.py so the window and menu icons match.
for size in $ICON_SIZES; do
  install -d "$STAGE/usr/share/icons/hicolor/${size}x${size}/apps"
  install -m 644 "usr/share/icons/hicolor/${size}x${size}/apps/pastex.png" \
    "$STAGE/usr/share/icons/hicolor/${size}x${size}/apps/"
done
install -m 644 README.md "$STAGE/usr/share/doc/$PKG/README.md"
install -m 644 LICENSE "$STAGE/usr/share/doc/$PKG/copyright"

# Root helper (called through pkexec) and the pastex group udev rule.
install -m 755 usr/lib/pastex/pastex-uinput-group "$STAGE/usr/lib/pastex/"
install -m 644 usr/lib/udev/rules.d/70-pastex-uinput.rules "$STAGE/usr/lib/udev/rules.d/"
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
Conflicts: paste-teks
Replaces: paste-teks
Recommends: wl-clipboard, xclip, xsel, xdotool, ydotool
Description: One-click desktop text snippet manager for Ubuntu 26.10
 Pastex displays .txt files as a hierarchy of categories and folders.
 Click a text file once to copy its contents and send Ctrl+V to the previously
 active app. A preview, search, and built-in editor are also included.
 .
 Supports Ubuntu 26.10 (GNOME Wayland and X11), using wl-copy/xclip/xsel
 for clipboard access and ydotool/xdotool for automatic paste.
 .
 Choose a source folder in the GUI, or create example snippets with
 pastex --export-data. User snippets are stored under
 ~/.local/share/pastex/snippets and can be managed in the GUI.
Homepage: https://github.com/patrickbuhagiar24/paste
EOF

mkdir -p dist
dpkg-deb --build --root-owner-group "$STAGE" "$OUT"
echo "OK: $OUT"
dpkg-deb --info "$OUT" | head -n 20
