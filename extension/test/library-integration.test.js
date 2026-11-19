"use strict";
const assert = require("node:assert/strict");
const { webcrypto } = require("node:crypto");
const Drive = require("../drive.js");
const Library = require("../library.js");

function json(data) {
  return { ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => data, text: async () => JSON.stringify(data) };
}
function text(data) {
  return { ok: true, status: 200, headers: { get: () => "text/plain" }, text: async () => data };
}

test("Drive library signs in and lists and loads a shared folder through the API", async () => {
  const pair = await webcrypto.subtle.generateKey({ name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]);
  const encoded = Buffer.from(await webcrypto.subtle.exportKey("pkcs8", pair.privateKey)).toString("base64");
  const account = { client_email: "test@example.com", private_key: "-----BEGIN PRIVATE KEY-----\n" + encoded + "\n-----END PRIVATE KEY-----" };
  const previousFetch = globalThis.fetch;
  const previousCrypto = globalThis.crypto;
  const calls = [];
  Object.defineProperty(globalThis, "crypto", { value: webcrypto, configurable: true });
  globalThis.fetch = async (url, options) => {
    const target = new URL(url);
    calls.push(target);
    if (target.hostname === "oauth2.googleapis.com") return json({ access_token: "token", expires_in: 3600 });
    assert.equal(options.headers.Authorization, "Bearer token");
    if (target.pathname === "/drive/v3/files/root") return json({ id: "root", name: "Shared", mimeType: Drive.FOLDER_MIME });
    if (target.pathname === "/drive/v3/files") return json({ files: [{ id: "item", name: "Synced.txt", mimeType: "text/plain" }] });
    if (target.pathname === "/drive/v3/files/item" && target.searchParams.get("alt") === "media") return text("Hello from Drive");
    throw new Error("Unexpected API request: " + target);
  };
  try {
    Drive.clearTokenCache();
    const result = await Library.load({ readConfig: async () => ({ serviceAccount: account, drive: { folderId: "root" } }) }, Drive);
    assert.equal(result.tree[0].name, "Synced");
    assert.equal(result.tree[0].text, "Hello from Drive");
    assert.equal(calls.length, 4);
    assert.equal(calls[2].searchParams.get("includeItemsFromAllDrives"), "true");
  } finally {
    Drive.clearTokenCache();
    globalThis.fetch = previousFetch;
    Object.defineProperty(globalThis, "crypto", { value: previousCrypto, configurable: true });
  }
});
