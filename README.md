# Pastex

Pastex is a Google Chrome extension for inserting text snippets on selected
websites. It is intended for one device and stores its library in your own
Google Drive. Linux Mint and Chrome 142 or newer are required. An internet
connection is needed for Drive access.

## Install on Linux Mint

Obtain the full APT signing-key fingerprint through an independent trusted
maintainer channel before running these commands. Do not trust a fingerprint
obtained only from the repository website. The commands verify the downloaded
key before installing the signed `stable` source and `/etc/apt/keyrings/pastex.gpg`.
They require Bash, curl and GnuPG.

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

Enable Pastex in Chrome and restart the browser. For OAuth and configuration,
follow the [extension setup guide](extension/README.md). For archive maintenance,
see the [APT repository guide](apt-repo/README.md).

Update with `sudo apt update && sudo apt install --only-upgrade pastex`.

## Security and verification

The APT repository is signed; compare its fingerprint through independent
channels, including a trusted maintainer channel. The GitHub Release contains
checksums and the extension ID. Do not use unsigned APT sources. To verify
release files, run `sha256sum -c checksums.txt --ignore-missing` in the directory
containing the downloaded files.

The extension requests full Google Drive access. Its configuration contains a
client ID and may contain a client secret; protect the configuration and your
browser profile. A refresh token and snippet cache are stored locally. The site
allowlist controls where Pastex injects scripts, but the manifest requests broad
HTTP(S) host permissions.

## How Pastex works

The side panel lists snippets and queues edits locally. The worker sends pending
changes when an allowed tab is closed, on browser startup, or via Sync when no
allowed site is active. Drive remains the source of truth after synchronization.
The in-page launcher opens the panel on allowed sites. Every Drive folder is
checked for changes; cached bodies avoid unnecessary downloads.

| Component | Files |
| --- | --- |
| Panel and page insertion | `extension/sidepanel.*`, `extension/content.*` |
| Queue and cache | `extension/queue.js`, `extension/cache.js` |
| Drive and library | `extension/drive.js`, `extension/library.js` |
| Site and configuration | `extension/sites.js`, `extension/config-source.js`, `extension/config-cache-reader.js` |
| Worker and package | `extension/background.js`, `scripts/`, `.github/workflows/` |

## Where order and folded folders live

Order and folded state are stored in the Drive file named by `pastex-index`.
Snippet bodies are separate text files in the configured library folder.

## Development policy

Keep Content (Drive snippets) **out of bounds** for cleanup; Presentation metadata
(order and folded state) and Cache are separate. Do not discard queued changes.
Use one implementation, keep tests with behavior, and document user-visible
removals with `BREAKING:` in the changelog. Living project files use English.

Run tests and build locally:

```bash
node extension/test/run.js
python3 -m pytest tests/ -q
./scripts/build-deb.sh
```

A build without `PASTEX_CRX_KEY` uses a temporary signing key and a different
extension ID; it is for testing only. The workflow publishes from `main` on a
push and from a selected branch on manual dispatch. A later deployment can
replace the published archive. Release builds require the signing secrets.
