// Unit tests for extension/drive.js: the OAuth token handshake with Chrome and
// the Drive API wrappers against stub fetch().
"use strict";

const assert = require("node:assert/strict");
const Drive = require("../drive.js");

function stubFetch(handler) {
  const calls = [];
  const impl = async (url, options) => {
    calls.push({ url, options: options || {} });
    return handler(url, options || {}, calls.length);
  };
  impl.calls = calls;
  return impl;
}

function jsonResponse(data, status) {
  return {
    ok: (status === undefined ? 200 : status) < 300,
    status: status === undefined ? 200 : status,
    headers: { get: (name) => (name.toLowerCase() === "content-type" ? "application/json" : "") },
    json: async () => data,
    text: async () => JSON.stringify(data),
  };
}

function textResponse(text, status) {
  return {
    ok: (status === undefined ? 200 : status) < 300,
    status: status === undefined ? 200 : status,
    headers: { get: () => "text/plain" },
    json: async () => {
      throw new Error("not json");
    },
    text: async () => text,
  };
}

// --- the OAuth handshake ---------------------------------------------------
//
// The token comes from Google's token endpoint, after Google's sign-in page
// handed back an authorization code through Chrome. The client id and secret
// this installation recorded in its configuration file travel with that
// exchange, and what it returns is a refresh token, which is the part that
// keeps the connection alive without another page.

const CLIENT = "123456789012-abcdefghijklmnop.apps.googleusercontent.com";
// Every test uses its own client id: the token cache is keyed by it and lives
// for the whole module, exactly as it does in a running worker.
const client = (n) => "10000000000" + n + "-abcdefghijklmnop.apps.googleusercontent.com";
const REDIRECT = "https://abcdefghijklmnop.chromiumapp.org/";

// A stand-in for chrome.identity that answers the way Google does: the token
// arrives in the fragment of the redirect URL.
function fakeIdentity(answer) {
  const asked = [];
  return {
    asked,
    getRedirectURL: () => REDIRECT,
    launchWebAuthFlow: (options, callback) => {
      asked.push(options);
      const redirected = answer(options);
      // Called asynchronously, like the real API.
      Promise.resolve().then(() => callback(redirected));
    },
  };
}
function granted(options) {
  const url = new URL(options.url);
  const state = url.searchParams.get("state");
  return REDIRECT + "?code=4/authorization-code&state=" + state;
}

// A token endpoint that answers the code exchange and the renewal. Every test
// that needs a token supplies one; a call that reaches it without one is a bug
// (the extension would be signing in to reach Google's token endpoint).
function tokenEndpoint(answers) {
  return async (url, options) => {
    assert.equal(String(url), "https://oauth2.googleapis.com/token");
    const form = new URLSearchParams(String(options.body));
    return jsonResponse((answers || {})[form.get("grant_type")] || {
      access_token: "ya29.token", expires_in: 3600, refresh_token: "1//refresh",
    });
  };
}

// A refresh-token store, which only a worker or the panel has.
function memoryStore(initial) {
  const data = { ...(initial || {}) };
  return {
    data,
    async get(key) { return key === null ? { ...data } : { [key]: data[key] }; },
    async set(values) { Object.assign(data, values); },
    async remove(key) { delete data[key]; },
  };
}

test("a Drive token comes from Google's sign-in page, never from a key file", async () => {
  // Pastex used to sign a JWT with a service-account key; Google holds that
  // credential now, and the extension never stores one.
  for (const gone of ["parseServiceAccount", "fetchAccessToken", "signJwt"]) {
    assert.equal(Drive[gone], undefined, gone + " must not exist");
  }
  const store = memoryStore();
  Drive.setTokenStore(store);
  const identity = fakeIdentity(granted);
  const fetchImpl = tokenEndpoint({ authorization_code: {
    access_token: "ya29.token", expires_in: 3600, refresh_token: "1//refresh",
  } });
  assert.equal(await Drive.getAuthToken({
    identity: identity, clientId: CLIENT, clientSecret: "GOCSPX-abc", interactive: true, fetchImpl: fetchImpl,
  }), "ya29.token");
  const url = new URL(identity.asked[0].url);
  assert.equal(url.searchParams.get("client_id"), CLIENT);
  assert.equal(url.searchParams.get("redirect_uri"), REDIRECT);
  assert.equal(url.searchParams.get("response_type"), "code", "a code, because only that flow can return a refresh token");
  assert.equal(url.searchParams.get("scope"), "https://www.googleapis.com/auth/drive");
  assert.equal(url.searchParams.get("state").length, 32);
  assert.equal(url.searchParams.get("access_type"), "offline", "offline is what makes Google hand out a refresh token");
  assert.equal(url.searchParams.get("code_challenge_method"), "S256");
  assert.equal(url.searchParams.get("code_challenge").length > 20, true, "PKCE challenge travels in the URL");
  assert.equal(url.searchParams.get("client_secret"), null, "the secret never travels in a URL");
  // Even an interactive request tries silently first: Chrome's worker restarts
  // often, and a grant Google already holds needs no page.
  assert.deepEqual(identity.asked[0].interactive, false);
  assert.equal(url.searchParams.get("prompt"), "none");
  // The refresh token is what makes the next start free.
  assert.equal(store.data["pastex.refresh.v1:" + CLIENT], "1//refresh");
});

test("the client secret goes in the token exchange, and the refresh token renews silently", async () => {
  Drive.setTokenStore(memoryStore());
  const mine = client(7);
  const exchanges = [];
  const fetchImpl = stubFetch(async (url, options) => {
    exchanges.push(new URLSearchParams(String(options.body)));
    return jsonResponse({ access_token: "ya29.first", expires_in: 3600, refresh_token: "1//kept" });
  });
  const identity = fakeIdentity(granted);
  await Drive.getAuthToken({ identity: identity, clientId: mine, clientSecret: "GOCSPX-secret", interactive: true, fetchImpl });
  // The exchange carries both halves of the client, and the PKCE verifier.
  assert.equal(exchanges[0].get("grant_type"), "authorization_code");
  assert.equal(exchanges[0].get("client_secret"), "GOCSPX-secret");
  assert.equal(exchanges[0].get("code"), "4/authorization-code");
  assert.equal(exchanges[0].get("code_verifier").length > 20, true);
});

test("a stored refresh token renews the access token without opening a window", async () => {
  const mine = client(8);
  const store = memoryStore({ ["pastex.refresh.v1:" + mine]: "1//stored" });
  Drive.setTokenStore(store);
  // A different module instance would be needed to clear the cache; a new
  // client id is the same effect and keeps the tests independent.
  const identity = fakeIdentity(() => { throw new Error("no window may open"); });
  const bodies = [];
  const fetchImpl = stubFetch(async (url, options) => {
    bodies.push(new URLSearchParams(String(options.body)));
    return jsonResponse({ access_token: "ya29.renewed", expires_in: 3600 });
  });
  const token = await Drive.getAuthToken({ identity: identity, clientId: mine, clientSecret: "GOCSPX-secret", fetchImpl });
  assert.equal(token, "ya29.renewed");
  assert.equal(identity.asked.length, 0, "a renewal never opens Google's page");
  assert.equal(bodies[0].get("grant_type"), "refresh_token");
  assert.equal(bodies[0].get("refresh_token"), "1//stored");
  assert.equal(bodies[0].get("client_secret"), "GOCSPX-secret");
  // The stored grant survives a renewal that does not rotate it.
  assert.equal(store.data["pastex.refresh.v1:" + mine], "1//stored");
});

test("a revoked refresh token is forgotten, and the sign-in page is asked again", async () => {
  const mine = client(9);
  const store = memoryStore({ ["pastex.refresh.v1:" + mine]: "1//revoked" });
  Drive.setTokenStore(store);
  const identity = fakeIdentity(granted);
  let renewed = 0;
  const fetchImpl = stubFetch(async (url, options) => {
    const form = new URLSearchParams(String(options.body));
    if (form.get("grant_type") === "refresh_token") {
      renewed++;
      return jsonResponse({ error: "invalid_grant", error_description: "Token has been expired or revoked." }, 400);
    }
    return jsonResponse({ access_token: "ya29.fresh", expires_in: 3600, refresh_token: "1//new" });
  });
  const token = await Drive.getAuthToken({ identity, clientId: mine, clientSecret: "GOCSPX-secret", interactive: true, fetchImpl });
  assert.equal(token, "ya29.fresh");
  assert.equal(renewed, 1, "the renewal was attempted first");
  assert.equal(identity.asked.length, 1, "then Google's page");
  assert.equal(store.data["pastex.refresh.v1:" + mine], "1//new", "the dead grant is replaced");
});

test("signing out revokes the refresh token, so nothing can renew the connection", async () => {
  const mine = client(10);
  const store = memoryStore({ ["pastex.refresh.v1:" + mine]: "1//stored" });
  Drive.setTokenStore(store);
  const fetchImpl = stubFetch(async () => jsonResponse({}));
  const realFetch = globalThis.fetch;
  globalThis.fetch = fetchImpl;
  try {
    await Drive.signOut();
  } finally { globalThis.fetch = realFetch; }
  assert.equal(store.data["pastex.refresh.v1:" + mine], undefined, "the grant is gone from storage");
  const revoked = fetchImpl.calls.map((call) => new URLSearchParams(String(call.options.body)).get("token"));
  assert.ok(revoked.includes("1//stored"), "and it was revoked at Google, not just forgotten");
});

test("a token already held is reused instead of opening a window again", async () => {
  Drive.setTokenStore(memoryStore());
  const identity = fakeIdentity(granted);
  const mine = client(2);
  const fetchImpl = tokenEndpoint();
  await Drive.getAuthToken({ identity: identity, clientId: mine, interactive: true, fetchImpl });
  await Drive.getAuthToken({ identity: identity, clientId: mine, fetchImpl });
  assert.equal(identity.asked.length, 1, "one sign-in serves the session");
});

test("the sign-in page is opened only when a silent request is refused", async () => {
  // Interactive: the silent attempt is refused, so Google's page is opened.
  const asked = [];
  const identity = {
    getRedirectURL: () => REDIRECT,
    launchWebAuthFlow: (options, callback) => {
      asked.push(options);
      const state = new URL(options.url).searchParams.get("state");
      Promise.resolve().then(() => callback(options.interactive
        ? REDIRECT + "?code=4/granted&state=" + state
        : REDIRECT + "?error=interaction_required&state=" + state));
    },
  };
  Drive.setTokenStore(memoryStore());
  assert.equal(await Drive.getAuthToken({
    identity: identity, clientId: client(3), interactive: true, fetchImpl: tokenEndpoint(),
  }), "ya29.token");
  assert.equal(asked.length, 2, "one silent attempt, then the page");
  assert.equal(asked[0].interactive, false);
  assert.equal(new URL(asked[0].url).searchParams.get("prompt"), "none");
  assert.equal(asked[1].interactive, true);
  assert.equal(new URL(asked[1].url).searchParams.get("prompt"), "consent", "a page that must return a refresh token");
  // The two attempts use different PKCE verifiers, so a spent challenge is not
  // replayed with the code that follows.
  assert.notEqual(new URL(asked[0].url).searchParams.get("code_challenge"),
    new URL(asked[1].url).searchParams.get("code_challenge"));

  // Non-interactive: a refusal is reported, never turned into a page.
  const quiet = fakeIdentity((options) => {
    const url = new URL(options.url);
    return REDIRECT + "#error=interaction_required&state=" + url.searchParams.get("state");
  });
  await assert.rejects(Drive.getAuthToken({ identity: quiet, clientId: client(4) }), /not signed in yet/);
  assert.equal(quiet.asked.length, 1);
  assert.equal(quiet.asked[0].interactive, false);
});

test("a sign-in failure is reported, not swallowed", async () => {
  const mine = client(5);
  const denied = fakeIdentity((options) => {
    const url = new URL(options.url);
    return REDIRECT + "#error=access_denied&state=" + url.searchParams.get("state");
  });
  await assert.rejects(Drive.getAuthToken({ identity: denied, clientId: mine }), /access_denied/);
  // A window the person closes comes back empty.
  await assert.rejects(Drive.getAuthToken({ identity: fakeIdentity(() => ""), clientId: mine }),
    /not signed in/);
  await assert.rejects(Drive.getAuthToken({ identity: fakeIdentity(() => ""), clientId: mine, interactive: true }),
    /no answer/);
  // An answer that does not belong to this request is refused outright.
  await assert.rejects(
    Drive.getAuthToken({ identity: fakeIdentity(() => REDIRECT + "#access_token=stolen&state=other"), clientId: mine }),
    /did not match this request/);
  await assert.rejects(Drive.getAuthToken({ identity: {}, clientId: mine }), /sign-in window is unavailable/);
  await assert.rejects(Drive.getAuthToken({ identity: fakeIdentity(granted) }),
    /configuration file needs the client_id/);
});

test("an unregistered redirect URL is named in the error", async () => {
  const identity = fakeIdentity((options) => {
    const url = new URL(options.url);
    return REDIRECT + "#error=redirect_uri_mismatch&state=" + url.searchParams.get("state");
  });
  await assert.rejects(Drive.getAuthToken({ identity: identity, clientId: client(8) }),
    (err) => {
      assert.match(err.message, /redirect_uri_mismatch/);
      assert.match(err.message, /abcdefghijklmnop\.chromiumapp\.org/, "the exact URL to register is quoted");
      return true;
    });
});

test("a token Google has revoked is dropped so the next call can renew it", async () => {
  Drive.setTokenStore(memoryStore());
  const identity = fakeIdentity(granted);
  const mine = client(6);
  const fetchImpl = tokenEndpoint();
  const token = await Drive.getAuthToken({ identity: identity, clientId: mine, fetchImpl });
  assert.equal(await Drive.getAuthToken({ identity: identity, clientId: mine, fetchImpl }), token);
  await Drive.removeCachedToken(token);
  // After a 401 the token is dropped; the stored refresh token renews it, so no
  // page is needed and the count of sign-in windows stays at one.
  await Drive.getAuthToken({ identity: identity, clientId: mine, fetchImpl });
  assert.equal(identity.asked.length, 1, "the refresh token did the work, not a window");
});

test("signing out revokes the token at Google and forgets it here", async () => {
  Drive.setTokenStore(memoryStore());
  const identity = fakeIdentity(granted);
  const mine = client(11);
  await Drive.getAuthToken({ identity: identity, clientId: mine, fetchImpl: tokenEndpoint() });
  const revoked = [];
  const previousFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => { revoked.push({ url, body: options.body }); return jsonResponse({}); };
  try {
    await Drive.signOut();
  } finally {
    globalThis.fetch = previousFetch;
  }
  // Every token this worker held is revoked, not only the last one, so nothing
  // handed out earlier stays usable.
  assert.ok(revoked.length >= 1);
  for (const call of revoked) assert.equal(call.url, "https://oauth2.googleapis.com/revoke");
  assert.ok(revoked.some((call) => /token=ya29\.token/.test(call.body)), "the token itself is revoked");
  // Nothing is left to reuse: even after the in-memory token is gone, the next
  // call has to reach Google's page again, because the grant was revoked.
  await Drive.removeCachedToken("ya29.token");
  await assert.rejects(
    Drive.getAuthToken({ identity: fakeIdentity(() => REDIRECT + "?error=interaction_required&state=x"), clientId: mine, fetchImpl: tokenEndpoint() }),
    /Google's answer did not match/);
});

test("the requested scope is the full Drive scope, and nothing is narrowed", () => {
  // One scope, exactly as Google publishes it:
  // https://www.googleapis.com/auth/drive — every file the signed-in account
  // can open, not only the ones this app created.
  assert.equal(Drive.SCOPES, "https://www.googleapis.com/auth/drive");
});

test("listChildren follows pagination", async () => {
  const fetchImpl = stubFetch(async (url) => {
    if (url.includes("pageToken")) {
      return jsonResponse({ files: [{ id: "f2", name: "B", mimeType: "text/plain", modifiedTime: "2026-01-01T00:00:00.000Z" }] });
    }
    return jsonResponse({
      nextPageToken: "next",
      files: [{ id: "f1", name: "A", mimeType: "text/plain", modifiedTime: "2026-01-01T00:00:00.000Z" }],
    });
  });
  const files = await Drive.listChildren("tok", "root", fetchImpl);
  assert.deepEqual(
    files.map((f) => f.id),
    ["f1", "f2"]
  );
  assert.ok(fetchImpl.calls[0].options.headers.Authorization.includes("Bearer"));
});

test("listChildren asks only for the metadata the library uses", async () => {
  const fetchImpl = stubFetch(async () => jsonResponse({ files: [] }));
  await Drive.listChildren("tok", "root", fetchImpl);
  const url = new URL(fetchImpl.calls[0].url);
  const fields = url.searchParams.get("fields");
  assert.match(fields, /files\(id,name,mimeType,modifiedTime,sha256Checksum,size,properties\)/);
  assert.equal(url.searchParams.get("orderBy"), "folder,name");
  assert.equal(url.searchParams.get("includeItemsFromAllDrives"), "true");
});

test("a failed page is retried from the same cursor", async () => {
  let attempts = 0;
  const seen = [];
  const fetchImpl = stubFetch(async (url) => {
    seen.push(url);
    if (url.includes("pageToken=next")) {
      attempts++;
      if (attempts === 1) throw new Error("Drive API GET /files failed (HTTP 503): busy");
      return jsonResponse({ files: [{ id: "f2", name: "B", mimeType: "text/plain" }] });
    }
    return jsonResponse({ nextPageToken: "next", files: [{ id: "f1", name: "A", mimeType: "text/plain" }] });
  });
  const files = await Drive.listChildren("tok", "root", fetchImpl);
  assert.deepEqual(files.map((f) => f.id), ["f1", "f2"]);
  assert.equal(seen.length, 3, "only the failed page is fetched again");
  assert.ok(seen[1].includes("pageToken=next") && seen[2].includes("pageToken=next"));
});

test("a listing reports the cursor of its last page", async () => {
  const fetchImpl = stubFetch(async (url) => url.includes("pageToken")
    ? jsonResponse({ files: [{ id: "f2", name: "B", mimeType: "text/plain" }] })
    : jsonResponse({ nextPageToken: "next", files: [{ id: "f1", name: "A", mimeType: "text/plain" }] }));
  let cursor = "";
  await Drive.listChildren("tok", "root", fetchImpl, { onCursor: (value) => { cursor = value; } });
  assert.equal(cursor, "next");
});

test("the index is read straight from its link, with no listing", async () => {
  // The configuration names the index file, so reading it is one GET. It used to
  // be found by listing the library folder, which cost a second listing on every
  // load for a file whose id the user already knew.
  const fetchImpl = stubFetch(async () => textResponse(JSON.stringify({ v: 2, order: { root: ["b", "a"] }, collapsed: { f: "1" } })));
  const stored = await Drive.readIndexFile("tok", "index-file", fetchImpl);
  assert.equal(stored.id, "index-file");
  assert.deepEqual(stored.index, { v: 2, order: { root: ["b", "a"] }, collapsed: { f: "1" } });
  assert.equal(fetchImpl.calls.length, 1);
  const url = new URL(fetchImpl.calls[0].url);
  assert.equal(url.pathname, "/drive/v3/files/index-file");
  assert.equal(url.searchParams.get("alt"), "media");
});

test("an index link that no longer opens leaves the library readable", async () => {
  for (const status of [403, 404]) {
    const fetchImpl = stubFetch(async () => jsonResponse({ error: { message: "not found" } }, status));
    const stored = await Drive.readIndexFile("tok", "index-file", fetchImpl);
    assert.deepEqual(stored, { id: "", index: null, missing: true },
      "a deleted or unshared index file is metadata drift, not a failed load");
  }
});

test("an index write edits the linked file, and refuses to invent a second one", async () => {
  const index = { v: 2, order: { root: ["b", "a"] }, collapsed: { f: "1" } };
  const body = JSON.stringify(index);

  // With a link: one media PATCH to that file.
  const linked = stubFetch(async () => jsonResponse({ id: "index-file" }));
  assert.equal(await Drive.writeIndexFile("tok", "index-file", index, linked), "index-file");
  assert.equal(linked.calls.length, 1);
  const patch = linked.calls[0];
  assert.equal(patch.options.method, "PATCH");
  assert.ok(patch.url.includes("/files/index-file"));
  assert.ok(patch.url.includes("uploadType=media"));
  assert.equal(patch.options.body, body, "the whole label is written, unclipped");

  // Without a link there is nowhere to write, and creating a file would make a
  // second source of truth for the same fact. The caller is told what to do.
  const created = stubFetch(async () => jsonResponse({ id: "new-index" }));
  await assert.rejects(Drive.writeIndexFile("tok", "", index, created), (err) => {
    assert.match(err.message, /pastex-index link/);
    assert.match(err.message, /create a new\s+empty file/);
    return true;
  });
  assert.equal(created.calls.length, 0, "no second index file is ever created");
});

test("the index no longer has to fit in 124 bytes, which is what refused every reorder", async () => {
  // Two rows with realistic Drive ids already exceeded the 124-byte property
  // limit, so Drive answered 400 to every reorder and every fold. The file has
  // no such limit: a library of 400 rows is still one small write.
  const ids = Array.from({ length: 400 }, (_, i) => "1AbCdEfGhIjKlMnOpQrStUvWxYz0123" + String(i).padStart(4, "0"));
  const index = { v: 2, order: { "1RootFolderIdAbCdEfGhIjKlMnOpQrStUvW": ids }, collapsed: { [ids[0]]: "1" } };
  const body = JSON.stringify(index);
  assert.ok(body.length > 124, "the label is far larger than a property can hold");
  const fetchImpl = stubFetch(async () => jsonResponse({ id: "index-file" }));
  await Drive.writeIndexFile("tok", "index-file", index, fetchImpl);
  assert.equal(fetchImpl.calls[0].options.body, body);
  assert.equal(Drive.setIndex, undefined, "there is no property writer any more");
});

test("the root folder is read with its timestamp", async () => {
  const fetchImpl = stubFetch(async () => jsonResponse({
    id: "root", name: "Library", mimeType: "application/vnd.google-apps.folder",
    modifiedTime: "2026-01-01T00:00:00.000Z",
  }));
  const folder = await Drive.getFolder("tok", "root", fetchImpl);
  const fields = new URL(fetchImpl.calls[0].url).searchParams.get("fields");
  assert.match(fields, /modifiedTime/);
  assert.equal(folder.modifiedTime, "2026-01-01T00:00:00.000Z");
});

test("uploadNew posts multipart, updateText patches media", async () => {
  const fetchImpl = stubFetch(async () => jsonResponse({ id: "n1", modifiedTime: "2026-01-01T00:00:00.000Z" }));
  await Drive.uploadNew("tok", "parent", "Note", "hello", fetchImpl);
  const create = fetchImpl.calls[0];
  assert.equal(create.options.method, "POST");
  assert.ok(create.url.startsWith("https://www.googleapis.com/upload/drive/v3/files"));
  assert.ok(create.options.headers["Content-Type"].includes("multipart/related"));
  assert.ok(create.options.body.includes("Note.txt"));

  await Drive.updateText("tok", "f9", "bye", fetchImpl);
  const update = fetchImpl.calls[1];
  assert.equal(update.options.method, "PATCH");
  assert.ok(update.url.includes("/files/f9"));
  assert.ok(update.url.includes("uploadType=media"));
});

test("Drive reads shared folders and preserves plain text even when MIME is JSON", async () => {
  const fetchImpl = stubFetch(async (url) => url.includes("alt=media")
    ? { ok: true, status: 200, headers: { get: () => "application/json" }, text: async () => '{"hello":1}', json: async () => ({ hello: 1 }) }
    : jsonResponse({ id: "folder", mimeType: Drive.FOLDER_MIME }));
  await Drive.getFolder("tok", "shared-id", fetchImpl);
  assert.match(fetchImpl.calls[0].url, /supportsAllDrives=true/);
  assert.equal(await Drive.downloadText("tok", "file", fetchImpl), '{"hello":1}');
  await Drive.listChildren("tok", "root", fetchImpl);
  assert.match(fetchImpl.calls[2].url, /includeItemsFromAllDrives=true/);
});

test("rename and delete use Drive metadata PATCH, with trash rather than permanent delete", async () => {
  const fetchImpl = stubFetch(async () => jsonResponse({ id: "f" }));
  await Drive.renameFile("tok", "f", "New.txt", fetchImpl);
  await Drive.trashFile("tok", "f", fetchImpl);
  assert.equal(fetchImpl.calls[0].options.method, "PATCH");
  assert.deepEqual(JSON.parse(fetchImpl.calls[0].options.body), { name: "New.txt" });
  assert.deepEqual(JSON.parse(fetchImpl.calls[1].options.body), { trashed: true });
});

test("a move adds the new parent and removes the old one in the same request", async () => {
  const fetchImpl = stubFetch(async () => jsonResponse({ id: "f", parents: ["dest"] }));
  await Drive.moveFile("tok", "f", "old", "dest", fetchImpl);
  const call = fetchImpl.calls[0];
  assert.equal(call.options.method, "PATCH");
  assert.match(call.url, /addParents=dest/);
  assert.match(call.url, /removeParents=old/);
});

test("a move without a known parent reads it first, so the row is never in two folders", async () => {
  const fetchImpl = stubFetch(async (url) => (url.includes("addParents")
    ? jsonResponse({ id: "f" })
    : jsonResponse({ id: "f", parents: ["now"] })));
  await Drive.moveFile("tok", "f", "", "dest", fetchImpl);
  assert.equal(fetchImpl.calls.length, 2);
  assert.match(fetchImpl.calls[1].url, /removeParents=now/);
});

test("driveFetch reports API errors with the message", async () => {
  const fetchImpl = stubFetch(async () =>
    jsonResponse({ error: { message: "File not found: root" } }, 404)
  );
  await assert.rejects(Drive.listChildren("tok", "root", fetchImpl), /File not found: root/);
});

test("stalled Google responses and body reads time out and abort", async () => {
  let signal;
  const hanging = async (url, options) => { signal = options.signal; return new Promise(() => {}); };
  await assert.rejects(Drive.driveFetch("tok", "/files", { fetchImpl: hanging, timeoutMs: 10 }), /timed out/);
  assert.equal(signal.aborted, true);
  const slowBody = async (url, options) => { signal = options.signal; return { ok: true, headers: { get: () => "text/plain" }, text: () => new Promise(() => {}) }; };
  await assert.rejects(Drive.driveFetch("tok", "/files/x", { fetchImpl: slowBody, timeoutMs: 10 }), /timed out/);
  assert.equal(signal.aborted, true);
});

test("Drive pagination rejects a repeated token instead of loading forever", async () => {
  const fetchImpl = async () => jsonResponse({ files: [], nextPageToken: "same" });
  await assert.rejects(Drive.listChildren("tok", "root", fetchImpl), /repeated page token/);
});

test("writing the index keeps shared-drive support", async () => {
  const fetchImpl = stubFetch(async (url) => (url.startsWith("https://www.googleapis.com/upload/")
    ? jsonResponse({ id: "index-file" })
    : jsonResponse({ files: [] })));
  await Drive.writeIndexFile("tok", "index-file", { v: 2, order: {}, collapsed: {} }, fetchImpl);
  const patch = fetchImpl.calls.find((call) => call.options.method === "PATCH");
  assert.equal(new URL(patch.url).searchParams.get("supportsAllDrives"), "true");
});

test("no per-item order or collapse writer is exported any more", () => {
  // Reorder and fold share the root label; a per-item writer would be a second
  // source of truth for the same fact.
  assert.equal(Drive.setOrder, undefined);
  assert.equal(Drive.setCollapsed, undefined);
});

test("a linked index file costs one write, not a listing first", async () => {
  const fetchImpl = stubFetch(async () => jsonResponse({ id: "index-file" }));
  await Drive.writeIndexFile("tok", "index-file", { v: 2, order: {}, collapsed: {} }, fetchImpl);
  assert.equal(fetchImpl.calls.length, 1, "the id from the link is reused");
  assert.equal(fetchImpl.calls[0].options.method, "PATCH");
});
