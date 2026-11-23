// Unit tests for the in-memory Drive tree helpers.
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

test("multi-select moves blocks within each folder and ignores selected descendants on delete", () => {
  const tree = [
    { id: "a", kind: "text" }, { id: "b", kind: "text" },
    { id: "c", kind: "text" }, { id: "d", kind: "text" },
    { id: "folder", kind: "folder", children: [ { id: "x", kind: "text" }, { id: "y", kind: "text" } ] },
  ];
  const selected = new Set(["b", "c", "y"]);
  assert.deepEqual(Store.reorderGroups(tree, selected, -1), [["b", "c", "a", "d", "folder"], ["y", "x"]]);
  assert.deepEqual(Store.reorderGroups(tree, selected, 1), [["a", "d", "b", "c", "folder"]]);
  assert.deepEqual(Store.selectedRoots(tree, new Set(["folder", "x", "b"])).map((n) => n.id), ["b", "folder"]);
});

test("createIndex precomputes id, folder and lowercase search lookups", () => {
  const tree = sampleTree();
  const index = Store.createIndex(tree);
  assert.equal(index.byId.get("t2").name, "Follow Up");
  assert.equal(index.folders.length, 1);
  assert.equal(index.texts.length, 3);
  assert.deepEqual(index.search("PING").map((n) => n.id), ["t2"]);
  assert.deepEqual(index.search("").map((n) => n.id), ["t1", "t2", "t3"]);
  // Name hits sort before body hits, and an empty query returns every text.
  const mixed = [
    { id: "body", kind: "text", name: "zzz", text: "alpha here" },
    { id: "name", kind: "text", name: "alpha title", text: "nothing" },
  ];
  assert.deepEqual(Store.createIndex(mixed).search("alpha").map((n) => n.id), ["name", "body"]);
});
