#!/usr/bin/env bash
# Build a local Pastex APT repository and register it as an APT source.
#
# Usage:
#   ./scripts/setup-apt-repo.sh            # build the package and local repo
#   ./scripts/setup-apt-repo.sh --serve    # also serve on port 8080
#   GPG_KEY=... ./scripts/setup-apt-repo.sh  # sign the Release with GPG
#
# When complete:
#   sudo apt update && sudo apt install pastex
set -euo pipefail
cd "$(dirname "$0")/.."

./scripts/build-deb.sh

REPO_DIR="apt-repo/dist"
mkdir -p "$REPO_DIR"
cp dist/*.deb "$REPO_DIR/"

# Generate Packages and Packages.gz (requires dpkg-scanpackages from dpkg-dev).
if command -v dpkg-scanpackages >/dev/null 2>&1; then
  (cd "$REPO_DIR" && dpkg-scanpackages --arch all . > Packages)
  gzip -9 -k -f "$REPO_DIR/Packages"
else
  echo "Warning: dpkg-scanpackages was not found (install dpkg-dev)." >&2
  exit 1
fi

# Generate Release (needs apt-ftparchive from apt-utils; manual fallback).
if command -v apt-ftparchive >/dev/null 2>&1; then
  cat > "$REPO_DIR/apt-ftparchive.conf" <<'EOF'
APT::FTPArchive::Release::Origin "Pastex";
APT::FTPArchive::Release::Label "Pastex";
APT::FTPArchive::Release::Suite "stable";
APT::FTPArchive::Release::Codename "stable";
APT::FTPArchive::Release::Architectures "all";
APT::FTPArchive::Release::Components "main";
APT::FTPArchive::Release::Description "Pastex APT repository for Ubuntu 26.10";
EOF
  (cd "$REPO_DIR" && apt-ftparchive -c apt-ftparchive.conf release . > Release)
else
  echo "Warning: apt-ftparchive was not found (install apt-utils); creating Release manually." >&2
  {
    echo "Origin: Pastex"
    echo "Label: Pastex"
    echo "Suite: stable"
    echo "Codename: stable"
    echo "Architectures: all"
    echo "Components: main"
    echo "Description: Pastex APT repository for Ubuntu 26.10"
    echo "Date: $(date -Ru)"
  } > "$REPO_DIR/Release"
fi

# Sign the Release when GPG_KEY is set.
if [ -n "${GPG_KEY:-}" ]; then
  gpg --default-key "$GPG_KEY" --armor --detach-sign -o "$REPO_DIR/Release.gpg" "$REPO_DIR/Release"
  gpg --default-key "$GPG_KEY" --clearsign -o "$REPO_DIR/InRelease" "$REPO_DIR/Release"
  echo "Release signed with key $GPG_KEY."
  echo "Export the public key: gpg --armor --export $GPG_KEY > pastex.gpg"
fi

REPO_ABS="$(pwd)/$REPO_DIR"
echo "APT repository is ready at: $REPO_ABS"
echo
echo "Add the source (file://):"
echo "  echo \"deb [trusted=yes] file://$REPO_ABS ./\" | sudo tee /etc/apt/sources.list.d/pastex.list"
if [ -n "${GPG_KEY:-}" ]; then
  echo "Or use signed metadata (recommended):"
  echo "  gpg --armor --export $GPG_KEY | sudo gpg --dearmor -o /usr/share/keyrings/pastex.gpg"
  echo "  echo \"deb [signed-by=/usr/share/keyrings/pastex.gpg] file://$REPO_ABS ./\" | sudo tee /etc/apt/sources.list.d/pastex.list"
fi
echo
echo "Then run: sudo apt update && sudo apt install pastex"

if [ "${1:-}" = "--serve" ]; then
  echo "Serving HTTP at http://localhost:8080 (press Ctrl+C to stop)..."
  echo "On another machine, add: deb [trusted=yes] http://<IP>:8080 ./"
  (cd "$REPO_DIR" && python3 -m http.server 8080)
fi
