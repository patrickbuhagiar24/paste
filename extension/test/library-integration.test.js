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
    // token, folder, the index listing (none yet), the library listing, the body.
    assert.equal(calls.length, 5);
    assert.equal(calls[3].searchParams.get("includeItemsFromAllDrives"), "true");
  } finally {
    Drive.clearTokenCache();
    globalThis.fetch = previousFetch;
    Object.defineProperty(globalThis, "crypto", { value: previousCrypto, configurable: true });
  }
});

test("a second load lists and downloads only the branch that changed", async () => {
  const pair = await webcrypto.subtle.generateKey({ name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]);
  const encoded = Buffer.from(await webcrypto.subtle.exportKey("pkcs8", pair.privateKey)).toString("base64");
  const account = { client_email: "test@example.com", private_key: "-----BEGIN PRIVATE KEY-----\n" + encoded + "\n-----END PRIVATE KEY-----" };
  const previousFetch = globalThis.fetch;
  const previousCrypto = globalThis.crypto;
  const downloads = [];
  const listings = [];
  const files = {
    root: [
      { id: "folder", name: "Folder", mimeType: Drive.FOLDER_MIME, modifiedTime: "f1" },
      { id: "top", name: "Top.txt", mimeType: "text/plain", modifiedTime: "t1" },
    ],
    folder: [{ id: "inside", name: "Inside.txt", mimeType: "text/plain", modifiedTime: "i1" }],
  };
  Object.defineProperty(globalThis, "crypto", { value: webcrypto, configurable: true });
  globalThis.fetch = async (url) => {
    const target = new URL(url);
    if (target.hostname === "oauth2.googleapis.com") return json({ access_token: "token", expires_in: 3600 });
    if (target.pathname === "/drive/v3/files/root") {
      return json({ id: "root", name: "Shared", mimeType: Drive.FOLDER_MIME, modifiedTime: "r1" });
    }
    if (target.pathname === "/drive/v3/files" && target.searchParams.get("q")) {
      const parent = target.searchParams.get("q").match(/'([^']+)' in parents/)[1];
      listings.push(parent);
      // The index is a plain file in the library folder; it is never a row.
      if (parent === "root") {
        return json({ files: [
          { id: "index", name: Drive.INDEX_NAME, mimeType: "text/plain", modifiedTime: "i1" },
          ...(files[parent] || []),
        ] });
      }
      return json({ files: files[parent] || [] });
    }
    if (target.pathname === "/drive/v3/files/index" && target.searchParams.get("alt") === "media") {
      return text(JSON.stringify({ v: 2, order: { root: ["top", "folder"] }, collapsed: {} }));
    }
    if (target.pathname === "/drive/v3/files/inside" && target.searchParams.get("alt") === "media") {
      downloads.push("inside");
      return text("Inside body");
    }
    if (target.pathname === "/drive/v3/files/top" && target.searchParams.get("alt") === "media") {
      downloads.push("top");
      return text("Top body");
    }
    throw new Error("Unexpected API request: " + target);
  };
  try {
    Drive.clearTokenCache();
    const native = { readConfig: async () => ({ serviceAccount: account, drive: { folderId: "root" } }) };
    const first = await Library.load(native, Drive);
    // The central index of the root folder decides the order.
    assert.deepEqual(first.tree.map((node) => node.id), ["top", "folder"]);
    assert.deepEqual(downloads.sort(), ["inside", "top"]);
    assert.deepEqual(listings.sort(), ["folder", "root", "root"], "the changed folder and the two root listings");

    listings.length = 0; downloads.length = 0;
    const second = await Library.load(native, Drive, undefined, {
      tree: first.tree, bodies: first.bodies, texts: first.texts,
      metadata: first.metadata, signatures: first.signatures,
    });
    // Reading the index costs one listing of the root; the tree itself only
    // needs the root listed again, and the unchanged folder is reused.
    assert.deepEqual(listings.sort(), ["root", "root"], "only the root is listed again");
    assert.deepEqual(downloads, [], "no body is downloaded again");
    assert.equal(second.tree[1].children[0].text, "Inside body");

    // One file inside the folder changes: only that branch is listed again.
    listings.length = 0; downloads.length = 0;
    files.folder[0].modifiedTime = "i2";
    files.root[0].modifiedTime = "f2";
    const third = await Library.load(native, Drive, undefined, {
      tree: second.tree, bodies: second.bodies, texts: second.texts,
      metadata: second.metadata, signatures: second.signatures,
    });
    assert.deepEqual(listings.sort(), ["folder", "root", "root"],
      "the index costs one root listing, the tree needs the other");
    assert.deepEqual(downloads, ["inside"], "only the changed body is downloaded again");
    assert.equal(third.tree[0].text, "Top body");
    assert.equal(third.tree[1].children[0].text, "Inside body");
  } finally {
    Drive.clearTokenCache();
    globalThis.fetch = previousFetch;
    Object.defineProperty(globalThis, "crypto", { value: previousCrypto, configurable: true });
  }
});
