/* Google Drive API access with OAuth, as the person using Pastex.
 *
 * The access token is issued to the signed-in Google account, so the files it
 * creates belong to that account and are counted against its own storage. A
 * service account was used before and could not do this: it has no storage of
 * its own, so Google refused every new file.
 *
 * There is no client secret anywhere. The extension is a public client: its
 * bundle can be read by anyone, so a secret shipped in it would not be one.
 *
 * Snippet files are read and written directly in the configured Drive folder.
 */

(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module && module.exports) {
    module.exports = api;
  } else {
    root.PastexDrive = api;
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var DRIVE_URL = "https://www.googleapis.com/drive/v3";
  // Google's own sign-in page, and the endpoint that revokes a token.
  var AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth";
  var REVOKE_URL = "https://oauth2.googleapis.com/revoke";
  var UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3";
  // drive.file covers exactly the files and folders this extension created or
  // that the user handed to it, which is everything Pastex needs. The full
  // drive scope would also let it read unrelated files, so it is not requested.
  var SCOPES = "https://www.googleapis.com/auth/drive.file";
  var FOLDER_MIME = "application/vnd.google-apps.folder";
  var TEXT_MIME = "text/plain";
  var REQUEST_TIMEOUT_MS = 20000;

  // Bound both the response and body read. Some connections otherwise leave
  // the panel on "loading" indefinitely. Abort also releases the socket.
  async function withTimeout(operation, timeoutMs) {
    var controller = new AbortController();
    var timer;
    var timeout = new Promise(function (_, reject) {
      timer = setTimeout(function () {
        reject(new Error("Google request timed out. Check your internet connection and try Refresh."));
        controller.abort();
      }, timeoutMs || REQUEST_TIMEOUT_MS);
    });
    try {
      return await Promise.race([operation(controller.signal), timeout]);
    } finally {
      clearTimeout(timer);
    }
  }

  // --- OAuth tokens ---------------------------------------------------------
  //
  // Pastex has no server and no client secret, so it uses Google's implicit
  // flow through chrome.identity.launchWebAuthFlow(): Chrome opens Google's
  // consent page and hands the resulting redirect URL back to the extension.
  // The access token arrives in that URL's fragment and is never written to
  // disk. Every request then runs as the signed-in person, so the files Pastex
  // creates belong to that person and count against their own Drive storage.
  //
  // The client id is not compiled into the extension: this project is
  // self-hosted, so each installation brings its own OAuth client, recorded in
  // pastex-oauth.json (see the guide in README.md).

  function identityApi(overrides) {
    var api = (overrides && overrides.identity) || (typeof chrome !== "undefined" && chrome && chrome.identity);
    if (!api || typeof api.launchWebAuthFlow !== "function" || typeof api.getRedirectURL !== "function") {
      throw new Error("Chrome's sign-in window is unavailable. Reload Pastex from chrome://extensions and try again.");
    }
    return api;
  }

  // One token per client id, kept only in memory: an access token lives about
  // an hour, and a discarded worker simply signs in silently again.
  var tokens = new Map();

  function cachedToken(clientId) {
    var entry = tokens.get(clientId);
    if (!entry) return "";
    if (entry.expiresAt <= Date.now()) { tokens.delete(clientId); return ""; }
    return entry.token;
  }

  function storeToken(clientId, token, expiresIn) {
    var seconds = Number(expiresIn) > 0 ? Number(expiresIn) : 3600;
    // A minute of slack: a token that expires mid-request is worse than one
    // fetched a moment early.
    tokens.set(clientId, { token: token, expiresAt: Date.now() + Math.max(60, seconds - 60) * 1000 });
  }

  function randomState() {
    var bytes = new Uint8Array(16);
    globalCrypto().getRandomValues(bytes);
    return Array.from(bytes, function (b) { return b.toString(16).padStart(2, "0"); }).join("");
  }

  function globalCrypto() {
    var api = typeof crypto !== "undefined" ? crypto : null;
    if (!api || !api.getRandomValues) throw new Error("A secure random source is unavailable in this context.");
    return api;
  }

  // Google answers either in the fragment (response_type=token) or, when it
  // refuses, in the query string. Both are read, and `state` is checked before
  // a token is accepted.
  function readRedirect(redirectUrl, expectedState) {
    var url = new URL(redirectUrl);
    var fragment = new URLSearchParams(url.hash.replace(/^#/, ""));
    var query = url.searchParams;
    var state = fragment.get("state") || query.get("state") || "";
    if (expectedState && state !== expectedState) throw new Error("Google's answer did not match this request. Try signing in again.");
    var failure = fragment.get("error") || query.get("error");
    if (failure) {
      var detail = fragment.get("error_description") || query.get("error_description") || failure;
      throw new Error(detail);
    }
    var token = fragment.get("access_token") || query.get("access_token") || "";
    if (!token) throw new Error("Google's answer held no access token.");
    return { token: token, expiresIn: fragment.get("expires_in") || query.get("expires_in") };
  }

  function authUrl(clientId, redirectUri, interactive, state) {
    var url = new URL(AUTH_URL);
    url.searchParams.set("client_id", clientId);
    url.searchParams.set("redirect_uri", redirectUri);
    url.searchParams.set("response_type", "token");
    url.searchParams.set("scope", SCOPES);
    url.searchParams.set("state", state);
    // A silent request never shows a page: it only works while the person has
    // already granted Pastex access, which is exactly when no page is wanted.
    url.searchParams.set("prompt", interactive ? "select_account" : "none");
    return url.toString();
  }

  // One trip through Chrome's sign-in window. `interactive` decides whether a
  // page may appear: false means Google must answer silently from a grant it
  // already holds, which is the normal case on every sync after the first.
  function authorize(api, clientId, redirectUri, interactive) {
    var state = randomState();
    return new Promise(function (resolve, reject) {
      try {
        api.launchWebAuthFlow({ url: authUrl(clientId, redirectUri, interactive, state), interactive: interactive },
          function (redirectedTo) {
            var failure = typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.lastError;
            if (failure) { reject(new Error(failure.message || "Google sign-in failed.")); return; }
            if (!redirectedTo) {
              reject(new Error(interactive ? "Google sign-in returned no answer."
                : "Pastex is not signed in yet. Sync once and accept Google's request."));
              return;
            }
            try { resolve(readRedirect(redirectedTo, state)); }
            catch (err) { reject(err); }
          });
      } catch (err) {
        reject(new Error("Google sign-in failed: " + (err && err.message ? err.message : err)));
      }
    });
  }

  /* getAuthToken({ clientId, interactive }) -> access token string.
   *
   * The token comes from the browser's sign-in window, never from a key file.
   * A cached, unexpired token is reused, so an ordinary sync costs nothing.
   * Otherwise a silent attempt comes first: Chrome's worker is restarted often,
   * and the grant Google already holds is enough, so no page appears. Only when
   * that is refused — first run on this computer, or access withdrawn — is the
   * interactive page opened, and only if the caller allows it. */
  async function getAuthToken(options) {
    options = options || {};
    var clientId = String(options.clientId || "").trim();
    if (!clientId) throw new Error("Add pastex-oauth.json with the client_id of a Google OAuth client.");
    var cached = cachedToken(clientId);
    if (cached) return cached;
    var api = identityApi(options);
    var redirectUri = options.redirectUri || api.getRedirectURL();
    var parsed;
    try {
      parsed = await authorize(api, clientId, redirectUri, false);
    } catch (err) {
      // A silent refusal is not a bug to report: Google simply will not answer
      // without asking the person, so the message says what to do instead.
      if (!options.interactive) throw explainSignIn(err, redirectUri, true);
      try {
        parsed = await authorize(api, clientId, redirectUri, true);
      } catch (prompted) {
        throw explainSignIn(prompted, redirectUri);
      }
    }
    storeToken(clientId, parsed.token, parsed.expiresIn);
    return parsed.token;
  }

  // The one sign-in failure that is a configuration value rather than a bug: the
  // console must list the exact redirect URL Chrome reports. It is quoted in the
  // message because the panel has a single status line and no help panel.
  function explainSignIn(err, redirectUri, silent) {
    var reason = err && err.message ? err.message : String(err);
    if (silent && !/redirect_uri/i.test(reason)) {
      return new Error("Pastex is not signed in yet. Sync once and accept Google's request. Google said: " + reason);
    }
    if (/redirect_uri/i.test(reason)) {
      return new Error("Google does not recognise this extension's redirect URL. Add " +
        redirectUri + " to the authorized redirect URIs of the OAuth client. Google said: " + reason);
    }
    return err instanceof Error ? err : new Error(reason);
  }

  // The redirect URL to register in the Google Cloud console. Chrome derives it
  // from the extension id, so it must be read from a running installation
  // rather than from a document. Used by Config to show the exact value.
  function getRedirectURL(overrides) {
    return identityApi(overrides).getRedirectURL();
  }

  // A token Google has already revoked cannot be reused. Dropping it from the
  // cache makes the next call sign in again instead of leaving the panel stuck
  // until the browser restarts.
  async function removeCachedToken(token) {
    tokens.forEach(function (entry, clientId) {
      if (entry.token === token) tokens.delete(clientId);
    });
  }

  /* Signing out revokes the token at Google as well as forgetting it here, so
   * nothing that was handed out stays valid. */
  async function signOut() {
    var issued = Array.from(tokens.values()).map(function (entry) { return entry.token; });
    tokens.clear();
    for (var i = 0; i < issued.length; i++) {
      await withTimeout(async function (signal) {
        await fetch(REVOKE_URL, {
          method: "POST", signal: signal,
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: new URLSearchParams({ token: issued[i] }).toString(),
        });
      }, REQUEST_TIMEOUT_MS).catch(function () { /* the token is already forgotten */ });
    }
  }

  // --- Drive v3 REST helpers ---

  async function driveFetch(token, path, options) {
    options = options || {};
    var fetchImpl = options.fetchImpl || fetch;
    var base = options.upload ? UPLOAD_URL : DRIVE_URL;
    var url = new URL(base + path);
    // Shared drives require explicit opt-in on Drive file operations.
    if (path.indexOf("/files") === 0) url.searchParams.set("supportsAllDrives", "true");
    Object.keys(options.query || {}).forEach(function (key) {
      url.searchParams.set(key, options.query[key]);
    });
    var headers = { Authorization: "Bearer " + token };
    if (options.contentType) {
      headers["Content-Type"] = options.contentType;
    }
    return withTimeout(async function (signal) {
      var res = await fetchImpl(url.toString(), {
        method: options.method || "GET", headers: headers, signal: signal,
        body: options.body === undefined ? undefined : options.body,
      });
      if (!res.ok) {
        var text = await res.text().catch(function () { return ""; });
        var detail = text;
        try {
          var parsed = JSON.parse(text);
          if (parsed && parsed.error && parsed.error.message) detail = parsed.error.message;
        } catch (ignored) { /* keep the raw text */ }
        var message = String(detail);
        // A full Drive is not something Pastex can retry away, and Drive's own
        // wording does not say whose storage is full. The sentence is spelled
        // out so the panel can be honest about what to free.
        if (/storage quota|storageQuotaExceeded/i.test(message)) {
          throw new Error("Your Google account is out of Drive storage, so Google will not " +
            "accept new files. Free space in Drive, or use a folder inside a Shared Drive " +
            "(that space is counted against the Shared Drive, not your account). " +
            "Drive said: " + message.slice(0, 200));
        }
        throw new Error("Drive API " + (options.method || "GET") + " " + path + " failed (HTTP " + res.status + "): " + message.slice(0, 300));
      }
      var contentType = res.headers.get("content-type") || "";
      if (!options.textResponse && contentType.indexOf("json") !== -1) return res.json();
      return res.text();
    }, options.timeoutMs);
  }

  // A page that fails for a transient reason is retried from the very same
  // cursor, so the pages before it are never fetched again.
  function transientFailure(err) {
    var message = String((err && err.message) || err || "");
    return /HTTP (429|5\d\d)\b/.test(message) ||
      /network|fetch failed|timed out|timeout|ECONN/i.test(message);
  }

  async function listChildrenPage(token, query, fetchImpl, attempts) {
    var tries = attempts === undefined ? 1 : attempts;
    for (var attempt = 0; ; attempt++) {
      try {
        return await driveFetch(token, "/files", { query: query, fetchImpl: fetchImpl });
      } catch (err) {
        if (attempt >= tries || !transientFailure(err)) throw err;
      }
    }
  }

  // Only the metadata the library really uses is requested: the name, the
  // type, the timestamps, Drive's own content checksum and the labels. The
  // page token is Drive's cursor: it walks the pages of a folder and the
  // last cursor is reported back to the caller.
  async function listChildren(token, folderId, fetchImpl, options) {
    options = options || {};
    var files = [];
    var pageToken = options.startPageToken || "";
    var seenTokens = new Set();
    var cursor = "";
    do {
      var query = {
        q: "'" + folderId + "' in parents and trashed = false",
        fields: "nextPageToken,files(id,name,mimeType,modifiedTime,sha256Checksum,size,properties)",
        pageSize: "1000",
        includeItemsFromAllDrives: "true",
        orderBy: "folder,name",
      };
      if (pageToken) {
        query.pageToken = pageToken;
      }
      // The cursor of this page is remembered, so a failed page is retried
      // from the same place and the pages before it are not fetched again.
      cursor = pageToken;
      var data = await listChildrenPage(token, query, fetchImpl, options.retries);
      (data.files || []).forEach(function (file) {
        files.push(file);
      });
      pageToken = data.nextPageToken || "";
      if (pageToken && seenTokens.has(pageToken)) throw new Error("Drive returned a repeated page token.");
      if (pageToken) seenTokens.add(pageToken);
    } while (pageToken);
    if (typeof options.onCursor === "function") options.onCursor(cursor || "");
    return files;
  }

  // The root folder carries the labels of the library. Its custom properties
  // cannot: Drive allows 124 bytes per property including the key, and the
  // index names every folder and every id, so two rows already overflow it and
  // every reorder or fold was refused with HTTP 400. The index therefore lives
  // in one small JSON file next to the snippets (see INDEX_NAME).
  var INDEX_NAME = "pastex-index.json";

  async function getFolder(token, folderId, fetchImpl) {
    return driveFetch(token, "/files/" + encodeURIComponent(folderId), {
      query: { fields: "id,name,mimeType,modifiedTime,properties" }, fetchImpl: fetchImpl,
    });
  }

  async function downloadText(token, fileId, fetchImpl) {
    return readFileText(token, fileId, fetchImpl);
  }

  async function renameFile(token, fileId, name, fetchImpl) {
    return driveFetch(token, "/files/" + encodeURIComponent(fileId), {
      method: "PATCH", contentType: "application/json; charset=UTF-8",
      query: { fields: "id,name" }, body: JSON.stringify({ name: name }), fetchImpl: fetchImpl,
    });
  }

  // The order and the collapse state of the whole library live in one file in
  // the library folder, so a reorder or a collapse costs one small write
  // instead of one write per item. The file is created on the first write and
  // replaced afterwards, and it is never one of the snippet files: it is the
  // one name the reader skips when it lists the library.
  async function readIndexFile(token, rootId, fetchImpl) {
    var files = await listChildren(token, rootId, fetchImpl);
    var found = null;
    files.forEach(function (file) { if (!found && file.name === INDEX_NAME) found = file; });
    if (!found) return { id: "", index: null };
    var raw = await readFileText(token, found.id, fetchImpl);
    var data = null;
    try { data = JSON.parse(raw); } catch (ignored) { data = null; }
    return { id: found.id, index: data };
  }

  async function writeIndexFile(token, rootId, index, fetchImpl, known) {
    var body = JSON.stringify(index);
    var current = known && known.id ? known.id : (await readIndexFile(token, rootId, fetchImpl)).id;
    if (current) {
      await updateText(token, current, body, fetchImpl);
      return current;
    }
    var made = await uploadNew(token, rootId, INDEX_NAME, body, fetchImpl, true);
    return (made && made.id) || "";
  }

  // Move a file or a folder into another folder. Drive has no move endpoint:
  // a move is a PATCH whose addParents names the new folder and whose
  // removeParents names the old one. Both are applied in the same request, so
  // the item is never left in two folders or in none.
  async function moveFile(token, fileId, fromParentId, toParentId, fetchImpl) {
    var from = fromParentId || "";
    if (!from) {
      // Removing the old parent in the same request is what makes this a move
      // rather than a second place to look: without it the item would turn up
      // in both folders. The parent is read first when the caller has none.
      var current = await driveFetch(token, "/files/" + encodeURIComponent(fileId), {
        query: { fields: "parents" }, fetchImpl: fetchImpl,
      });
      from = ((current && current.parents) || []).join(",");
    }
    return driveFetch(token, "/files/" + encodeURIComponent(fileId), {
      method: "PATCH", contentType: "application/json; charset=UTF-8",
      query: { addParents: toParentId, removeParents: from, fields: "id,parents" },
      body: JSON.stringify({}), fetchImpl: fetchImpl,
    });
  }

  async function trashFile(token, fileId, fetchImpl) {
    return driveFetch(token, "/files/" + encodeURIComponent(fileId), {
      method: "PATCH", contentType: "application/json; charset=UTF-8",
      query: { fields: "id,trashed" }, body: JSON.stringify({ trashed: true }), fetchImpl: fetchImpl,
    });
  }

  async function createFolder(token, name, parentId, fetchImpl) {
    var data = await driveFetch(token, "/files", {
      method: "POST",
      contentType: "application/json; charset=UTF-8",
      query: { fields: "id" },
      body: JSON.stringify({ name: name, mimeType: FOLDER_MIME, parents: [parentId] }),
      fetchImpl: fetchImpl,
    });
    return data.id;
  }

  function multipartBody(metadata, text, boundary) {
    return (
      "--" + boundary + "\r\n" +
      "Content-Type: application/json; charset=UTF-8\r\n\r\n" +
      JSON.stringify(metadata) + "\r\n" +
      "--" + boundary + "\r\n" +
      "Content-Type: text/plain; charset=UTF-8\r\n\r\n" +
      text + "\r\n" +
      "--" + boundary + "--"
    );
  }

  // A snippet is a .txt file. The index file is the one exception: it is stored
  // under its own name, so a reader can tell it apart from the snippets.
  async function uploadNew(token, parentId, name, text, fetchImpl, exactName) {
    var boundary = "pastex-" + Date.now().toString(36) + "-" + Math.floor(Math.random() * 1e9).toString(36);
    var data = await driveFetch(token, "/files", {
      method: "POST",
      upload: true,
      contentType: 'multipart/related; boundary="' + boundary + '"',
      query: { uploadType: "multipart", fields: "id,modifiedTime" },
      body: multipartBody({ name: exactName ? name : name + ".txt", mimeType: TEXT_MIME, parents: [parentId] }, text, boundary),
      fetchImpl: fetchImpl,
    });
    return data;
  }

  async function readFileText(token, fileId, fetchImpl) {
    return driveFetch(token, "/files/" + encodeURIComponent(fileId), {
      query: { alt: "media" }, textResponse: true, fetchImpl: fetchImpl,
    });
  }

  async function updateText(token, fileId, text, fetchImpl) {
    return driveFetch(token, "/files/" + encodeURIComponent(fileId), {
      method: "PATCH",
      upload: true,
      contentType: "text/plain; charset=UTF-8",
      query: { uploadType: "media", fields: "id,modifiedTime" },
      body: text,
      fetchImpl: fetchImpl,
    });
  }

  return {
    FOLDER_MIME: FOLDER_MIME,
    TEXT_MIME: TEXT_MIME,
    SCOPES: SCOPES,
    getAuthToken: getAuthToken,
    getRedirectURL: getRedirectURL,
    removeCachedToken: removeCachedToken,
    signOut: signOut,
    driveFetch: driveFetch,
    listChildren: listChildren,
    getFolder: getFolder,
    downloadText: downloadText,
    renameFile: renameFile,
    moveFile: moveFile,
    INDEX_NAME: INDEX_NAME,
    readIndexFile: readIndexFile,
    writeIndexFile: writeIndexFile,
    trashFile: trashFile,
    createFolder: createFolder,
    uploadNew: uploadNew,
    updateText: updateText,
  };
});
