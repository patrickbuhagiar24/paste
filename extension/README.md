# Pastex for Chrome and Chromium

Pastex is a browser extension for text snippets stored directly in Google
Drive. It operates on **only the sites you choose**. Configure Pastex by selecting a folder containing all three JSON files below.
The extension reads the files directly from that folder; it does not copy the
credentials or create a configuration directory. Google Drive and a working
internet connection are required.

## Install

Follow the [APT repository instructions](https://github.com/patrickbuhagiar24/paste/blob/main/apt-repo/README.md), then run `sudo apt update` and `sudo apt install pastex`. Enable the extension and restart the browser. Chrome/Chromium 116 or newer is required.

## Set up the selected folder

1. Enable the **Drive API** in Google Cloud, create a service account and
   download its JSON key. Share the Drive folder with its `client_email` as
   an Editor. Place the downloaded key in the configuration folder as
   `service-account.json` or leave its Google-downloaded JSON filename intact.
   Protect this private key and do not share it.
2. Add `pastex-google-drive.json` to that folder, for example:

   ```json
   {"folder_url": "https://drive.google.com/drive/folders/YOUR_FOLDER_ID"}
   ```

   Alternatively use `{"folder_id": "YOUR_FOLDER_ID"}`. When both are set,
   the ID wins.
3. Add `pastex-sites.json` to that folder, for example:

   ```json
   {"allowed_sites": ["https://example.com", "https://*.example.org"]}
   ```

   An empty list allows no sites. Use HTTP(S) origins only, without paths,
   ports, credentials or global wildcards. Wildcard subdomains include the
   base domain. If missing, invalid or inaccessible, the allowlist fails closed.
4. Open the side panel, click **Config** then **Choose folder**, and select
   the folder containing these files. All three must be valid before the
   selection is saved. Pastex searches only this folder, not its subfolders.
   Multiple service-account candidates are rejected unless the desired one is
   named `service-account.json`. Legacy `google-drive.json` and `sites.json`
   are also recognized, with the new names taking precedence.

Chrome stores a permissioned folder handle in its extension IndexedDB, not the
JSON content. Pastex uses that handle to read the files in place; it does not
copy them into `~/.config/pastex` or another configuration store. It does not
create, delete or modify old files in that directory. If you previously used it,
move or copy your files to your chosen folder yourself, and keep a backup.
The browser may revoke access after a restart; if prompted, choose the folder
again in **Config**. Until access is restored, sites fail closed and Drive
cannot load. Some browser variants may not support the folder picker.

After modifying site JSON, choose the folder again to update script registration
and reload open tabs. Existing tabs may retain a script until reloaded, but
insertion and picker requests re-check the current allowlist. Changes to Drive
configuration are picked up on refresh. The service-account key is not logged,
stored in Chrome storage or displayed; it is read into extension memory and used
to sign authentication requests to Google.

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

Old configuration files remain untouched. To use them, place copies in the
folder you select; Pastex no longer automatically reads or writes them.

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

- If APT has installed a newer version but Chrome still displays the older
  extension, fully exit and restart Chrome. Verify the installed package version
  with `dpkg-query -W pastex` and compare it with `chrome://extensions`. As a
  manual workaround, close the Pastex side panel, enable Developer mode in
  `chrome://extensions`, then drag `/usr/share/pastex/pastex.crx` from the file
  manager onto that page. This updates an extension with the same signing key
  and ID; it is not proof that subsequent automatic updates will work. Confirm
  the version and ID afterwards. Do not remove the old extension or clear its
  data without first backing up any browser-only snippets.
- `getcwd() failed: No such file or directory` during apt means your terminal's
  current directory was removed. Run `cd ~` before retrying; check install
  status with `dpkg -s pastex`. The warning itself does not prove failure.
- If configuration access is missing, open **Config** and select the folder
  again. The browser must support `showDirectoryPicker`; if access is refused,
  check browser version and permissions. The worker console can show errors.
- If Drive says 404, share the folder with the service account email. Token
  errors can indicate a malformed key or a disabled Drive API. Snippets appear
  progressively while loading; a stalled Google request times out and reports
  an error. Check network access, the folder ID, permissions, and Drive API,
  then use **↻ Refresh**. Individual unreadable files display a warning.
- The built-in Chrome Web Store, `chrome://` pages and other browser-protected
  pages cannot run extensions, even if listed.

## Maintainers: builds and permissions

The apt package ships `/usr/share/pastex/pastex.crx`, the unpacked extension,
and external extension metadata for Chrome and Chromium. `external_crx`
registers the CRX in `/usr/share/google-chrome/extensions` and
`/usr/share/chromium/extensions`. It does not install a native host.

CRX IDs come from the signing key. Configure the `PASTEX_CRX_PRIVATE_KEY` GitHub Actions secret for
release CI (use `PASTEX_CRX_KEY` as the local build environment variable) and use the same key on every upgrade. Without it, local builds use
an ephemeral key: the extension ID and browser storage change, and the build is
for testing only. Verify with `python3 scripts/pack-crx.py --verify FILE.crx`.
An enterprise administrator may configure `ExtensionInstallForcelist` with
an update manifest; the package does not silently force-install extensions.

Permissions: `sidePanel`, `scripting` and `activeTab` provide
the editor and approved-site picker; `storage` provides the temporary session
cache; `clipboardWrite` is the fallback on approved sites; `offscreen` reads user-granted folder handles in an extension document. HTTP/HTTPS host permission covers dynamic user-approved sites
and the Google API endpoints; Chrome displays this as access to all sites even
though Pastex restricts injection using the allowlist described above.
