#!/usr/bin/env bash
# Build the Pastex Debian package without debhelper (requires dpkg-deb only).
# Output: dist/pastex_<version>_all.deb
#
# The payload is the Chrome extension in extension/: this script packs it
# into a signed .crx and registers it for auto-detection by Chrome and
# Chromium through /usr/share/<browser>/extensions/<id>.json.
#
# The CRX signing key sets the extension id, so releases must reuse one
# stable key: pass its path in PASTEX_CRX_KEY (release builds use the
# PASTEX_CRX_PRIVATE_KEY CI secret). Without it an ephemeral key is generated and
# the build gets a fresh id — fine for testing, not for release.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="$(python3 -c 'import json; print(json.load(open("extension/manifest.json"))["version"])')"
PKG="pastex"
ARCH="all"
STAGE="build/deb"
OUT="dist/${PKG}_${VERSION}-1_${ARCH}.deb"

CRX_KEY="${PASTEX_CRX_KEY:-}"
if [ -z "$CRX_KEY" ] && [ -f debian/crx-key.pem ]; then
  CRX_KEY="debian/crx-key.pem"
fi
if [ -n "$CRX_KEY" ] && [ ! -f "$CRX_KEY" ]; then
  echo "warning: the CRX key '$CRX_KEY' does not exist; using an ephemeral key." >&2
  CRX_KEY=""
fi
if [ -z "$CRX_KEY" ]; then
  echo "warning: no CRX signing key (PASTEX_CRX_KEY); using an ephemeral key." >&2
  echo "warning: this build gets a fresh extension id — fine for testing, not for release." >&2
  CRX_KEY="$(mktemp -d)/ephemeral-crx-key.pem"
  openssl genrsa -out "$CRX_KEY" 2048 2>/dev/null
fi

rm -rf "$STAGE"
mkdir -p "$STAGE/DEBIAN" \
  "$STAGE/usr/share/pastex" \
  "$STAGE/usr/share/google-chrome/extensions" \
  "$STAGE/usr/share/chromium/extensions" \
  "$STAGE/usr/share/doc/$PKG"

# Unpacked extension (auditable; also enables manual loading from source).
cp -r extension "$STAGE/usr/share/pastex/extension"
rm -rf "$STAGE/usr/share/pastex/extension/test"

# Signed CRX, verified before it is packed.
EXT_ID="$(python3 scripts/pack-crx.py --key "$CRX_KEY" --src extension --out "$STAGE/usr/share/pastex/pastex.crx")"
python3 scripts/pack-crx.py --verify "$STAGE/usr/share/pastex/pastex.crx" >/dev/null
echo "extension id: $EXT_ID"

# Auto-detection: Chrome and Chromium pick these JSON files up on launch
# and offer to enable the extension with one click.
for browser_dir in google-chrome chromium; do
  printf '{"external_crx": "/usr/share/pastex/pastex.crx", "external_version": "%s"}\n' \
    "$VERSION" >"$STAGE/usr/share/$browser_dir/extensions/$EXT_ID.json"
done

install -m 644 extension/README.md "$STAGE/usr/share/doc/$PKG/README.md"
install -m 644 LICENSE "$STAGE/usr/share/doc/$PKG/copyright"
install -m 755 debian/postinst "$STAGE/DEBIAN/postinst"
install -m 755 debian/postrm "$STAGE/DEBIAN/postrm"
gzip -9 -n "$STAGE/usr/share/doc/$PKG/README.md" 2>/dev/null || true

# Quoted heredoc on purpose: an unquoted one lets the shell expand backticks and
# "$" inside the Description, which silently deleted the command text from the
# published 1.10.0 package. Only the placeholders below are substituted.
cat > "$STAGE/DEBIAN/control" <<'EOF'
Package: @PKG@
Version: @VERSION@-1
Section: web
Priority: optional
Architecture: @ARCH@
Depends: python3
Maintainer: Patrick Buhagiar <patrickbuhagiar24@users.noreply.github.com>
Conflicts: paste-teks
Replaces: paste-teks
Recommends: google-chrome-stable | chromium
Description: One-click text snippet manager for Google Chrome
 Pastex is a Chrome extension (Manifest V3): a snippet library in the
 Side Panel and an in-page launcher at the cursor, using Google Drive
 through a service account.
 .
 Installing the package registers the extension with Chrome and
 Chromium: on the next launch the browser offers to enable Pastex
 with one click. No daemons, groups, or system services are used.
 .
 Snippets are read and written as .txt files directly in Google Drive.
 Setup and required credentials are described in the README under
 /usr/share/doc/pastex/.
Homepage: https://github.com/patrickbuhagiar24/paste
EOF

sed -i \
  -e "s|@PKG@|$PKG|g" \
  -e "s|@VERSION@|$VERSION|g" \
  -e "s|@ARCH@|$ARCH|g" \
  "$STAGE/DEBIAN/control"

mkdir -p dist
dpkg-deb --build --root-owner-group "$STAGE" "$OUT"
echo "OK: $OUT"
dpkg-deb --info "$OUT"
