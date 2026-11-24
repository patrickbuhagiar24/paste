# Running Pastex on Linux Mint

This guide has two parts:

1. **[Inspecting the Drive label](#1-inspecting-the-drive-label)** — a script
   that shows exactly what Pastex stores in Google Drive.
2. **[Installing and running Pastex](#2-installing-and-running-pastex)** — the
   Chrome extension itself on Linux Mint.

## 1. Inspecting the Drive label

`scripts/get-pastex-label.sh` builds its own OAuth token from the service
account key (an RS256 JWT signed with `openssl`) and then calls the Drive API.
Nothing has to be installed beyond Mint's defaults: `curl`, `jq`, `openssl`.

### Requirements

| Requirement | How to install on Linux Mint |
| --- | --- |
| A `service-account.json` key | Google Cloud → *Credentials* → service account → download the JSON key |
| The library folder shared with `client_email` as **Editor** | Drive → right-click the folder → *Share* |
| `curl`, `jq`, `openssl` | `sudo apt install curl jq openssl` (all present by default) |

### Usage

```bash
chmod +x scripts/get-pastex-label.sh

# The root folder recorded in ./pastex-google-drive.json
./scripts/get-pastex-label.sh -k ~/keys/service-account.json

# Any specific file or folder id
./scripts/get-pastex-label.sh -k ~/keys/service-account.json 1AbCdEfGhIjKlMnOpQrStUv
```

The output is the raw `properties` object, then the index file decoded. The
index lives in `pastex-index.json` inside the library folder; if the script
reports it is missing, the library is simply being shown in Drive's order with
every folder open, and Pastex writes the file on the next reorder or fold.

### The request it makes, in one line

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "https://www.googleapis.com/drive/v3/files/$FOLDER_ID?fields=id,name,mimeType,properties"
```

The token is obtained from `https://oauth2.googleapis.com/token` with a
`jwt-bearer` assertion. The private key is written to a private temporary file
(`chmod 600`) and removed on exit; it is never printed.

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
3. Open the side panel, click **Config**, and pick the folder holding the three
   JSON files.

### Common messages

| Message | Meaning |
| --- | --- |
| `pastex-google-drive.json is missing` | the file is not in the chosen folder, or it was renamed |
| `pastex-sites.json: Invalid site origin: <all_urls>` | use `https://example.com`, never a global wildcard |
| `Folder has problems: …` | the folder was rejected; the previous configuration is still active |
| `The request failed. Make sure the folder is shared with: …` | share the Drive folder with that `client_email` as **Editor** |
| `Could not obtain an access token` | malformed key, or the Drive API is not enabled for the project |

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
