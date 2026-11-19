// Unit tests for extension/drive.js: key parsing, JWT signing (WebCrypto),
// and Drive API wrappers against stub fetch().
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
  const { pem } = await testKeyPair();
  Drive.clearTokenCache();
  await assert.rejects(Drive.fetchAccessToken({ client_email: "a@b", private_key: pem }, { subtle, fetchImpl: hanging, timeoutMs: 10 }), /timed out/);
  assert.equal(signal.aborted, true);
});

test("Drive pagination rejects a repeated token instead of loading forever", async () => {
  const fetchImpl = async () => jsonResponse({ files: [], nextPageToken: "same" });
  await assert.rejects(Drive.listChildren("tok", "root", fetchImpl), /repeated page token/);
});
