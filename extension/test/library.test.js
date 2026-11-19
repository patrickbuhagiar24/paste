"use strict";
const assert = require("node:assert/strict");
const Library = require("../library.js");

function fakeDrive() {
  return {
    FOLDER_MIME: "application/vnd.google-apps.folder", TEXT_MIME: "text/plain",
    parseServiceAccount: (text) => JSON.parse(text), fetchAccessToken: async () => "token",
    getFolder: async () => ({ id: "root", mimeType: "application/vnd.google-apps.folder" }),
    listChildren: async (token, parent) => parent === "root" ? [
      { id: "folder", name: "Folder", mimeType: "application/vnd.google-apps.folder" },
      { id: "empty", name: "Empty", mimeType: "application/vnd.google-apps.folder" },
      { id: "top", name: "Top.txt", mimeType: "text/plain" },
      { id: "ignored", name: "slides", mimeType: "application/vnd.google-apps.presentation" },
    ] : parent === "folder" ? [{ id: "nested", name: "Nested.txt", mimeType: "application/octet-stream" }] : [],
    downloadText: async (token, id) => ({ top: "top content", nested: "nested content" })[id],
  };
}
const native = { readConfig: async () => ({ drive: { folderId: "root" }, serviceAccount: { client_email: "x", private_key: "y" } }) };

test("Drive library loads nested snippets, contents, ids, and empty folders", async () => {
  const result = await Library.load(native, fakeDrive());
  assert.equal(result.session.rootId, "root");
  assert.equal(result.tree.length, 3);
  assert.equal(result.tree[0].children[0].name, "Nested");
  assert.equal(result.tree[0].children[0].text, "nested content");
  assert.deepEqual(result.tree[1].children, []);
  assert.equal(result.tree[2].id, "top");
  assert.equal(result.tree[2].text, "top content");
});

test("Drive library reports missing configuration, invalid folder, and fetch errors", async () => {
  await assert.rejects(Library.load({ readConfig: async () => ({}) }, fakeDrive()), /service-account/);
  await assert.rejects(Library.load({ readConfig: async () => ({ serviceAccount: {} }) }, fakeDrive()), /google-drive/);
  const drive = fakeDrive();
  drive.getFolder = async () => ({ mimeType: "text/plain" });
  await assert.rejects(Library.load(native, drive), /not a folder/);
  const offline = fakeDrive();
  offline.listChildren = async () => { throw new Error("permission denied"); };
  await assert.rejects(Library.load(native, offline), /permission denied/);
});

test("Drive tree is reported progressively with bounded parallel downloads", async () => {
  const drive = fakeDrive();
  let active = 0, max = 0;
  const waiting = [];
  drive.listChildren = async () => Array.from({ length: 9 }, (_, i) => ({ id: String(i), name: i + ".txt", mimeType: drive.TEXT_MIME }));
  drive.downloadText = async (token, id) => {
    active++; max = Math.max(max, active);
    await new Promise((resolve) => waiting.push(resolve));
    active--;
    return "content " + id;
  };
  let partial;
  const loading = Library.load(native, drive, (tree) => { partial = tree; });
  await new Promise(setImmediate);
  assert.equal(partial.length, 9);
  assert.equal(partial[0].loadError, "Loading from Google Drive…");
  assert.equal(max, 6);
  waiting.splice(0).forEach((resolve) => resolve());
  await new Promise(setImmediate);
  assert.equal(partial[0].text, "content 0");
  assert.equal(partial[0].loadError, undefined);
  waiting.splice(0).forEach((resolve) => resolve());
  const result = await loading;
  assert.equal(result.tree[8].text, "content 8");
  assert.equal(max, 6);
});
