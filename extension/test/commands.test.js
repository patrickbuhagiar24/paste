"use strict";
const assert = require("node:assert/strict");
const Panel = require("../panel-command.js");
const Edit = require("../edit-command.js");

test("panel commands identify the current row and its folder", () => {
  const node = { id: "file", kind: "text", name: "Note", driveName: "Note.txt" };
  const tree = [{ id: "folder", kind: "folder", children: [node] }];
  const index = { byId: new Map([[node.id, node]]) };
  const command = Panel.identify({ action: "save", mode: "edit-text", name: "Note", text: "body", node }, index, tree, "root");
  assert.equal(command.kind, "edit");
  assert.equal(command.item.parentId, "folder");
  assert.equal(command.item.id, "file");
  assert.equal(Panel.identify({ action: "save", mode: "edit-text", node: { id: "missing" } }, index, tree, "root"), null);
});

test("queue command conversion filters local ids and validates names", () => {
  let id = 0;
  const edit = Edit.create(() => "entry-" + ++id);
  assert.throws(() => edit.validate({ kind: "edit", name: "bad/name" }), /Invalid name/);
  const reorder = edit.queuedCommand({ kind: "reorder", groups: [["local-1", "drive-id"]], parents: ["root"] });
  assert.deepEqual(reorder.groups, [["drive-id"]]);
  assert.equal(edit.queuedCommand({ kind: "move", ids: ["local-1"] }), null);
  const create = edit.queuedCommand({ kind: "create", name: "Note", text: "body", item: { kind: "text" } }, "local-1");
  assert.equal(create.item.localId, "local-1");
  assert.equal(create.item.isNew, true);
});
