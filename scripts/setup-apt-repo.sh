#!/usr/bin/env bash
# Build a local Pastex APT archive using the same layout as GitHub Pages.
#
# Usage:
#   ./scripts/setup-apt-repo.sh                # build the package and local archive
#   ./scripts/setup-apt-repo.sh --serve        # also serve it on port 8080
#   APT_SIGNING_KEY_ID=... GPG_PASSPHRASE=... ./scripts/setup-apt-repo.sh
#
# The archive is written to apt-repo/dist and contains:
#   index.html, pool/main/p/pastex/*.deb, dists/mint/{Release,Packages}
# plus InRelease, Release.gpg and archive-keyring.* when signing is configured.
set -euo pipefail
cd "$(dirname "$0")/.."

SUITE="${APT_SUITE:-mint}"

./scripts/build-deb.sh

rm -rf apt-repo/dist
mkdir -p apt-repo/dist

# A signed archive publishes pastex.sources; otherwise the unsigned variant is
# used so APT does not look for a keyring that was never produced.
if [ -n "${APT_SIGNING_KEY_ID:-}" ]; then
  PASSTEX_SIGNED=1 ./scripts/build-apt-repository.sh dist apt-repo/dist "$SUITE"
  ./scripts/sign-apt-repository.sh apt-repo/dist "$SUITE"
else
  ./scripts/build-apt-repository.sh dist apt-repo/dist "$SUITE"
fi

REPO_ABS="$(pwd)/apt-repo/dist"
echo "APT archive is ready at: $REPO_ABS"
echo
echo "Add the local archive as a deb822 source:"
if [ -n "${APT_SIGNING_KEY_ID:-}" ]; then
  echo "  sudo install -d -m 0755 /etc/apt/keyrings"
  echo "  sudo cp $REPO_ABS/archive-keyring.gpg /etc/apt/keyrings/pastex.gpg"
  echo "  sudo sed \"s#https://patrickbuhagiar24.github.io/paste/#file://$REPO_ABS/#\" \\"
  echo "    $REPO_ABS/pastex.sources | sudo tee /etc/apt/sources.list.d/pastex.sources"
else
  echo "  sudo sed \"s#https://patrickbuhagiar24.github.io/paste/#file://$REPO_ABS/#\" \\"
  echo "    $REPO_ABS/pastex-unsigned.sources | sudo tee /etc/apt/sources.list.d/pastex.sources"
fi
echo
echo "  sudo apt update && sudo apt install pastex"

if [ "${1:-}" = "--serve" ]; then
  echo "Serving the archive at http://localhost:8080 (press Ctrl+C to stop)..."
  (cd apt-repo/dist && python3 -m http.server 8080)
fi
