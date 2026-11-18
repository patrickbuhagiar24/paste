# Pastex for Chrome

One-click text snippet manager as a Chrome extension (Manifest V3):

- **Side Panel library** — folders + snippets, search by name or contents,
  live preview, built-in editor.
- **In-page picker** — `Ctrl+Shift+V`, the ⚡ button next to focused fields,
  or right-click → *Paste snippet*. The picker appears at the field and
  inserts the snippet at the cursor.
- **Google Drive sync** — snippets mirror as `.txt` files through a service
  account. No OAuth popups.

No servers, no accounts, no tracking. Snippets live in local browser storage
until you connect Drive.

## Install

### From apt (recommended on Linux Mint)

```bash
sudo apt install pastex
```

The package registers the extension for **auto-detection**: on the next
launch, Chrome and Chromium notice
`/usr/share/<browser>/extensions/*.json` and offer to enable Pastex with one
click. No daemons, groups, or services are involved.

### Unpacked (development)

1. Open `chrome://extensions`, enable *Developer mode*.
2. *Load unpacked* → select this `extension/` directory. Config lives in
   browser storage; see `~/.config/pastex` above for the apt install.

## Use

- Click the Pastex toolbar icon to open the Side Panel.
- Click a snippet once to insert it into the focused field of the active tab.
  When no field is focused, the text is copied instead — press `Ctrl+V`.
- In any page: focus a text field, then press `Ctrl+Shift+V`, click the ⚡
  button beside the field, or right-click → *Paste snippet*.
- Manage everything (folders, rename, reorder, edit, delete) in the Side
  Panel. Search matches names first, then contents.

## Config files (`~/.config/pastex`)

With the apt install, Drive credentials live in two plain JSON files:

- `~/.config/pastex/google-drive.json` — the shared Drive folder:
  `{"folder_id": "…", "folder_url": "https://drive.google.com/…"}`
  (either field is enough; if both are set, `folder_id` takes precedence.
  Clear it when changing `folder_url`, so the ID is parsed from the new link).
- `~/.config/pastex/service-account.json` — the Google Cloud JSON key
  (`client_email` + `private_key`).

A `.deb` runs as root and must not create per-user files at install time, so
the folder is created **automatically on login** (an XDG autostart one-shot)
**and on first use** (by the native host below), with templates and `0700` /
`0600` permissions. You can edit both files by hand or from the Settings page
— the files always win over browser storage, which remains as the fallback
for installs without apt (unpacked, non-Linux).

The browser cannot read local files by itself, so a tiny native host bridges
the gap: `/usr/lib/pastex/pastex-native-host` (Python 3, standard library
only), registered as `com.pastex.native` for Chrome and Chromium. It only
serves the two config files over the native-messaging protocol and creates
nothing else.

## Google Drive sync (service account)

Pastex signs in as a Google Cloud **service account**: one key, no per-user
consent screens, works in the background. With apt, the Settings page reads
and writes `~/.config/pastex/*.json`; otherwise it uses browser storage.

1. In Google Cloud Console, select (or create) a project and enable the
   **Google Drive API**.
2. *IAM & Admin → Service Accounts* → create a service account (no roles
   needed), then *Keys → Add key → JSON*. A `.json` file downloads.
3. Decide where the snippets live:
   - **Share a folder**: in Google Drive, share a folder with the service
     account's `client_email` (role Editor), and copy the folder ID from the
     folder URL (`.../folders/<ID>`); or
   - **Let Pastex create one**: the Settings page can create a `Pastex`
     folder owned by the service account.
4. Open Pastex *Settings* (⚙ in the Side Panel), paste the JSON key, save it,
   then enter/verify the folder ID (or create the folder).
5. Press ⇄ *Sync* in the Side Panel.

Sync rules (v1, deliberately simple):

- Each snippet maps to `<path>/<Name>.txt` in the Drive folder.
- **Sync** merges newest-wins per path: newer uploads, newer downloads.
- ⇪ uploads everything local (overwrites newer remote copies); ⇩ downloads
  everything remote (replaces local). Both ask for confirmation.
- **Deletions never propagate**: delete on both sides if you mean it.

Key security: anyone holding the JSON key acts as the service account, so
treat it like a password. On apt installs the native host reads the key file;
when saved through Settings a copy of `client_email` and `private_key` is
also stored in local browser storage as a fallback. The private key itself is
never sent to Google — only signed login requests go to the token endpoint.

## Permissions (why each one)

| Permission | Used for |
|---|---|
| `storage` | Snippets, settings, sync state (local only) |
| `sidePanel` | The snippet library panel |
| `contextMenus` | Right-click → *Paste snippet* with recent items |
| `scripting`, `activeTab` | Talking to the current page (insert text, open picker) |
| `clipboardWrite` | Copy fallback when no field is focused |
| `nativeMessaging` | Read `~/.config/pastex/*.json` through the apt-shipped host |
| `https://www.googleapis.com/`, `https://oauth2.googleapis.com/` | Drive sync only |

Content scripts run on `http://`/`https://` pages to insert text and show the
picker; page contents are never read except the field being typed into.

## For maintainers: CRX signing and auto-detection

The apt package ships a signed `.crx` plus external-install JSON:

- `/usr/share/pastex/pastex.crx` and `/usr/share/pastex/extension/`
- `/usr/share/google-chrome/extensions/<id>.json`
- `/usr/share/chromium/extensions/<id>.json`

The `<id>` is derived from the CRX signing key, so **releases must reuse one
stable key** or every upgrade looks like a new extension. Generate once:

```bash
openssl genrsa -out crx-key.pem 2048   # keep secret, never commit it
```

and build with `PASTEX_CRX_KEY=/path/to/crx-key.pem ./scripts/build-deb.sh`
(release CI reads the `PASTEX_CRX_KEY` secret). Without a key the build uses
an ephemeral one and says so — fine for testing, not for release.

Verify any build without a browser:

```bash
python3 scripts/pack-crx.py --verify /usr/share/pastex/pastex.crx
```

This checks the CRX structure, the RSA signature, and the embedded zip.

### Silent force-install (managed machines)

Auto-detection still asks the user to click *Enable*. For fully silent
installs, publish the `.crx` over HTTPS with an update manifest:

```xml
<?xml version='1.0' encoding='UTF-8'?>
<gupdate xmlns='http://www.google.com/update2/response' protocol='2.0'>
  <app appid='<EXTENSION ID>'>
    <updatecheck codebase='https://example.com/pastex.crx' version='2.0.0' />
  </app>
</gupdate>
```

and ship `/etc/opt/chrome/policies/managed/pastex.json`:

```json
{"ExtensionInstallForcelist": ["<EXTENSION ID>;https://example.com/updates.xml"]}
```

The regular apt package deliberately does not do this: silent force-installs
are an administrator decision.

## Troubleshooting

- *Chrome never offers to enable*: check the JSON exists
  (`/usr/share/google-chrome/extensions/*.json`), its `external_crx` path
  exists, and restart the browser (not just the window).
- *Sync says 404 on the folder*: the folder is not shared with the service
  account email. Re-share it as Editor and verify again.
- *Token request fails*: the JSON key is truncated or the Drive API is not
  enabled in the Cloud project.
- *Insert does nothing on some pages*: `chrome://`, the Web Store, and a few
  privileged pages block content scripts by design; the text is copied to the
  clipboard instead.
