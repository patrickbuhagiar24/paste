"use strict";
const assert = require("node:assert/strict");
const { webcrypto } = require("node:crypto");
const Cache = require("../cache.js");

function setup() {
  const data = {};
  let calls = 0;
  const config = { drive: { folderId: "root" }, serviceAccount: { client_email: "account", private_key: "private-secret" } };
  const storage = {
    async get(key) { return { [key]: data[key] }; },
    async set(values) { Object.assign(data, structuredClone(values)); },
    async remove(key) { delete data[key]; },
  };
  const native = { readConfig: async () => structuredClone(config) };
  const library = { load: async () => ({ tree: [{ id: String(++calls), kind: "text", name: "One", text: "contents" }], session: { rootId: config.drive.folderId }, warnings: [] }) };
  function make() { return Cache.create(native, {}, library, storage, webcrypto); }
  return { data, config, storage, native, library, make, get calls() { return calls; } };
}

test("cache is session-only, shared after worker restart and keyed by account and folder", async () => {
  const x = setup();
  const a = x.make();
  const first = await a.get(false);
  assert.equal(first.cached, false);
  assert.equal((await a.get(false)).cached, true);
  const b = x.make();
  assert.equal((await b.get(false)).tree[0].id, "1");
  assert.equal(x.calls, 1);
  assert.ok(!JSON.stringify(x.data).includes("private-secret"));
  assert.equal((await b.get(true)).tree[0].id, "2");
  x.config.drive.folderId = "other";
  assert.equal((await b.get(false)).tree[0].id, "3");
  x.config.serviceAccount.private_key = "rotated-key";
  assert.equal((await b.get(false)).tree[0].id, "4");
  x.config.serviceAccount = null;
  await assert.rejects(b.get(false), /service-account/);
  assert.equal(x.data[Cache.KEY], undefined);
});

test("partial or failed refresh does not overwrite an existing complete cache", async () => {
  const x = setup();
  const cache = x.make();
  await cache.get(false);
  x.library.load = async () => ({ tree: [{ id: "broken", loadError: "403" }], session: { rootId: "root" }, warnings: ["403"] });
  assert.equal((await cache.get(true)).tree[0].id, "broken");
  assert.equal((await cache.get(false)).tree[0].id, "1");
  x.library.load = async () => { throw new Error("offline"); };
  await assert.rejects(cache.get(true), /offline/);
  assert.equal((await cache.get(false)).tree[0].id, "1");
});

test("mutations invalidate cache even on partial failure and block reads until finished", async () => {
  const x = setup();
  const cache = x.make();
  await cache.get(false);
  let release;
  const started = new Promise((resolve) => { release = resolve; });
  let finish;
  const gate = new Promise((resolve) => { finish = resolve; });
  const mutation = cache.mutate(async () => { release(); await gate; throw new Error("partially failed"); });
  await started;
  const pending = cache.get(false);
  await new Promise(setImmediate);
  assert.equal(x.calls, 1);
  finish();
  await assert.rejects(mutation, /partially failed/);
  assert.equal((await pending).tree[0].id, "2");
});

test("simultaneous misses share a load; an explicit refresh fetches again", async () => {
  const x = setup();
  let release;
  const initial = x.library.load;
  x.library.load = async (...args) => { await new Promise((resolve) => { release = resolve; }); return initial(...args); };
  const cache = x.make();
  const first = cache.get(false), second = cache.get(false);
  while (!release) await new Promise(setImmediate);
  release();
  assert.equal((await first).tree[0].id, "1");
  assert.equal((await second).tree[0].id, "1");
  assert.equal(x.calls, 1);
  x.library.load = initial;
  assert.equal((await cache.get(true)).tree[0].id, "2");
});

test("session storage quota failure falls back to memory without retaining old stored data", async () => {
  const x = setup();
  const cache = x.make();
  await cache.get(false);
  x.storage.set = async () => { throw new Error("quota"); };
  assert.equal((await cache.get(true)).tree[0].id, "2");
  assert.equal((await cache.get(false)).tree[0].id, "2");
  assert.equal(x.data[Cache.KEY], undefined);
});

test("an older load cannot repopulate cache after a mutation starts", async () => {
  const x = setup();
  const original = x.library.load;
  let release, began;
  const started = new Promise((resolve) => { began = resolve; });
  x.library.load = async (...args) => { began(); await new Promise((resolve) => { release = resolve; }); return original(...args); };
  const cache = x.make();
  const loading = cache.get(false);
  await started;
  await cache.mutate(async () => {});
  release();
  await assert.rejects(loading, /changed while loading/);
  assert.equal(x.data[Cache.KEY], undefined);
  x.library.load = original;
  assert.equal((await cache.get(false)).tree[0].id, "2");
});

test("an explicit Drive permission denial clears an old snapshot", async () => {
  const x = setup();
  const cache = x.make();
  await cache.get(false);
  x.library.load = async () => { throw new Error("Drive API failed (HTTP 403)"); };
  await assert.rejects(cache.get(true), /403/);
  assert.equal(x.data[Cache.KEY], undefined);
});
