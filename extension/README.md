# Pastex for Chrome and Chromium

Pastex is a browser extension for text snippets stored directly in Google
Drive, accessed as **you** through Google OAuth — no service account and no key
file. It operates on **only the sites you choose**. Configure Pastex with one
file of Drive links, `pastex-storage.json`, described below; the extension reads
it when it needs configuration and never publishes it. Google Drive and a
working internet connection are required.

## Install

Follow the [APT repository instructions](https://github.com/patrickbuhagiar24/paste/blob/main/apt-repo/README.md), then run `sudo apt update` and `sudo apt install pastex`. Enable the extension and restart the browser. Chrome/Chromium 141 or newer is required.

## Create the Google OAuth client

Pastex talks to Drive **as you**. There is no service account and no key file:
the extension asks Google for permission through Chrome's sign-in window, and
the token it receives belongs to the Google account that signed in. Files Pastex
creates are therefore yours, they sit in your own Drive storage, and they open
normally in Drive.

Each installation brings its own OAuth client, because this project is
self-hosted (the `.deb` is built by whoever installs it). There is no client
secret and none is needed: an extension is a public client, and Google's
implicit flow hands the token back to Chrome directly.

You need, once, about ten minutes:

1. **Note your extension's ID and redirect URL.** In `chrome://extensions`,
   enable *Developer mode*, find **Pastex**, and copy the 32-letter ID. Its
   redirect URL is

   ```
   https://<extension-id>.chromiumapp.org/
   ```

   Pastex prints the exact value for you, so it can be copied rather than
   typed: open the side panel, click **Config**, pick the configuration folder,
   and the status line reports the redirect URL for this installation. The ID
   is derived from the CRX signing key, so it is stable across upgrades of the
   signed package.

2. **Create a Google Cloud project.** Open
   <https://console.cloud.google.com/projectcreate>, give it a name such as
   `pastex`, and click *Create*. Select the new project in the project picker at
   the top of the page.

3. **Enable the Drive API.** Go to *APIs & Services → Library*, search for
   **Google Drive API**, open it and click *Enable*.

4. **Fill in the OAuth consent screen.** *APIs & Services → OAuth consent
   screen*: choose **External** (or **Internal** if the client is only for one
   Workspace organisation), set the app name (`Pastex`), your e-mail as the
   support address, and your e-mail as the developer contact. Save.
   Add the scope `https://www.googleapis.com/auth/drive.item` — Pastex asks for
   nothing else. It is the per-item scope: access reaches the items the links in
   `pastex-storage.json` name, not the rest of Drive. This scope is classified by Google as **non-sensitive**, so it
   needs no verification review and no security assessment.

5. **Create the OAuth client.** *APIs & Services → Credentials → Create
   credentials → OAuth client ID*. Application type: **Web application**. Name
   it `Pastex (Chrome extension)`. Under *Authorized redirect URIs* click *Add
   URI* and paste the redirect URL from step 1 **exactly**, including the
   trailing `/`. Leave *Authorized JavaScript origins* empty. Click *Create* and
   copy the **client ID** shown — it looks like
   `123456789012-abcdefghijklmnop.apps.googleusercontent.com`.

   Do not choose the *Chrome Extension* application type: Google only accepts
   that type for an extension published in the Chrome Web Store, and Pastex is
   installed from a package. The *Web application* type plus a registered
   `chromiumapp.org` redirect is the combination that works for a self-hosted
   extension.

6. **Publish the consent screen.** Still under *OAuth consent screen*, click
   *Publish app* and confirm. While the status stays *Testing*, every sign-in
   shows a warning page, the grant expires after seven days, and only the test
   users you listed may sign in. *In production* with a non-sensitive scope
   needs no review.

7. **Note the client ID.** It goes into `client_id` in the configuration file
   described in the next section.

That is the whole Google side. Nothing is shared with an e-mail address, no
folder has to be handed over, and no credential is stored on disk: the grant
stays with Google, and Pastex keeps only an access token in memory for about an
hour.

## Set up the configuration folder

Configuration is **one file of Drive links on your computer**. Click **Config**
once, pick the folder holding it, and Pastex reads it. Nothing needs to be
public, and no file in the folder is a credential.

Create `pastex-storage.json`:

```json
{
  "pastex-folder": "https://drive.google.com/drive/folders/YOUR_LIBRARY_FOLDER_ID",
  "pastex-index": "https://drive.google.com/file/d/YOUR_INDEX_FILE_ID/view",
  "pastex-sites": ["https://example.com", "https://*.example.org"],
  "client_id": "123456789012-abcdefghijklmnop.apps.googleusercontent.com"
}
```

| Key | Points at | Notes |
| --- | --- | --- |
| `pastex-folder` | the library folder in Drive | the snippets and folders Pastex manages |
| `pastex-index` | the order/collapse file | the id is read, never the listing; if the link does not open, the library still loads in Drive's order |
| `pastex-sites` | the site allowlist | either an inline array (as above) or a link to a JSON file holding `{"allowed_sites": [...]}` |
| `client_id` | the OAuth client from the guide above | public, not a secret |

**The links are the ones Drive's Share button gives you.** Every shape is
accepted and reduced to the id the API needs:

```
1AbCdEfGhIjKlMnOpQrStUv                                    (a bare id)
https://drive.google.com/drive/folders/1AbCdEf...          (folder link)
https://drive.google.com/drive/u/0/folders/1AbCdEf...?usp=sharing
https://drive.google.com/file/d/1AbCdEf.../view            (file link)
https://drive.google.com/open?id=1AbCdEf...                (?id= link)
```

A link that is none of those is reported by key name, not guessed at.

The allowlist takes HTTP(S) origins only, without paths, ports, credentials or
global wildcards; wildcard subdomains include the base domain. An empty list
allows no sites, and a missing, invalid or unreadable list fails closed.

Then:

1. Open the side panel and click **Config**.
2. Pick the folder holding `pastex-storage.json`. A folder picker opens directly
   from the click. The file must carry that exact name. Any other `.json` file in the
   folder is ignored, and a renamed file is reported as missing rather than
   guessed at. Nested folders are ignored.
3. Every problem is reported at once and named by key, so one pick is enough to
   see everything: `pastex-storage.json needs a pastex-folder link to the
   library folder`, `needs an pastex-index link to the order/collapse file`,
   `needs the client_id of a Google OAuth client`. Fix them all, then pick the
   folder again.
4. The first read opens Google's sign-in window once. Choose the Google account
   that owns the library folder and accept. Every later read is silent: Pastex
   asks Chrome for a token without a page, and the page only appears again if
   you signed out or withdrew access in your Google account.

Only the **surface** of the folder is scanned: files ending in `.json` are
read, subfolders are skipped without being entered, and a `.json` file that does
not parse is ignored rather than failing the whole folder.

Configuration is then **cached**, not re-read. The panel scans the folder once,
validates it, and stores the parsed JSON under `pastex-config-cache` in
`chrome.storage.local`. The side panel and the background worker both read that
cache afterwards, so opening Pastex later never opens the folder again, not even
after a browser restart. Choosing a new folder replaces the cache; a folder that
fails validation leaves the previous cache untouched.

There is no folder-handle store: the grant is used inside the picker gesture
and never persisted, so `extension/handle-store.js` no longer exists.

This is why the worker needs no offscreen document: a File System Access handle
is not usable outside the page that holds it, and asking for one from an
extension document fails with *"The request is not allowed by the user agent or
the platform in the current context."* Nothing publishes configuration to Drive,
and the folder holds no credential at all — only a public client ID — so a
leaked configuration folder grants nothing. Snippet text is cached separately,
keyed by a SHA-256 hash of the folder and the client ID.

After modifying site JSON, open **Config** again and reselect the folder to
update script registration and reload open tabs. Existing tabs may retain a script until reloaded, but
insertion requests re-check the current allowlist. The in-page launcher checks the allowlist on focus and refreshes that authorization while the field stays focused; it expires after 30 seconds. Changes to Drive
configuration are picked up on refresh. No credential is logged, stored in
Chrome storage or displayed: the access token Google returns lives in the
worker's memory for about an hour, and the permission itself stays with Google.

Each snippet maps to a Drive text file. Creating, editing and renaming write
straight to Drive; deleting moves the item (including a folder's contents) to
Drive trash. After changes, Pastex reloads the library from Drive. The side
panel uses an in-memory Chrome session cache plus a derived local cache that
stores the **whole** library, folders and every body (a collapsed folder is
not deferred when the cache is written). Reopening the panel therefore shows
every snippet without a Drive request, and opening a snippet never waits for
a download. The first open after a browser restart is served from
`chrome.storage.local` before a silent refresh starts.

`storage.local` is bounded, so a write degrades in stages instead of failing
silently, in one fixed order: every stored library complete, then this library
alone complete, then this library alone compacted (each body kept once), then
nothing. A snapshot that is on screen keeps its bodies before another account's
snapshot keeps its place, and a complete snapshot is never replaced by a
compacted one while it still fits. A cached open reports how many rows still miss a
body; the panel shows the list at once and the worker fills those rows without
listing Drive again, then repaints. The cache contains snippet text and no
credential, and is accessible to the extension, not websites.

A background check is also cheaper than before: a metadata-only pass
(`verify`) compares folder and item signatures and reads the timing label
without downloading a body, and only a real difference falls back to the full
listing. The panel always paints from the cache in the same frame; Drive is
contacted in the background, never in front of the next click.

### Background synchronization

Pastex never polls Drive while you work. The service worker watches the
browser's idle state (`chrome.idle`) and an alarm (`chrome.alarms`);
when there has been no keyboard or mouse input for a minute, or the screen is
locked, it performs exactly one Drive check. The result is compared with the
cached snapshot: an unchanged library only moves a small freshness stamp, and
the snapshot itself is rewritten only when Drive really reports a difference.
An open side panel is repainted from the fresh cache without a visible loading
state. A panel operation in progress (a save, a reorder, a manual refresh)
suppresses the background check, and the background check never writes to
Drive.

The interval between two checks adapts: it starts at two minutes, doubles
after every pair of checks that finds no change, and stops at thirty minutes.
A change returns it to the fast interval at once. The learned interval is
stored in `chrome.storage.local`, so a restarted worker keeps it.

A check asks Drive only for the metadata the library needs, and a listing
walks the pages with Drive's own cursor, so a page that fails is retried from
the same place instead of starting the folder over. Every node keeps a
signature of the metadata Drive reported for it (name, type, timestamp, Drive's
content checksum and labels). A node with the same signature is reused as it
is, so only the branch that really changed is listed and downloaded again.
Snippet bodies are stored under the SHA-256 of their content, which keeps
them across a rebuild of the tree and recognizes an unchanged body even when
its timestamp moved.

The panel shows when the library was last confirmed with Google Drive, next to
the drive status. **↻ Sync** sends the waiting changes; it does not read
Drive, so the list on screen is never reloaded by it.

Cached content can be stale, including after a folder is changed or access is
revoked elsewhere. Reopening the panel reads the cached list; a configuration
change or a fresh install performs the full sync. Explicit Drive permission
errors clear the cached snapshot. An internet connection is
required for the initial load and all changes. Account or configuration changes
invalidate the snapshot. No snippet changes are queued for offline sync.

**Select** enables multi-selection instead of pasting. Use **Up** or **Down**
to move selected items within their own folders, and **Delete** to move them to
Drive trash after confirmation. Click the folder arrow to expand or collapse it
while selecting. Moving is disabled during search; clear the search first.
Manual order and the collapsed state of the folders are stored together in one
small file in the library folder, `pastex-index.json`. One reorder or one
**Collapse all** is therefore a single Drive write instead of one write per
item, and the structure survives a panel reload, a browser restart and a move
to another computer using the same Drive folder. The file is Pastex's own
metadata: it is never shown as a snippet, and it can be deleted at any time —
the library then appears in Drive's own order with every folder open. It used
to be a custom property on the root folder, which Drive limits to 124 bytes
including the key; two rows already exceeded that, so every reorder and every
fold was refused. Libraries that were ordered with earlier versions keep their
per-item `pastex_order` and `pastex_collapsed` labels, but those labels are
**ignored**: the index file is the only source of order and collapse state. Nothing in the snippets
themselves changes. Unranked new files follow ranked items in Drive's
normal order. Concurrent reordering on different devices can overwrite the
same metadata; refresh before editing shared lists.

### Upgrading from an older version

Old configuration files remain untouched. To use them, copy the three JSON
files into a local folder and pick that folder in **Config**; Pastex no longer
reads or writes the old files.

Before upgrading, sync any browser-only snippets to Drive using the previous
version and verify them in Google Drive. The new version does not read, migrate,
or erase the old browser-storage library; unsynchronized local snippets will
not appear in the new interface. Do not clear the old extension's data until
all needed snippets have been safely backed up. A stable CRX signing key is
needed to keep the same extension identity across upgrades.

## Usage

Open the side panel with the toolbar icon to create, edit, search and organize
snippets. The toolbar reads **▸ Collapse all**, **Select**, **↻ Sync**,
**Config**, **＋ Text** and **＋ Folder**. **＋ Text** and **＋ Folder** are
filled primary buttons; **Sync** takes that role while Drive is
unavailable, because it is the action that helps then. **Collapse all** folds
every folder and clears the search first. The drive bar shows a state dot and
`Google Drive • session cache · N snippets` when the list came from the
session or local cache, and the moment of the last check with Drive is shown
next to it.

## Reading from the cache, writing when you leave

Reading never touches Google Drive. The first time a configuration folder is
loaded, the library is downloaded once and stored whole — every folder, every
snippet body — and every later open is served from that snapshot, so the list
appears instantly even when Drive is slow. Later changes arrive from the
background check, which runs only while the machine is idle or locked.

While you are on an allowed site, an edit from the panel is applied to the
cached list at once and its Drive write waits in a durable queue
(`chrome.storage.local`, so it survives a browser restart). Nothing is sent to
Drive while you are still on the page.

Every edit is treated the same way, including the two that are easy to forget:

| Edit | What the cache holds at once | What the Drive write is |
| --- | --- | --- |
| create text / folder | the new row, with a `local-` id | one upload |
| edit body, rename | the new name and text | one `edit-text` write |
| delete | the row is gone | trash, in batches of 6 |
| **reorder** | the new order of every folder | **one** write to the root folder's index label |
| **fold / unfold** | the new collapsed flags | **one** write to the same label |

Reorder and fold share that single label, so neither costs a write per row, and
a fold never loses a pending reorder — the whole index is sent together.

The queue is flushed when the tab is **closed**, and not when it is merely
hidden: switching to another tab is not closing the site. It is sent once more
on the next browser start if the browser was quit first. A browser start with
an empty queue touches Drive not at all — there is nothing to send and the
cache already holds the whole library. The flush finishes with one full read
so the cache matches Drive again, including the ids Drive gave new rows.
One rule holds everywhere: while any allowed site still has a visible tab, the
queue waits, and so does the background check.
The drive bar shows how many changes are waiting. A write Drive refuses
permanently (the row was deleted elsewhere) is dropped from both the queue and
the cache instead of being retried forever.

**↻ Sync** is the one button that reaches Drive on purpose, and it only writes:
it sends whatever the queue holds and stops there, so the list on screen is not
reloaded and nothing is fetched. A row created locally keeps its `local-` id in
the cache until the next read (the next panel open) turns it into the id Drive
assigned. If an allowed site is still open the button holds the queue back and
the status line says how many changes are waiting: it never writes to Drive
while you are on a site.

The panel is built to be readable at a glance. Every text pair reaches WCAG
AA (body text and row labels reach AAA), buttons have a visible outline and
the selected row is marked by a strong tint, a filled accent bar and a
heavier label. Actions have one meaning each: **Save**, **＋ Text** and
**＋ Folder** are filled primary buttons, **Delete** is filled red and turns
red under the pointer, and a disabled primary steps back to the plain
outline. Row actions (rename, edit, delete) appear on hover, on the selected
row and on keyboard focus. A dark theme follows the system preference, and
`prefers-contrast: more` and `prefers-reduced-motion: reduce` are honoured.
The palette and these rules are checked numerically in
`extension/test/appearance.test.js`.

The toolbar icon itself is a toggle: click it to open the side panel, click it
again to close it (the browser owns that behavior through
`sidePanel.setPanelBehavior`). The panel has no Close button of its own — the
toolbar icon is the switch. Chrome/Chromium 141 or newer is required, so the
toggle is always available.

On an allowed site, focus an editable field and click the floating
**⚡** button to open the Pastex side panel. The same button is a toggle:
while the panel is open it reads **×** and closes it again. The soundness of
the click is unchanged: the same short-lived capability authorizes either
action. The launcher is a deep indigo
disc with a near-black rim, a white inner ring and a soft halo, so it reads
at a glance on a light page, on a dark page and on a colourful one. The halo
stops when the system asks for reduced motion, and the rim grows when the
system asks for more contrast. `assets/launcher-preview.html` shows the
launcher on all three backgrounds. There is
no Pastex keyboard shortcut or context-menu entry. Click a side-panel snippet
to insert it; its row has an **Edit** button, which opens a small menu holding
**Rename**, **Edit text** and **Move to folder…**, and a **Delete** button. The
complete snippet text is inserted verbatim, including newlines and trailing
spaces, into textareas and editable fields; a single-line input cannot display
line breaks. The allowlist is cached for 30 seconds so a click does not have to
re-read the configuration folder; reselecting the folder refreshes it
immediately. If no editable field is focused on an allowed page, the text is
copied to the clipboard. Restricted browser pages cannot receive scripts. Sites
not listed in `pastex-sites.json` are not injected or targeted.

## Troubleshooting

- If the status line says *Google does not recognise this extension's redirect
  URL*, copy the URL it quotes (`https://<extension-id>.chromiumapp.org/`) into
  *Authorized redirect URIs* of the OAuth client and try **↻ Sync** again. The
  URL changes if the extension ID changes, which happens when the package is
  rebuilt with another CRX key.
- If the status line says *Your Google account is out of Drive storage*, free
  space in Drive, or move the library into a folder inside a **Shared Drive**;
  files created there are counted against the Shared Drive, not your account.
  The snippet you typed is **not lost**: it stays queued and the panel says so.
- If the sign-in window never appears, or Pastex reports that the sign-in window
  is unavailable, sign in to Chrome with a Google account and avoid a managed
  profile that blocks extensions. Reload Pastex from `chrome://extensions`.
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
- If configuration is missing or the file in the folder cannot be read, open
  **Config** and reselect the folder. The folder must contain
  `pastex-storage.json` under exactly that name. The worker console can show
  errors.
- If the order and the folded folders were forgotten, the `pastex-index` link
  probably points at a file the signed-in account cannot open; the library then
  loads in Drive's order with every folder open, and the next reorder writes the
  file again.
- If Drive says 404, check the folder ID and that the signed-in account can see
  that folder. Token errors can mean a deleted OAuth client or a disabled Drive
  API. Snippets appear progressively while loading; a stalled Google request
  times out and reports an error. Check network access, the folder ID, the
  registered redirect URL and the Drive API, then use **↻ Sync**. Individual
  unreadable files display a warning.
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
cache; `alarms` and `idle` drive the background synchronization that runs only
while the user is away; `clipboardWrite` is the fallback on approved sites;
`fileSystemAccess` lets the side panel's folder picker read the chosen
directory; the grant is used once, then the parsed configuration is cached in
`storage`. HTTP/HTTPS host
permission covers dynamic user-approved sites and the Google API endpoints;
Chrome displays this as access to all sites even
though Pastex restricts injection using the allowlist described above.
