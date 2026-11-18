#!/usr/bin/env bash
# Install Pastex directly from a Debian package (without an APT repository).
# Usage: sudo ./scripts/install.sh
set -euo pipefail
cd "$(dirname "$0")/.."

./scripts/build-deb.sh
version="$(python3 -c 'import json; print(json.load(open("extension/manifest.json"))["version"])')"
sudo apt install -y "./dist/pastex_${version}-1_all.deb"
echo "Done. Launch Chrome/Chromium and enable Pastex when the browser asks."
