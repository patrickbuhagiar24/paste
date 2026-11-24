"use strict";
const assert = require("node:assert/strict");
const { webcrypto } = require("node:crypto");
const Cache = require("../cache.js");
const Library = require("../library.js");

function setup() {
  const data = {};
  const localData = {};
  let calls = 0;
  const config = { drive: { folderId: "root", indexId: "" }, oauth: { client_id: "123-abc.apps.googleusercontent.com" } };
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

test("the cache is read without Drive and survives a worker restart, keyed by account and folder", async () => {
  const x = setup();
  const a = x.make();
  // The first read of a configuration is its one full sync.
  const first = await a.get(false);
  assert.equal(first.cached, false);
  // Every ordinary read is served from the cache.
  assert.equal((await a.get(false)).cached, true);
  assert.equal(x.calls, 1, "reading the cache never loads Drive");
  // Sync is the one explicit request for a fresh read, so it must reach Drive
  // and answer from it rather than from the snapshot.
  assert.equal((await a.get(true)).cached, false);
  assert.equal(x.calls, 2, "Sync reads Drive again");
  assert.equal((await a.get(false)).cached, true);
  const b = x.make();
  // The Sync above rewrote the snapshot from the second read.
  assert.equal((await b.get(false)).tree[0].id, "2");
  assert.equal(x.calls, 2, "a new worker reads the stored snapshot");
  assert.ok(!JSON.stringify(x.data).includes("123-abc"), "the stored snapshot carries no client id");
  x.config.drive.folderId = "other";
  // A different library has no stored snapshot: its first read syncs once,
  // and then it is cached like the others.
  assert.equal((await b.get(false)).tree[0].id, "3");
  assert.equal((await b.get(false)).tree[0].id, "3");
  x.config.oauth.client_id = "456-def.apps.googleusercontent.com";
  assert.equal((await b.get(false)).tree[0].id, "4");
  assert.equal((await b.get(false)).tree[0].id, "4");
  x.config.oauth = null;
  await assert.rejects(b.get(false), /pastex-storage\.json/);
  assert.equal(x.data[Cache.KEY], undefined);
});

test("partial or failed refresh does not overwrite an existing complete cache", async () => {
  const x = setup();
  const cache = x.make();
  await cache.get(false);
  x.library.load = async () => ({ tree: [{ id: "broken", loadError: "403" }], session: { rootId: "root" }, warnings: ["403"] });
  // A background check is the only path that reads Drive again.
  assert.equal((await cache.sync()).skipped, true);
  assert.equal((await cache.get(false)).tree[0].id, "1", "a partial listing never replaces the stored one");
  x.library.load = async () => { throw new Error("offline"); };
  await assert.rejects(cache.sync(), /offline/);
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

test("simultaneous misses share one load, and a background check reads again", async () => {
  const x = setup();
  let release;
  let gate = true;
  const initial = x.library.load;
  x.library.load = async (...args) => { if (gate) await new Promise((resolve) => { release = resolve; }); return initial(...args); };
  const cache = x.make();
  const first = cache.get(false), second = cache.get(false);
  while (!release) await new Promise(setImmediate);
  release();
  assert.equal((await first).tree[0].id, "1");
  assert.equal((await second).tree[0].id, "1");
  assert.equal(x.calls, 1, "two opens share the one sync");
  gate = false;
  // The background check is what reads Drive afterwards: a changed library
  // replaces the snapshot, an unchanged one does not touch it.
  await cache.sync();
  assert.equal(x.calls, 2);
  assert.equal((await cache.get(false)).tree[0].id, "2", "the check's result is served from the cache");
});

test("session storage quota failure falls back to memory without retaining old stored data", async () => {
  const x = setup();
  const cache = x.make();
  await cache.get(false);
  x.storage.set = async () => { throw new Error("quota"); };
  // The background check writes the snapshot; when storage refuses it, the
  // session copy still serves the new list and the stale blob is gone.
  await cache.sync();
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
  await assert.rejects(cache.sync(), /403/);
  assert.equal(x.data[Cache.KEY], undefined, "a revoked item is not served again");
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

test("a local snapshot survives a worker restart without reading Drive", async () => {
  const x = setup();
  const first = x.make();
  await first.get(false);
  assert.ok(x.localData[Cache.LKEY], "a successful load must persist a derived local snapshot");
  const id = x.data[Cache.KEY].identity;
  const second = x.make();
  await x.storage.remove(Cache.KEY); // simulate a fresh worker process
  // The last Drive confirmation is older than the freshness window.
  x.localData[Cache.FKEY][id].savedAt = 0;
  const result = await second.get(false);
  assert.equal(result.cached, true);
  assert.equal(result.tree[0].id, "1");
  assert.equal(x.calls, 1, "opening from the local cache must not read Drive");
  await new Promise((resolve) => setTimeout(resolve, 350));
  assert.equal(x.calls, 1, "an open never schedules a Drive refresh in the background");
  // The check that corrects a stale snapshot is the idle one.
  await second.sync();
  assert.equal(x.calls, 2, "the background check is what reads Drive again");
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
  // Order and collapse live in the root label, so the signature sees them
  // through the rows the library returns, not through per-item properties.
  const state = {
    tree: [
      { id: "folder", kind: "folder", name: "Folder", collapsed: false,
        children: [{ id: "a", kind: "text", name: "A", driveName: "A.txt", text: "a",
          modifiedTime: "t1" }] },
      { id: "b", kind: "text", name: "B", driveName: "B.txt", text: "b", modifiedTime: "t2" },
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

  // A reordered item: the library returns the rows in the index's order.
  state.tree = [state.tree[1], state.tree[0]];
  assert.equal((await cache.sync()).changed, true, "a new order must be detected");
  assert.deepEqual((await cache.get(false)).tree.map((node) => node.id), ["b", "folder"]);

  // A collapsed folder: the index folds it. The tree is re-read from the
  // snapshot, so the folder is the node the cache hands back.
  const folderAt = (tree) => tree.find((node) => node.id === "folder");
  folderAt(state.tree).collapsed = true;
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
  assert.equal(skipped.changed, false);
  assert.equal(skipped.skipped, true);
  assert.equal(skipped.tree, null);
  assert.equal(skipped.rootId, "");
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

test("the snapshot stores bodies by content hash with their signatures and index", async () => {
  const x = setup();
  x.library.load = async () => ({
    tree: [{ id: "item", kind: "text", name: "Item", text: "contents", hash: "abc" }],
    session: { rootId: "root" }, warnings: [],
    bodies: { item: { modifiedTime: "v1", hash: "abc", driveHash: "def" } },
    texts: { abc: "contents" },
    metadata: { root: "m1" },
    signatures: { item: "sig" },
    index: { order: { root: ["item"] }, collapsed: {} },
    cursors: { root: "cursor" },
  });
  const cache = x.make();
  await cache.get(false);
  const snapshot = x.data[Cache.KEY];
  assert.equal(snapshot.bodies.item.hash, "abc", "a body is addressed by its content hash");
  assert.equal(snapshot.texts.abc, "contents");
  assert.equal(snapshot.signatures.item, "sig");
  assert.deepEqual(snapshot.index, { order: { root: ["item"] }, collapsed: {} });
  assert.equal(snapshot.cursors.root, "cursor");
});

test("the cache reports when the library was last checked", async () => {
  const x = setup();
  const cache = x.make();
  const first = await cache.get(false);
  assert.ok(first.lastCheckedAt > 0, "a load reports the moment of the check");
  const second = await cache.get(false);
  assert.equal(second.lastCheckedAt, first.lastCheckedAt, "a cached read keeps the confirmation time");
  const third = await cache.get(true);
  assert.ok(third.lastCheckedAt >= first.lastCheckedAt, "a fresh load moves the confirmation time");
});

test("a changed node signature refreshes the snapshot without disturbing the panel", async () => {
  const x = setup();
  let signature = "one";
  x.library.load = async () => ({
    tree: [{ id: "item", kind: "text", name: "Item", text: "contents", hash: "abc" }],
    session: { rootId: "root" }, warnings: [],
    bodies: { item: { modifiedTime: "v1", hash: "abc", driveHash: "def" } },
    texts: { abc: "contents" },
    metadata: { root: "m1" },
    signatures: { item: signature },
    index: { order: {}, collapsed: {} },
  });
  const cache = x.make();
  await cache.get(false);
  assert.equal((await cache.sync()).changed, false);
  signature = "two";
  const result = await cache.sync();
  assert.equal(result.changed, false, "the visible tree did not change");
  assert.equal(x.data[Cache.KEY].signatures.item, "two", "the new signature is stored");
});

// --- the stored snapshot is the whole library ------------------------------

function nestedWorld() {
  const data = {}, localData = {};
  const config = { drive: { folderId: "root", indexId: "" }, oauth: { client_id: "123-abc.apps.googleusercontent.com" } };
  const area = (store) => ({
    async get(key) { return { [key]: store[key] }; },
    async set(values) { Object.assign(store, structuredClone(values)); },
    async remove(key) { delete store[key]; },
  });
  const sessionArea = area(data), localArea = area(localData);
  let downloads = 0, listings = 0;
  const drive = {
    FOLDER_MIME: "application/vnd.google-apps.folder", TEXT_MIME: "text/plain",
    getAuthToken: async () => "token",
    getFolder: async () => ({ mimeType: "application/vnd.google-apps.folder", id: "root" }),
    // Order and collapse live in the index file, not on the folder.
    readIndexFile: async (token, indexId) => ({ id: indexId || "", index: null }),
    listChildren: async (token, parent) => {
      listings++;
      return parent === "root"
        ? [{ id: "f1", name: "F", mimeType: "application/vnd.google-apps.folder", properties: { pastex_collapsed: "1" } }]
        : [{ id: "t2", name: "Two.txt", mimeType: "text/plain", modifiedTime: "m2" }];
    },
    downloadText: async (token, id) => { downloads++; return "body of " + id; },
  };
  const native = { readConfig: async () => structuredClone(config) };
  const make = () => Cache.create(native, drive, Library, sessionArea, webcrypto, localArea);
  return { data, localData, sessionArea, localArea, make,
    get downloads() { return downloads; }, get listings() { return listings; } };
}

test("a cold load stores every body, including collapsed folders", async () => {
  const w = nestedWorld();
  const cache = w.make();
  const first = await cache.get(false);
  assert.equal(first.pending, 0);
  const snapshot = w.data[Cache.KEY];
  assert.ok(Object.values(snapshot.texts).includes("body of t2"),
    "a collapsed folder is cached instead of deferred");
  assert.equal(w.downloads, 1);
});

test("a cached open serves every row without a Drive request", async () => {
  const w = nestedWorld();
  await w.make().get(false);
  const downloads = w.downloads, listings = w.listings;
  const reopened = await w.make().get(false);
  assert.equal(reopened.cached, true);
  assert.equal(reopened.pending, 0);
  assert.equal(reopened.tree[0].children[0].text, "body of t2");
  assert.equal(w.downloads, downloads, "opening from the cache downloads nothing");
  assert.equal(w.listings, listings, "opening from the cache lists nothing");
});

test("a metadata-only verify confirms an unchanged library without a body download", async () => {
  const w = nestedWorld();
  const cache = w.make();
  await cache.get(false);
  const downloads = w.downloads;
  const result = await cache.verify();
  assert.equal(result.checked, true);
  assert.equal(result.changed, false);
  assert.equal(w.downloads, downloads, "verification must not download a body");
});

test("a snapshot missing a body reports it, and the fill pass restores it", async () => {
  const w = nestedWorld();
  await w.make().get(false);
  delete w.data[Cache.KEY];
  const key = Object.keys(w.localData[Cache.LKEY])[0];
  const stored = (await w.localArea.get(Cache.LKEY))[Cache.LKEY][key];
  // Simulate a degraded write: the row survives, its body does not.
  stored.texts = {};
  delete stored.tree[0].children[0].text;
  delete stored.tree[0].children[0].hash;
  await w.localArea.set({ [Cache.LKEY]: { [key]: stored } });
  const cache = w.make();
  const cached = await cache.get(false);
  assert.equal(cached.pending, 1, "the panel is told which rows are missing a body");
  const filled = await cache.fill();
  assert.equal(filled.filled, 1);
  assert.equal(filled.pending, 0);
  assert.equal((await w.localArea.get(Cache.LKEY))[Cache.LKEY][key].texts[
    Object.keys((await w.localArea.get(Cache.LKEY))[Cache.LKEY][key].texts)[0]], "body of t2");
});

test("a complete snapshot is not replaced by a degraded one when the quota is tight", async () => {
  const w = nestedWorld();
  const cache = w.make();
  await cache.get(false);
  // The next write cannot fit: the writer must keep a usable snapshot.
  const original = w.localArea.set;
  let writes = 0;
  w.localArea.set = async (values) => {
    writes++;
    if (writes === 1) throw new Error("QUOTA_BYTES quota exceeded");
    return original(values);
  };
  await cache.get(true);
  const stored = (await w.localArea.get(Cache.LKEY))[Cache.LKEY];
  assert.ok(stored, "a failed complete write still leaves a snapshot");
  const rows = [].concat(...Object.values(stored).map((snapshot) =>
    (snapshot.tree || []).flatMap((node) => node.children || [])));
  assert.ok(rows.every((node) => node.kind !== "text" || node.hash || node.text),
    "the degraded snapshot still describes every row");
});

test("a tight quota drops other libraries before it drops this one's bodies", async () => {
  const w = nestedWorld();
  const cache = w.make();
  await cache.get(false);
  const key = Object.keys(w.localData[Cache.LKEY])[0];
  // Another account's snapshot sits in the same store and makes every write
  // too large. It is not on screen, so it goes first.
  w.localData[Cache.LKEY].other = { identity: "other", tree: [], savedAt: 1, padding: "x".repeat(400) };
  const original = w.localArea.set;
  w.localArea.set = async (values) => {
    const all = values[Cache.LKEY] || {};
    if (Object.keys(all).length > 1) throw new Error("QUOTA_BYTES quota exceeded");
    return original(values);
  };
  await cache.get(true);
  const stored = (await w.localArea.get(Cache.LKEY))[Cache.LKEY];
  assert.deepEqual(Object.keys(stored), [key], "the library on screen keeps its slot");
  assert.ok(Object.values(stored[key].texts).includes("body of t2"),
    "the bodies of the library on screen survive");
});

test("bodies are given up only when nothing else is left, and the rows stay readable", async () => {
  const w = nestedWorld();
  const cache = w.make();
  await cache.get(false);
  const key = Object.keys(w.localData[Cache.LKEY])[0];
  const original = w.localArea.set;
  w.localArea.set = async (values) => {
    const all = values[Cache.LKEY] || {};
    const snap = all[key];
    // Anything carrying the inline copy of a row's text is too large.
    const heavy = snap && (snap.tree || []).some((node) =>
      (node.children || []).some((child) => typeof child.text === "string" && child.text));
    if (heavy) throw new Error("QUOTA_BYTES quota exceeded");
    return original(values);
  };
  await cache.get(true);
  const stored = (await w.localArea.get(Cache.LKEY))[Cache.LKEY][key];
  const row = stored.tree[0].children[0];
  assert.equal(row.kind, "text", "every row still describes itself");
  assert.ok(row.hash, "the row points at a body instead of carrying it");
  assert.ok(stored.texts[row.hash], "the body itself is still stored once");
});

test("when nothing fits, the library stays in session memory", async () => {
  const w = nestedWorld();
  const cache = w.make();
  w.localArea.set = async () => { throw new Error("QUOTA_BYTES quota exceeded"); };
  const first = await cache.get(false);
  assert.equal(first.pending, 0, "the session copy is complete");
  const afterFirst = w.listings;
  const second = await cache.get(false);
  assert.equal(second.tree[0].children[0].id, "t2", "reading still works");
  assert.equal(w.listings, afterFirst, "a failed write forces no extra Drive read");
});
