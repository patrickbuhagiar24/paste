# Pastex for Chrome and Chromium

Pastex is a browser extension for text snippets stored directly in Google
Drive. It operates on **only the sites you choose**. Configure Pastex by
editing the files below or importing them from the side panel. A working
internet connection and access to the configured Drive folder are required.

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

Edit `~/.config/pastex/pastex-sites.json`, for example:

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

After editing `pastex-sites.json` manually, restart the browser and reload open tabs.
If imported through **Config** in the side panel, registrations update immediately;
reload open tabs to load the new script. Already-open tabs can retain an earlier
injected script until reloaded.
When upgrading, Chrome may ask you to re-enable the extension because its site
permissions changed. Insertion requests also check the current file each time,
so a removed site is denied even before restart.

## Configure Google Drive (required)

1. Enable the **Drive API** in Google Cloud, create a service account, and
   download its JSON key. Share the desired Drive folder with the key's
   `client_email` as an Editor.
2. Copy the downloaded JSON key to `~/.config/pastex/service-account.json`.
   It must contain `type`, `client_email` and `private_key`. Keep it private.
3. Edit `~/.config/pastex/pastex-google-drive.json`, for example:

   ```json
   {"folder_url": "https://drive.google.com/drive/folders/YOUR_FOLDER_ID"}
   ```

   A `folder_id` field may be used instead. When both are present, `folder_id`
   wins; clear it when changing the link. No browser-storage credential fallback
   is used. Older browser-storage credentials are not
   automatically deleted on upgrade; remove them via Chrome's extension
   storage developer tools if desired, after verifying your file setup.
4. Open the Pastex side panel. It loads the folder and its `.txt` files from
   Drive on first use; subsequent openings reuse the browser-session cache.
   Use **↻ Refresh** to reload after changes made outside Pastex.
   The service account key never leaves the machine; only signed authentication
   requests are sent to Google's token endpoint.

Alternatively, click **Config** in the side panel and choose a source folder.
Pastex detects `pastex-google-drive.json`, `pastex-sites.json` and
`service-account.json` in the folder itself, as well as a Google-downloaded
service-account JSON with a project-specific filename. If several keys are
found, use a folder containing only one or name the desired key
`service-account.json`. Subfolders are not searched. Old `google-drive.json`
and `sites.json` names are also recognized as import sources; if both names are
present, the new name takes precedence.

The detected files are validated and copied to `~/.config/pastex` (or the XDG
config directory) only after confirmation. Missing files remain unchanged. If a
filesystem write fails after another succeeds, the dialog reports which files
were imported. The native host must be installed; folder selection does not
give the extension unrestricted access to your files. The key is not shown,
logged, or saved to browser storage. To select `~/.config/pastex` in a Linux
file chooser, you may need to show hidden folders with **Ctrl+H**. Importing
Drive configuration reloads the library; reload open tabs after importing sites.
For manual site changes, restart the browser and reload tabs.

Each snippet maps to a Drive text file. Creating, editing and renaming write
straight to Drive; deleting moves the item (including a folder's contents) to
Drive trash. After changes, Pastex reloads the library from Drive. The side
panel and in-page picker share an in-memory Chrome session cache: reopening
either does not download the library again, and closing the browser clears the
cache. The cache contains snippet text (not the service-account key) and is
accessible to the extension, not websites. If the library exceeds Chrome's
session-storage quota, Pastex falls back to the service worker's memory; the
cache may then disappear when the worker stops.

Cached content can be stale, including after a folder is changed or access is
revoked elsewhere. Use **↻ Refresh** to fetch current Drive contents; explicit
Drive permission errors clear the cached snapshot. An internet connection is
required for the initial load and all changes. Account or folder configuration
changes invalidate the snapshot. No snippet changes are queued for offline sync.

**Select** enables multi-selection instead of pasting. Use **Up** or **Down**
to move selected items within their own folders, and **Delete** to move them to
Drive trash after confirmation. Click the folder arrow to expand or collapse it
while selecting. Moving is disabled during search; clear the search first.
Manual order is saved in each item's Google Drive `pastex_order` property, so
other Pastex instances can see it after refreshing. Unranked new files follow
ranked items in Drive's normal order. Concurrent reordering on different devices
can overwrite the same metadata; refresh before editing shared lists.

### Upgrading from an older version

On first use, existing `google-drive.json` and `sites.json` are copied to
`pastex-google-drive.json` and `pastex-sites.json` if the new names do not
already exist. The old files are left untouched as backups; after migration,
edit the new names because the old names are no longer read. Existing new-name
files always take precedence and are never overwritten during migration.

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
to insert it; its row has **Rename**, **Edit**, and **Delete** actions. If no editable field is focused on an allowed page, it is copied
to the clipboard. Restricted browser pages cannot receive scripts. Sites not
listed in `pastex-sites.json` are not injected or targeted.

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
  errors can indicate a malformed key or a disabled Drive API. Snippets appear
  progressively while loading; a stalled Google request times out and reports
  an error. Check network access, the folder ID, permissions, and Drive API,
  then use **↻ Refresh**. Individual unreadable files display a warning.
- The built-in Chrome Web Store, `chrome://` pages and other browser-protected
  pages cannot run extensions, even if listed.

## Maintainers: builds and permissions

The apt package ships `/usr/share/pastex/pastex.crx`, the unpacked extension,
the Python 3 native host, XDG autostart initialization, and external extension
and native-host manifests for Chrome and Chromium. `external_crx` registers the
CRX in `/usr/share/google-chrome/extensions` and `/usr/share/chromium/extensions`.

CRX IDs come from the signing key. Configure the `PASTEX_CRX_PRIVATE_KEY` GitHub Actions secret for
release CI (use `PASTEX_CRX_KEY` as the local build environment variable) and use the same key on every upgrade. Without it, local builds use
an ephemeral key: the extension ID and browser storage change, and the build is
for testing only. Verify with `python3 scripts/pack-crx.py --verify FILE.crx`.
An enterprise administrator may configure `ExtensionInstallForcelist` with
an update manifest; the package does not silently force-install extensions.

Permissions: `sidePanel`, `scripting` and `activeTab` provide
the editor and approved-site picker; `storage` provides the temporary session
cache; `clipboardWrite` is the fallback on approved sites; `nativeMessaging` reads
the config files. HTTP/HTTPS host permission covers dynamic user-approved sites
and the Google API endpoints; Chrome displays this as access to all sites even
though Pastex restricts injection using the allowlist described above.
