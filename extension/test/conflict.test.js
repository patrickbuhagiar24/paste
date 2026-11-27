"use strict";
const assert = require("node:assert/strict");
const Queue = require("../queue.js");
const Conflict = require("../conflict.js");

function scenario() {
  const data = {};
  const storage = { get: async (key) => ({ [key]: data[key] }),
    set: async (value) => Object.assign(data, structuredClone(value)),
    remove: async (key) => { delete data[key]; } };
  const queue = Queue.create(storage);
  let remote = "Drive version";
  const library = { connect: async () => ({ token: "token" }) };
  const drive = { downloadText: async () => remote };
  return { queue, conflict: Conflict.create(queue, {}, library, drive), setRemote: (text) => { remote = text; } };
}

test("conflict review compares the first edit and rebases only after confirmation", async () => {
  const { queue, conflict } = scenario();
  await queue.push([{ id: "q1", kind: "edit", name: "Note", text: "Local version", baseText: "Original", item: { id: "file", kind: "text" } }]);
  const review = await conflict.preview();
  assert.deepEqual(review, { id: "q1", name: "Note", local: "Local version", base: "Original", remote: "Drive version" });
  assert.equal((await queue.all())[0].baseText, "Original");
  await conflict.keepLocal(review);
  assert.equal((await queue.all())[0].baseText, "Drive version");
  assert.equal((await queue.all())[0].text, "Local version");
});

test("conflict resolution refuses a Drive version that changed after review", async () => {
  const { queue, conflict, setRemote } = scenario();
  await queue.push([{ id: "q1", kind: "edit", name: "Note", text: "local", baseText: "base", item: { id: "file", kind: "text" } }]);
  const review = await conflict.preview();
  setRemote("newer Drive version");
  await assert.rejects(conflict.keepLocal(review), /changed again/);
  assert.equal((await queue.all())[0].baseText, "base");
});

test("conflict resolution does not modify a replaced queue entry", async () => {
  const { queue, conflict } = scenario();
  await queue.push([{ id: "q1", kind: "edit", text: "local", baseText: "base", item: { id: "file", kind: "text" } }]);
  const review = await conflict.preview();
  await queue.remove(["q1"]);
  await queue.push([{ id: "q2", kind: "edit", text: "other", baseText: "base", item: { id: "file", kind: "text" } }]);
  await assert.rejects(conflict.keepLocal(review), /Review them again/);
  assert.equal((await queue.all())[0].id, "q2");
});

test("a legacy edit with no baseline can be reviewed without overwriting Drive", async () => {
  const { queue, conflict } = scenario();
  await queue.push([{ id: "q1", kind: "edit", text: "local", item: { id: "file", kind: "text" } }]);
  const review = await conflict.preview();
  assert.equal(review.base, null);
  await conflict.keepLocal(review);
  assert.equal((await queue.all())[0].baseText, "Drive version");
});

test("using Drive text replaces only the reviewed pending text", async () => {
  const { queue, conflict } = scenario();
  await queue.push([{ id: "q1", kind: "edit", text: "local", baseText: "base", item: { id: "file", kind: "text" }, command: { kind: "edit", text: "local" } }]);
  await conflict.keepDrive(await conflict.preview());
  const entry = (await queue.all())[0];
  assert.equal(entry.text, "Drive version");
  assert.equal(entry.command.text, "Drive version");
  assert.equal(entry.baseText, "Drive version");
});

test("using Drive text refuses when later edits depend on it", async () => {
  const { queue, conflict } = scenario();
  await queue.push([{ id: "q1", kind: "edit", text: "first", baseText: "base", item: { id: "file", kind: "text" } }]);
  await queue.push([{ id: "q2", kind: "edit", text: "later", baseText: "first", item: { id: "file", kind: "text" } }]);
  await assert.rejects(conflict.keepDrive(await conflict.preview()), /Later edits/);
  assert.equal((await queue.all())[0].text, "first");
});

test("a failed rebase persistence keeps the previous queue entry", async () => {
  const data = {};
  let fail = false;
  const storage = { get: async (key) => ({ [key]: data[key] }),
    set: async (values) => { if (fail) throw new Error("quota"); Object.assign(data, structuredClone(values)); },
    remove: async (key) => { delete data[key]; } };
  const queue = Queue.create(storage);
  await queue.push([{ id: "q", kind: "edit", text: "local", baseText: "old", item: { id: "f", kind: "text" } }]);
  fail = true;
  await assert.rejects(queue.rebaseFirst("q", "local", "old", "new", true), /Cannot save/);
  assert.equal((await queue.all())[0].text, "local");
  assert.equal((await queue.all())[0].baseText, "old");
});
