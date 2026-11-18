// Unit tests for extension/drive.js: key parsing, JWT signing (WebCrypto),
// sync planning, and Drive API wrappers against stub fetch().
"use strict";

const assert = require("node:assert/strict");
const { subtle } = require("node:crypto").webcrypto;
const Drive = require("../drive.js");

function pemBlock(base64, label) {
  const lines = base64.match(/.{1,64}/g) || [];
  return "-----BEGIN " + label + "-----\n" + lines.join("\n") + "\n-----END " + label + "-----\n";
}

async function testKeyPair() {
  const pair = await subtle.generateKey(
    { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
    true,
    ["sign", "verify"]
  );
  const pkcs8 = Buffer.from(await subtle.exportKey("pkcs8", pair.privateKey)).toString("base64");
  return { pair, pem: pemBlock(pkcs8, "PRIVATE KEY") };
}

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

test("parseServiceAccount accepts a key and rejects junk", async () => {
  const { pem } = await testKeyPair();
  const sa = Drive.parseServiceAccount(JSON.stringify({ type: "service_account", client_email: "a@b.iam.gserviceaccount.com", private_key: pem }));
  assert.equal(sa.client_email, "a@b.iam.gserviceaccount.com");
  assert.throws(() => Drive.parseServiceAccount("not json"), /not valid JSON/);
  assert.throws(() => Drive.parseServiceAccount("{}"), /client_email/);
  assert.throws(
    () => Drive.parseServiceAccount(JSON.stringify({ type: "authorized_user", client_email: "a", private_key: pem })),
    /not "service_account"/
  );
  assert.throws(
    () => Drive.parseServiceAccount(JSON.stringify({ client_email: "a", private_key: "bogus" })),
    /private_key|import/i
  );
});

test("signJwt produces a verifiable RS256 token", async () => {
  const { pair, pem } = await testKeyPair();
  const now = Math.floor(Date.now() / 1000);
  const claims = Drive.claimSet("svc@example.iam.gserviceaccount.com", now);
  assert.equal(claims.aud, Drive.TOKEN_URL);
  assert.equal(claims.exp - claims.iat, 3600);
  assert.ok(claims.scope.includes("auth/drive"));

  const jwt = await Drive.signJwt(subtle, pem, claims);
  const parts = jwt.split(".");
  assert.equal(parts.length, 3);
  const header = JSON.parse(Buffer.from(parts[0].replace(/-/g, "+").replace(/_/g, "/"), "base64").toString("utf8"));
  assert.equal(header.alg, "RS256");
  const body = JSON.parse(Buffer.from(parts[1].replace(/-/g, "+").replace(/_/g, "/"), "base64").toString("utf8"));
  assert.equal(body.iss, "svc@example.iam.gserviceaccount.com");

  const data = Buffer.from(parts[0] + "." + parts[1], "utf8");
  const signature = Buffer.from(parts[2].replace(/-/g, "+").replace(/_/g, "/"), "base64");
  assert.equal(await subtle.verify("RSASSA-PKCS1-v1_5", pair.publicKey, signature, data), true);
});

test("fetchAccessToken posts a JWT assertion and caches the token", async () => {
  Drive.clearTokenCache();
  const { pem } = await testKeyPair();
  const sa = { client_email: "svc@example.iam.gserviceaccount.com", private_key: pem };
  const fetchImpl = stubFetch(async () => jsonResponse({ access_token: "tok123", expires_in: 3600 }));
  const first = await Drive.fetchAccessToken(sa, { subtle, fetchImpl });
  assert.equal(first, "tok123");
  const second = await Drive.fetchAccessToken(sa, { subtle, fetchImpl });
  assert.equal(second, "tok123");
  assert.equal(fetchImpl.calls.length, 1);
  assert.equal(fetchImpl.calls[0].url, Drive.TOKEN_URL);
  assert.ok(fetchImpl.calls[0].options.body.includes("grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer"));
  assert.ok(fetchImpl.calls[0].options.body.includes("assertion="));
  Drive.clearTokenCache();
});

test("fetchAccessToken does not reuse a token after file credentials change", async () => {
  Drive.clearTokenCache();
  const { pem } = await testKeyPair();
  const other = await testKeyPair();
  const fetchImpl = stubFetch(async () => jsonResponse({ access_token: "tok", expires_in: 3600 }));
  await Drive.fetchAccessToken({ client_email: "first@example.com", private_key: pem }, { subtle, fetchImpl });
  await Drive.fetchAccessToken({ client_email: "second@example.com", private_key: pem }, { subtle, fetchImpl });
  await Drive.fetchAccessToken({ client_email: "second@example.com", private_key: other.pem }, { subtle, fetchImpl });
  assert.equal(fetchImpl.calls.length, 3);
  Drive.clearTokenCache();
});

test("fetchAccessToken surfaces token-endpoint errors", async () => {
  Drive.clearTokenCache();
  const { pem } = await testKeyPair();
  const sa = { client_email: "svc@example.iam.gserviceaccount.com", private_key: pem };
  const fetchImpl = stubFetch(async () => jsonResponse({ error: "invalid_grant", error_description: "bad jwt" }, 400));
  await assert.rejects(Drive.fetchAccessToken(sa, { subtle, fetchImpl }), /invalid_grant|bad jwt/);
  Drive.clearTokenCache();
});

test("base64 helpers round-trip", () => {
  const bytes = Drive.base64ToBytes("aGVsbG8td29ybGQ");
  assert.equal(Buffer.from(bytes).toString("utf8"), "hello-world");
  assert.equal(Drive.stringToBase64Url("hello-world"), "aGVsbG8td29ybGQ");
  // URL-safe alphabet, no padding.
  assert.match(Drive.stringToBase64Url(">>>???"), /^[A-Za-z0-9_-]+$/);
});

test("planSync is newest-wins per path", () => {
  const local = [
    { path: "Email/Reply", updatedAt: "2026-02-01T00:00:00.000Z" },
    { path: "Local/Only", updatedAt: "2026-02-01T00:00:00.000Z" },
    { path: "Same", updatedAt: "2026-01-01T00:00:00.000Z" },
    { path: "Stale", updatedAt: "2026-01-01T00:00:00.000Z" },
  ];
  const remote = [
    { path: "Email/Reply", id: "r1", modifiedTime: "2026-01-01T00:00:00.000Z" },
    { path: "Same", id: "r2", modifiedTime: "2026-01-01T00:00:00.000Z" },
    { path: "Stale", id: "r3", modifiedTime: "2026-03-01T00:00:00.000Z" },
    { path: "Remote/Only", id: "r4", modifiedTime: "2026-01-01T00:00:00.000Z" },
  ];
  const ops = Drive.planSync(local, remote);
  const byPath = {};
  ops.forEach((op) => {
    byPath[op.path] = op;
  });
  assert.equal(byPath["Email/Reply"].type, "upload-update");
  assert.equal(byPath["Email/Reply"].remoteId, "r1");
  assert.equal(byPath["Local/Only"].type, "upload-new");
  assert.equal(byPath["Same"].type, "noop");
  assert.equal(byPath["Stale"].type, "download-update");
  assert.equal(byPath["Stale"].remoteId, "r3");
  assert.equal(byPath["Remote/Only"].type, "download-new");
  assert.equal(byPath["Remote/Only"].remoteId, "r4");
});

test("planUploadAll/planDownloadAll overwrite one side", () => {
  const local = [{ path: "A", updatedAt: "2026-01-01T00:00:00.000Z" }];
  const remote = [{ path: "A", id: "r1", modifiedTime: "2026-05-01T00:00:00.000Z" }];
  const up = Drive.planUploadAll(local, remote);
  assert.deepEqual(
    up.map((op) => op.type),
    ["upload-update"]
  );
  assert.equal(up[0].remoteId, "r1");
  const down = Drive.planDownloadAll(remote);
  assert.deepEqual(
    down.map((op) => op.type),
    ["download-new"]
  );
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

test("walkRemote maps folders and .txt files to paths", async () => {
  const pages = {
    root: [
      { id: "d1", name: "Email", mimeType: Drive.FOLDER_MIME, modifiedTime: "2026-01-01T00:00:00.000Z" },
      { id: "f1", name: "Top.txt", mimeType: "text/plain", modifiedTime: "2026-01-01T00:00:00.000Z" },
      { id: "doc1", name: "Slides", mimeType: "application/vnd.google-apps.presentation", modifiedTime: "2026-01-01T00:00:00.000Z" },
    ],
    d1: [{ id: "f2", name: "Reply.txt", mimeType: "application/octet-stream", modifiedTime: "2026-01-02T00:00:00.000Z" }],
  };
  const fetchImpl = stubFetch(async (url) => {
    // URLSearchParams encodes spaces as "+", so match the raw id instead.
    const parent = url.includes("d1") ? "d1" : "root";
    return jsonResponse({ files: pages[parent] });
  });
  const tree = await Drive.walkRemote("tok", "root", fetchImpl);
  assert.deepEqual(
    tree.texts.map((t) => t.path),
    ["Email/Reply", "Top"]
  );
  assert.deepEqual(
    tree.folders.map((f) => f.path),
    ["Email"]
  );
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

test("ensureFolderPath reuses existing folders", async () => {
  const fetchImpl = stubFetch(async (url, options) => {
    if ((options.method || "GET") === "POST") {
      return jsonResponse({ id: "created" });
    }
    if (url.includes("root")) {
      return jsonResponse({ files: [{ id: "have", name: "Email", mimeType: Drive.FOLDER_MIME }] });
    }
    return jsonResponse({ files: [] });
  });
  const leaf = await Drive.ensureFolderPath("tok", "root", ["Email", "New"], fetchImpl, {});
  assert.equal(leaf, "created");
  const posts = fetchImpl.calls.filter((c) => (c.options.method || "GET") === "POST");
  assert.equal(posts.length, 1);
});

test("driveFetch reports API errors with the message", async () => {
  const fetchImpl = stubFetch(async () =>
    jsonResponse({ error: { message: "File not found: root" } }, 404)
  );
  await assert.rejects(Drive.listChildren("tok", "root", fetchImpl), /File not found: root/);
});
