# Pastex for Chrome and Chromium

Pastex is a browser extension for text snippets stored directly in Google
Drive. It operates on **only the sites you choose**. Configure Pastex with one
JSON manifest that points to the three configuration files below. The
extension fetches those links when it needs configuration; it does not copy
credentials into a configuration directory. Google Drive and a working
internet connection are required.

## Install

Follow the [APT repository instructions](https://github.com/patrickbuhagiar24/paste/blob/main/apt-repo/README.md), then run `sudo apt update` and `sudo apt install pastex`. Enable the extension and restart the browser. Chrome/Chromium 116 or newer is required.

## Set up the configuration manifest

1. Enable the **Drive API** in Google Cloud, create a service account and
   download its JSON key. Share the snippets folder with its `client_email` as
   an Editor. Upload the key as `service-account.json`.
   **Security warning:** the key must be downloadable by the browser for this
   link-based flow to work. If the Drive link is public, anyone with the link
   has your service-account private key. Use a restricted, short-lived key and
   rotate it immediately if the link leaks. Do not use a link you cannot
   afford to expose.
2. Upload `pastex-google-drive.json`, for example:

   ```json
   {"folder_url": "https://drive.google.com/drive/folders/YOUR_FOLDER_ID"}
   ```

   Alternatively use `{"folder_id": "YOUR_FOLDER_ID"}`. When both are set,
   the ID wins.
3. Upload `pastex-sites.json`, for example:

   ```json
   {"allowed_sites": ["https://example.com", "https://*.example.org"]}
   ```

   An empty list allows no sites. Use HTTP(S) origins only, without paths,
   ports, credentials or global wildcards. Wildcard subdomains include the
   base domain. If missing, invalid or inaccessible, the allowlist fails closed.
4. Open the side panel and click **Config**. The file picker opens directly;
   select a local JSON manifest file with exactly these three keys:

   ```json
   {
     "pastex-google-drive.json": "https://drive.google.com/file/d/DRIVE_FILE_ID/view?usp=sharing",
     "pastex-sites.json": "https://drive.google.com/file/d/SITES_FILE_ID/view?usp=sharing",
     "service-account.json": "https://drive.google.com/file/d/KEY_FILE_ID/view?usp=sharing"
   }
   ```

   Markdown-wrapped links such as `[file](https://drive.google.com/file/d/ID/view)` are also accepted. Only HTTPS `drive.google.com/file/d/…/view` and `drive.google.com/open?id=…` links are allowed; folder URLs, other hosts and local paths are rejected. All three files are downloaded and validated before the manifest is accepted. If any is invalid, the previous configuration is kept.

Chrome stores only the three download URLs in extension IndexedDB, never the
downloaded JSON or the service-account key. Pastex fetches them into memory on
demand. The links keep working across restarts as long as they stay downloadable.
Snippet text is cached separately in `chrome.storage.local`, keyed by a
SHA-256 hash of the configuration; credentials are never written there.

After modifying site JSON, open **Config** again and reselect the manifest to
update script registration and reload open tabs. Existing tabs may retain a script until reloaded, but
insertion requests re-check the current allowlist. The in-page launcher checks the allowlist on focus and refreshes that authorization while the field stays focused; it expires after 30 seconds. Changes to Drive
configuration are picked up on refresh. The service-account key is not logged,
stored in Chrome storage or displayed; it is read into extension memory and used
to sign authentication requests to Google.

Each snippet maps to a Drive text file. Creating, editing and renaming write
straight to Drive; deleting moves the item (including a folder's contents) to
Drive trash. After changes, Pastex reloads the library from Drive. The side
panel uses an in-memory Chrome session cache plus a derived local cache:
reopening it does not download the library again, and the first open after a
browser restart is served from `chrome.storage.local` before a silent refresh
starts. A snapshot older than one minute refreshes in the background. The
cache contains snippet text (not the service-account key) and is accessible to
the extension, not websites. If the library exceeds Chrome's
session-storage quota, Pastex falls back to the service worker's memory; the
cache may then disappear when the worker stops.

Cached content can be stale, including after a folder is changed or access is
revoked elsewhere. Use **↻ Refresh** to fetch current Drive contents; explicit
Drive permission errors clear the cached snapshot. An internet connection is
required for the initial load and all changes. Account or configuration changes
invalidate the snapshot. No snippet changes are queued for offline sync.

**Select** enables multi-selection instead of pasting. Use **Up** or **Down**
to move selected items within their own folders, and **Delete** to move them to
Drive trash after confirmation. Click the folder arrow to expand or collapse it
while selecting. Moving is disabled during search; clear the search first.
Manual order is saved in each item's Google Drive `pastex_order` property, and
a folder's collapsed state in its `pastex_collapsed` property. Both are Drive
metadata, so the whole structure survives a panel reload, a browser restart and
a move to another computer using the same Drive folder. **Collapse all** writes
`pastex_collapsed` to every folder. Unranked new files follow ranked items in
Drive's normal order. Concurrent reordering on different devices can overwrite
the same metadata; refresh before editing shared lists.

### Upgrading from an older version

Old configuration files remain untouched. To use them, upload copies and put
their links in a manifest; Pastex no longer reads or writes the old files.

Before upgrading, sync any browser-only snippets to Drive using the previous
version and verify them in Google Drive. The new version does not read, migrate,
or erase the old browser-storage library; unsynchronized local snippets will
not appear in the new interface. Do not clear the old extension's data until
all needed snippets have been safely backed up. A stable CRX signing key is
needed to keep the same extension identity across upgrades.

## Usage

Open the side panel with the toolbar icon to create, edit, search and organize
snippets. **Collapse all** folds every folder and clears the search first. The
Drive bar shows `Google Drive • session cache · N snippets` when the list came
from the session or local cache; the label is kept for compatibility.

On an allowed site, focus an editable field and click the floating
**⚡** button to open the Pastex side panel. There is
no Pastex keyboard shortcut or context-menu entry. Click a side-panel snippet
to insert it; its row has **Rename**, **Edit**, and **Delete** actions. The
complete snippet text is inserted verbatim, including newlines and trailing
spaces, into textareas and editable fields; a single-line input cannot display
line breaks. The allowlist is cached for 30 seconds so a click does not have to
re-download the linked configuration; reselecting the manifest refreshes it
immediately. If no editable field is focused on an allowed page, the text is
copied to the clipboard. Restricted browser pages cannot receive scripts. Sites
not listed in `pastex-sites.json` are not injected or targeted.

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
- If configuration is missing or a linked file cannot be downloaded, open
  **Config** and reselect the manifest. Each link must be an HTTPS
  `drive.google.com/file/d/…/view` URL that downloads without a sign-in; HTML
  confirmation pages are reported as invalid JSON. The worker console can show
  errors.
- If Drive says 404, share the folder with the service account email. Token
  errors can indicate a malformed key or a disabled Drive API. Snippets appear
  progressively while loading; a stalled Google request times out and reports
  an error. Check network access, the folder ID, permissions, and Drive API,
  then use **↻ Refresh**. Individual unreadable files display a warning.
- The built-in Chrome Web Store, `chrome://` pages and other browser-protected
  pages cannot run extensions, even if listed.

## Maintainers: builds and permissions

`scripts/install.sh` is for local development only: it builds a `.deb` from
the checkout and installs it with `apt`, which produces an ephemeral extension
ID unless `PASTEX_CRX_KEY` points at the release key. Users should install the
signed package from the APT repository.

The apt package ships `/usr/share/pastex/pastex.crx`, the unpacked extension,
and external extension metadata for Chrome and Chromium. `external_crx`
registers the CRX in `/usr/share/google-chrome/extensions` and
`/usr/share/chromium/extensions`. It does not install a native host. Chrome
reads those registration files when the browser process starts, not on every
window close: after an upgrade, quit the browser completely (or reboot) before
checking that the new extension version is active.

CRX IDs come from the signing key. Configure the `PASTEX_CRX_PRIVATE_KEY` GitHub Actions secret for
release CI (use `PASTEX_CRX_KEY` as the local build environment variable) and use the same key on every upgrade. Without it, local builds use
an ephemeral key: the extension ID and browser storage change, and the build is
for testing only. Verify with `python3 scripts/pack-crx.py --verify FILE.crx`.
An enterprise administrator may configure `ExtensionInstallForcelist` with
an update manifest; the package does not silently force-install extensions.

Permissions: `sidePanel`, `scripting` and `activeTab` provide
the editor and approved-site launcher; `storage` provides the temporary session
cache; `clipboardWrite` is the fallback on approved sites; `offscreen` reads configuration and downloads manifest links in an extension document. HTTP/HTTPS host permission covers dynamic user-approved sites,
the Google API endpoints and the manifest's Drive links; Chrome displays this as access to all sites even
though Pastex restricts injection using the allowlist described above.
