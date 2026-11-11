#!/usr/bin/env bash
# Install Pastex directly from a Debian package (without an APT repository).
# Usage: sudo ./scripts/install.sh
set -euo pipefail
cd "$(dirname "$0")/.."

./scripts/build-deb.sh
sudo apt install -y ./dist/*.deb
echo "Done. Run `pastex` or find Pastex in the Applications menu."
