# Running Pastex on Linux Mint

This guide has two parts:

1. **[Signing in to Google](#1-signing-in-to-google)** — the one-time OAuth
   client every installation needs.
2. **[Installing and running Pastex](#2-installing-and-running-pastex)** — the
   Chrome extension itself on Linux Mint.

## 1. Signing in to Google

Pastex uses Google OAuth as **you**: no service account, no key file, no folder
shared with an e-mail address. The full walkthrough, with the exact console
paths, is in [extension/README.md](extension/README.md#create-the-google-oauth-client).
In short:

| What you need | Where it comes from |
| --- | --- |
| The extension ID | `chrome://extensions` (Developer mode) |
| A Google Cloud project with the Drive API enabled | <https://console.cloud.google.com> |
| An OAuth client of type **Web application** whose *Authorized redirect URI* is `https://<extension-id>.chromiumapp.org/` | *APIs & Services → Credentials* |
| `client_id` holding that client ID | written by you, in your configuration file (any JSON name) |

There is no client secret: an extension is a public client, and Google hands the
token straight back to Chrome. If the redirect URL is wrong, Google answers
`redirect_uri_mismatch` and Pastex prints the URL you must register, so the
mistake is visible without a debugger.

### Why not a service account

A service account has no Drive storage of its own, so Google refuses every file
it tries to create in a personal folder — a folder could be created while a
snippet could not, which is exactly the symptom that led here. With OAuth the
files belong to you, count against your own storage, and open normally in Drive.
If your Drive is full, Google says so and Pastex keeps the snippet queued.

## 2. Installing and running Pastex

### Requirements

- Linux Mint (the only supported target)
- Chrome or Chromium **141 or newer**

Chromium from Snap or Flatpak runs in its own sandbox and does **not** read
`/usr/share/chromium/extensions`, so the `.deb` cannot register the extension
there. For those builds, load it by hand:

1. `chrome://extensions` → enable **Developer mode**
2. **Load unpacked** → `/usr/share/pastex/extension`

### Install from the signed APT repository

The archive uses the suite **`stable`** (a fixed archive label for this
project, not a Linux Mint release codename), and it serves every architecture
because the package is `Architecture: all`. The full command, including the
fingerprint check, is in the [project README](README.md).

### After installing

1. Restart the browser completely (not just close the window).
2. Enable Pastex when the browser offers it.
3. Note the extension ID in `chrome://extensions` and register its redirect URL
   in the OAuth client (see [section 1](#1-signing-in-to-google)).
4. Open the side panel, click **Config**, and pick your configuration file — one
   JSON file of Drive links, under any name you like.

### Common messages

| Message | Meaning |
| --- | --- |
| `Choose your configuration file in Config first.` | no configuration has been picked yet, or the cache was cleared by an upgrade |
| `<name> is not valid JSON.` | the picked file does not parse; the name in the message is the file you chose |
| `pastex-sites: Invalid site origin: <all_urls>` | use `https://example.com`, never a global wildcard |
| `Configuration has problems: …` | the file was rejected; the previous configuration is still active |
| `Google does not recognise this extension's redirect URL` | copy the quoted URL into the OAuth client's *Authorized redirect URIs* |
| `... needs a pastex-folder link` | that key is empty or is not a Drive link |
| `... needs the client_id of a Google OAuth client` | the client ID is missing or malformed |
| `Your Google account is out of Drive storage` | free space in Drive, or use a folder in a Shared Drive |
| `interaction_required` / `access_denied` | sign-in was cancelled; choose **↻ Sync** and accept once |

### Uninstalling

```bash
sudo apt remove pastex
```

The package touches nothing outside `/usr/share/pastex`,
`/usr/share/{google-chrome,chromium}/extensions` and one marker file in
`/var/lib/pastex`. Your snippets live in Drive and are never removed by the
package.

## Trying a build before it is released

If you are testing rather than installing a release, do not follow the signed
archive instructions above. Use the test deployment, which keeps one stable
test extension ID and never touches the signed archive:

```bash
sudo ./scripts/test-deploy.sh --local      # this machine holds the checkout
./scripts/test-deploy.sh --serve           # another machine on the network installs
sudo ./scripts/test-deploy.sh --uninstall  # remove it again
```

Quit the browser completely after installing, start it again, and enable
Pastex when it asks. Your snippets in Google Drive are never touched by these
commands.
