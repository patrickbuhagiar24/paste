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
#
# The archive is reproducible: every timestamp comes from SOURCE_DATE_EPOCH
# (the last commit time by default), so two builds of the same checkout with
# the same key are byte-identical and the published checksums.txt can be
# verified by rebuilding.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="$(python3 -c 'import json; print(json.load(open("extension/manifest.json"))["version"])')"
PKG="pastex"
ARCH="all"
STAGE="build/deb"
OUT="dist/${PKG}_${VERSION}-1_${ARCH}.deb"

# Debian's reproducible-builds convention. dpkg-deb and gzip both honour it, and
# the staging tree below is touched to match, so the ar and tar headers are fixed.
if [ -z "${SOURCE_DATE_EPOCH:-}" ]; then
  SOURCE_DATE_EPOCH="$(git log -1 --format=%ct 2>/dev/null || true)"
  # No git (release tarball, shallow export): a fixed date beats "now".
  SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-315532800}"
fi
case "$SOURCE_DATE_EPOCH" in
  ''|*[!0-9]*) echo "error: SOURCE_DATE_EPOCH must be a unix timestamp" >&2; exit 2 ;;
esac
export SOURCE_DATE_EPOCH

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
# Published alongside the archive so users can compare the ID Chrome shows.

# Auto-detection: Chrome and Chromium pick these JSON files up on launch
# and offer to enable the extension with one click.
for browser_dir in google-chrome chromium; do
  printf '{"external_crx": "/usr/share/pastex/pastex.crx", "external_version": "%s"}\n' \
    "$VERSION" >"$STAGE/usr/share/$browser_dir/extensions/$EXT_ID.json"
done

install -m 644 extension/README.md "$STAGE/usr/share/doc/$PKG/README.md"
install -m 644 LICENSE "$STAGE/usr/share/doc/$PKG/copyright"
# Debian policy: every package ships its changelog, so `apt changelog pastex`
# and /usr/share/doc/pastex/changelog.Debian.gz both work. Without it the
# package's own history never reaches the user (lintian: no-changelog).
gzip -9 -n -c debian/changelog >"$STAGE/usr/share/doc/$PKG/changelog.Debian.gz"
chmod 0644 "$STAGE/usr/share/doc/$PKG/changelog.Debian.gz"
install -m 755 debian/postinst "$STAGE/DEBIAN/postinst"
install -m 755 debian/postrm "$STAGE/DEBIAN/postrm"
gzip -9 -n "$STAGE/usr/share/doc/$PKG/README.md" 2>/dev/null || true

# Installed-Size lets apt size the install; Multi-Arch: foreign marks the
# payload as data (an unpacked extension plus a .crx), so it can satisfy a
# dependency from any architecture. deb822 has no comments, so these two
# fields are documented here rather than inside the file.
#
# Quoted heredoc on purpose: an unquoted one lets the shell expand backticks and
# "$" inside the Description, which silently deleted the command text from the
# published 1.10.0 package. Only the placeholders below are substituted.
# No Depends field: this script does not run debhelper substitutions, and the
# extension package needs no runtime packages beyond the browser.
cat > "$STAGE/DEBIAN/control" <<'EOF'
Package: @PKG@
Version: @VERSION@-1
Section: web
Priority: optional
Architecture: @ARCH@
Installed-Size: @SIZE@
Multi-Arch: foreign
Maintainer: Patrick Buhagiar <patrickbuhagiar24@users.noreply.github.com>
Conflicts: paste-teks
Replaces: paste-teks
Provides: paste-teks
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

# Installed size in KiB, excluding the control area, the way dpkg-gencontrol
# computes it.
INSTALLED_SIZE="$(du -sk --exclude=DEBIAN "$STAGE" | cut -f1)"

sed -i \
  -e "s|@PKG@|$PKG|g" \
  -e "s|@VERSION@|$VERSION|g" \
  -e "s|@ARCH@|$ARCH|g" \
  -e "s|@SIZE@|$INSTALLED_SIZE|g" \
  "$STAGE/DEBIAN/control"

# md5sums lets `dpkg -V pastex` notice a corrupted or locally edited file.
# Paths are relative to the filesystem root, without a leading "./".
(
  cd "$STAGE"
  find . -path ./DEBIAN -prune -o -type f -print |
    sed 's|^\./||' |
    LC_ALL=C sort |
    while IFS= read -r file; do md5sum "$file"; done >DEBIAN/md5sums
)
chmod 0644 "$STAGE/DEBIAN/md5sums"

# One timestamp for the whole tree: the only reason two builds used to differ.
find "$STAGE" -depth -exec touch --no-dereference --date="@$SOURCE_DATE_EPOCH" {} +

mkdir -p dist
printf '%s\n' "$EXT_ID" >dist/pastex-id.txt
dpkg-deb --build --root-owner-group --uniform-compression "$STAGE" "$OUT"
echo "OK: $OUT"
dpkg-deb --info "$OUT"
