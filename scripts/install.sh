#!/usr/bin/env bash
# DEVELOPMENT ONLY: build a .deb from this checkout and install it with apt.
# End users must use the signed APT repository documented in README.md; a
# locally built package gets an ephemeral extension ID unless PASTEX_CRX_KEY
# points at the release key.
# Usage: sudo ./scripts/install.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo "warning: development-only local install; use the signed APT repository for production." >&2
./scripts/build-deb.sh
version="$(python3 -c 'import json; print(json.load(open("extension/manifest.json"))["version"])')"
sudo apt install -y "./dist/pastex_${version}-1_all.deb"
echo "Done. Launch Chrome/Chromium and enable Pastex when the browser asks."
