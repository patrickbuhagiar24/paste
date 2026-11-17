# Pastex APT repository

Pastex publishes a standard APT archive through GitHub Pages, using the same layout as `patrickbuhagiar24/youtube`:

```text
index.html
.nojekyll
pastex.sources                       (signed archive)
pastex-unsigned.sources              (archive without a signing key)
archive-keyring.asc
archive-keyring.gpg
archive-keyring.fingerprint.txt
pool/main/p/pastex/pastex_<version>_all.deb
dists/stonking/Release
dists/stonking/InRelease
dists/stonking/Release.gpg
dists/stonking/main/binary-all/Packages
dists/stonking/main/binary-all/Packages.gz
```

The suite is **`stonking`**, the Ubuntu 26.10 codename, because the package is architecture-independent and is not built per release. The archive URL is `https://patrickbuhagiar24.github.io/paste/`, so `apt update` reports:

```text
Hit:6 https://patrickbuhagiar24.github.io/paste stonking InRelease
```

## Use the published archive

```bash
sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://patrickbuhagiar24.github.io/paste/archive-keyring.gpg \
  | sudo tee /etc/apt/keyrings/pastex.gpg > /dev/null
sudo chmod 0644 /etc/apt/keyrings/pastex.gpg
curl -fsSL https://patrickbuhagiar24.github.io/paste/pastex.sources \
  | sudo tee /etc/apt/sources.list.d/pastex.sources > /dev/null
sudo apt update
sudo apt install pastex
```

Verify `archive-keyring.fingerprint.txt` against a trusted maintainer announcement before installing the key. Do not use `[trusted=yes]`, `--allow-unauthenticated`, or `apt-key` for a signed archive.

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
2. Under **Settings → Environments → github-pages**, allow the branch that publishes the archive (`main`), or choose **No restriction**.
3. Merge to `main`, or run **Actions → Build .deb & APT repo → Run workflow**.

The workflow builds the `.deb`, generates the archive with `scripts/build-apt-repository.sh`, signs it with `scripts/sign-apt-repository.sh`, deploys `site/` to Pages, and uploads the `.deb` as a build artifact.

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

The workflow imports the key into an ephemeral keyring under `$RUNNER_TEMP`, derives the fingerprint itself, signs `Release` into `InRelease` and `Release.gpg`, verifies both signatures with `gpgv`, and exports only the public key. The private key is never printed or uploaded.

Without `GPG_PRIVATE_KEY` the workflow still publishes, but with a warning: the archive stays unsigned and users must install `pastex-unsigned.sources` (`Trusted: yes`).

## Build locally

```bash
./scripts/setup-apt-repo.sh                       # unsigned archive in apt-repo/dist
APT_SIGNING_KEY_ID=<fingerprint> GPG_PASSPHRASE=<pass> ./scripts/setup-apt-repo.sh
```

The individual steps are:

```bash
./scripts/build-deb.sh
./scripts/build-apt-repository.sh dist apt-repo/dist stonking
./scripts/sign-apt-repository.sh apt-repo/dist stonking
```

`PASSTEX_SIGNED=1 ./scripts/build-apt-repository.sh …` publishes `pastex.sources` (signed flow) instead of `pastex-unsigned.sources`.
