"use strict";
const assert = require("node:assert/strict");
const Drive = require("../drive.js");
const Library = require("../library.js");

function json(data) {
  return { ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => data, text: async () => JSON.stringify(data) };
}
function text(data) {
  return { ok: true, status: 200, headers: { get: () => "text/plain" }, text: async () => data };
}
// Configuration as the panel caches it: a library folder plus the OAuth client
// of this extension. No key file is involved anywhere.
const oauthConfig = {
  readConfig: async () => ({
    drive: { folderId: "root" },
    oauth: { client_id: "123456789012-abcdefghijklmnop.apps.googleusercontent.com" },
  }),
};

test("Drive library signs in and lists and loads a shared folder through the API", async () => {
  const previousFetch = globalThis.fetch;
  const previousToken = Drive.getAuthToken;
  // The token now comes from Chrome's Google sign-in; the library only needs
  // to see that it was asked for one before it touched the API.
  let signIns = 0;
  Drive.getAuthToken = async () => { signIns++; return "token"; };
  const calls = [];
  globalThis.fetch = async (url, options) => {
    const target = new URL(url);
    calls.push(target);
    assert.equal(options.headers.Authorization, "Bearer token");
    if (target.pathname === "/drive/v3/files/root") return json({ id: "root", name: "Shared", mimeType: Drive.FOLDER_MIME });
    if (target.pathname === "/drive/v3/files") return json({ files: [{ id: "item", name: "Synced.txt", mimeType: "text/plain" }] });
    if (target.pathname === "/drive/v3/files/item" && target.searchParams.get("alt") === "media") return text("Hello from Drive");
    throw new Error("Unexpected API request: " + target);
  };
  try {
    const result = await Library.load(oauthConfig, Drive);
    assert.equal(result.tree[0].name, "Synced");
    assert.equal(result.tree[0].text, "Hello from Drive");
    assert.equal(signIns, 1, "one sign-in per session");
    // folder, the index listing (none yet), the library listing, the body.
    assert.equal(calls.length, 4);
    assert.equal(calls[2].searchParams.get("includeItemsFromAllDrives"), "true");
  } finally {
    globalThis.fetch = previousFetch;
    Drive.getAuthToken = previousToken;
  }
});

test("a second load lists and downloads only the branch that changed", async () => {
  const previousFetch = globalThis.fetch;
  const previousToken = Drive.getAuthToken;
  Drive.getAuthToken = async () => "token";
  const downloads = [];
  const listings = [];
  const files = {
    root: [
      { id: "folder", name: "Folder", mimeType: Drive.FOLDER_MIME, modifiedTime: "f1" },
      { id: "top", name: "Top.txt", mimeType: "text/plain", modifiedTime: "t1" },
    ],
    folder: [{ id: "inside", name: "Inside.txt", mimeType: "text/plain", modifiedTime: "i1" }],
  };
  globalThis.fetch = async (url) => {
    const target = new URL(url);
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
    const native = oauthConfig;
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
    globalThis.fetch = previousFetch;
    Drive.getAuthToken = previousToken;
  }
});
