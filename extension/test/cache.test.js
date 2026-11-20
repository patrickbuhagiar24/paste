"use strict";
const assert = require("node:assert/strict");
const { webcrypto } = require("node:crypto");
const Cache = require("../cache.js");

function setup() {
  const data = {};
  const localData = {};
  let calls = 0;
  const config = { drive: { folderId: "root" }, serviceAccount: { client_email: "account", private_key: "private-secret" } };
  const storage = {
    async get(key) { return { [key]: data[key] }; },
    async set(values) { Object.assign(data, structuredClone(values)); },
    async remove(key) { delete data[key]; },
  };
  const local = {
    async get(key) { return { [key]: localData[key] }; },
    async set(values) { Object.assign(localData, structuredClone(values)); },
    async remove(key) { delete localData[key]; },
  };
  const native = { readConfig: async () => structuredClone(config) };
  const library = { load: async () => ({ tree: [{ id: String(++calls), kind: "text", name: "One", text: "contents" }], session: { rootId: config.drive.folderId }, warnings: [] }) };
  function make() { return Cache.create(native, {}, library, storage, webcrypto, local); }
  return { data, localData, config, storage, native, library, make, get calls() { return calls; } };
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

test("verified text bodies survive mutation invalidation and are reused on refresh", async () => {
  const x = setup();
  let observed;
  x.library.load = async (native, drive, onUpdate, options) => {
    observed = options;
    const cached = options.bodies && options.bodies.item;
    return {
      tree: [{ id: "item", kind: "text", name: "Item", text: cached ? cached.text : "fresh" }],
      session: { rootId: "root" }, warnings: [],
      bodies: { item: { modifiedTime: "v1", text: "fresh" } },
    };
  };
  const cache = x.make();
  await cache.get(false);
  await cache.mutate(async () => {});
  assert.equal(x.data[Cache.KEY], undefined, "the tree snapshot must be invalidated");
  await cache.get(true);
  assert.equal(observed.bodies.item.text, "fresh");
  assert.equal(x.data[Cache.KEY].bodies.item.text, "fresh");
});

test("configuration identity is memoized while the config object is unchanged", async () => {
  const x = setup();
  const stable = structuredClone(x.config);
  x.native.readConfig = async () => stable;
  const cache = x.make();
  await cache.get(false);
  const digests = [];
  const original = webcrypto.subtle.digest;
  webcrypto.subtle.digest = async (...args) => { digests.push(args); return original(...args); };
  try {
    await cache.get(false);
    await cache.get(false);
    assert.equal(digests.length, 0);
  } finally { webcrypto.subtle.digest = original; }
});

test("a local snapshot survives a browser restart and a stale one silently refreshes", async () => {
  const x = setup();
  const first = x.make();
  await first.get(false);
  assert.ok(x.localData[Cache.LKEY], "a successful load must persist a derived local snapshot");
  const id = x.data[Cache.KEY].identity;
  const second = x.make();
  await x.storage.remove(Cache.KEY); // simulate a fresh browser process
  // The last Drive confirmation is older than the freshness window.
  x.localData[Cache.FKEY][id].savedAt = 0;
  const result = await second.get(false);
  assert.equal(result.cached, true);
  assert.equal(result.tree[0].id, "1");
  assert.equal(x.calls, 1, "opening from local cache must not block on Drive");
  await new Promise((resolve) => setTimeout(resolve, 350));
  assert.equal(x.calls, 2, "a stale local snapshot should refresh in the background");
});

test("mutations clear derived local snapshots but keep verified bodies in memory", async () => {
  const x = setup();
  const cache = x.make();
  await cache.get(false);
  assert.ok(Object.keys(x.localData[Cache.LKEY]).length);
  await cache.mutate(async () => {});
  assert.deepEqual(x.localData[Cache.LKEY], {});
  assert.equal(x.data[Cache.KEY], undefined);
  const next = await cache.get(false);
  assert.equal(next.tree[0].id, "2");
});

test("a configuration identity change clears the local snapshot", async () => {
  const x = setup();
  const cache = x.make();
  await cache.get(false);
  const old = x.data[Cache.KEY].identity;
  assert.ok(x.localData[Cache.LKEY][old]);
  x.config.drive.folderId = "other";
  await cache.get(false);
  assert.equal(x.localData[Cache.LKEY][old], undefined);
});

test("idle sync reports whether Drive actually changed", async () => {
  const x = setup();
  const cache = x.make();
  await cache.get(false);
  x.library.load = async () => ({
    tree: [{ id: "1", kind: "text", name: "One", text: "contents" }],
    session: { rootId: "root" }, warnings: [], bodies: {}, metadata: {},
  });
  const same = await cache.sync();
  assert.equal(same.changed, false);
  assert.equal(same.skipped, false);
  x.library.load = async () => ({
    tree: [{ id: "changed", kind: "text", name: "Changed", text: "new" }],
    session: { rootId: "root" }, warnings: [], bodies: {}, metadata: {},
  });
  const different = await cache.sync();
  assert.equal(different.changed, true);
  assert.equal(different.skipped, false);
});

test("an unchanged idle sync keeps the stored snapshot and only moves the freshness stamp", async () => {
  const x = setup();
  const cache = x.make();
  await cache.get(false);
  const id = x.data[Cache.KEY].identity;
  const snapshot = x.localData[Cache.LKEY][id];
  const before = JSON.stringify(snapshot);
  x.localData[Cache.FKEY][id].savedAt = 0;
  x.library.load = async () => ({
    tree: [{ id: "1", kind: "text", name: "One", text: "contents" }],
    session: { rootId: "root" }, warnings: [], bodies: {}, metadata: {},
  });
  const result = await cache.sync();
  assert.equal(result.changed, false);
  assert.equal(JSON.stringify(x.localData[Cache.LKEY][id]), before, "an unchanged library must not rewrite the snapshot");
  assert.ok(x.localData[Cache.FKEY][id].savedAt > 0, "the freshness stamp must move");
});

test("an idle sync detects reordered, renamed and collapsed items", async () => {
  const x = setup();
  const cache = x.make();
  // The state is mutated step by step, so every check sees the previous one.
  const state = {
    tree: [
      { id: "folder", kind: "folder", name: "Folder", collapsed: false,
        properties: { pastex_order: "0", pastex_collapsed: "0" },
        children: [{ id: "a", kind: "text", name: "A", driveName: "A.txt", text: "a",
          modifiedTime: "t1", properties: { pastex_order: "0" } }] },
      { id: "b", kind: "text", name: "B", driveName: "B.txt", text: "b",
        modifiedTime: "t2", properties: { pastex_order: "1" } },
    ],
    metadata: { root: "m1" },
  };
  x.library.load = async () => ({
    tree: structuredClone(state.tree),
    session: { rootId: "root" }, warnings: [], bodies: {},
    metadata: structuredClone(state.metadata),
  });
  await cache.get(false);
  assert.equal((await cache.sync()).changed, false, "an identical library reports no change");

  // A reordered item.
  state.tree[1].properties = { pastex_order: "0" };
  state.tree[0].properties = { pastex_order: "1" };
  assert.equal((await cache.sync()).changed, true, "a new order must be detected");

  // A collapsed folder.
  state.tree[0].collapsed = true;
  state.tree[0].properties = { pastex_order: "1", pastex_collapsed: "1" };
  assert.equal((await cache.sync()).changed, true, "a collapsed folder must be detected");

  // A renamed item.
  state.tree[1].name = "Renamed";
  state.tree[1].driveName = "Renamed.txt";
  assert.equal((await cache.sync()).changed, true, "a renamed item must be detected");

  // Changed folder metadata only: the visible tree is unchanged.
  state.metadata = { root: "m2" };
  assert.equal((await cache.sync()).changed, false, "a metadata-only move does not change the visible tree");
});

test("an idle sync never runs while a mutation is in flight", async () => {
  const x = setup();
  const cache = x.make();
  await cache.get(false);
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const mutation = cache.mutate(async () => { await gate; });
  await new Promise(setImmediate); // let the mutation start
  const skipped = await cache.sync();
  assert.deepEqual(skipped, { changed: false, skipped: true, tree: null, rootId: "" });
  release();
  await mutation;
});

test("a mutation only invalidates the identity that was written", async () => {
  const x = setup();
  const cache = x.make();
  await cache.get(false);
  const id = x.data[Cache.KEY].identity;
  await cache.mutate(async () => {});
  assert.equal(x.localData[Cache.LKEY][id], undefined, "the written identity is invalidated");
  assert.equal(x.data[Cache.KEY], undefined);
  assert.deepEqual(x.localData[Cache.FKEY], {}, "the freshness stamp is invalidated too");
});
