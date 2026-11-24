#!/usr/bin/env bash
# Deploy Pastex for TESTING on a real computer.
#
# Two modes, because there are two situations:
#
#   --local    build the package and install it on this machine, with apt.
#              Use this when Pastex is being tested on the machine that holds
#              the checkout: it is one command, no repository hosting involved.
#
#   --serve    build the package, publish a local APT archive and serve it, so
#              *another* machine on the same network can install it. The
#              script prints a single command to paste on that machine.
#
# Neither mode is a release: both sign the extension with a TEST key stored in
# this checkout, so the extension ID stays the same across test builds and is
# deliberately different from the release ID. The public archive stays the only
# place a user gets the signed, released package (see README.md).
#
# Usage:
#   sudo ./scripts/test-deploy.sh --local [--key FILE] [--yes]
#   ./scripts/test-deploy.sh --serve [--port 8099] [--output DIR] [--key FILE]
#   sudo ./scripts/test-deploy.sh --uninstall
set -euo pipefail
cd "$(dirname "$0")/.."

MODE=""
PORT="8099"
OUTPUT="apt-repo/test-site"
KEY=""
ASSUME_YES="0"

usage() {
  # Print the header comment only: stop at the first line of code.
  awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --local|--serve|--uninstall) MODE="${1#--}"; shift ;;
    --port) PORT="${2:-}"; shift 2 ;;
    --output) OUTPUT="${2:-}"; shift 2 ;;
    --key) KEY="${2:-}"; shift 2 ;;
    --yes|-y) ASSUME_YES="1"; shift ;;
    --help|-h) usage 0 ;;
    *) echo "Unknown argument: $1" >&2; usage 2 ;;
  esac
done

say() { printf 'pastex-test: %s\n' "$*"; }
die() { printf 'pastex-test: error: %s\n' "$*" >&2; exit 1; }

[[ -n "$MODE" ]] || { echo "Choose --local, --serve or --uninstall." >&2; usage 2; }

need_root() {
  [[ "${EUID:-$(id -u)}" -eq 0 ]] || die "this mode installs packages; run it with sudo."
}

require_tools() {
  local missing=0 tool
  for tool in "$@"; do
    command -v "$tool" >/dev/null 2>&1 || { say "missing tool: $tool"; missing=1; }
  done
  [[ "$missing" -eq 0 ]] || die "install the missing tools first (dpkg-dev provides dpkg-scanpackages)."
}

# A test build needs a stable key, otherwise every build gets a new extension ID
# and Chrome treats it as a different, unknown extension. The key is created
# once, kept out of Git by .gitignore (*-crx-key.pem), and is never the release
# key: the release build uses the PASTEX_CRX_PRIVATE_KEY CI secret.
test_key() {
  if [[ -n "$KEY" ]]; then
    [[ -f "$KEY" ]] || die "the key '$KEY' does not exist."
    return
  fi
  KEY="debian/pastex-test-crx-key.pem"
  if [[ ! -f "$KEY" ]]; then
    mkdir -p "$(dirname "$KEY")"
    openssl genrsa -out "$KEY" 2048 2>/dev/null
    chmod 600 "$KEY"
    say "created a test signing key at $KEY (keeps the extension id stable)."
    say "this key is for testing only; it is not the release key."
  fi
}

build() {
  test_key
  PASTEX_CRX_KEY="$KEY" ./scripts/build-deb.sh >/dev/null
  VERSION="$(python3 -c 'import json; print(json.load(open("extension/manifest.json"))["version"])')"
  DEB="dist/pastex_${VERSION}-1_all.deb"
  [[ -f "$DEB" ]] || die "the build did not produce $DEB"
  EXT_ID="$(python3 scripts/pack-crx.py --print-id --key "$KEY")"
  say "built $DEB"
  say "extension id (test build): $EXT_ID"
}

confirm() {
  [[ "$ASSUME_YES" == "1" ]] && return
  local answer
  read -r -p "pastex-test: $1 [y/N] " answer
  [[ "$answer" == "y" || "$answer" == "Y" ]] || { say "nothing was changed."; exit 0; }
}

local_install() {
  need_root
  require_tools dpkg-deb openssl python3 apt-get dpkg
  build
  say "installing with apt (this is how an end user receives the package)."
  apt-get install -y --allow-downgrades "$(pwd)/$DEB" >/dev/null
  hand_back_build_tree
  verify_local
}

# Running as root builds dist/ and build/ as root, which then breaks the next
# ordinary build (rm cannot delete root-owned files). Handing the checkout back
# to the user who invoked sudo keeps the two modes composable.
hand_back_build_tree() {
  local owner="${SUDO_USER:-}"
  [[ -n "$owner" ]] || return 0
  chown -R "$owner": "$(pwd)/dist" "$(pwd)/build" 2>/dev/null || true
}

verify_local() {
  local name
  name="$(dpkg-query -W -f='${Status} ${Version}' pastex 2>/dev/null || true)"
  [[ "$name" == *"install ok installed"* ]] || die "the package is not installed (dpkg says: ${name:-nothing})."
  say "installed: $name"
  for browser_dir in google-chrome chromium; do
    if compgen -G "/usr/share/$browser_dir/extensions/*.json" >/dev/null; then
      say "registered with $browser_dir: $(basename "$(compgen -G "/usr/share/$browser_dir/extensions/*.json" | head -1)")"
    else
      say "warning: no registration file under /usr/share/$browser_dir/extensions (is that browser installed?)"
    fi
  done
  cat <<'DONE'

Next steps on this machine:
  1. Quit the browser completely (every window), then start it again.
     Chrome and Chromium read the registration file only at startup.
  2. Open chrome://extensions (or chrome://extensions/?id=<the id above>)
     and enable Pastex when the browser offers it.
  3. Register this extension id and its redirect URL in your Google OAuth
     client (see extension/README.md, "Create the Google OAuth client").
  4. Open the side panel, click Config, and pick your configuration file
     (one JSON file of Drive links, any name).

Remove it again:
  sudo ./scripts/test-deploy.sh --uninstall
DONE
}

serve() {
  require_tools dpkg-deb dpkg-scanpackages openssl python3 curl
  build
  rm -rf "$OUTPUT"
  mkdir -p "$OUTPUT"
  PASSTEX_SIGNED=0 ./scripts/build-apt-repository.sh dist "$OUTPUT" stable >/dev/null

  # The archive is unsigned, so the machine that installs it must be told to
  # trust the host. That is acceptable only on a network you control, and the
  # generated installer says so before it changes anything.
  local addresses address base
  addresses="$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+\.' | grep -vE '^127\.' || true)"
  [[ -n "$addresses" ]] || addresses="127.0.0.1"
  base="http://$(printf '%s\n' "$addresses" | head -1):$PORT/"

  ( cd "$OUTPUT" && sha256sum pool/main/p/pastex/*.deb > SHA256SUMS )

  cat >"$OUTPUT/install-from-here.sh" <<'INSTALLER'
#!/bin/sh
# Install the Pastex test build from this machine's local archive.
#
# Usage: sh install-from-here.sh [ARCHIVE_URL]
# Needs root. Nothing else is downloaded, and no repository is added
# permanently: the source file is removed again once the package is installed.
set -eu
BASE="${1:-__BASE__}"
SUITE="__SUITE__"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this with sudo: sudo sh install-from-here.sh $BASE" >&2
  exit 1
fi

echo "Pastex TEST build from $BASE"
echo "This archive is NOT the signed release archive."
echo "It is trusted only because you are installing it on your own network."
printf 'Continue? [y/N] '
read -r answer
case "$answer" in
  y|Y) ;;
  *) echo "Nothing was changed."; exit 0 ;;
esac

# Fetch the package and check it against the checksums served next to it: that
# catches a truncated or half-written download. It cannot prove the file was not
# replaced, which is exactly why the public release uses a signed archive.
rel="$(curl -fsSL "$BASE/SHA256SUMS" | awk '{print $2}' | head -1)"
[ -n "$rel" ] || { echo "No package listed at $BASE/SHA256SUMS" >&2; exit 1; }
mkdir -p "$WORK/$(dirname "$rel")"
curl -fsSL "$BASE/$rel" -o "$WORK/$rel"
curl -fsSL "$BASE/SHA256SUMS" -o "$WORK/SHA256SUMS"
( cd "$WORK" && sha256sum -c SHA256SUMS ) >/dev/null 2>&1 || {
  echo "Checksum mismatch: the download does not match $BASE/SHA256SUMS" >&2
  exit 1
}
echo "checksum ok: $rel"

# Use this archive only: -o Dir::Etc::sourcelist keeps every other apt source
# out of the run, so "apt update" here cannot fail because of an unrelated
# repository the machine already has.
SOURCE_FILE="/etc/apt/sources.list.d/pastex-test.list"
printf 'deb [trusted=yes] %s %s main\n' "$BASE" "$SUITE" >"$SOURCE_FILE"
chmod 0644 "$SOURCE_FILE"
cleanup() { rm -f "$SOURCE_FILE"; }
trap 'cleanup; rm -rf "$WORK"' EXIT
APT_ARGS="-o Dir::Etc::sourcelist=$SOURCE_FILE -o Dir::Etc::sourceparts=/dev/null"
# shellcheck disable=SC2086
apt-get $APT_ARGS update >/dev/null
if ! apt-get $APT_ARGS install -y --allow-downgrades pastex; then
  echo "apt could not install from the test archive; trying the package directly" >&2
  dpkg -i "$WORK/$rel" || { echo "Installation failed." >&2; exit 1; }
fi
cleanup

echo
echo "Installed. Now quit the browser completely and start it again, then"
echo "enable Pastex in chrome://extensions when the browser asks."
INSTALLER
  sed -i "s#__BASE__#${base}#; s#__SUITE__#stable#" "$OUTPUT/install-from-here.sh"
  chmod 0755 "$OUTPUT/install-from-here.sh"

  cat <<DONE

Test archive ready: $(pwd)/$OUTPUT

Install it on another machine on this network with:
    curl -fsSL ${base}install-from-here.sh | sudo sh

or open ${base}install-from-here.sh and copy the commands by hand.
Addresses this machine answers on:
$(printf '%s\n' "$addresses" | sed 's/^/    /')

The archive is unsigned (there is no gpg on this machine), and the generated
installer removes the extra apt source again after installing. This is a test
path only: the public archive is signed and never asks a machine to trust a
host.
DONE

  say "serving on 0.0.0.0:$PORT (Ctrl+C to stop)"
  cd "$OUTPUT"
  exec python3 -m http.server "$PORT" --bind 0.0.0.0
}

uninstall() {
  need_root
  require_tools apt-get dpkg
  confirm "remove the pastex package, its browser registration and its files?"
  # An already-absent package is the desired end state, so it is not an error.
  apt-get remove -y --purge pastex >/dev/null 2>&1 || true
  say "package removed."
  if [[ -x ./scripts/reset-pastex-state.sh ]]; then
    say "the extension's cached state is cleared separately, and Drive is never touched:"
    say "  ./scripts/reset-pastex-state.sh --apply"
  fi
  cat <<'DONE'

The extension itself may still be listed in chrome://extensions until the
browser restarts; the package no longer registers it, so a restart removes it.
Your snippets in Google Drive were never touched.
DONE
}

case "$MODE" in
  local) local_install ;;
  serve) serve ;;
  uninstall) uninstall ;;
esac
