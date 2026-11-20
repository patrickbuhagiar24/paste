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

test("Drive tree respects persistent item properties, then Drive order for unranked items", async () => {
  const drive = fakeDrive();
  drive.listChildren = async () => [
    { id: "unranked", name: "New.txt", mimeType: drive.TEXT_MIME },
    { id: "second", name: "Second.txt", mimeType: drive.TEXT_MIME, properties: { pastex_order: "1" } },
    { id: "first", name: "First.txt", mimeType: drive.TEXT_MIME, properties: { pastex_order: "0" } },
  ];
  drive.downloadText = async (token, id) => id;
  const result = await Library.load(native, drive);
  assert.deepEqual(result.tree.map((node) => node.id), ["first", "second", "unranked"]);
});

test("folder collapse state is loaded from Drive metadata", async () => {
  const drive = fakeDrive();
  drive.listChildren = async (token, parent) => parent === "root" ? [
    { id: "closed", name: "Closed", mimeType: drive.FOLDER_MIME, properties: { pastex_collapsed: "1" } },
    { id: "open", name: "Open", mimeType: drive.FOLDER_MIME, properties: { pastex_collapsed: "0" } },
    { id: "unset", name: "Unset", mimeType: drive.FOLDER_MIME },
  ] : [];
  const result = await Library.load(native, drive);
  assert.deepEqual(result.tree.map((node) => [node.id, node.collapsed]), [
    ["closed", true], ["open", false], ["unset", false],
  ]);
});

test("cached text is reused by modifiedTime and stale bodies are refreshed", async () => {
  const drive = fakeDrive();
  const downloads = [];
  drive.listChildren = async (token, parent) => parent === "root" ? [
    { id: "folder", name: "Closed", mimeType: drive.FOLDER_MIME, properties: { pastex_collapsed: "1" } },
    { id: "top", name: "Top.txt", mimeType: drive.TEXT_MIME, modifiedTime: "v2" },
  ] : [{ id: "nested", name: "Nested.txt", mimeType: drive.TEXT_MIME, modifiedTime: "v2" }];
  drive.downloadText = async (token, id) => { downloads.push(id); return "fresh " + id; };
  const result = await Library.load(native, drive, undefined, { bodies: {
    nested: { modifiedTime: "v2", text: "cached nested" },
    top: { modifiedTime: "v1", text: "stale" },
  } });
  assert.deepEqual(downloads, ["top"]);
  assert.equal(result.tree[0].children[0].text, "cached nested");
  assert.equal(result.tree[1].text, "fresh top");
  assert.equal(result.bodies.nested.text, "cached nested");
  assert.equal(result.bodies.top.text, "fresh top");
});

test("text inside a collapsed folder is deferred until the folder is opened", async () => {
  const drive = fakeDrive();
  let downloads = 0;
  drive.listChildren = async (token, parent) => parent === "root" ? [
    { id: "closed", name: "Closed", mimeType: drive.FOLDER_MIME, properties: { pastex_collapsed: "1" } },
  ] : [{ id: "inside", name: "Inside.txt", mimeType: drive.TEXT_MIME, modifiedTime: "v1" }];
  drive.downloadText = async () => { downloads++; return "loaded"; };
  const closed = await Library.load(native, drive);
  assert.equal(closed.tree[0].children[0].deferred, true);
  assert.equal(closed.tree[0].children[0].loadError, undefined);
  assert.equal(downloads, 0);
  drive.listChildren = async (token, parent) => parent === "root" ? [
    { id: "closed", name: "Closed", mimeType: drive.FOLDER_MIME, properties: { pastex_collapsed: "0" } },
  ] : [{ id: "inside", name: "Inside.txt", mimeType: drive.TEXT_MIME, modifiedTime: "v1" }];
  const opened = await Library.load(native, drive, undefined, { bodies: closed.bodies });
  assert.equal(opened.tree[0].children[0].text, "loaded");
  assert.equal(downloads, 1);
});

test("unchanged nested folders are reused instead of being listed again", async () => {
  const drive = fakeDrive();
  let lists = 0;
  drive.getFolder = async () => ({ id: "root", mimeType: drive.FOLDER_MIME, modifiedTime: "root-v1" });
  drive.listChildren = async (token, parent) => {
    lists++;
    if (parent === "root") return [{ id: "f", name: "Folder", mimeType: drive.FOLDER_MIME, modifiedTime: "f-v1", properties: { pastex_collapsed: "0" } }];
    return [{ id: "inside", name: "Inside.txt", mimeType: drive.TEXT_MIME, modifiedTime: "t-v1" }];
  };
  const first = await Library.load(native, drive);
  assert.equal(lists, 2);
  const second = await Library.load(native, drive, undefined, {
    tree: first.tree,
    bodies: first.bodies,
    metadata: first.metadata,
  });
  assert.equal(lists, 3, "only the root should be listed; the unchanged folder is reused");
  assert.equal(second.tree[0].children[0].text, first.tree[0].children[0].text);
  assert.equal(second.metadata.f, "f-v1");
});
