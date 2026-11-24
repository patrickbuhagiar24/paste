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
// The token comes from Google's sign-in page through Chrome, using the client
// id this installation recorded in pastex-storage.json. There is no key file and
// no client secret: the extension is a public client and its bundle is public.

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
  return REDIRECT + "#access_token=ya29.token&expires_in=3600&token_type=Bearer&state=" + state;
}

test("a Drive token comes from Google's sign-in page, never from a key file", async () => {
  // Pastex used to sign a JWT with a service-account key; Google holds that
  // credential now, and the extension never stores one.
  for (const gone of ["parseServiceAccount", "fetchAccessToken", "signJwt"]) {
    assert.equal(Drive[gone], undefined, gone + " must not exist");
  }
  const identity = fakeIdentity(granted);
  assert.equal(await Drive.getAuthToken({ identity: identity, clientId: CLIENT, interactive: true }), "ya29.token");
  const url = new URL(identity.asked[0].url);
  assert.equal(url.searchParams.get("client_id"), CLIENT);
  assert.equal(url.searchParams.get("redirect_uri"), REDIRECT);
  assert.equal(url.searchParams.get("response_type"), "token");
  assert.equal(url.searchParams.get("scope"), "https://www.googleapis.com/auth/drive.file");
  assert.equal(url.searchParams.get("state").length, 32);
  // Even an interactive request tries silently first: Chrome's worker restarts
  // often, and a grant Google already holds needs no page.
  assert.deepEqual(identity.asked[0].interactive, false);
  assert.equal(url.searchParams.get("prompt"), "none");
});

test("a token already held is reused instead of opening a window again", async () => {
  const identity = fakeIdentity(granted);
  const mine = client(2);
  await Drive.getAuthToken({ identity: identity, clientId: mine, interactive: true });
  await Drive.getAuthToken({ identity: identity, clientId: mine });
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
        ? REDIRECT + "#access_token=ya29.granted&expires_in=3600&state=" + state
        : REDIRECT + "#error=interaction_required&state=" + state));
    },
  };
  assert.equal(await Drive.getAuthToken({ identity: identity, clientId: client(3), interactive: true }), "ya29.granted");
  assert.equal(asked.length, 2, "one silent attempt, then the page");
  assert.equal(asked[0].interactive, false);
  assert.equal(new URL(asked[0].url).searchParams.get("prompt"), "none");
  assert.equal(asked[1].interactive, true);
  assert.equal(new URL(asked[1].url).searchParams.get("prompt"), "select_account");

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
  const identity = fakeIdentity(granted);
  const mine = client(6);
  const token = await Drive.getAuthToken({ identity: identity, clientId: mine });
  assert.equal(await Drive.getAuthToken({ identity: identity, clientId: mine }), token);
  await Drive.removeCachedToken(token);
  // A second call after the removal asks Google again.
  await Drive.getAuthToken({ identity: identity, clientId: mine });
  assert.equal(identity.asked.length, 2);
});

test("signing out revokes the token at Google and forgets it here", async () => {
  const identity = fakeIdentity(granted);
  const mine = client(7);
  await Drive.getAuthToken({ identity: identity, clientId: mine });
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
  // Nothing is left to reuse.
  await Drive.getAuthToken({ identity: identity, clientId: mine });
  assert.equal(identity.asked.length, 2);
});

test("the requested scope covers only the files the user handed to Pastex", () => {
  // The per-file scope Google documents, not the account-wide drive scope: the
  // files Pastex writes and the ones the configuration links name are what
  // grant the access.
  assert.equal(Drive.SCOPES, "https://www.googleapis.com/auth/drive.file");
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

test("an index write edits the linked file, and creates it only when there is no link", async () => {
  const index = { v: 2, order: { root: ["b", "a"] }, collapsed: { f: "1" } };
  const body = JSON.stringify(index);

  // With a link: one media PATCH to that file.
  const linked = stubFetch(async () => jsonResponse({ id: "index-file" }));
  assert.equal(await Drive.writeIndexFile("tok", "index-file", "root", index, linked), "index-file");
  assert.equal(linked.calls.length, 1);
  const patch = linked.calls[0];
  assert.equal(patch.options.method, "PATCH");
  assert.ok(patch.url.includes("/files/index-file"));
  assert.ok(patch.url.includes("uploadType=media"));
  assert.equal(patch.options.body, body, "the whole label is written, unclipped");

  // Without a link: one multipart POST into the library folder, whose new id is
  // reported back so the session edits that file from then on.
  const created = stubFetch(async () => jsonResponse({ id: "new-index" }));
  assert.equal(await Drive.writeIndexFile("tok", "", "root", index, created), "new-index");
  const post = created.calls[0];
  assert.equal(post.options.method, "POST");
  assert.ok(post.url.startsWith("https://www.googleapis.com/upload/drive/v3/files"));
  assert.ok(post.options.body.includes("pastex-index.json"));
  assert.ok(post.options.body.includes(body));
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
  await Drive.writeIndexFile("tok", "index-file", "root", index, fetchImpl);
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
  await Drive.writeIndexFile("tok", "index-file", "root", { v: 2, order: {}, collapsed: {} }, fetchImpl);
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
  await Drive.writeIndexFile("tok", "index-file", "root", { v: 2, order: {}, collapsed: {} }, fetchImpl);
  assert.equal(fetchImpl.calls.length, 1, "the id from the link is reused");
  assert.equal(fetchImpl.calls[0].options.method, "PATCH");
});
