# Pastex APT repository

Pastex publishes a standard APT archive through GitHub Pages, using the same layout as `patrickbuhagiar24/youtube`:

```text
index.html
.nojekyll
pastex.sources                       (signed archive)
pastex-unsigned.sources              (local test builds only; never published to Pages)
archive-keyring.asc
archive-keyring.gpg
archive-keyring.fingerprint.txt
pool/main/p/pastex/pastex_<version>_all.deb
dists/stable/Release
dists/stable/InRelease
dists/stable/Release.gpg
dists/stable/main/binary-all/Packages
dists/stable/main/binary-all/Packages.gz
```

The pool holds **one** version at a time: only the build that the index names.
`scripts/build-apt-repository.sh` refuses a directory with two versions in it,
because APT installs a single version of an architecture-independent package
and an older file would stay public forever without being reachable.

`dists/stable/Release` carries `Date` and `Valid-Until` (30 days apart). APT
refuses expired metadata instead of trusting a stale mirror, so a repository
that stops being updated becomes a visible error rather than a silent risk.
Both fields come from `SOURCE_DATE_EPOCH`, which is what keeps a rebuild of the
same commit byte-identical. `scripts/build-apt-repository.sh` derives that value
itself (the last commit time, exactly as `scripts/build-deb.sh` does) and works
from any directory, so a rebuild does not depend on remembering to export it.
The test suite checks that two builds of one checkout produce the same `Release`
file.

The suite is **`stable`** — a fixed archive label for this project, not a Mint release codename — because the package is architecture-independent and is not built per release. The archive URL is `https://patrickbuhagiar24.github.io/paste/`, so `apt update` reports:

```text
Hit:6 https://patrickbuhagiar24.github.io/paste stable InRelease
```

## Migrating from the retired `mint` suite

The archive used to publish `dists/mint/`. It now publishes `dists/stable/`,
and nothing writes `dists/mint/` any more. A machine that was configured for
`mint` cannot see the new suite on its own, so it must replace the source file
once. The signing key does not change.

```bash
# 1. Replace the suite in the installed source file.
sudo sed -i 's/^Suites: mint$/Suites: stable/' /etc/apt/sources.list.d/pastex.sources
# 2. Re-read the signed metadata.
sudo apt update
# 3. Confirm the new suite is the one being read.
apt-cache policy pastex
```

A one-line check before you change anything shows whether migration is needed:

```bash
grep -q '^Suites: mint$' /etc/apt/sources.list.d/pastex.sources 2>/dev/null &&
  echo "still on the retired mint suite" || echo "already on stable"
```

What the user sees without this step: `apt update` reports

```text
E: The repository 'https://patrickbuhagiar24.github.io/paste mint Release' does not have a Release file.
N: Updating from such a repository can't be done securely, and is therefore disabled by default.
```

That message is the only symptom. Installed packages keep working; only
updates stop.

Alternatively, reinstall the published source file verbatim, which also
restores the correct `Signed-By` line:

```bash
curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
  -o /tmp/pastex.sources https://patrickbuhagiar24.github.io/paste/pastex.sources
sudo install -m 0644 /tmp/pastex.sources /etc/apt/sources.list.d/pastex.sources
sudo apt update
```

## Use the published archive

The published `pastex.sources` file is installed verbatim, after the key fingerprint has been confirmed through an independent channel. This keeps the deb822 entry in sync with the signed `dists/stable/InRelease` metadata.

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

Verify `archive-keyring.fingerprint.txt` against a trusted maintainer announcement before installing the key. Do not use `[trusted=yes]`, `--allow-unauthenticated`, or `apt-key` for a signed archive. The key must be world-readable (`0644`) because APT reads it as the unprivileged `_apt` user.

## Migrating from the old flat repository

Earlier releases published a flat repository at `https://patrickbuhagiar24.github.io/paste/apt/` with `Packages` in its root and no `Release`/`InRelease`. That layout is gone; `apt update` used to print `Get:… …/paste/apt ./ Packages` because it had to be trusted with `trusted=yes`.

Replace the old entry:

```bash
sudo rm -f /etc/apt/sources.list.d/pastex.list
sudo rm -f /usr/share/keyrings/pastex.gpg
```

Then follow [Use the published archive](#use-the-published-archive) above.

## Publish through GitHub Pages

1. Open **Settings → Pages** and set **Source** to **GitHub Actions**.
2. Under **Settings → Environments → github-pages**, choose **No restriction** so a manual dispatch from a working branch can deploy.
3. Push to `main`, or run **Actions → Build .deb & APT repo → Run workflow** and pick the branch to deploy. Manual dispatch publishes that branch directly; no PR or merge is required.

The workflow checks that `extension/manifest.json` and `debian/changelog` agree, builds the `.deb`, generates the archive with `scripts/build-apt-repository.sh`, signs it with `scripts/sign-apt-repository.sh`, deploys `site/` to Pages, and uploads the `.deb`, CRX and extension ID as build artifacts. `site/extension-id.txt` and `site/checksums.txt` are published with the archive.

### Release tags

Push a `vX.Y.Z` tag **after** the matching version has been merged and published from `main`. A tag build verifies the tag against the manifest version and creates an immutable GitHub Release containing the `.deb`, signed CRX, `checksums.txt`, `extension-id.txt`, and both public keyring exports. The release notes repeat the full signing fingerprint and the extension ID, giving users a second, tag-pinned place to cross-check them against the repository website. Tag builds do not deploy to Pages; only `main` pushes and manual dispatches do.

## Signing key

Create a dedicated signing key (never a personal key) and store its exports outside the repository:

```bash
gpg --full-generate-key            # RSA 4096, sign-only, with an expiry
gpg --list-secret-keys --keyid-format long
export FINGERPRINT=YOUR_FULL_40_CHARACTER_FINGERPRINT
umask 077
gpg --armor --export-secret-keys "$FINGERPRINT" > apt-signing.private.asc
```

Add these repository **secrets** in **Settings → Secrets and variables → Actions**:

| Secret | Value |
| --- | --- |
| `GPG_PRIVATE_KEY` | Armored private key export with exactly one primary identity |
| `GPG_PASSPHRASE` | Key passphrase (empty only for an unprotected key) |

The workflow imports the key into an ephemeral keyring under `$RUNNER_TEMP`, derives the fingerprint itself, exports it as `APT_SIGNING_KEY_ID` (the variable `scripts/sign-apt-repository.sh` requires), signs `Release` into `InRelease` and `Release.gpg`, verifies both signatures with `gpgv`, and exports only the public key. The private key is never printed or uploaded.

If signing fails, the workflow prints the last `gpg` lines and adds an `APT signing failed: …` error annotation to the run, so the reason is visible without downloading logs. A passphrase is only passed to `gpg` when `GPG_PASSPHRASE` is set; otherwise the key must be unprotected.

Without `GPG_PRIVATE_KEY` the workflow fails before deploying, so a `Trusted: yes` archive is never published to the public Pages site. The unsigned source file exists only for local, explicitly trusted test archives.

## Verify the key out of band

A fingerprint served only by the repository website is not independent verification. Compare it with at least one separate channel before installing:

- the GitHub Release notes for the version you are installing (created by the tag workflow);
- a signed maintainer announcement or another maintainer-controlled channel you already trust.

See [SECURITY.md](../SECURITY.md) for the verification checklist. If the values differ or the key has changed unexpectedly, stop and contact the maintainer.

## Build locally

```bash
./scripts/setup-apt-repo.sh                       # unsigned archive in apt-repo/dist
APT_SIGNING_KEY_ID=<fingerprint> GPG_PASSPHRASE=<pass> ./scripts/setup-apt-repo.sh
```

The individual steps are:

```bash
./scripts/build-deb.sh
./scripts/build-apt-repository.sh dist apt-repo/dist stable
APT_SIGNING_KEY_ID=<fingerprint> ./scripts/sign-apt-repository.sh apt-repo/dist stable
```

The signer prints the key algorithm, bit length, capabilities, and fingerprint (public metadata only) before signing, and aborts with a hint when `GPG_PASSPHRASE` is missing for a protected key.

`PASSTEX_SIGNED=1 ./scripts/build-apt-repository.sh …` publishes `pastex.sources` (signed flow) instead of `pastex-unsigned.sources`.
