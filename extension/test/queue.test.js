// Edits made on a site are applied to the cached snapshot and queued; the
// queue is what reaches Drive, once the tab is left.
"use strict";
const assert = require("node:assert/strict");
const Queue = require("../queue.js");

function snapshot() {
  return {
    identity: "key", rootId: "root",
    tree: [
      { id: "a", kind: "text", name: "Alpha", driveName: "Alpha.txt", text: "A" },
      { id: "f", kind: "folder", name: "Folder", driveName: "Folder", collapsed: false,
        children: [{ id: "b", kind: "text", name: "Beta", driveName: "Beta.txt", text: "B" }] },
    ],
    bodies: {}, texts: {},
  };
}

function store() {
  const data = {};
  return {
    data,
    async get(key) { return { [key]: data[key] }; },
    async set(values) { Object.assign(data, structuredClone(values)); },
    async remove(key) { delete data[key]; },
  };
}

test("a created row is added to the cache and gets a local id until it syncs", () => {
  const snap = snapshot();
  const applied = Queue.apply(snap, { kind: "create", name: "New", text: "hello", parentId: "root",
    item: { kind: "text", name: "New", parentId: "root", localId: "" } });
  assert.equal(applied.length, 1);
  assert.match(applied[0], /^local-/);
  const row = Queue.find(snap.tree, applied[0]);
  assert.equal(row.name, "New");
  assert.equal(row.text, "hello");
  assert.equal(row.kind, "text");
  // The tree is the content of the root folder, so it is a top-level row.
  assert.equal(snap.tree.indexOf(row), snap.tree.length - 1);
});

test("a row created inside a folder lands there", () => {
  const snap = snapshot();
  const applied = Queue.apply(snap, { kind: "create", name: "Inside", text: "x", parentId: "f",
    item: { kind: "text", name: "Inside", parentId: "f", localId: "" } });
  assert.equal(Queue.find(snap.tree, "f").children.length, 2);
  assert.equal(Queue.find(snap.tree, applied[0]).name, "Inside");
});

test("a second edit on a row that never reached Drive still names it", () => {
  const snap = snapshot();
  const [id] = Queue.apply(snap, { kind: "create", name: "Draft", text: "one", parentId: "root",
    item: { kind: "text", parentId: "root", localId: "" } });
  Queue.apply(snap, { kind: "edit", name: "Draft", text: "two", item: { id: id, localId: id, kind: "text" } });
  const row = Queue.find(snap.tree, id);
  assert.equal(row.name, "Draft");
  assert.equal(row.text, "two");
  assert.equal(row.id, id, "an edit does not change the identity of the row");
});

test("an edit changes the body, a rename does not", () => {
  const snap = snapshot();
  Queue.apply(snap, { kind: "edit", name: "Alpha", text: "changed", item: { id: "a", kind: "text" } });
  assert.equal(Queue.find(snap.tree, "a").text, "changed");
  Queue.apply(snap, { kind: "rename", name: "Renamed", item: { id: "a", kind: "text" } });
  assert.equal(Queue.find(snap.tree, "a").name, "Renamed");
  assert.equal(Queue.find(snap.tree, "a").text, "changed", "a rename keeps the body");
});

test("a reorder in the root folder reorders the top-level rows only", () => {
  const snap = snapshot();
  Queue.apply(snap, { kind: "create", name: "Gamma", text: "g", parentId: "root",
    item: { kind: "text", parentId: "root", localId: "" } });
  const top = snap.tree.map((node) => node.id);
  const moved = [top[2]].concat(top.slice(0, 2));
  Queue.apply(snap, { kind: "reorder", groups: [moved], parents: ["root"] });
  assert.deepEqual(snap.tree.map((node) => node.id), moved);
  assert.equal(Queue.find(snap.tree, "f").children.length, 1, "a nested folder keeps its own order");
});

test("a reorder inside a folder leaves the root alone", () => {
  const snap = snapshot();
  Queue.apply(snap, { kind: "create", name: "X", text: "x", parentId: "f",
    item: { kind: "text", parentId: "f", localId: "" } });
  const top = snap.tree.map((node) => node.id);
  Queue.apply(snap, { kind: "reorder", groups: [["b", Queue.find(snap.tree, "f").children[1].id]], parents: ["f"] });
  assert.deepEqual(snap.tree.map((node) => node.id), top, "the root order is untouched");
});

test("a collapse applied locally is part of the snapshot", () => {
  const snap = snapshot();
  Queue.apply(snap, { kind: "collapse", ids: ["f"], collapsed: true });
  assert.equal(Queue.find(snap.tree, "f").collapsed, true);
});

test("a delete removes the row and its body, and leaves the rest", () => {
  const snap = snapshot();
  snap.bodies = { a: { hash: "h" }, b: { hash: "h2" } };
  Queue.apply(snap, { kind: "delete", ids: ["a"] });
  assert.equal(Queue.find(snap.tree, "a"), null);
  assert.equal(snap.bodies.a, undefined);
  assert.ok(Queue.find(snap.tree, "b"), "a sibling survives");
});

test("the queue survives a worker restart and keeps its order", async () => {
  const area = store();
  const first = Queue.create(area);
  await first.push([{ id: "1", kind: "create", name: "One", item: {} }]);
  await first.push([{ id: "2", kind: "edit", name: "Two", item: {} }]);
  // A new worker reads the same storage.
  const second = Queue.create(area);
  const entries = await second.all();
  assert.deepEqual(entries.map((entry) => entry.id), ["1", "2"]);
  await second.remove(["1"]);
  const third = Queue.create(area);
  assert.deepEqual((await third.all()).map((entry) => entry.id), ["2"]);
  await third.clear();
  assert.equal(area.data[Queue.KEY], undefined, "an empty queue leaves no blob behind");
});

test("the index written to Drive skips rows that never reached it", () => {
  const snap = snapshot();
  snap.tree.push({ id: "local-1", kind: "text", name: "Draft" });
  snap.tree[1].collapsed = true;
  const index = Queue.indexOf(snap.tree);
  assert.deepEqual(index.order.f, ["b"]);
  assert.equal(index.collapsed.f, "1");
  assert.equal(index.order.root, undefined, "the root folder owns its own order");
});

test("a row that is created and then edited waits as one upload", async () => {
  const area = store();
  const queue = Queue.create(area);
  await queue.push([{ id: "1", kind: "create", name: "Draft", text: "one",
    item: { id: "local-7", localId: "local-7", kind: "text", isNew: true } }]);
  await queue.push([{ id: "2", kind: "edit", name: "Draft", text: "two",
    item: { id: "local-7", localId: "local-7", kind: "text", isNew: true } }]);
  const entries = await queue.all();
  assert.equal(entries.length, 1, "one new row costs one upload, not two");
  assert.equal(entries[0].id, "2", "the newest state wins");
  assert.equal(entries[0].text, "two");
});

test("deleting a row that never reached Drive cancels its queued upload", async () => {
  const area = store();
  const queue = Queue.create(area);
  await queue.push([{ id: "1", kind: "create", name: "Draft",
    item: { id: "local-9", localId: "local-9", kind: "text", isNew: true } }]);
  await queue.push([{ id: "2", kind: "delete", ids: [], item: { id: "local-9", localId: "local-9" } }]);
  assert.equal((await queue.all()).length, 0, "nothing is left to send");
});

test("two edits of a row that already exists stay two separate writes", async () => {
  const area = store();
  const queue = Queue.create(area);
  await queue.push([{ id: "1", kind: "edit", name: "A", text: "one", item: { id: "drive-1", kind: "text" } }]);
  await queue.push([{ id: "2", kind: "edit", name: "A", text: "two", item: { id: "drive-1", kind: "text" } }]);
  assert.equal((await queue.all()).length, 2, "an existing row is not collapsed silently");
});

test("the order the panel writes is the order the loader applies after a restart", () => {
  // BUG: indexOf() keyed the root folder's order as "", while the loader asks
  // for it by the root folder's Drive id. A reorder was therefore written to
  // Drive and then silently ignored on the next read.
  const snap = snapshot();
  snap.tree = [snap.tree[1], snap.tree[0]];   // the panel moved the second row up
  const index = Queue.indexOf(snap.tree, snap.rootId);
  assert.deepEqual(index.order.root, ["f", "a"],
    "the root folder's order must be stored under its own id");
  assert.equal(index.order[""], undefined,
    "an unnamed key is the bug: the loader never asks for it");
});

test("the index the queue writes and the index the loader reads use the same keys", () => {
  const snap = snapshot();
  snap.tree.reverse();
  snap.tree.forEach((node) => { if (node.kind === "folder") node.collapsed = true; });
  const written = Queue.indexOf(snap.tree, snap.rootId);
  const parents = Object.keys(written.order);
  assert.deepEqual(parents.sort(), ["f", "root"]);
  assert.ok(parents.includes("f"), "a nested folder's order is keyed by its id");
  assert.equal(written.collapsed.f, "1", "a fold is still recorded by folder id");
});

test("the index the queue writes is the index the loader accepts", async () => {
  const Library = require("../library.js");
  const tree = [
    { id: "a", kind: "text", name: "A" },
    { id: "f", kind: "folder", name: "F", collapsed: true, children: [{ id: "b", kind: "text", name: "B" }] },
  ];
  // background.js writes cloneIndex(session.index), never the raw tree walk, so
  // this is the shape that must be readable: both spellings of the version are
  // covered by building the label the way the worker builds it.
  const written = JSON.stringify(Library.cloneIndex(Queue.indexOf(tree, "root")));
  // The loader only decodes a label whose version field it recognises. The two
  // files once disagreed on the field name, so every reorder and every fold was
  // stored and then ignored on the next read.
  const drive = { FOLDER_MIME: "folder", TEXT_MIME: "text/plain", INDEX_NAME: "pastex-index.json",
    getFolder: async () => ({ id: "root", mimeType: "folder" }),
    // The label travels as a file body now, so this reads the very bytes the
    // writer produced.
    readIndexFile: async () => ({ id: "index-file", index: JSON.parse(written) }),
    listChildren: async (t, id) => (id === "root"
      ? [{ id: "a", name: "A.txt", mimeType: "text/plain" }, { id: "f", name: "F", mimeType: "folder" }]
      : [{ id: "b", name: "B.txt", mimeType: "text/plain" }]),
    downloadText: async () => "body" };
  const native = { readConfig: async () => ({ drive: { folderId: "root" },
    oauth: { client_id: "123-abc.apps.googleusercontent.com" } }) };
  drive.getAuthToken = async () => "token";
  const result = await Library.load(native, drive);
  assert.deepEqual(result.index.order.root, ["a", "f"], "the order survived the round trip");
  assert.equal(result.tree[1].collapsed, true, "the fold survived the round trip");
});
