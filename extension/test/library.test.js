"use strict";
const assert = require("node:assert/strict");
const Library = require("../library.js");

function fakeDrive() {
  return {
    FOLDER_MIME: "application/vnd.google-apps.folder", TEXT_MIME: "text/plain",
    getAuthToken: async () => "token",
    getFolder: async () => ({ id: "root", mimeType: "application/vnd.google-apps.folder" }),
    // The index is its own file, named by the configuration; this library has
    // no index yet.
    readIndexFile: async (token, indexId) => ({ id: indexId || "", index: null }),
    listChildren: async (token, parent) => parent === "root" ? [
      { id: "folder", name: "Folder", mimeType: "application/vnd.google-apps.folder" },
      { id: "empty", name: "Empty", mimeType: "application/vnd.google-apps.folder" },
      { id: "top", name: "Top.txt", mimeType: "text/plain" },
      { id: "ignored", name: "slides", mimeType: "application/vnd.google-apps.presentation" },
    ] : parent === "folder" ? [{ id: "nested", name: "Nested.txt", mimeType: "application/octet-stream" }] : [],
    downloadText: async (token, id) => ({ top: "top content", nested: "nested content" })[id],
  };
}
const native = { readConfig: async () => ({
  drive: { folderId: "root", indexId: "" },
  oauth: { client_id: "123-abc.apps.googleusercontent.com" },
  sites: { fileId: "", allowedSites: ["https://example.org"] },
}) };

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
  await assert.rejects(Library.load({ readConfig: async () => ({}) }, fakeDrive()),
    /configuration file/);
  await assert.rejects(Library.load({ readConfig: async () => ({ drive: { folderId: "root" } }) }, fakeDrive()), /client_id/);
  const drive = fakeDrive();
  drive.getFolder = async () => ({ mimeType: "text/plain" });
  await assert.rejects(Library.load(native, drive), /does not point at a folder/);
  // A folder this grant cannot see comes back as 404 from Drive, which is not
  // the same thing as a wrong link. The message has to say both and name the fix.
  const invisible = fakeDrive();
  invisible.getFolder = async () => {
    throw new Error("Drive API GET /files/1CZ0skmI8Gy1EFd2WKxcnqBqyloy9AjnJ failed (HTTP 404): File not found: 1CZ0skmI8Gy1EFd2WKxcnqBqyloy9AjnJ.");
  };
  const named = { readConfig: async () => ({
    drive: { folderId: "1CZ0skmI8Gy1EFd2WKxcnqBqyloy9AjnJ", indexId: "" },
    oauth: { client_id: "123-abc.apps.googleusercontent.com" },
    sites: { fileId: "", allowedSites: ["https://example.org"] },
  }) };
  await assert.rejects(Library.load(named, invisible), (err) => {
    assert.match(err.message, /cannot see the library folder 1CZ0skmI8Gy1EFd2WKxcnqBqyloy9AjnJ/);
    assert.match(err.message, /pastex-folder link is the folder itself/);
    assert.match(err.message, /pick your configuration file again/);
    return true;
  });
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

test("the folder's index label decides order, then Drive order for unnamed rows", async () => {
  const drive = fakeDrive();
  // The root folder carries the centralized index; the rows themselves carry
  // nothing. No per-item label is read any more.
  // The index is its own file in the library, read before the listing.
  let indexRead = null;
  drive.readIndexFile = async () => (indexRead = { id: "idx",
    index: { v: 2, order: { root: ["second", "first"] }, collapsed: {} } });
  drive.listChildren = async () => [
    { id: "unranked", name: "New.txt", mimeType: drive.TEXT_MIME },
    { id: "first", name: "First.txt", mimeType: drive.TEXT_MIME },
    { id: "second", name: "Second.txt", mimeType: drive.TEXT_MIME },
  ];
  drive.downloadText = async (token, id) => id;
  const result = await Library.load(native, drive);
  assert.deepEqual(result.tree.map((node) => node.id), ["second", "first", "unranked"]);
  assert.equal(indexRead.id, "idx", "the index file's id is kept for the next write");
});

test("an index with another version is discarded, not decoded", async () => {
  const drive = fakeDrive();
  // v1 and an unversioned shape both mean "unknown": Drive's order wins.
  drive.readIndexFile = async () => ({ id: "idx",
    index: { order: { root: ["b", "a"] }, collapsed: { a: "1" } } });
  drive.listChildren = async () => [
    { id: "a", name: "A.txt", mimeType: drive.TEXT_MIME },
    { id: "b", name: "B.txt", mimeType: drive.TEXT_MIME },
    { id: "f", name: "F", mimeType: drive.FOLDER_MIME },
  ];
  drive.downloadText = async (token, id) => id;
  const result = await Library.load(native, drive);
  assert.deepEqual(result.tree.map((node) => node.id), ["a", "b", "f"]);
  assert.equal(result.tree[2].collapsed, false, "an unknown index must not fold anything");
});

test("folder collapse state comes from the index label, never from the folder", async () => {
  const drive = fakeDrive();
  drive.readIndexFile = async () => ({ id: "idx",
    index: { v: 2, order: {}, collapsed: { closed: "1" } } });
  drive.listChildren = async (token, parent) => parent === "root" ? [
    // A per-item label is ignored even when it is present.
    { id: "closed", name: "Closed", mimeType: drive.FOLDER_MIME },
    { id: "open", name: "Open", mimeType: drive.FOLDER_MIME, properties: { pastex_collapsed: "1" } },
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
    { id: "folder", name: "Closed", mimeType: drive.FOLDER_MIME },
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
  // A body is addressed by the SHA-256 of its content.
  const nested = result.bodies.nested;
  assert.equal(nested.hash, require("node:crypto").createHash("sha256").update("cached nested", "utf8").digest("hex"));
  assert.equal(result.texts[nested.hash], "cached nested");
  assert.equal(result.texts[result.bodies.top.hash], "fresh top");
});

test("a body with the same Drive checksum is reused even when the timestamp moved", async () => {
  const drive = fakeDrive();
  const downloads = [];
  const checksum = "a".repeat(64);
  drive.listChildren = async (token, parent) => parent === "root" ? [
    { id: "top", name: "Top.txt", mimeType: drive.TEXT_MIME, modifiedTime: "v2", sha256Checksum: checksum },
  ] : [];
  drive.downloadText = async (token, id) => { downloads.push(id); return "same content"; };
  const hash = require("node:crypto").createHash("sha256").update("same content", "utf8").digest("hex");
  const result = await Library.load(native, drive, undefined, {
    bodies: { top: { modifiedTime: "v1", hash, driveHash: checksum } },
    texts: { [hash]: "same content" },
  });
  assert.deepEqual(downloads, [], "an unchanged content checksum needs no download");
  assert.equal(result.tree[0].text, "same content");
  assert.equal(result.bodies.top.hash, hash);
});

test("only the branch that changed is listed and downloaded again", async () => {
  const drive = fakeDrive();
  const listed = [];
  const downloads = [];
  const state = {
    root: [
      { id: "left", name: "Left", mimeType: drive.FOLDER_MIME, modifiedTime: "l1" },
      { id: "right", name: "Right", mimeType: drive.FOLDER_MIME, modifiedTime: "x1" },
    ],
    left: [{ id: "la", name: "LA.txt", mimeType: drive.TEXT_MIME, modifiedTime: "la1" }],
    right: [{ id: "rb", name: "RB.txt", mimeType: drive.TEXT_MIME, modifiedTime: "rb1" }],
  };
  drive.getFolder = async () => ({ id: "root", mimeType: drive.FOLDER_MIME, modifiedTime: "r1" });
  drive.listChildren = async (token, parent) => {
    listed.push(parent);
    return state[parent] || [];
  };
  drive.downloadText = async (token, id) => { downloads.push(id); return "body " + id; };
  const first = await Library.load(native, drive);
  assert.deepEqual(listed.sort(), ["left", "right", "root"]);
  assert.deepEqual(downloads.sort(), ["la", "rb"]);
  listed.length = 0; downloads.length = 0;

  // Only one file inside "right" changes: "left" is reused as it is.
  state.right[0].modifiedTime = "rb2";
  state.root[1].modifiedTime = "x2";
  const second = await Library.load(native, drive, undefined, {
    tree: first.tree, bodies: first.bodies, texts: first.texts,
    metadata: first.metadata, signatures: first.signatures,
  });
  assert.deepEqual(listed.sort(), ["right", "root"], "the unchanged folder is not listed again");
  assert.deepEqual(downloads, ["rb"], "only the changed body is downloaded again");
  assert.equal(second.tree[0].children[0].text, "body la");
  assert.equal(second.tree[1].children[0].text, "body rb");
});

test("the index file orders and folds the library", async () => {
  const drive = fakeDrive();
  drive.getFolder = async () => ({ id: "root", mimeType: drive.FOLDER_MIME, modifiedTime: "r1" });
  drive.readIndexFile = async () => ({ id: "idx", index: {
    v: 2,
    order: { root: ["second", "first"] },
    collapsed: { folder: "1" },
  } });
  drive.listChildren = async (token, parent) => parent === "root" ? [
    { id: "first", name: "First.txt", mimeType: drive.TEXT_MIME },
    { id: "second", name: "Second.txt", mimeType: drive.TEXT_MIME },
    { id: "folder", name: "Folder", mimeType: drive.FOLDER_MIME },
  ] : [];
  drive.downloadText = async (token, id) => "body " + id;
  const result = await Library.load(native, drive);
  assert.deepEqual(result.tree.map((node) => node.id), ["second", "first", "folder"]);
  assert.equal(result.tree[2].collapsed, true);
  assert.deepEqual(result.index.order.root, ["second", "first"]);
});

test("text inside a collapsed folder is deferred until the folder is opened", async () => {
  const drive = fakeDrive();
  let downloads = 0;
  // The index lives in its own file, so a fold is a different file body, not a
  // different folder property.
  const folderWith = (collapsed) => async () => ({ id: "idx",
    index: { v: 2, order: {}, collapsed: { closed: collapsed } } });
  drive.readIndexFile = folderWith("1");
  drive.listChildren = async (token, parent) => parent === "root" ? [
    { id: "closed", name: "Closed", mimeType: drive.FOLDER_MIME },
  ] : [{ id: "inside", name: "Inside.txt", mimeType: drive.TEXT_MIME, modifiedTime: "v1" }];
  drive.downloadText = async () => { downloads++; return "loaded"; };
  const closed = await Library.load(native, drive);
  assert.equal(closed.tree[0].children[0].deferred, true);
  assert.equal(closed.tree[0].children[0].loadError, undefined);
  assert.equal(downloads, 0);
  drive.readIndexFile = folderWith("0");
  drive.listChildren = async (token, parent) => parent === "root" ? [
    { id: "closed", name: "Closed", mimeType: drive.FOLDER_MIME },
  ] : [{ id: "inside", name: "Inside.txt", mimeType: drive.TEXT_MIME, modifiedTime: "v1" }];
  const opened = await Library.load(native, drive, undefined, { bodies: closed.bodies });
  assert.equal(opened.tree[0].children[0].text, "loaded");
  assert.equal(downloads, 1);
});

test("unchanged nested folders are reused instead of being listed again", async () => {
  const drive = fakeDrive();
  let lists = 0;
  let downloads = 0;
  drive.getFolder = async () => ({ id: "root", mimeType: drive.FOLDER_MIME, modifiedTime: "root-v1" });
  drive.listChildren = async (token, parent) => {
    lists++;
    if (parent === "root") return [{ id: "f", name: "Folder", mimeType: drive.FOLDER_MIME, modifiedTime: "f-v1", properties: { pastex_collapsed: "0" } }];
    return [{ id: "inside", name: "Inside.txt", mimeType: drive.TEXT_MIME, modifiedTime: "t-v1" }];
  };
  drive.downloadText = async (token, id) => { downloads++; return "inside body"; };
  const first = await Library.load(native, drive);
  assert.equal(lists, 2);
  const second = await Library.load(native, drive, undefined, {
    tree: first.tree,
    bodies: first.bodies,
    texts: first.texts,
    metadata: first.metadata,
    signatures: first.signatures,
  });
  assert.equal(lists, 3, "only the root should be listed; the unchanged folder is reused");
  assert.equal(downloads, 1, "the reused folder does not download its body again");
  assert.equal(second.tree[0].children[0].text, first.tree[0].children[0].text);
  assert.equal(second.metadata.f, "f-v1");
});

test("a snapshot without node signatures is listed again but keeps its bodies", async () => {
  const drive = fakeDrive();
  let lists = 0;
  let downloads = 0;
  drive.getFolder = async () => ({ id: "root", mimeType: drive.FOLDER_MIME, modifiedTime: "root-v1" });
  drive.listChildren = async (token, parent) => {
    lists++;
    if (parent === "root") return [{ id: "f", name: "Folder", mimeType: drive.FOLDER_MIME, modifiedTime: "f-v1" }];
    return [{ id: "inside", name: "Inside.txt", mimeType: drive.TEXT_MIME, modifiedTime: "t-v1" }];
  };
  drive.downloadText = async (token, id) => { downloads++; return "inside body"; };
  const first = await Library.load(native, drive);
  const second = await Library.load(native, drive, undefined, {
    tree: first.tree, bodies: first.bodies, texts: first.texts, metadata: first.metadata,
  });
  assert.equal(lists, 4, "without signatures the folder is listed once more");
  assert.equal(downloads, 1, "the body is still reused through its content hash");
  assert.equal(second.tree[0].children[0].text, "inside body");
});

test("a body whose text is missing is downloaded again instead of turning into nothing", async () => {
  const drive = fakeDrive();
  let downloads = 0;
  drive.getFolder = async () => ({ id: "root", mimeType: drive.FOLDER_MIME, modifiedTime: "root-v1" });
  drive.listChildren = async (token, parent) => parent === "root" ? [
    { id: "inside", name: "Inside.txt", mimeType: drive.TEXT_MIME, modifiedTime: "t-v1" },
  ] : [];
  drive.downloadText = async (token, id) => { downloads++; return "inside body"; };
  const first = await Library.load(native, drive);
  const second = await Library.load(native, drive, undefined, {
    tree: first.tree, bodies: first.bodies, metadata: first.metadata,
    signatures: first.signatures,
  });
  assert.equal(downloads, 2, "a body without its text is fetched again");
  assert.equal(second.tree[0].text, "inside body");
});

test("a body that failed to load is retried by the fill pass", async () => {
  const drive = fakeDrive();
  let attempt = 0;
  drive.downloadText = async (token, id) => {
    if (id === "top" && attempt++ === 0) throw new Error("quota exceeded");
    return id === "top" ? "top content" : "nested content";
  };
  const first = await Library.load(native, drive);
  const row = first.tree.find((node) => node.id === "top");
  assert.equal(row.loadError, "quota exceeded", "the first read reports the failure");
  // The fill pass used to skip every row that had a loadError, so a body that
  // failed once was never read again until the snapshot was thrown away.
  const filled = await Library.fillMissing(native, drive, first.tree, { bodies: first.bodies, texts: first.texts });
  assert.equal(filled.filled, 1, "the failed body is downloaded on the retry");
  assert.equal(filled.tree.find((node) => node.id === "top").loadError, undefined,
    "the warning is cleared once the row is readable");
});

test("a deferred row waits for its folder and is not treated as a failure", async () => {
  const drive = fakeDrive();
  let downloads = 0;
  drive.downloadText = async (token, id) => { downloads++; return "nested content"; };
  const result = await Library.load(native, drive, null, { deferCollapsed: true });
  const folder = result.tree.find((node) => node.id === "folder");
  folder.collapsed = true;
  const before = downloads;
  await Library.fillMissing(native, drive, result.tree, { bodies: result.bodies, texts: result.texts });
  assert.equal(downloads, before, "a deferred row is not fetched by a fill pass");
});
