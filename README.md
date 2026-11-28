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

Enable Pastex in Chrome and restart the browser. Follow the [setup guide](extension/README.md)
to configure Google OAuth, Drive and allowed websites. For archive details, see
the [APT repository guide](apt-repo/README.md).

To update an existing installation:

```bash
sudo apt update && sudo apt install --only-upgrade pastex
```

Quit Chrome completely and reopen it after updating.

## How Pastex works

- Click a snippet to insert it with a final newline. The left-arrow button on a
  row inserts it without trailing newlines; the toolbar toggle changes normal
  clicks. Internal newlines are preserved.
- Edits are saved locally and queued for Drive. They are sent when an allowed
  tab is closed, on browser startup, or with Sync when no allowed site is active.
  The first load after setup may take longer while the library is fetched.
- If a text edit conflicts with Drive, use the review button by the status to
  compare and choose a version. The copy button exports pending changes as JSON;
  keep the copy private. Do not clear extension storage while changes are pending.
- Drive writes are not guaranteed atomic: avoid editing the same file outside
  Pastex while a change is pending. Back up important snippets in Drive.

## Security and verification

The extension requests full Google Drive access and broad HTTP(S) host
permissions, while its allowlist controls where it injects scripts. The browser
profile stores the configuration, refresh token and snippet cache locally; protect
access to it and do not share tokens or client secrets.

The APT archive is signed. Verify its signing-key fingerprint through an
independent trusted maintainer channel, not just the repository website. Do not
use an unsigned APT source. Tagged GitHub Releases include verification files;
manual branch deployments publish the archive without creating a release. Verify
downloaded release files with `sha256sum -c checksums.txt --ignore-missing`.

## Where order and folded folders live

Snippet bodies are text files in the configured Drive folder. Order and folded
state are stored in the file named by `pastex-index`.

## Development policy

Keep Content (Drive snippets) out of bounds for cleanup; Presentation metadata
and Cache are separate. Never discard queued changes. Document user-visible
removals with `BREAKING:` in the changelog and keep tests with behavior.


```bash
node extension/test/run.js
python3 -m pytest tests/ -q
./scripts/build-deb.sh
```

A local build without `PASTEX_CRX_KEY` uses a temporary signing key and a new
extension ID; it is for testing only. Release builds require signing secrets.
The optional `node scripts/benchmark-library.js` benchmark uses synthetic
in-memory Drive responses, not network or browser timings. See the
[setup guide](extension/README.md) for usage and troubleshooting.
