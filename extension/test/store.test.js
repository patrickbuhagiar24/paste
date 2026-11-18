// Unit tests for extension/store.js (pure helpers only; storage I/O needs
// chrome.storage and is covered by registration, not execution, here).
"use strict";

const assert = require("node:assert/strict");
const Store = require("../store.js");

function sampleTree() {
  return [
    {
      id: "f1",
      kind: "folder",
      name: "Email",
      updatedAt: "2026-01-01T00:00:00.000Z",
      children: [
        { id: "t1", kind: "text", name: "Reply", text: "Thanks!", updatedAt: "2026-01-02T00:00:00.000Z" },
        { id: "t2", kind: "text", name: "Follow Up", text: "ping", updatedAt: "2026-01-03T00:00:00.000Z" },
      ],
    },
    { id: "t3", kind: "text", name: "Greeting", text: "hello", updatedAt: "2026-01-04T00:00:00.000Z" },
  ];
}

test("uuid returns unique v4-shaped ids", () => {
  const a = Store.uuid();
  const b = Store.uuid();
  assert.notEqual(a, b);
  assert.match(a, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
});

test("makeFolder/makeText build timestamped nodes", () => {
  const folder = Store.makeFolder("F");
  const text = Store.makeText("T", "body");
  assert.equal(folder.kind, "folder");
  assert.deepEqual(folder.children, []);
  assert.equal(text.kind, "text");
  assert.equal(text.text, "body");
  assert.ok(Date.parse(folder.updatedAt) > 0);
  assert.ok(Date.parse(text.updatedAt) > 0);
});

test("flatten returns texts in tree order", () => {
  const ids = Store.flatten(sampleTree()).map((n) => n.id);
  assert.deepEqual(ids, ["t1", "t2", "t3"]);
});

test("flattenWithPaths prefixes folder names", () => {
  const paths = Store.flattenWithPaths(sampleTree()).map((e) => e.path);
  assert.deepEqual(paths, ["Email/Reply", "Email/Follow Up", "Greeting"]);
});

test("findById finds nested nodes", () => {
  assert.equal(Store.findById(sampleTree(), "t2").name, "Follow Up");
  assert.equal(Store.findById(sampleTree(), "missing"), null);
});

test("search matches names before contents", () => {
  const tree = sampleTree();
  assert.deepEqual(
    Store.search(tree, "").map((n) => n.id),
    ["t1", "t2", "t3"]
  );
  assert.deepEqual(
    Store.search(tree, "repl").map((n) => n.id),
    ["t1"]
  );
  // "ping" is body-only; "hello" is body-only too — both found.
  assert.deepEqual(
    Store.search(tree, "ping").map((n) => n.id),
    ["t2"]
  );
  // Name hit sorts before body hit.
  const mixed = [
    { id: "body", kind: "text", name: "zzz", text: "alpha here", updatedAt: "" },
    { id: "name", kind: "text", name: "alpha title", text: "nothing", updatedAt: "" },
  ];
  assert.deepEqual(
    Store.search(mixed, "alpha").map((n) => n.id),
    ["name", "body"]
  );
});

test("removeById deletes nested nodes", () => {
  const tree = sampleTree();
  assert.equal(Store.removeById(tree, "t1"), true);
  assert.equal(Store.findById(tree, "t1"), null);
  assert.equal(Store.removeById(tree, "missing"), false);
});

test("moveNode reorders within its level", () => {
  const tree = sampleTree();
  assert.equal(Store.moveNode(tree, "t2", -1), true);
  assert.deepEqual(
    tree[0].children.map((n) => n.id),
    ["t2", "t1"]
  );
  // Moving past the edge keeps the order.
  assert.equal(Store.moveNode(tree, "t3", 1), true);
  assert.deepEqual(
    tree.map((n) => n.id),
    ["f1", "t3"]
  );
  assert.equal(Store.moveNode(tree, "missing", 1), false);
});

test("mergeDownloaded creates folders and updates texts", () => {
  const tree = sampleTree();
  Store.mergeDownloaded(tree, [
    { segments: ["Email", "Reply"], text: "new body", updatedAt: "2026-02-01T00:00:00.000Z" },
    { segments: ["New", "Note"], text: "fresh", updatedAt: "2026-02-01T00:00:00.000Z" },
  ]);
  const reply = Store.findById(tree, "t1");
  assert.equal(reply.text, "new body");
  assert.equal(reply.updatedAt, "2026-02-01T00:00:00.000Z");
  const paths = Store.flattenWithPaths(tree).map((e) => e.path);
  assert.ok(paths.includes("New/Note"));
});

test("touch refreshes updatedAt", () => {
  const node = { updatedAt: "2000-01-01T00:00:00.000Z" };
  Store.touch(node);
  assert.ok(Date.parse(node.updatedAt) > Date.parse("2000-01-01T00:00:00.000Z"));
});

test("storage fns fail loudly without chrome", async () => {
  await assert.rejects(Store.loadTree, /chrome\.storage/);
});
