#!/usr/bin/env bash
# Buat repo APT lokal dari .deb paste-teks dan daftarkan sebagai apt source.
#
# Pakai:
#   ./scripts/setup-apt-repo.sh            # bangun .deb + repo lokal (file://)
#   ./scripts/setup-apt-repo.sh --serve    # + jalankan server HTTP di port 8080
#   GPG_KEY=... ./scripts/setup-apt-repo.sh  # tanda tangani Release dengan GPG
#
# Setelah selesai:
#   sudo apt update && sudo apt install paste-teks
set -euo pipefail
cd "$(dirname "$0")/.."

./scripts/build-deb.sh

REPO_DIR="apt-repo/dist"
mkdir -p "$REPO_DIR"
cp dist/*.deb "$REPO_DIR/"

# Generate Packages (+ Packages.gz) — butuh dpkg-scanpackages (dpkg-dev).
if command -v dpkg-scanpackages >/dev/null 2>&1; then
  (cd "$REPO_DIR" && dpkg-scanpackages --arch all . > Packages)
  gzip -9 -k -f "$REPO_DIR/Packages"
else
  echo "Peringatan: dpkg-scanpackages tidak ditemukan (pasang dpkg-dev)." >&2
  exit 1
fi

# Generate Release (butuh apt-ftparchive dari apt-utils; fallback manual).
if command -v apt-ftparchive >/dev/null 2>&1; then
  cat > "$REPO_DIR/apt-ftparchive.conf" <<'EOF'
APT::FTPArchive::Release::Origin "Paste Teks";
APT::FTPArchive::Release::Label "Paste Teks";
APT::FTPArchive::Release::Suite "stable";
APT::FTPArchive::Release::Codename "stable";
APT::FTPArchive::Release::Architectures "all";
APT::FTPArchive::Release::Components "main";
APT::FTPArchive::Release::Description "Repo APT Paste Teks untuk Ubuntu 26.10";
EOF
  (cd "$REPO_DIR" && apt-ftparchive -c apt-ftparchive.conf release . > Release)
else
  echo "Peringatan: apt-ftparchive tidak ditemukan (pasang apt-utils); Release dibuat manual." >&2
  {
    echo "Origin: Paste Teks"
    echo "Label: Paste Teks"
    echo "Suite: stable"
    echo "Codename: stable"
    echo "Architectures: all"
    echo "Components: main"
    echo "Description: Repo APT Paste Teks untuk Ubuntu 26.10"
    echo "Date: $(date -Ru)"
  } > "$REPO_DIR/Release"
fi

# Tandatangani Release bila GPG_KEY diisi.
if [ -n "${GPG_KEY:-}" ]; then
  gpg --default-key "$GPG_KEY" --armor --detach-sign -o "$REPO_DIR/Release.gpg" "$REPO_DIR/Release"
  gpg --default-key "$GPG_KEY" --clearsign -o "$REPO_DIR/InRelease" "$REPO_DIR/Release"
  echo "Release ditandatangani dengan kunci $GPG_KEY."
  echo "Ekspor kunci publik: gpg --armor --export $GPG_KEY > paste-teks.gpg"
fi

REPO_ABS="$(pwd)/$REPO_DIR"
echo "Repo APT siap di: $REPO_ABS"
echo
echo "Tambahkan source (file://):"
echo "  echo \"deb [trusted=yes] file://$REPO_ABS ./\" | sudo tee /etc/apt/sources.list.d/paste-teks.list"
if [ -n "${GPG_KEY:-}" ]; then
  echo "Atau dengan tanda tangan (lebih aman):"
  echo "  gpg --armor --export $GPG_KEY | sudo gpg --dearmor -o /usr/share/keyrings/paste-teks.gpg"
  echo "  echo \"deb [signed-by=/usr/share/keyrings/paste-teks.gpg] file://$REPO_ABS ./\" | sudo tee /etc/apt/sources.list.d/paste-teks.list"
fi
echo
echo "Lalu: sudo apt update && sudo apt install paste-teks"

if [ "${1:-}" = "--serve" ]; then
  echo "Menjalankan server HTTP di http://localhost:8080 (Ctrl+C untuk berhenti)..."
  echo "Di mesin lain tambahkan: deb [trusted=yes] http://<IP>:8080 ./"
  (cd "$REPO_DIR" && python3 -m http.server 8080)
fi
