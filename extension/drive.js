/* Google Drive API access with OAuth, as the person using Pastex.
 *
 * The access token is issued to the signed-in Google account, so the files it
 * creates belong to that account and are counted against its own storage. A
 * service account was used before and could not do this: it has no storage of
 * its own, so Google refused every new file.
 *
 * The flow is the authorization-code flow with PKCE, which is the only one that
 * can return a refresh token, and a refresh token is the only thing that keeps
 * the connection alive without showing Google's page again. The implicit flow
 * used before handed back an access token that died after an hour and could not
 * be renewed, which is why the panel had to ask for a sign-in again and again.
 *
 * The client secret travels with the token exchange instead of being assumed
 * absent: Google's own Personal mode onboarding creates a "Web application"
 * client and that client requires one. It lives in the configuration file next
 * to the client id, so nothing is compiled into the extension. A secret in a
 * self-hosted, single-user tool's own configuration file is not a secret shared
 * with the world, and it is never written to the snippet library or to the
 * snapshot cache.
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
  var TOKEN_URL = "https://oauth2.googleapis.com/token";
  var REVOKE_URL = "https://oauth2.googleapis.com/revoke";
  var UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3";
  // Full, unrestricted Drive: every file and folder the signed-in account can
  // open, including the ones it did not create. The narrow `drive.file` scope
  // sees only what Pastex created or the user opened with it, which left every
  // folder and snippet that already existed unreadable (Drive answers 404 for a
  // file the grant cannot see), and `drive.item` is not in Google's scope tables
  // at all. One scope, no narrowing of our own: the library folder named in the
  // configuration is a preference, not a boundary.
  var SCOPES = "https://www.googleapis.com/auth/drive";
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
  // Chrome opens Google's consent page (chrome.identity.launchWebAuthFlow) and
  // hands the resulting redirect URL back. The code in that URL is exchanged for
  // an access token and a refresh token; the refresh token is what makes the
  // connection survive an hour, a browser restart and a discarded worker.
  //
  // The client id and secret are not compiled into the extension: this project
  // is self-hosted, so each installation brings its own OAuth client, recorded
  // in the configuration file (see the guide in README.md).

  function identityApi(overrides) {
    var api = (overrides && overrides.identity) || (typeof chrome !== "undefined" && chrome && chrome.identity);
    if (!api || typeof api.launchWebAuthFlow !== "function" || typeof api.getRedirectURL !== "function") {
      throw new Error("Chrome's sign-in window is unavailable. Reload Pastex from chrome://extensions and try again.");
    }
    return api;
  }

  // One token per client id, kept only in memory: an access token lives about
  // an hour, and the refresh token in storage.local renews it silently.
  var tokens = new Map();

  function cachedToken(clientId) {
    var entry = tokens.get(clientId);
    if (!entry) return "";
    if (entry.expiresAt <= Date.now()) { tokens.delete(clientId); return ""; }
    return entry.token;
  }

  // Renewing needs somewhere durable, which only the worker and the panel have.
  var tokenStore = null;
  function setTokenStore(store) { tokenStore = store || null; }

  var REFRESH_PREFIX = "pastex.refresh.v1:";
  function refreshKey(clientId) { return REFRESH_PREFIX + clientId; }

  async function readAllRefresh() {
    if (!tokenStore) return {};
    try {
      var all = await tokenStore.get(null);
      var found = {};
      Object.keys(all || {}).forEach(function (key) {
        if (key.indexOf(REFRESH_PREFIX) === 0 && typeof all[key] === "string" && all[key]) found[key] = all[key];
      });
      return found;
    } catch (ignored) { return {}; }
  }

  async function readRefresh(clientId) {
    if (!tokenStore || !clientId) return "";
    try {
      var saved = await tokenStore.get(refreshKey(clientId));
      var value = saved && saved[refreshKey(clientId)];
      return typeof value === "string" ? value : "";
    } catch (ignored) { return ""; }
  }

  async function writeRefresh(clientId, value) {
    if (!tokenStore || !clientId) return;
    try {
      if (value) await tokenStore.set({ [refreshKey(clientId)]: value });
      else await tokenStore.remove(refreshKey(clientId));
    } catch (ignored) { /* the next sign-in hands out another one */ }
  }

  function base64Url(bytes) {
    var binary = Array.from(bytes, function (b) { return String.fromCharCode(b); }).join("");
    return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  // PKCE: the verifier never leaves this scope, and only its SHA-256 travels in
  // the sign-in URL. The hash is stripped by a sparse array on purpose: a plain
  // argument object would lose the algorithm name.
  async function pkcePair() {
    var bytes = new Uint8Array(32);
    globalCrypto().getRandomValues(bytes);
    var verifier = base64Url(bytes);
    var digest = await globalCrypto().subtle.digest("SHA-256", new TextEncoder().encode(verifier));
    return { verifier: verifier, challenge: base64Url(new Uint8Array(digest)) };
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
    // The authorization-code flow answers with a code, which is exchanged for
    // tokens over HTTPS. The implicit flow used to put the token in the URL.
    var code = query.get("code") || fragment.get("code") || "";
    if (!code) throw new Error("Google's answer held no authorization code.");
    return { code: code };
  }

  function authUrl(clientId, redirectUri, interactive, state, challenge) {
    var url = new URL(AUTH_URL);
    url.searchParams.set("client_id", clientId);
    url.searchParams.set("redirect_uri", redirectUri);
    url.searchParams.set("response_type", "code");
    url.searchParams.set("scope", SCOPES);
    url.searchParams.set("state", state);
    url.searchParams.set("code_challenge", challenge);
    url.searchParams.set("code_challenge_method", "S256");
    // offline is what makes Google hand out a refresh token at all.
    url.searchParams.set("access_type", "offline");
    // A silent request never shows a page: it only works while the person has
    // already granted Pastex access, which is exactly when no page is wanted.
    url.searchParams.set("prompt", interactive ? "consent" : "none");
    return url.toString();
  }

  // Chrome allows one web auth flow per extension at a time, and the flow holds
  // that slot while its window exists, even after the worker that started it
  // was stopped. A second request is refused with a sentence about Chrome's
  // internals ("Only one web auth flow is allowed at a time."), which is not
  // something a person using Pastex can act on and not what went wrong.
  var FLOW_BUSY = /only one web auth flow/i;
  // Waits between attempts. A flow whose window is still open clears itself as
  // soon as the window closes, so a few short waits cover the ordinary case.
  var FLOW_RETRY_MS = [300, 600, 1200, 2400];

  // One trip through Chrome's sign-in window. `interactive` decides whether a
  // page may appear: false means Google must answer silently from a grant it
  // already holds, which is the normal case on every sync after the first.
  function authorize(api, clientId, redirectUri, interactive, challenge, retryMs) {
    var state = randomState();
    var waits = retryMs || FLOW_RETRY_MS;
    function attempt(at) {
      return new Promise(function (resolve, reject) {
        function failed(err) {
          var reason = err && err.message ? err.message : String(err);
          if (FLOW_BUSY.test(reason)) {
            if (at < waits.length) {
              setTimeout(function () { attempt(at + 1).then(resolve, reject); }, waits[at]);
              return;
            }
            reject(new Error("Chrome is still finishing an earlier Google sign-in window. " +
              "Close that window if it is open, then try again."));
            return;
          }
          reject(err);
        }
        try {
          api.launchWebAuthFlow({ url: authUrl(clientId, redirectUri, interactive, state, challenge), interactive: interactive },
            function (redirectedTo) {
              var failure = typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.lastError;
              if (failure) { failed(new Error(failure.message || "Google sign-in failed.")); return; }
              if (!redirectedTo) {
                reject(new Error(interactive ? "Google sign-in returned no answer."
                  : "Pastex is not signed in yet. Sync once and accept Google's request."));
                return;
              }
              try { resolve(readRedirect(redirectedTo, state)); }
              catch (err) { reject(err); }
            });
        } catch (err) {
          failed(new Error("Google sign-in failed: " + (err && err.message ? err.message : err)));
        }
      });
    }
    return attempt(0);
  }

  /* The code -> tokens exchange. This is the one request that carries the
   * client secret, and it is also the only way to receive a refresh token. */
  /* A client that needs a secret and did not get one is a configuration gap, not
   * a Google outage. Google's own wording ("client_secret is missing") is kept
   * as the reason, and the sentence around it says where the value belongs.
   * Sending the exchange again without a secret cannot help, so it is not sent
   * twice. */
  function needsSecret(detail) {
    return /client_secret/i.test(String(detail || "")) && /missing|required|invalid/i.test(String(detail || ""));
  }

  var SECRET_HELP = " This OAuth client is a Web application client, which Google's Personal mode " +
    "creates, and such a client must have a client_secret next to client_id in the configuration file. " +
    "Copy it from the Google Cloud console (APIs & Services, Credentials, the OAuth client) and pick " +
    "the configuration file again in Config.";

  async function exchangeCode(clientId, clientSecret, code, redirectUri, verifier, fetchImpl) {
    var body = new URLSearchParams({
      grant_type: "authorization_code",
      code: code,
      client_id: clientId,
      redirect_uri: redirectUri,
      code_verifier: verifier,
    });
    // Sent only when the configuration has one: a client that Google created
    // without a secret still exchanges the code.
    if (clientSecret) body.set("client_secret", clientSecret);
    var res = await (fetchImpl || fetch)(TOKEN_URL, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: body.toString(),
    });
    var data = await res.json().catch(function () { return {}; });
    if (!res.ok || !data.access_token) {
      var detail = data && data.error_description ? data.error_description : (data && data.error ? data.error : "HTTP " + res.status);
      throw new Error("Google would not exchange the sign-in code: " + detail +
        (!clientSecret && needsSecret(detail) ? "." + SECRET_HELP : ""));
    }
    return data;
  }

  /* An access token from the refresh token, with no page at all.
   *
   * This is what keeps the connection alive: the refresh token does not expire
   * on an ordinary use, so a worker that was discarded can come back, renew, and
   * carry on. Without it every restart depended on Google answering the
   * sign-in window silently, and any hiccup there forced the person to sign in
   * again by hand. */
  async function refreshAccessToken(clientId, clientSecret, refreshToken, fetchImpl) {
    var body = new URLSearchParams({
      grant_type: "refresh_token",
      refresh_token: refreshToken,
      client_id: clientId,
    });
    if (clientSecret) body.set("client_secret", clientSecret);
    var res = await (fetchImpl || fetch)(TOKEN_URL, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: body.toString(),
    });
    var data = await res.json().catch(function () { return {}; });
    if (!res.ok || !data.access_token) {
      var detail = data && data.error_description ? data.error_description : (data && data.error ? data.error : "HTTP " + res.status);
      // invalid_grant means the grant is gone (revoked, or the account's access
      // was withdrawn). The stored token is useless from now on. The code is
      // checked as well as the sentence: Google's wording is not a contract.
      var err = new Error("Google would not renew the connection: " + detail);
      err.invalidGrant = /invalid_grant/i.test(String(detail)) || String(data && data.error) === "invalid_grant";
      throw err;
    }
    return data;
  }

  /* getAuthToken({ clientId, clientSecret, interactive }) -> access token.
   *
   * Four steps, cheapest first: the in-memory token, the stored refresh token,
   * a silent sign-in, and only then Google's page. The refresh token is the
   * step that makes the rest unnecessary; the sign-in window exists for the
   * first run and for the day the grant is withdrawn. */
  async function getAuthToken(options) {
    options = options || {};
    var clientId = String(options.clientId || "").trim();
    var clientSecret = String(options.clientSecret || "").trim();
    if (!clientId) throw new Error("The configuration file needs the client_id of a Google OAuth client.");
    var cached = cachedToken(clientId);
    if (cached) return cached;
    // One sign-in per client at a time. The panel opens the library while the
    // worker checks it, so two requests for a token at once is the ordinary
    // case on the very first load, not an accident: without this, both opened a
    // window and Chrome refused the second ("Only one web auth flow is allowed
    // at a time."). A renewal racing another renewal abuses a refresh token
    // Google may rotate, which is the second reason to share one flight.
    return sharedSignIn(clientId, function () { return acquireToken(clientId, clientSecret, options); });
  }

  var signIns = new Map();

  function sharedSignIn(clientId, run) {
    var existing = signIns.get(clientId);
    if (existing) return existing;
    var flight = (async function () { return await run(); })();
    signIns.set(clientId, flight);
    function done() { if (signIns.get(clientId) === flight) signIns.delete(clientId); }
    flight.then(done, done);
    return flight;
  }

  async function acquireToken(clientId, clientSecret, options) {
    // The flight that just finished may have stored a token while this one was
    // waiting for it.
    var cached = cachedToken(clientId);
    if (cached) return cached;

    // 1. The stored refresh token: no page, no code, just the token endpoint.
    var refreshToken = await readRefresh(clientId);
    if (refreshToken) {
      try {
        var renewed = await refreshAccessToken(clientId, clientSecret, refreshToken, options.fetchImpl);
        storeToken(clientId, renewed.access_token, renewed.expires_in);
        // Google may rotate the refresh token; keeping the new one matters.
        if (renewed.refresh_token) await writeRefresh(clientId, renewed.refresh_token);
        return renewed.access_token;
      } catch (err) {
        if (err && err.invalidGrant) await writeRefresh(clientId, "");
        else throw explainSignIn(err, "", true);
      }
    }

    // 2. The sign-in window. A silent attempt first, then, if the caller allows
    //    it, the page that asks the person.
    var api = identityApi(options);
    var redirectUri = options.redirectUri || api.getRedirectURL();
    var pair = await pkcePair();
    var parsed;
    try {
      parsed = await authorize(api, clientId, redirectUri, false, pair.challenge, options.flowRetryMs);
    } catch (err) {
      if (!options.interactive) throw explainSignIn(err, redirectUri, true);
      // A new PKCE verifier, because the previous challenge is spent.
      pair = await pkcePair();
      try {
        parsed = await authorize(api, clientId, redirectUri, true, pair.challenge, options.flowRetryMs);
      } catch (prompted) {
        throw explainSignIn(prompted, redirectUri);
      }
    }
    var exchanged = await exchangeCode(clientId, clientSecret, parsed.code, redirectUri, pair.verifier, options.fetchImpl);
    storeToken(clientId, exchanged.access_token, exchanged.expires_in);
    // offline access on a first grant returns one; a renewal may not, which is
    // why the old one is only replaced when a new one arrives.
    if (exchanged.refresh_token) await writeRefresh(clientId, exchanged.refresh_token);
    return exchanged.access_token;
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

  /* Signing out revokes what Google handed out as well as forgetting it here,
   * so nothing that was issued stays valid. The refresh token is revoked first:
   * revoking only the access token would leave the stored refresh token able to
   * renew the connection a minute later. */
  async function signOut() {
    var issued = Array.from(tokens.values()).map(function (entry) { return entry.token; });
    tokens.clear();
    var grants = await readAllRefresh();
    for (var key in grants) {
      if (!Object.prototype.hasOwnProperty.call(grants, key)) continue;
      issued.push(grants[key]);
      await writeRefresh(key.replace(REFRESH_PREFIX, ""), "");
    }
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

  // The order and the collapse state of the whole library live in one file, and
  // the configuration names it directly (the `pastex-index` key of the
  // configuration file). Reading it is one GET: no listing is needed to find
  // it, so a folder listing costs one request instead of two.
  //
  // It cannot live in the root folder's custom properties: Drive allows 124
  // bytes per property including the key, and the index names every folder and
  // every id, so two rows already overflow it and every reorder or fold was
  // refused with HTTP 400.
  async function readIndexFile(token, indexId, fetchImpl) {
    // Placement and fold state are written only to a file the configuration
    // names: `writeIndexFile` has no create-a-second-file path any more, so a
    // missing link cannot make the session look read-only while an edit is
    // silently dropped.
    if (!indexId) return { id: "", index: null, missing: true };
    var raw;
    try {
      raw = await readFileText(token, indexId, fetchImpl);
    } catch (err) {
      // A link to a file that is gone or no longer readable is not a listing
      // failure: the library still opens, in Drive's own order with every
      // folder open, and the next reorder writes the file again.
      if (/HTTP (403|404)\b/.test(String(err && err.message))) return { id: "", index: null, missing: true };
      throw err;
    }
    var data = null;
    try { data = JSON.parse(raw); } catch (ignored) { data = null; }
    return { id: indexId, index: data };
  }

  async function writeIndexFile(token, indexId, index, fetchImpl) {
    // There is exactly one place order and collapse state can live: the file the
    // `pastex-index` link names. Without that link there is nowhere to write, and
    // creating a second file would be a second source of truth for the same fact,
    // so the caller is told instead of being given a new id.
    if (!indexId) {
      throw new Error("The configuration file has no pastex-index link, so the " +
        "empty order/collapse file has yet to be made. Open Drive, create a new " +
        "empty file, and put its link on pastex-index in your configuration file.");
    }
    await updateText(token, indexId, JSON.stringify(index), fetchImpl);
    return indexId;
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
    setTokenStore: setTokenStore,
    refreshAccessToken: refreshAccessToken,
    exchangeCode: exchangeCode,
    getRedirectURL: getRedirectURL,
    removeCachedToken: removeCachedToken,
    signOut: signOut,
    driveFetch: driveFetch,
    listChildren: listChildren,
    getFolder: getFolder,
    downloadText: downloadText,
    renameFile: renameFile,
    moveFile: moveFile,
    readIndexFile: readIndexFile,
    writeIndexFile: writeIndexFile,
    trashFile: trashFile,
    createFolder: createFolder,
    uploadNew: uploadNew,
    updateText: updateText,
  };
});
