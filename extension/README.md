# Pastex for Chrome and Chromium

Pastex is a browser extension for text snippets stored directly in Google
Drive. It operates on **only the sites you choose**. There is no settings
window: configure Pastex by editing the files below. A working internet
connection and access to the configured Drive folder are required.

## Install

See the [APT repository instructions](https://github.com/patrickbuhagiar24/paste/blob/main/apt-repo/README.md), then run:

```bash
cd ~
sudo apt update
sudo apt install pastex
```

Enable the extension when the browser offers it and restart the browser.
Installing the package as root does not create files in another user's home:
the config directory is created on the next user login, when the extension is
installed/enabled, or when the browser starts. To create it immediately as
your normal user (do **not** use sudo):

```bash
/usr/lib/pastex/pastex-native-host --init-only
```

If `XDG_CONFIG_HOME` is set, use `$XDG_CONFIG_HOME/pastex` instead of
`~/.config/pastex`. The directory is private (0700) and JSON files are 0600.

## Choose sites

Edit `~/.config/pastex/sites.json`, for example:

```json
{
  "allowed_sites": [
    "https://example.com",
    "https://*.example.org",
    "http://localhost"
  ]
}
```

The default list is **empty**, so no websites are enabled until configured.
Use origins with an `http` or `https` scheme and hostname only; no paths,
credentials, ports or global wildcards. `*.example.org` includes the base domain
and all subdomains. HTTP and HTTPS are separate; matching includes any port.
If the file is malformed, contains an invalid entry, or the native host is
unavailable, no sites are enabled. The extension requests access to HTTP/HTTPS
in Chrome so it can register your chosen sites dynamically; it registers its
content script only for the configured origins.

**After changing sites.json, completely restart the browser and reload open
tabs.** Already-open tabs can retain an earlier injected script until reloaded.
When upgrading, Chrome may ask you to re-enable the extension because its site
permissions changed. Insertion requests also check the current file each time,
so a removed site is denied even before restart.

## Configure Google Drive (required)

1. Enable the **Drive API** in Google Cloud, create a service account, and
   download its JSON key. Share the desired Drive folder with the key's
   `client_email` as an Editor.
2. Copy the downloaded JSON key to `~/.config/pastex/service-account.json`.
   It must contain `type`, `client_email` and `private_key`. Keep it private.
3. Edit `~/.config/pastex/google-drive.json`, for example:

   ```json
   {"folder_url": "https://drive.google.com/drive/folders/YOUR_FOLDER_ID"}
   ```

   A `folder_id` field may be used instead. When both are present, `folder_id`
   wins; clear it when changing the link. No settings page or browser-storage
   credential fallback is used. Older browser-storage credentials are not
   automatically deleted on upgrade; remove them via Chrome's extension
   storage developer tools if desired, after verifying your file setup.
4. Open the Pastex side panel. It loads the folder and its `.txt` files from
   Drive automatically; use **↻ Refresh** to reload after external changes.
   The service account key never leaves the machine; only signed authentication
   requests are sent to Google's token endpoint.

Each snippet maps to a Drive text file. Creating, editing and renaming write
straight to Drive; deleting moves the item (including a folder's contents) to
Drive trash. The side panel and in-page picker load from Drive and hold data
only in memory while open; there is no offline snippet library or local sync
copy. Drive must be reachable to load, edit or paste snippets. Folder and file
order follows Drive's name ordering; manual reordering is not supported.

### Upgrading from an older version

Before upgrading, sync any browser-only snippets to Drive using the previous
version and verify them in Google Drive. The new version does not read, migrate,
or erase the old browser-storage library; unsynchronized local snippets will
not appear in the new interface. Do not clear the old extension's data until
all needed snippets have been safely backed up. A stable CRX signing key is
needed to keep the same extension identity across upgrades.

## Usage

Open the side panel with the toolbar icon to create, edit, search and organize
snippets. On an allowed site, focus an editable field and click the floating
**⚡** button to open the picker; click it again to close the picker. There is
no Pastex keyboard shortcut or context-menu entry. Click a side-panel snippet
to insert it. If no editable field is focused on an allowed page, it is copied
to the clipboard. Restricted browser pages cannot receive scripts. Sites not
listed in `sites.json` are not injected or targeted.

## Troubleshooting

- `getcwd() failed: No such file or directory` during apt means your terminal's
  current directory was removed. Run `cd ~` before retrying; check install
  status with `dpkg -s pastex`. The warning itself does not prove failure.
- If configuration is missing, run `--init-only` as shown above and restart
  the browser. The apt package installs `com.pastex.native`; the host manifest
  authorizes only the packaged extension ID. An unpacked development extension
  needs a matching native host manifest and permissions, otherwise it fails
  closed. The extension service-worker console shows native-host errors.
- If Drive says 404, share the folder with the service account email. Token
  errors can indicate a malformed key or a disabled Drive API.
- The built-in Chrome Web Store, `chrome://` pages and other browser-protected
  pages cannot run extensions, even if listed.

## Maintainers: builds and permissions

The apt package ships `/usr/share/pastex/pastex.crx`, the unpacked extension,
the Python 3 native host, XDG autostart initialization, and external extension
and native-host manifests for Chrome and Chromium. `external_crx` registers the
CRX in `/usr/share/google-chrome/extensions` and `/usr/share/chromium/extensions`.

CRX IDs come from the signing key. Configure a stable `PASTEX_CRX_KEY` for
release CI and use the same key on every upgrade. Without it, local builds use
an ephemeral key: the extension ID and browser storage change, and the build is
for testing only. Verify with `python3 scripts/pack-crx.py --verify FILE.crx`.
An enterprise administrator may configure `ExtensionInstallForcelist` with
an update manifest; the package does not silently force-install extensions.

Permissions: `sidePanel`, `scripting` and `activeTab` provide
the editor and approved-site picker;
`clipboardWrite` is the fallback on approved sites; `nativeMessaging` reads
the config files. HTTP/HTTPS host permission covers dynamic user-approved sites
and the Google API endpoints; Chrome displays this as access to all sites even
though Pastex restricts injection using the allowlist described above.
