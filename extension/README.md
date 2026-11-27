# Pastex extension setup

Pastex is for one device running Linux Mint and Google Chrome 142 or newer. It
inserts Drive-backed snippets on explicitly allowed websites. The signed APT
package automatically registers the extension in Chrome only. See the
[installation instructions](../README.md#install-on-linux-mint).

## Google OAuth client

1. Install and enable Pastex in `chrome://extensions`. The extension ID is
   shown there; its redirect URL is `https://<extension-id>.chromiumapp.org/`.
2. Create a Google Cloud project and **Enable the Drive API**.
3. Configure the **OAuth consent screen**. Add your account as a test user if
   using Testing mode, and add `https://www.googleapis.com/auth/drive`.
4. **Create the OAuth client** as a **Web application**. Add the URL above as an
   **Authorized redirect URI**, including the trailing slash. Copy the client ID
   and client secret for your configuration file. Pastex uses authorization
   code flow with PKCE; it does not use the implicit flow.
5. **Publish the consent screen** only if appropriate for your Google project.
   A Testing-mode external app may require renewed authorization and is limited
   to test users. Restricted Drive scope can require Google's review for public
   distribution; check Google Cloud's current requirements.

The Drive scope grants access to all files the account can open, not only the
Pastex folder. Use an account and client you trust. A web-client secret is not a
secret from a person with access to this browser profile. Never publish your
configuration or tokens.

## Configuration

Create a folder in Drive for snippets and a separate empty index file. Create a
local JSON file (any name) with their Drive links and the OAuth client values:

```json
{
  "pastex-folder": "https://drive.google.com/drive/folders/YOUR_FOLDER_ID",
  "pastex-index": "https://drive.google.com/file/d/YOUR_INDEX_ID/view",
  "pastex-sites": ["https://web.whatsapp.com", "https://example.com"],
  "client_id": "YOUR_CLIENT_ID.apps.googleusercontent.com",
  "client_secret": "YOUR_CLIENT_SECRET"
}
```

`pastex-index` stores ordering and folded state. `pastex-sites` may instead be a
link to a Drive JSON file containing `{"allowed_sites": ["https://example.com"]}`.
Origins may use a leading `*.` wildcard, but not paths, ports or global
wildcards. `client_secret` is required if your OAuth client requires it.

Open the side panel and click **Config**. A file picker opens directly; choose
the JSON file and authorize your Google account. Configuration is then **cached**
in `chrome.storage.local` as `pastex-config-cache`; selecting a file again
updates it. The cache includes the client secret if supplied. The refresh token
and a derived snippet cache are also stored locally. Back up important snippets
in Drive and protect access to the browser profile.

Sync pending changes before changing configuration or upgrading to 2.34.2.
Older queued edits without a recorded original text are kept but cannot sync
automatically. If a site is added or removed, reload its tab after choosing the
updated configuration.

## Use and synchronization

- Click a snippet to insert it with a newline at the end. The crossed-out return
  button on a row inserts without trailing newlines. The toolbar toggle applies
  that behavior to normal clicks. Internal newlines are preserved.
- The Edit menu renames, edits or moves an item. Delete moves items to Drive
  trash. On WhatsApp, test in a chat with yourself before sending a message.
- Edits are saved locally in a queue, then sent when an allowed tab closes or
  the browser starts. Sync sends them when no allowed site is active. If Drive
  refuses a change or browser storage is full, the panel reports the problem.
- A changed Drive snippet is checked against its original text before an edit
  is sent; a conflict keeps the local edit queued. Drive API v3 does not
  document an atomic conditional media update here. A change between the check
  and upload can still race.
  Avoid editing the same files outside Pastex while changes are pending.
- If insertion only partly succeeds, inspect the field before retrying. A
  single-line field cannot retain a newline. When no editable field is focused,
  Pastex can copy the snippet for manual paste.

## Packaging and troubleshooting

The package contains `/usr/share/pastex/pastex.crx` and registers it through
`external_crx` in `/usr/share/google-chrome/extensions`. Upgrades remove the old
package-owned Chromium registration. Quit Chrome completely after an upgrade.

A release uses the same CRX signing key to preserve its extension ID; local
builds accept `PASTEX_CRX_KEY`. A different ID requires a new OAuth redirect
URI. An administrator may use Chrome's `ExtensionInstallForcelist` policy.

If sign-in fails, verify the redirect URL, client ID, secret, Drive API and
account access. If the panel reports a conflict, use the review button next to the status to
compare the original, pending, and current Drive text of the first pending edit.
Choose the pending text or the Drive text, then sync. The Drive choice is blocked
when later queued edits depend on the same snippet. This is a manual review, not
an atomic write. The copy button exports pending changes as JSON; it contains
snippet text, so keep it private. Do not delete extension storage while edits
are pending.
