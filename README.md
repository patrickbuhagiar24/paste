# Pastex

Pastex is a Chrome/Chromium extension for organizing and inserting text
snippets on explicitly allowed sites. Snippets are read and saved directly
in Google Drive; an internet connection and Drive access are required. A derived
local cache keeps the side panel instant, including after a browser restart;
a stale cache is silently refreshed from Drive. While the machine is idle the
extension checks Drive in the background and stores the result only when the
library really changed.

In **Config**, choose one JSON manifest containing download links for
`pastex-sites.json`, `pastex-google-drive.json` and your service-account JSON.
Pastex fetches those links on demand and stores only the URLs; there is no
folder picker. See [the extension README](extension/README.md) for the manifest
format and the security trade-off of a linked service-account key.

## Install on Linux Mint through APT only

Linux Mint is the only supported target. The project ships a Debian package, a deb822 APT source, and a GitHub Pages publishing workflow that builds and signs the archive. The repository URL is:

```text
https://patrickbuhagiar24.github.io/paste/
```

Linux Mint uses the repository suite **`mint`**. The suite is a fixed label for this archive (it is not a Mint codename), so one architecture-independent package serves Cinnamon, MATE and Xfce and every Mint release.

**Availability:** the archive is published automatically to GitHub Pages on pushes to `main` and by manual workflow dispatch; see [APT repository publishing](apt-repo/README.md). The workflow refuses to deploy when the signing secret is missing, so the public archive is always signed.

Obtain the **full 40-character signing-key fingerprint** from a trusted maintainer channel independent of the repository website. Do not use the fingerprint downloaded from the same website as your only verification. The commands below require Bash, `curl`, and `gpg` (provided by `gnupg`). They download the key and the deb822 source file into a private temporary directory, ask for the trusted fingerprint before changing system files, and install the **published** `pastex.sources` verbatim so its URL and suite cannot drift from the signed archive. If verification fails, stop and contact the maintainer.

```bash
(
  set -euo pipefail
  tmpdir=$(mktemp -d)
  trap 'rm -rf "$tmpdir"' EXIT
  curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
    -o "$tmpdir/archive-keyring.gpg" \
    https://patrickbuhagiar24.github.io/paste/archive-keyring.gpg
  fingerprint=$(gpg --batch --with-colons --show-keys "$tmpdir/archive-keyring.gpg" |
    awk -F: '$1 == "pub" { count++; want = 1; next }
      want && $1 == "fpr" { print $10; want = 0 }
      END { if (count != 1) exit 1 }')
  [[ "$fingerprint" =~ ^[A-F0-9]{40}$ ]] || { echo 'Invalid key fingerprint.' >&2; exit 1; }
  printf 'Downloaded key fingerprint: %s\n' "$fingerprint"
  read -r -p 'Enter the independently verified fingerprint: ' expected
  expected=${expected//[[:space:]]/}
  [[ "${expected^^}" == "$fingerprint" ]] || { echo 'Fingerprint mismatch; nothing installed.' >&2; exit 1; }
  curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
    -o "$tmpdir/pastex.sources" \
    https://patrickbuhagiar24.github.io/paste/pastex.sources
  sudo install -d -m 0755 /etc/apt/keyrings
  sudo install -m 0644 "$tmpdir/archive-keyring.gpg" /etc/apt/keyrings/pastex.gpg
  sudo install -m 0644 "$tmpdir/pastex.sources" /etc/apt/sources.list.d/pastex.sources
  sudo apt update
  sudo apt install pastex
)
```

The published source explicitly scopes the verified key to this repository. No unsigned fallback, `Trusted: yes`, or `apt-key` is used. If a command fails, the subshell stops and removes its temporary directory. The keyring belongs in `/etc/apt/keyrings` because that is local administrator configuration; `/usr/share/keyrings` is for keyrings shipped by packages (dpkg overwrites it). APT reads keyrings as the unprivileged `_apt` user, so `ls -l /etc/apt/keyrings/pastex.gpg` must show `-rw-r--r--`.

That is the whole installation. `apt update` reports `Hit:… https://patrickbuhagiar24.github.io/paste mint InRelease`, which proves the metadata is signed and verified.

## Releases and verification

Pushes to `main` publish the signed APT archive. Pushing a `vX.Y.Z` tag after
that merge also creates a GitHub Release with the `.deb`, the signed CRX,
`checksums.txt`, `extension-id.txt`, and the public keyring. The tag must match
the version in `extension/manifest.json` and `debian/changelog`. The release
notes repeat the signing fingerprint so it can be cross-checked independently
of the repository website; see [SECURITY.md](SECURITY.md).

## Development and packaging

```bash
node extension/test/run.js
python3 -m pytest tests/ -q
./scripts/build-deb.sh
```

`scripts/install.sh` is a development shortcut for a locally built `.deb`; end
users should install from the signed repository. Release builds must use a
stable `PASTEX_CRX_PRIVATE_KEY` CI secret; see the extension README. The legacy
desktop application has been removed; it remains in Git history.
