# Pastex

Pastex is a Chrome/Chromium extension for organizing and inserting text
snippets on explicitly allowed sites. Snippets are read and saved directly
in Google Drive; an internet connection and Drive access are required. A temporary
browser-session cache makes repeat openings faster; refresh to pick up external changes.

Choose a configuration folder in **Config** containing `pastex-sites.json`,
`pastex-google-drive.json` and your service-account JSON. Pastex reads these
files directly; browser permission may need to be granted again after restart.
See [the extension README](extension/README.md) for setup and security details.

## Install on Linux Mint through APT only

Linux Mint is the only supported target. The project ships a Debian package, a deb822 APT source, and a GitHub Pages publishing workflow that builds and signs the archive. The repository URL is:

```text
https://patrickbuhagiar24.github.io/paste/
```

Linux Mint uses the repository suite **`mint`**. The suite is a fixed label for this archive (it is not a Mint codename), so one architecture-independent package serves Cinnamon, MATE and Xfce and every Mint release.

**Availability:** these commands work after the maintainer completes [APT repository publishing](apt-repo/README.md), including enabling GitHub Pages under **Settings → Pages → Build and deployment → Source: GitHub Actions**. Once configured, pushes to `main` build and publish the signed repository automatically; the workflow can also be started manually. Adding packaging files to the source tree does not itself configure Pages or create a signing key.

Obtain the **full 40-character signing-key fingerprint** from a trusted maintainer channel independent of the repository website. Do not use the fingerprint downloaded from the same website as your only verification. The commands below require Bash, `curl`, and `gpg` (provided by `gnupg`). They download the key into a private temporary directory and ask for the trusted fingerprint before changing system files. If it is unavailable or does not match, stop and contact the maintainer.

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
  cat > "$tmpdir/pastex.sources" <<'EOF'
Types: deb
URIs: https://patrickbuhagiar24.github.io/paste/
Suites: mint
Components: main
Architectures: all
Signed-By: /etc/apt/keyrings/pastex.gpg
EOF
  sudo install -d -m 0755 /etc/apt/keyrings
  sudo install -m 0644 "$tmpdir/archive-keyring.gpg" /etc/apt/keyrings/pastex.gpg
  sudo install -m 0644 "$tmpdir/pastex.sources" /etc/apt/sources.list.d/pastex.sources
  sudo apt update
  sudo apt install pastex
)
```

The local source explicitly scopes the verified key to this repository. No unsigned fallback, `Trusted: yes`, or `apt-key` is used. If a command fails, the subshell stops and removes its temporary directory. The keyring belongs in `/etc/apt/keyrings` because that is local administrator configuration; `/usr/share/keyrings` is for keyrings shipped by packages (dpkg overwrites it). APT reads keyrings as the unprivileged `_apt` user, so `ls -l /etc/apt/keyrings/pastex.gpg` must show `-rw-r--r--`.

That is the whole installation. `apt update` reports `Hit:… https://patrickbuhagiar24.github.io/paste mint InRelease`, which proves the metadata is signed and verified.

## Development and packaging

```bash
node extension/test/run.js
python3 -m pytest tests/ -q
./scripts/build-deb.sh
```

Release builds must use a stable `PASTEX_CRX_PRIVATE_KEY` CI secret; see the extension README.
The legacy desktop application has been removed; it remains in Git history.
