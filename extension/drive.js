/* Google Drive API access using a service-account key from the selected folder.
 * The private key is used locally to sign an assertion, not sent to Google.
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

  var TOKEN_URL = "https://oauth2.googleapis.com/token";
  var DRIVE_URL = "https://www.googleapis.com/drive/v3";
  var UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3";
  // Full drive scope: a folder the user shares with the service account is
  // NOT visible under the narrower drive.file scope, which only covers
  // files the app itself created.
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

  var tokenCache = { token: "", expiry: 0 };

  // --- base64 helpers (browser + node) ---

  function bytesToBase64Url(bytes) {
    var bin = "";
    var CHUNK = 0x8000;
    for (var i = 0; i < bytes.length; i += CHUNK) {
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
    }
    var b64;
    if (typeof btoa !== "undefined") {
      b64 = btoa(bin);
    } else {
      b64 = Buffer.from(bin, "binary").toString("base64");
    }
    return b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function stringToBase64Url(text) {
    var bytes;
    if (typeof TextEncoder !== "undefined") {
      bytes = new TextEncoder().encode(text);
    } else {
      bytes = Uint8Array.from(Buffer.from(text, "utf8"));
    }
    return bytesToBase64Url(bytes);
  }

  function base64ToBytes(b64) {
    var bin;
    if (typeof atob !== "undefined") {
      bin = atob(b64);
    } else {
      bin = Buffer.from(b64, "base64").toString("binary");
    }
    var bytes = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) {
      bytes[i] = bin.charCodeAt(i);
    }
    return bytes;
  }

  function pemToPkcs8(pem) {
    var body = String(pem || "")
      .replace(/-----BEGIN [^-]+-----/g, "")
      .replace(/-----END [^-]+-----/g, "")
      .replace(/\s+/g, "");
    if (!body) {
      throw new Error("the private_key field is empty");
    }
    try {
      return base64ToBytes(body);
    } catch (err) {
      throw new Error("the private_key is not valid base64");
    }
  }

  // --- service-account key handling ---

  function parseServiceAccount(text) {
    var data;
    try {
      data = JSON.parse(String(text || ""));
    } catch (err) {
      throw new Error("that is not valid JSON");
    }
    if (!data || typeof data !== "object") {
      throw new Error("that is not a service-account key file");
    }
    if (!data.client_email || !data.private_key) {
      throw new Error("the key needs client_email and private_key fields");
    }
    if (data.type && data.type !== "service_account") {
      throw new Error('the key type is "' + data.type + '", not "service_account"');
    }
    // Fail fast on a truncated key instead of at sync time.
    pemToPkcs8(data.private_key);
    return { client_email: data.client_email, private_key: data.private_key };
  }

  function claimSet(email, nowSec, ttlSec) {
    return {
      iss: email,
      scope: SCOPES,
      aud: TOKEN_URL,
      iat: nowSec,
      exp: nowSec + (ttlSec || 3600),
    };
  }

  function subtleCrypto(overrides) {
    if (overrides && overrides.subtle) {
      return overrides.subtle;
    }
    if (typeof crypto !== "undefined" && crypto.subtle) {
      return crypto.subtle;
    }
    if (typeof globalThis !== "undefined" && globalThis.crypto && globalThis.crypto.subtle) {
      return globalThis.crypto.subtle;
    }
    throw new Error("WebCrypto (crypto.subtle) is unavailable");
  }

  async function signJwt(subtle, privateKeyPem, claims) {
    var header = { alg: "RS256", typ: "JWT" };
    var unsigned = stringToBase64Url(JSON.stringify(header)) + "." + stringToBase64Url(JSON.stringify(claims));
    var keyBytes = pemToPkcs8(privateKeyPem);
    // A fresh ArrayBuffer: importKey rejects views with a nonzero byteOffset.
    var keyBuffer = keyBytes.buffer.slice(keyBytes.byteOffset, keyBytes.byteOffset + keyBytes.byteLength);
    var key;
    try {
      key = await subtle.importKey("pkcs8", keyBuffer, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["sign"]);
    } catch (err) {
      throw new Error("the private_key could not be imported (" + (err && err.message ? err.message : err) + ")");
    }
    var data = typeof TextEncoder !== "undefined" ? new TextEncoder().encode(unsigned) : Uint8Array.from(Buffer.from(unsigned, "utf8"));
    var signature = await subtle.sign("RSASSA-PKCS1-v1_5", key, data);
    return unsigned + "." + bytesToBase64Url(new Uint8Array(signature));
  }

  function clearTokenCache() {
    tokenCache = { token: "", expiry: 0 };
  }

  async function fetchAccessToken(sa, overrides) {
    overrides = overrides || {};
    var nowSec = Math.floor(Date.now() / 1000);
    if (tokenCache.token && tokenCache.expiry - 60 > nowSec &&
        tokenCache.email === sa.client_email && tokenCache.privateKey === sa.private_key) {
      return tokenCache.token;
    }
    var subtle = subtleCrypto(overrides);
    var assertion = await signJwt(subtle, sa.private_key, claimSet(sa.client_email, nowSec));
    var fetchImpl = overrides.fetchImpl || fetch;
    var tokenResult = await withTimeout(async function (signal) {
      var response = await fetchImpl(TOKEN_URL, {
        method: "POST", signal: signal,
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: "grant_type=" + encodeURIComponent("urn:ietf:params:oauth:grant-type:jwt-bearer") +
          "&assertion=" + encodeURIComponent(assertion),
      });
      return { res: response, data: await response.json().catch(function () { return {}; }) };
    }, overrides.timeoutMs);
    var res = tokenResult.res;
    var data = tokenResult.data;
    if (!res.ok || !data.access_token) {
      throw new Error("token request failed (HTTP " + res.status + "): " + (data.error_description || data.error || "unknown error"));
    }
    tokenCache = { token: data.access_token, expiry: nowSec + (data.expires_in || 3600),
      email: sa.client_email, privateKey: sa.private_key };
    return tokenCache.token;
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
        throw new Error("Drive API " + (options.method || "GET") + " " + path + " failed (HTTP " + res.status + "): " + String(detail).slice(0, 300));
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
    return driveFetch(token, "/files/" + encodeURIComponent(fileId), {
      query: { alt: "media" }, textResponse: true,
      fetchImpl: fetchImpl,
    });
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
    TOKEN_URL: TOKEN_URL,
    FOLDER_MIME: FOLDER_MIME,
    TEXT_MIME: TEXT_MIME,
    stringToBase64Url: stringToBase64Url,
    base64ToBytes: base64ToBytes,
    parseServiceAccount: parseServiceAccount,
    claimSet: claimSet,
    signJwt: signJwt,
    clearTokenCache: clearTokenCache,
    fetchAccessToken: fetchAccessToken,
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
