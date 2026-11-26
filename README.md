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
client ID in the configuration file; the [step-by-step guide in the extension
README](extension/README.md#create-the-google-oauth-client) covers the Google
Cloud project, the consent screen, the redirect URL and the client ID, in about
ten minutes. Because the token belongs to your account, the files Pastex creates
are yours, sit in your own Drive storage, and open normally in Drive.

In **Config**, pick one local JSON file — any name will do, for example
`pastex-storage.json` — which names the library folder, the order/collapse file
and the site list by their **shared Drive links**. Pastex reads that file once, validates every link, and then works
from its own cache; the file is never uploaded or published, and it contains no
credential. See
[the extension README](extension/README.md#set-up-the-configuration-file) for
the keys and the link shapes that are accepted.

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
notes repeat the signing fingerprint so it can be cross-checked independently of
the repository website; see [Security and verification](#security-and-verification).

## Security and verification

Pastex is distributed as a signed APT archive on GitHub Pages and as a signed
Chrome/Chromium extension inside the Debian package. Treat the fingerprint as
trusted only after checking it on **two independent channels**.

**APT signing key.** The full 40-character fingerprint is published in
`https://patrickbuhagiar24.github.io/paste/archive-keyring.fingerprint.txt`.
Compare it with the fingerprint in the GitHub Release notes for the exact
version you install, and with a maintainer announcement you already trust out of
band; a fingerprint found only on the same website as the `.deb` files proves
nothing about that website. Only then install `pastex.sources` and
`/etc/apt/keyrings/pastex.gpg` as shown above. Never use `[trusted=yes]`,
`--allow-unauthenticated` or `apt-key` for this archive, and confirm that
`apt update` reports `InRelease` for `stable` and that
`/etc/apt/keyrings/pastex.gpg` is readable by `_apt` (`0644`). If the
fingerprint changes, stop the upgrade and ask the maintainer to confirm the
rotation. Key material is never committed; only the public keyring and its
fingerprint are published.

**Extension identity.** The Debian package derives the Chrome extension ID from
the CRX signing key, so the ID must stay the same across releases. Compare the
ID at `chrome://extensions` with `extension-id.txt` on the repository website or
in the GitHub Release. A change means the package was built with a different
key, and the ID is part of the OAuth redirect URL: after a genuine rotation,
register the new `https://<extension-id>.chromiumapp.org/` in your OAuth client.

**OAuth trade-off.** Pastex reaches Drive as you, so the token is yours. The
client secret in the configuration file is a value the same person supplies and
the extension sends only to `oauth2.googleapis.com/token`; it is never written to
the snippet library or to the cache. The configuration file otherwise holds
public Drive links, a public client id and that secret, and nothing else. The
extension requests the full `https://www.googleapis.com/auth/drive` scope, so
the access token reaches every file the signed-in account can open, not only the
ones Pastex created; the library folder in the configuration decides what Pastex
itself reads and writes. That scope is classified as restricted by Google, which
is why publishing the application beyond its own test users would require a
security assessment. The access token lives in the worker's memory for about an
hour and is never written to disk; the refresh token stays in
`chrome.storage.local` so the connection survives an hour and a restart. Signing
out revokes both at Google. Report credential-handling issues privately to the
maintainer rather than in a public issue.

**Release checksums.** `checksums.txt` on the site and in each GitHub Release
holds the SHA-256 digests of the `.deb` and the signed CRX. Verify a downloaded
file with:

```bash
sha256sum -c checksums.txt --ignore-missing
```

## Development policy

Pastex is in development: it moves forward, it does not carry two
implementations of the same thing and it does not adapt new code to old state.

**The three classes of state.** These decide what may be dropped:

| Class | Examples | Rule |
| --- | --- | --- |
| Content | the snippet `.txt` files in Drive | **out of bounds**: never rewritten, migrated or dropped |
| Presentation metadata | manual order, folded folders in `pastex-index` | may be dropped, and the panel then shows Drive's order |
| Cache | snapshots, bodies, the session copy | always disposable: it is rebuilt from Drive |

**The rules.**

1. One implementation. Replace the old mechanism; do not ship both.
2. No compatibility layer for older state. A stored shape has one writer and
   one reader, and they are tested together.
3. Content is never touched. Only the folder the configuration names is read and
   written, and the index file's content is read, not its listing.
4. Metadata and cache are deleted rather than migrated when their shape changes.
5. Anything a user will notice is a `BREAKING:` line in the changelog saying
   what was dropped and what the panel now shows instead.
6. One language: English in every living file — code, comments, docs, changelog.
7. When deleting a path, delete its test at the same time and add a test that the
   path stays gone (`extension/test/dead-code.test.js`).

**Why not keep compatibility while it is cheap?** Because "cheap now" is how a
codebase ends up with two ways to store the same fact. That is not hypothetical
here: `pastex_order`/`pastex_collapsed` per item were still read as a second
source of truth next to `pastex_index`, and `handle-store.js`/`config-import.js`
were still shipped for a design that had already been replaced. They were
removed in 2.16–2.18. When a user is on an older state the honest answer is that
presentation and cache state resets, while content is untouched and always will
be. If a real installed base ever exists, this policy changes deliberately, with
a written migration path and a test that exercises the old format.

Scratching a broken local state during development:
`./scripts/reset-pastex-state.sh` clears the cache only; Drive is never touched.

## How Pastex works

Three parts a user can imagine: the **side panel** (the list, the buttons, the
search), the **in-page launcher** (⚡ on an allowed site, which opens the same
panel) and the **background worker** (the only part that talks to Google Drive,
and the part that keeps the cache warm while you are away).

An edit is applied to the panel at once, written into a queue in
`chrome.storage.local`, and sent to Drive when the machine is idle or when you
press **↻ Sync** — so a closed browser or a lost connection does not lose typing.
Reads come from the snapshot in cache; a stale snapshot is corrected by the
background check. Drive is contacted for a listing when nothing is stored yet,
when you ask for a sync, and while idle to see whether the library changed.

**The file map — "to change X, open Y":**

| To change | Open |
| --- | --- |
| the panel's markup, styles, behaviour | `extension/sidepanel.html`, `.css`, `.js` |
| what happens inside a page, including newlines | `extension/content.js`, `content.css` |
| the queue that survives a restart | `extension/queue.js` |
| the cache: snapshots, bodies, freshness | `extension/cache.js` |
| reading Drive: listing, upload, OAuth | `extension/drive.js` |
| the library rules: order, folders, index | `extension/library.js` |
| the configuration file and its keys | `extension/config-source.js`, `config-cache-reader.js` |
| messages, alarms, registration of sites | `extension/background.js` |
| the allowlist matching | `extension/sites.js` |
| the package, the CRX, the APT archive | `scripts/`, `.github/workflows/build-deb.yml` |

The user-facing setup guide, the configuration keys and the troubleshooting list
live in [the extension README](extension/README.md); the APT archive layout and
its publishing rules live in [the APT repository README](apt-repo/README.md).

## Where order and folded folders live

The manual order and the folded state of every folder are stored in **one
label on the root folder in Drive**, named by the `pastex-index` link in the
configuration file. The file holds a small JSON object with a version, the order
of each folder's children and the collapsed ids. It is written only after a
reorder or a fold, is excluded from the snippet list, and is read by id, never by
name. A folder property would have to be repeated on every child and would
break as soon as a file is moved by hand; one label on one folder is a single
write and survives a rename. If the file is missing or unreadable, the library
loads in Drive's order with every folder open, and the next reorder writes the
file again — Pastex will not create a second index file behind your back.

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
`chrome://extensions` and pick your configuration file with **Config**.

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
desktop application has been removed; it remains in Git history. The reading
material that used to sit in separate documents — security, the development
policy, the architecture tour and the index file — is part of this README.
