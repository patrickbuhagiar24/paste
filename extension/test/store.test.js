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

test("flatten returns texts in tree order", () => {
  const ids = Store.flatten(sampleTree()).map((n) => n.id);
  assert.deepEqual(ids, ["t1", "t2", "t3"]);
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
