#!/usr/bin/env bash
# Pasang paste-teks langsung dari .deb (tanpa repo APT).
# Pakai: sudo ./scripts/install.sh
set -euo pipefail
cd "$(dirname "$0")/.."

./scripts/build-deb.sh
sudo apt install -y ./dist/*.deb
echo "Selesai. Jalankan: paste-teks  (atau cari 'Paste Teks' di menu Aplikasi)"
