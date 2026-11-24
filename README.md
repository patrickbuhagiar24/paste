# Pastex

Pastex is a Chrome/Chromium extension for organizing and inserting text
snippets on explicitly allowed sites. Snippets are read and saved directly
in Google Drive; an internet connection and Drive access are required. A derived
local cache keeps the side panel instant, including after a browser restart;
a stale cache is silently refreshed from Drive. While the machine is idle the
extension checks Drive in the background and stores the result only when the
library really changed. Bodies are cached by the SHA-256 of their content and
every node keeps the signature of the metadata Drive reported for it, so a
refresh only downloads the branch that really changed. The order and the
collapse state of the library live in one label on the root folder. The check
interval grows while Drive keeps reporting the same library and returns to
its fast value as soon as a change is seen; one status line reports the result
and **↻ Sync** sends waiting changes by hand.

Pastex reaches Drive as **you**, through Google OAuth: no service account and no
key file. Each installation registers one OAuth client and records its public
client ID in `pastex-oauth.json`; the [step-by-step guide in the extension
README](extension/README.md#create-the-google-oauth-client) covers the Google
Cloud project, the consent screen, the redirect URL and the client ID, in about
ten minutes. Because the token belongs to your account, the files Pastex creates
are yours, sit in your own Drive storage, and open normally in Drive.

In **Config**, pick a local folder holding `pastex-google-drive.json`,
`pastex-sites.json` and `pastex-oauth.json`. Pastex reads that folder once,
validates every file, and then works from its own cache; nothing in the folder is
ever uploaded or published, and it contains no credential. See
[the extension README](extension/README.md) for the three files and their
contents.

## Install on Linux Mint through APT only

Linux Mint is the only supported target. The project ships a Debian package, a deb822 APT source, and a GitHub Pages publishing workflow that builds and signs the archive. The repository URL is:

```text
https://patrickbuhagiar24.github.io/paste/
```

Linux Mint uses the repository suite **`stable`**. The suite is a fixed label for this archive (it is not a Mint codename), so one architecture-independent package serves Cinnamon, MATE and Xfce and every Mint release.

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

That is the whole installation. `apt update` reports `Hit:… https://patrickbuhagiar24.github.io/paste stable InRelease`, which proves the metadata is signed and verified.

### If you installed before 2.18.0 (suite `mint`)

Older instructions registered the archive under the suite **`mint`**. The
archive now publishes **`stable`**, and `dists/mint/` is no longer written, so
`apt update` on an old machine reports:

```text
E: The repository '…/paste mint Release' does not have a Release file.
```

Replace the suite in the installed source file once; the signing key and the
fingerprint do not change:

```bash
sudo sed -i 's/^Suites: mint$/Suites: stable/' /etc/apt/sources.list.d/pastex.sources
sudo apt update && apt-cache policy pastex
```

See [APT repository publishing](apt-repo/README.md) for the full migration note.

## Verifying an installed package

```bash
dpkg -V pastex            # lists any shipped file that changed; silence is good
apt changelog pastex      # the packaged changelog.Debian.gz
dpkg-deb -f /var/cache/apt/archives/pastex_*.deb Installed-Size   # size apt reserved
```

`dpkg -V` compares the files on disk with the `md5sums` list inside the
package, so a corrupted or locally edited extension file shows up as a line
starting with `??5??????`.

Builds are **reproducible**: the same commit with the same CRX key produces a
byte-identical `.deb`, because every timestamp comes from `SOURCE_DATE_EPOCH`
(the commit time by default). The `checksums.txt` attached to a release can
therefore be checked by rebuilding from the tag:

```bash
git checkout vX.Y.Z
PASTEX_CRX_KEY=/path/to/release-key.pem ./scripts/build-deb.sh
sha256sum dist/pastex_*_all.deb      # must equal the published checksum
```

CI also runs `lintian --fail-on error` on the package, and
`scripts/verify-site.py` on the directory that will be published (index,
checksums, digests, links and signature), so a broken archive stops the build
instead of reaching users.

## Releases and verification

Pushes to `main` publish the signed APT archive. Pushing a `vX.Y.Z` tag after
that merge also creates a GitHub Release with the `.deb`, the signed CRX,
`checksums.txt`, `extension-id.txt`, and the public keyring. The tag must match
the version in `extension/manifest.json` and `debian/changelog`. The release
notes repeat the signing fingerprint so it can be cross-checked independently
of the repository website; see [SECURITY.md](SECURITY.md).

## Development policy

Pastex is in development: it moves forward, it does not carry two
implementations of the same thing and it does not adapt new code to old state.
[DEVELOPMENT.md](DEVELOPMENT.md) states the rule and the one thing it never
touches — the snippet files in Drive. In short: content is out of bounds,
presentation metadata may be dropped, caches are always disposable, and
anything a user will notice is a `BREAKING:` entry in the changelog.

## Architecture in plain language

[ARCHITECTURE.md](ARCHITECTURE.md) explains how Pastex works without code: the four
parts a user can imagine (panel, in-page button, background worker, Google
Drive), what happens to an edit, when Drive is contacted, and a "to change X,
open Y" file map.

The other documents:

| Document | What it answers |
| --- | --- |
| [running-on-linux-mint.md](running-on-linux-mint.md) | installing and running Pastex on Linux Mint, step by step |
| [where-order-and-collapse-live.md](where-order-and-collapse-live.md) | where manual order and folded folders are stored, and when they reset |
| [ANALYSIS.md](ANALYSIS.md) | the colour and accessibility work, plus the improvements that are not done yet |
| [DEVELOPMENT.md](DEVELOPMENT.md) | the in-development policy: what may be dropped and what is never touched |
| [SECURITY.md](SECURITY.md) | the signing keys, the release checks and the OAuth trade-off |

## Testing a build on a real computer

There are two situations, and one command each. Both are **test** deployments:
the extension is signed with a test key kept in the checkout, so its extension
ID stays the same between test builds and is deliberately different from the
release ID. The signed archive above remains the only way an end user installs
a released version.

**Pastex is being tested on the machine that holds the checkout** — build and
install it there with apt:

```bash
sudo ./scripts/test-deploy.sh --local
```

**Another machine on the same network is the test machine** — publish a local
APT archive and serve it:

```bash
./scripts/test-deploy.sh --serve          # prints a one-line command to paste
```

The printed command is `curl -fsSL http://<address>:8099/install-from-here.sh |
sudo sh`. That installer verifies the downloaded `.deb` against `SHA256SUMS`,
adds the temporary apt source for one run only, removes it afterwards, and
asks for confirmation first, because a test archive is unsigned and has to be
trusted by host. Nothing about Google Drive is touched by either mode.

After installing, **quit the browser completely and start it again**: Chrome
reads the extension registration only at startup. Then enable Pastex in
`chrome://extensions` and pick the configuration folder with **Config**.

To undo it:

```bash
sudo ./scripts/test-deploy.sh --uninstall
```

The test signing key is written to `debian/pastex-test-crx-key.pem`. It is
ignored by Git and is never the release key; delete it to get a new test ID.

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
