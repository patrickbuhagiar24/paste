# Security verification

Pastex is distributed as a signed APT archive on GitHub Pages and as a signed
Chrome/Chromium extension inside the Debian package. Treat the fingerprint as
trusted only after checking it on **two independent channels**.

## APT signing key

1. Read the full 40-character fingerprint from
   `https://patrickbuhagiar24.github.io/paste/archive-keyring.fingerprint.txt`.
2. Compare it with the fingerprint in the GitHub Release notes for the exact
   version you are installing, and with a maintainer announcement you already
   trust out of band. A fingerprint found only on the same website as the
   `.deb` files proves nothing about that website.
3. Only then install `pastex.sources` and `/etc/apt/keyrings/pastex.gpg` as
   shown in [README.md](README.md). Never use `[trusted=yes]`,
   `--allow-unauthenticated`, or `apt-key` for this archive.
4. Confirm that `apt update` reports `InRelease` for `stable` and that
   `/etc/apt/keyrings/pastex.gpg` is readable by `_apt` (`0644`).

If the fingerprint changes, stop the upgrade and ask the maintainer to confirm
the rotation. Key material is never committed to this repository; only the
public keyring and its fingerprint are published.

## Extension identity

The Debian package derives the Chrome extension ID from the CRX signing key,
so the ID must stay the same across releases. Compare the ID shown at
`chrome://extensions` with `extension-id.txt` on the repository website or in
the GitHub release. A change means the package was built with a different key,
and the ID is part of the OAuth redirect URL: after a genuine rotation,
register the new `https://<extension-id>.chromiumapp.org/` in your OAuth client
until the old tokens are useless anyway.

Pastex holds no client secret and stores no credential. The configuration
folder contains only a public client id (`pastex-oauth.json`), so a leaked
folder grants no access. The access token Google issues lives in the worker's
memory for about an hour and is never written to disk; the permission itself
stays with Google. Signing out revokes the access token at Google. Report
credential-handling issues privately to the maintainer rather than opening a
public issue.

## Release checksums

`checksums.txt` on the site and in each GitHub Release contains SHA-256 digests
of the `.deb` and the signed CRX. Verify a downloaded file with:

```bash
sha256sum -c checksums.txt --ignore-missing
```
