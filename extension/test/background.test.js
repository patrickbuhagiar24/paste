// Simulate Chrome's lifecycle and the selected-folder configuration reader.
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");

const ext = path.resolve(__dirname, "..");

// Let timers and pending promises settle.
function settle(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms || 30));
}


function harness(home, unavailable, shared, options) {
  const opts = options || {};
  const listeners = {};
  const closed = [];
  const event = (name) => ({ addListener(fn) { listeners[name] = fn; } });
  let launches = 0;
  let registered = [];
  let injected = 0;
  const opened = [];
  const behaviors = [];
  let idleState = "idle";
  const listenersForApis = {};
  const alarmCalls = [];
  const alarms = {
    create(name, options) { alarmCalls.push({ name, options }); },
    onAlarm: { addListener(fn) { listenersForApis.alarm = fn; } },
  };
  const idleApi = {
    setDetectionInterval() {},
    queryState(seconds, respond) { respond(idleState); },
    onStateChanged: { addListener(fn) { listenersForApis.idle = fn; } },
  };
  const runtime = {
    id: "abc", getURL: (name) => "chrome-extension://abc/" + name,
    onInstalled: event("installed"), onStartup: event("startup"), onMessage: event("message"), onConnect: event("connect"),
    // The worker no longer asks an offscreen document for configuration; it
    // reads the cache the panel wrote. Any such request must fail loudly.
    sendMessage: async (message) => {
      if (message && message.target === "pastex-config-reader") {
        throw new Error("the offscreen configuration reader must not be used");
      }
      launches++;
      return { ok: true };
    },
  };
  const sessionData = {};
  const localData = shared && shared.local ? shared.local : {};
  if (shared) shared.local = localData;
  // The panel writes this record when it scans the chosen folder.
  if (!unavailable) {
    localData["pastex-config-cache"] = {
      version: 1, savedAt: "2026-01-01T00:00:00.000Z", folder: "chosen-folder",
      files: {
        "pastex-google-drive.json": { folder_id: "root" },
        "pastex-sites.json": opts.withoutSites ? {} : { allowed_sites: ["https://allowed.example"] },
        "service-account.json": { type: "service_account", client_email: "x",
          private_key: "-----BEGIN PRIVATE KEY-----\nk\n-----END PRIVATE KEY-----\n" },
      },
    };
  }
  function area(store) {
    return {
      get: async (key) => ({ [key]: store[key] }),
      set: async (values) => { Object.assign(store, structuredClone(values)); },
      remove: async (key) => { delete store[key]; },
    };
  }
  const context = vm.createContext({
    chrome: {
      runtime, offscreen: { hasDocument: async () => true, createDocument: async () => {} },
      storage: { session: area(sessionData), local: area(localData) },
      scripting: {
        getRegisteredContentScripts: async () => registered,
        unregisterContentScripts: async () => { registered = []; },
        registerContentScripts: async (scripts) => { registered = scripts; },
        executeScript: async () => { injected++; }, insertCSS: async () => {},
      },
      tabs: { get: async (id) => ({ id, url: id === 1 ? "https://allowed.example/path" : "https://blocked.example/" }),
        query: async () => [{ id: 1, url: "https://allowed.example/path" }],
        sendMessage: async () => { throw new Error("no receiver"); } },
      alarms, idle: idleApi,
      action: { onClicked: event("action") },
      sidePanel: {
        open: (options) => { opened.push(options); return Promise.resolve(); },
        close: (options) => { closed.push(options); return Promise.resolve(); },
        setPanelBehavior: (options) => { behaviors.push(options); return Promise.resolve(); },
      },
    },
    PastexStore: { loadTree: async () => [], loadRecent: async () => [], findById: () => null },
    crypto: require("node:crypto").webcrypto, TextEncoder, setTimeout, clearTimeout, URL,
    // The Drive helper decodes a base64 key. A vm context has no atob/btoa, so
    // a real load inside the harness failed for a reason that has nothing to do
    // with the code under test.
    atob: (data) => Buffer.from(String(data), "base64").toString("binary"),
    btoa: (data) => Buffer.from(String(data), "binary").toString("base64"),
    // The worker talks HTTP through fetch; the stubs replace the calls that
    // would use it, but the symbol must exist.
    fetch: async () => { throw new Error("the network is stubbed in this test"); },
    AbortController, AbortSignal, TextDecoder,
    // Warnings are visible: a queued write that is kept for retry must not
    // disappear silently, which is exactly how a flush bug hides.
    console: { warn: (...args) => { if (process.env.PASTEX_TEST_WARN) console.warn(...args); }, log() {}, error() {} },
  });
  context.self = context;
  context.importScripts = (...names) => {
    for (const name of names) {
      if (name !== "store.js") vm.runInContext(fs.readFileSync(path.join(ext, name), "utf8"), context);
    }
  };
  vm.runInContext(fs.readFileSync(path.join(ext, "background.js"), "utf8"), context);
  return { listeners, context, listenersForApis, opened, closed, behaviors, alarmCalls,
    set idle(value) { idleState = value; },
    get launches() { return launches; },
    get registered() { return registered; }, get injected() { return injected; },
    sharedLocal: localData };
}

test("extension startup never creates a configuration directory", async () => {
  for (const eventName of ["installed", "startup"]) {
    const home = fs.mkdtempSync(path.join(os.tmpdir(), "pastex-browser-"));
    try {
      const browser = harness(home, false);
      browser.listeners[eventName]();
      await new Promise(setImmediate);
      // Configuration is read from storage now: startup must not message any
      // other context and must not touch the filesystem.
      assert.equal(browser.launches, 0);
      assert.deepEqual(fs.readdirSync(home), []);
      // The cached configuration is usable immediately, so the allowlisted
      // site is registered without any folder access.
      assert.equal(browser.registered.length, 1);
      assert.deepEqual(browser.registered[0].matches, ["https://allowed.example/*"]);
    } finally { fs.rmSync(home, { recursive: true, force: true }); }
  }
});

test("site registration and on-demand injection are limited to configured sites", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "pastex-allow-"));
  try {
    const browser = harness(home, false, undefined, { withoutSites: true });
    browser.listeners.installed();
    await new Promise(setImmediate);
    assert.equal(browser.registered.length, 0, "no allowlist means no registration");
    // Configuration lives in storage: replacing the cached allowlist is what a
    // panel rescan does, and no folder or file is involved.
    const config = browser.sharedLocal["pastex-config-cache"];
    config.files["pastex-sites.json"] = { allowed_sites: ["https://allowed.example"] };
    browser.listeners.startup();
    await new Promise(setImmediate);
    assert.deepEqual(Array.from(browser.registered[0].matches), ["https://allowed.example/*"]);
    const allowedMessage = (url) => new Promise((resolve) => {
      assert.equal(browser.listeners.message({ type: "pastex-site-allowed" }, { id: "abc", tab: { id: 1, windowId: 8, url }, frameId: 0, url }, resolve), true);
    });
    assert.equal((await allowedMessage("https://allowed.example/path")).allowed, true);
    assert.equal((await allowedMessage("https://blocked.example/")).allowed, false);
    const sender = { id: "abc", tab: { id: 1, windowId: 8, url: "https://allowed.example/path" }, frameId: 0, url: "https://allowed.example/path" };
    const authorization = await new Promise((resolve) => browser.listeners.message({ type: "pastex-site-allowed" }, sender, resolve));
    let response;
    const event = browser.listeners.message({ type: "pastex-open-panel", capability: authorization.capability }, sender, (r) => { response = r; });
    assert.equal(event, true);
    assert.equal(browser.opened.length, 1, "side panel opened synchronously during gesture");
    await new Promise(setImmediate);
    assert.equal(response.ok, true);
    let denied;
    browser.listeners.message({ type: "pastex-open-panel", capability: authorization.capability }, { ...sender, url: "https://blocked.example/" }, (r) => { denied = r; });
    assert.equal(denied.ok, false);
    assert.equal(browser.opened.length, 1);
    await assert.rejects(browser.context.messageTab(2, { type: "pastex-insert" }), /site-not-allowed/);
    assert.equal(browser.injected, 0);
    await assert.rejects(browser.context.messageTab(1, { type: "pastex-insert" }), /no receiver/);
    assert.equal(browser.injected, 1);
    config.files["pastex-sites.json"] = { allowed_sites: ["<all_urls>"] };
    browser.listeners.startup();
    await new Promise(setImmediate);
    assert.equal(browser.registered.length, 0, "an invalid allowlist fails closed");
    assert.equal((await allowedMessage("https://allowed.example/")).allowed, false);
    await assert.rejects(browser.context.messageTab(1, { type: "pastex-insert" }), /site-not-allowed/);
  } finally { fs.rmSync(home, { recursive: true, force: true }); }
});

test("an unavailable configuration does not prevent extension startup", async () => {
  const browser = harness(os.tmpdir(), true);
  browser.listeners.installed();
  await new Promise(setImmediate);
  // A missing cache must not throw, and must not fall back to a folder read.
  assert.equal(browser.launches, 0);
});

test("only the extension panel can request a site registration reload", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "pastex-reload-"));
  try {
    const browser = harness(home, false, undefined, { withoutSites: true });
    browser.listeners.installed();
    await new Promise(setImmediate);
    assert.equal(browser.registered.length, 0, "nothing registered without an allowlist");
    const cached = browser.sharedLocal["pastex-config-cache"];
    cached.files["pastex-sites.json"] = { allowed_sites: ["https://allowed.example"] };
    let denied;
    assert.equal(browser.listeners.message({ type: "pastex-reload-sites" }, { id: "abc", url: "https://allowed.example" }, (res) => { denied = res; }), false);
    assert.equal(denied.ok, false);
    assert.equal(browser.registered.length, 0);
    const response = await new Promise((resolve) => {
      assert.equal(browser.listeners.message({ type: "pastex-reload-sites" }, { id: "abc", url: "chrome-extension://abc/sidepanel.html" }, resolve), true);
    });
    assert.equal(response.ok, true);
    assert.deepEqual(Array.from(browser.registered[0].matches), ["https://allowed.example/*"]);
  } finally { fs.rmSync(home, { recursive: true, force: true }); }
});

test("panel loads are cached and an edit changes the cache without reading Drive", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "pastex-cache-worker-"));
  try {
    const browser = harness(home, false);
    browser.listeners.installed(); await new Promise(setImmediate);
    fs.mkdirSync(path.join(home, "chosen-folder"), { recursive: true });
    fs.writeFileSync(path.join(home, "chosen-folder", "pastex-sites.json"), JSON.stringify({ allowed_sites: ["https://allowed.example"] }));
    browser.context.PastexConfig.readSites = async () => ({ allowedSites: ["https://allowed.example"], source: "linked" });
    browser.context.PastexConfig.readConfig = async () => ({ drive: { folderId: "root" }, serviceAccount: { client_email: "x", private_key: "k" } });
    let text = "old", loads = 0;
    browser.context.PastexLibrary.load = async () => ({
      tree: [{ id: "item", kind: "text", name: "Item", driveName: "Item.txt", text: text }],
      session: { rootId: "root" }, warnings: [], bodies: {}, metadata: {}, signatures: {}, index: { order: {}, collapsed: {} }, sequence: ++loads,
    });
    browser.context.PastexLibrary.connect = async () => ({ token: "token", rootId: "root", index: { order: {}, collapsed: {} }, properties: {} });
    browser.context.PastexDrive.fetchAccessToken = async () => "token";
    browser.context.PastexDrive.getFolder = async () => ({ mimeType: browser.context.PastexDrive.FOLDER_MIME, properties: { pastex_index: "" } });
    let driveWrites = 0;
    browser.context.PastexDrive.updateText = async (token, id, value) => { driveWrites++; text = value; };
    browser.context.PastexDrive.listChildren = async () => [];
    const panel = { id: "abc", url: "chrome-extension://abc/sidepanel.html" };
    const page = { id: "abc", tab: { id: 1 }, url: "https://allowed.example/" };
    function send(message, sender) { return new Promise((resolve) => browser.listeners.message(message, sender, resolve)); }
    assert.equal((await send({ type: "pastex-panel-library" }, panel)).result.tree[0].text, "old");
    assert.equal((await send({ type: "pastex-panel-library" }, panel)).result.tree[0].text, "old");
    assert.equal(loads, 1, "the second open is served from the cache");
    // The old immediate-write route is gone: it is not even recognised any
    // more, so it answers nothing and writes nothing.
    assert.equal(browser.listeners.message({ type: "pastex-panel-mutate", command: {} }, panel, () => {
      throw new Error("the removed route must not answer");
    }), false);
    await settle();
    assert.equal(driveWrites, 0);
    // An edit is cached at once...
    const edit = await send({ type: "pastex-panel-local-edit", command: {
      kind: "edit", name: "Item", text: "new", parentId: "root",
      item: { id: "item", kind: "text", driveName: "Item.txt" },
    } }, panel);
    assert.equal(edit.ok, true);
    assert.equal((await send({ type: "pastex-panel-library" }, panel)).result.tree[0].text, "new");
    assert.equal(loads, 1, "reading the cache back does not touch Drive");
    assert.equal(driveWrites, 0, "and an edit on a site never writes to Drive");
    // ...and sent when the tab is left.
    const site = { id: "abc", tab: { id: 1 }, frameId: 0, url: "https://allowed.example/" };
    await new Promise((resolve) => browser.listeners.message({ type: "pastex-flush-now" }, site, resolve));
    await settle();
    const after = await send({ type: "pastex-panel-status" }, panel);
    assert.equal(after.result.pending, 0, "the queue drained: " + JSON.stringify(after.result));
    assert.equal(driveWrites, 1);
    assert.equal(text, "new");
  } finally { fs.rmSync(home, { recursive: true, force: true }); }
});

// An opaque id shaped like the ones Drive hands out; a short key would look
// like a local row to the queue.
function driveId(key) { return "1" + key.repeat(20) + "AbCdEf"; }

// A queue-backed write scenario: the panel edits, the site is left, and the
// flush is the only thing that reaches Drive.
function queueScenario(stub) {
  const browser = harness(os.tmpdir(), true);
  const panel = { id: "abc", url: "chrome-extension://abc/sidepanel.html" };
  const site = { id: "abc", tab: { id: 1 }, frameId: 0, url: "https://allowed.example/" };
  browser.context.PastexConfig.readSites = async () => ({ allowedSites: ["https://allowed.example"], source: "linked" });
  browser.context.PastexConfig.readConfig = async () => ({ drive: { folderId: "root" }, serviceAccount: { client_email: "x", private_key: "k" } });
  let stored = { order: { root: [] }, collapsed: { folder: "1" } };
  // connect() reads the folder, so it must hand back the label the previous
  // write stored; returning a snapshot from before the writes would lose it.
  browser.context.PastexLibrary.connect = async () => ({
    token: "token", rootId: "root", properties: { keep: "1" }, index: structuredClone(stored),
  });
  // A real tree: a reorder or a collapse applies to the cached rows, so the
  // fixture must have the rows the test reorders. The ids are opaque, like the
  // ones Drive hands out; a short key would look like a local id.
  const rows = ["a", "b", "c", "d", "e", "f", "g"].map((key) => ({
    id: driveId(key), kind: "text", name: key.toUpperCase(),
    driveName: key.toUpperCase() + ".txt", text: key,
  }));
  browser.context.PastexLibrary.load = async () => ({
    tree: structuredClone(rows), session: { rootId: "root" }, warnings: [], bodies: {}, metadata: {}, signatures: {}, index: stored,
  });
  const drive = browser.context.PastexDrive;
  drive.fetchAccessToken = async () => "token";
  drive.getFolder = async () => ({ mimeType: drive.FOLDER_MIME });
  drive.listChildren = async () => [];
  drive.readIndexFile = async () => ({ id: "", index: null });
  drive.writeIndexFile = async (t, root, index) => { stored = index; };
  stub(drive);
  const send = (message) => new Promise((resolve) => browser.listeners.message(message, panel, resolve));
  const leave = () => new Promise((resolve) => browser.listeners.message({ type: "pastex-flush-now" }, site, resolve));
  return { browser, send, leave, stored: () => stored };
}

test("a flush reuses one session instead of reconnecting per entry", async () => {
  let connects = 0;
  const { browser, send, leave } = queueScenario((drive) => {
    drive.createFolder = async () => "f";
    drive.uploadNew = async (t, parent, name, text) => ({ id: "n" + name });
  });
  const realConnect = browser.context.PastexLibrary.connect;
  browser.context.PastexLibrary.connect = async (...args) => { connects++; return realConnect(...args); };
  await send({ type: "pastex-panel-library" });
  for (const name of ["One", "Two", "Three"]) {
    await send({ type: "pastex-panel-local-edit", command: {
      kind: "create", name, text: "x", parentId: "root",
      item: { kind: "text", parentId: "root", localId: "" },
    } });
  }
  connects = 0;
  await leave();
  await settle(40);
  assert.equal(connects, 1, "three queued writes must cost one token and one folder read");
});

test("the queue writes creates, renames, the index and bulk trash to Drive", async () => {
  const writes = [];
  const { send, leave } = queueScenario((drive) => {
    drive.createFolder = async (t, name, parent) => { writes.push(["folder", name, parent]); return "f"; };
    drive.uploadNew = async (t, parent, name, text) => { writes.push(["new", parent, name, text]); return { id: "n" }; };
    drive.renameFile = async (t, id, name) => { writes.push(["rename", id, name]); };
    drive.trashFile = async (t, id) => { writes.push(["trash", id]); };
    drive.setIndex = async (t, root, index, impl, known) => { writes.push(["index", root, index, known]); };
  });
  await send({ type: "pastex-panel-library" });
  const edit = (command) => send({ type: "pastex-panel-local-edit", command });
  assert.equal((await edit({ kind: "create", name: "Folder", parentId: "root",
    item: { kind: "folder", parentId: "root", localId: "" } })).ok, true);
  assert.equal((await edit({ kind: "create", name: "Note", text: "text", parentId: "root",
    item: { kind: "text", parentId: "root", localId: "" } })).ok, true);
  assert.equal(writes.length, 0, "nothing is written while the panel is used on a site");
  await leave();
  await settle();
  assert.deepEqual(writes.slice(0, 2), [["folder", "Folder", "root"], ["new", "root", "Note", "text"]]);
});

test("a reorder and a fold in one flush both survive, on one connection", async () => {
  const calls = [];
  let connects = 0;
  const { browser, send, leave } = queueScenario((drive) => {
    drive.writeIndexFile = async (t, root, index) => { calls.push(JSON.parse(JSON.stringify(index))); };
  });
  const realConnect = browser.context.PastexLibrary.connect;
  browser.context.PastexLibrary.connect = async (...args) => { connects++; return realConnect(...args); };
  await send({ type: "pastex-panel-library" });
  const ids = ["a", "b", "c", "d", "e", "f", "g"].map(driveId);
  await send({ type: "pastex-panel-local-edit", command: { kind: "reorder", groups: [ids], parents: ["root"] } });
  await send({ type: "pastex-panel-local-edit", command: { kind: "collapse", ids: [ids[0]], collapsed: true } });
  connects = 0;
  await leave();
  await settle(40);
  assert.equal(connects, 1, "both index writes share one connection");
  assert.equal(calls.length, 2, "calls=" + JSON.stringify(calls) + " queue=" + JSON.stringify(await browser.context.deferredQueue.all()));
  // The second write must build on the first: the order set by the reorder is
  // still there, and the fold is added next to it.
  assert.deepEqual(calls[1].order.root, ids, "the fold keeps the reorder that came before it: " + JSON.stringify(calls));
  assert.equal(calls[1].collapsed[ids[0]], "1", "and it records the fold");
});

test("a reorder and a collapse each become one index write, and the stored order survives", async () => {
  const calls = [];
  // The label is read back from the folder before each write, so the stub must
  // both record the write and store it, exactly like Drive does.
  const { browser, send, leave } = queueScenario((drive) => {
    drive.writeIndexFile = async (t, root, index) => { calls.push([root, index]); drive.__stored = index; };
  });
  // The folder already carries an unrelated folded folder, which must survive.
  browser.context.PastexDrive.__stored = { order: { root: [] }, collapsed: { folder: "1" } };
  browser.context.PastexLibrary.connect = async () => ({
    token: "token", rootId: "root", properties: { keep: "1" }, index: structuredClone(browser.context.PastexDrive.__stored),
  });
  await send({ type: "pastex-panel-library" });
  const edit = (command) => send({ type: "pastex-panel-local-edit", command });
  const ids = ["a", "b", "c", "d", "e", "f", "g"].map(driveId);
  const reorder = await edit({ kind: "reorder", groups: [ids], parents: ["root"] });
  assert.equal(reorder.ok, true, "the edit was accepted: " + JSON.stringify(reorder));
  await leave();
  await settle();
  const status = await send({ type: "pastex-panel-status" });
  assert.equal(calls.length, 1, "a reorder is a single Drive write");
  assert.equal(status.result.pending, 0, "and it left the queue");
  assert.deepEqual(JSON.parse(JSON.stringify(calls[0][1].order.root)), ids);
  calls.length = 0;
  await edit({ kind: "collapse", ids, collapsed: true });
  await leave();
  await settle();
  assert.equal(calls.length, 1, "a collapse is a single Drive write");
  const folded = Object.assign({ folder: "1" }, Object.fromEntries(ids.map((id) => [id, "1"])));
  assert.deepEqual(JSON.parse(JSON.stringify(calls[0][1].collapsed)), folded);
  assert.deepEqual(JSON.parse(JSON.stringify(calls[0][1].order.root)), ids, "the stored order survives a collapse");
});

test("bulk Drive deletes run with bounded parallelism", async () => {
  let active = 0, max = 0;
  const calls = [];
  const { browser, send, leave } = queueScenario((drive) => {
    drive.trashFile = async (t, id) => {
      active++; max = Math.max(max, active);
      await new Promise(setImmediate);
      active--;
      calls.push(id);
    };
  });
  await send({ type: "pastex-panel-library" });
  const ids = ["a", "b", "c", "d", "e", "f", "g"].map(driveId);
  const del = await send({ type: "pastex-panel-local-edit", command: { kind: "delete", ids } });
  assert.equal(del.ok, true, "the delete was accepted: " + JSON.stringify(del));
  const left = await leave();
  await settle(20);
  assert.equal(max, 6, "the writes of one bulk delete are bounded");
  assert.deepEqual(calls.sort(), ids.slice().sort());
});

test("repeated paste requests reuse the cached site allowlist", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "pastex-fast-paste-"));
  try {
    const browser = harness(home, false);
    browser.listeners.startup();
    await new Promise(setImmediate);
    // Count storage reads directly: that is the only path configuration takes
    // now, so "reused" means "not read again", not "no message sent".
    let configReads = 0;
    const storage = browser.context.chrome.storage.local;
    const originalGet = storage.get;
    storage.get = async function (key) { if (key === "pastex-config-cache") configReads++; return originalGet.call(storage, key); };
    await assert.rejects(browser.context.messageTab(1, { type: "pastex-insert" }), /no receiver/);
    await assert.rejects(browser.context.messageTab(1, { type: "pastex-insert" }), /no receiver/);
    assert.equal(configReads, 0, "a repeated paste must reuse the allowlist it already has");
  } finally { fs.rmSync(home, { recursive: true, force: true }); }
});

test("the background sync runs only while the user is away", async () => {
  const browser = harness(os.tmpdir(), true);
  browser.context.PastexConfig.readConfig = async () => ({
    drive: { folderId: "root" }, serviceAccount: { client_email: "x", private_key: "k" },
  });
  let loads = 0;
  browser.context.PastexLibrary.load = async () => {
    loads++;
    return { tree: [{ id: "item", kind: "text", name: "Item", text: "text" }],
      session: { rootId: "root" }, warnings: [], bodies: {}, metadata: {} };
  };
  browser.idle = "active";
  browser.listenersForApis.alarm({ name: "pastex-idle-sync" });
  await settle();
  assert.equal(loads, 0, "an active user must not trigger a background sync");
  browser.idle = "idle";
  browser.listenersForApis.alarm({ name: "pastex-idle-sync" });
  await settle();
  assert.equal(loads, 1, "an idle machine triggers exactly one background sync");
  browser.listenersForApis.alarm({ name: "pastex-idle-sync" });
  await settle();
  assert.equal(loads, 2, "the background check repeats on the next alarm");
});

test("the background sync keeps the cache and only notifies the panel when Drive changed", async () => {
  const browser = harness(os.tmpdir(), true);
  browser.context.PastexConfig.readConfig = async () => ({
    drive: { folderId: "root" }, serviceAccount: { client_email: "x", private_key: "k" },
  });
  let tree = [{ id: "item", kind: "text", name: "Item", text: "text" }];
  browser.context.PastexLibrary.load = async () => ({
    tree, session: { rootId: "root" }, warnings: [], bodies: {}, metadata: {},
  });
  const panel = { id: "abc", url: "chrome-extension://abc/sidepanel.html" };
  // Seed the cache with the current library.
  await new Promise((resolve) => browser.listeners.message({ type: "pastex-panel-library" }, panel, resolve));
  const posted = [];
  browser.listeners.connect({
    name: "pastex-library-progress",
    sender: { id: "abc", url: "chrome-extension://abc/sidepanel.html" },
    onDisconnect: { addListener() {} }, onMessage: { addListener() {} },
    postMessage: (message) => { posted.push(message); },
  });
  browser.idle = "idle";
  browser.listenersForApis.alarm({ name: "pastex-idle-sync" });
  await settle();
  assert.equal(posted.length, 0, "an unchanged library must not disturb the panel");
  tree = [{ id: "item", kind: "text", name: "Item", text: "new text" }];
  browser.listenersForApis.alarm({ name: "pastex-idle-sync" });
  await settle();
  assert.equal(posted.length, 1, "a changed library notifies the open panel exactly once");
  assert.equal(posted[0].changed, true);
});

test("the startup warm-up never syncs while the user is active", async () => {
  const browser = harness(os.tmpdir(), true);
  browser.context.PastexConfig.readConfig = async () => ({
    drive: { folderId: "root" }, serviceAccount: { client_email: "x", private_key: "k" },
  });
  let loads = 0;
  browser.context.PastexLibrary.load = async () => {
    loads++;
    return { tree: [{ id: "item", kind: "text", name: "Item", text: "text" }],
      session: { rootId: "root" }, warnings: [], bodies: {}, metadata: {} };
  };
  browser.idle = "active";
  browser.listeners.startup();
  await settle(3200);
  assert.equal(loads, 0, "the startup warm-up must respect the idle gate");
  // The idle transition still triggers the check afterwards.
  browser.idle = "idle";
  browser.listenersForApis.idle("idle");
  await settle();
  assert.equal(loads, 1, "an idle transition triggers exactly one background sync");
});

test("a browser start with nothing queued reads nothing from Drive", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "pastex-start-quiet-"));
  try {
    const browser = harness(home, false);
    fs.mkdirSync(path.join(home, "chosen-folder"), { recursive: true });
    fs.writeFileSync(path.join(home, "chosen-folder", "pastex-sites.json"),
      JSON.stringify({ allowed_sites: ["https://allowed.example"] }));
    browser.context.PastexConfig.readSites = async () => ({ allowedSites: ["https://allowed.example"], source: "linked" });
    browser.context.PastexConfig.readConfig = async () => ({
      drive: { folderId: "root" }, serviceAccount: { client_email: "x", private_key: "k" },
    });
    const drive = browser.context.PastexDrive;
    let remote = 0;
    // Every path to Drive goes through one of these.
    drive.fetchAccessToken = async () => { remote++; return "token"; };
    drive.getFolder = async () => { remote++; return { mimeType: drive.FOLDER_MIME, properties: {} }; };
    drive.listChildren = async () => { remote++; return []; };
    let loads = 0;
    browser.context.PastexLibrary.load = async () => {
      loads++;
      return { tree: [], session: { rootId: "root" }, warnings: [], bodies: {}, metadata: {} };
    };
    // The machine is idle, so the idle gate would have allowed a check before.
    browser.idle = "idle";
    browser.listeners.startup();
    await settle(60);
    assert.equal(loads, 0, "a start with an empty queue must not read the library");
    assert.equal(remote, 0, "and it must not make any request to Drive");
    // Idle is a separate trigger and still works, on the alarm path.
    browser.listenersForApis.alarm({ name: "pastex-idle-sync" });
    await settle(20);
    assert.ok(loads + remote > 0, "an idle alarm still reaches Drive");
  } finally { fs.rmSync(home, { recursive: true, force: true }); }
});

test("a running panel operation suppresses the background sync", async () => {  const browser = harness(os.tmpdir(), true);
  browser.context.PastexConfig.readConfig = async () => ({
    drive: { folderId: "root" }, serviceAccount: { client_email: "x", private_key: "k" },
  });
  let loads = 0;
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  browser.context.PastexLibrary.load = async () => {
    loads++;
    await gate;
    return { tree: [{ id: "item", kind: "text", name: "Item", text: "text" }],
      session: { rootId: "root" }, warnings: [], bodies: {}, metadata: {} };
  };
  const panel = { id: "abc", url: "chrome-extension://abc/sidepanel.html" };
  const pending = new Promise((resolve) => browser.listeners.message({ type: "pastex-panel-library" }, panel, resolve));
  await new Promise(setImmediate);
  browser.idle = "idle";
  browser.listenersForApis.alarm({ name: "pastex-idle-sync" });
  await settle();
  assert.equal(loads, 1, "the background sync must not start during panel work");
  release();
  await pending;
});

test("the background check slows down while Drive keeps reporting the same library", async () => {
  const browser = harness(os.tmpdir(), true);
  browser.context.PastexConfig.readConfig = async () => ({
    drive: { folderId: "root" }, serviceAccount: { client_email: "x", private_key: "k" },
  });
  let text = "text";
  browser.context.PastexLibrary.load = async () => ({
    tree: [{ id: "item", kind: "text", name: "Item", text }],
    session: { rootId: "root" }, warnings: [], bodies: {}, metadata: {},
  });
  const periods = () => browser.alarmCalls
    .filter((call) => call.name === "pastex-idle-sync")
    .map((call) => call.options.periodInMinutes);
  browser.idle = "idle";
  browser.listenersForApis.alarm({ name: "pastex-idle-sync" });
  await settle();
  assert.equal(periods().pop(), 2, "the first check uses the fast interval");
  browser.listenersForApis.alarm({ name: "pastex-idle-sync" });
  await settle();
  assert.equal(periods().pop(), 2, "one check without a change keeps the interval");
  browser.listenersForApis.alarm({ name: "pastex-idle-sync" });
  await settle();
  assert.equal(periods().pop(), 4, "the interval grows after several checks without a change");
  browser.listenersForApis.alarm({ name: "pastex-idle-sync" });
  await settle();
  assert.equal(periods().pop(), 8, "the interval keeps growing while nothing changes");
  // A change brings the fast interval back.
  text = "changed";
  browser.listenersForApis.alarm({ name: "pastex-idle-sync" });
  await settle();
  assert.equal(periods().pop(), 2, "a change returns to the fast interval");
  const last = browser.alarmCalls[browser.alarmCalls.length - 1];
  assert.equal(last.options.delayInMinutes, 2, "the next check waits for the new interval");
});

test("the growing interval is kept when the worker restarts", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "pastex-backoff-"));
  const shared = {};
  try {
    const browser = harness(home, false, shared);
    browser.context.PastexConfig.readConfig = async () => ({
      drive: { folderId: "root" }, serviceAccount: { client_email: "x", private_key: "k" },
    });
    browser.context.PastexLibrary.load = async () => ({
      tree: [{ id: "item", kind: "text", name: "Item", text: "text" }],
      session: { rootId: "root" }, warnings: [], bodies: {}, metadata: {},
    });
    browser.idle = "idle";
    for (let i = 0; i < 3; i++) {
      browser.listenersForApis.alarm({ name: "pastex-idle-sync" });
      await settle();
    }
    const periods = browser.alarmCalls
      .filter((call) => call.name === "pastex-idle-sync")
      .map((call) => call.options.periodInMinutes);
    assert.equal(periods.pop(), 4, "the interval grew");
    // A new worker keeps the interval it learned.
    const restarted = harness(home, false, shared);
    await settle(50);
    const kept = restarted.alarmCalls
      .filter((call) => call.name === "pastex-idle-sync")
      .map((call) => call.options.periodInMinutes);
    assert.ok(kept.includes(4), "the stored interval is used again");
  } finally { fs.rmSync(home, { recursive: true, force: true }); }
});

// --- the in-page launcher toggles the panel -------------------------------

function panelScenario() {
  const browser = harness(os.tmpdir(), true, undefined);
  browser.context.PastexConfig.readSites = async () => ({ allowedSites: ["https://allowed.example"], source: "linked pastex-sites.json" });
  browser.context.PastexConfig.readConfig = async () => ({
    drive: { folderId: "root" }, serviceAccount: { client_email: "x", private_key: "k" },
  });
  const sender = { id: "abc", tab: { id: 1, windowId: 8, url: "https://allowed.example/path" }, frameId: 0, url: "https://allowed.example/path" };
  const ask = (message) => new Promise((resolve) => browser.listeners.message(message, sender, resolve));
  return { browser, sender, ask };
}

test("the launcher's capability opens and closes the panel", async () => {
  const { browser, ask } = panelScenario();
  await browser.listeners.startup();
  await settle();
  const authorization = await ask({ type: "pastex-site-allowed" });
  assert.equal(authorization.allowed, true);
  assert.equal(authorization.toggle, undefined, "there is no capability flag to check");
  const closed = await ask({ type: "pastex-open-panel", action: "close", capability: authorization.capability });
  assert.equal(closed.ok, true);
  assert.equal(browser.closed.length, 1, "the same button closes the panel again");
  assert.equal(browser.closed[0].windowId, 8);
});

test("closing needs the same capability as opening", async () => {
  const { browser, ask } = panelScenario();
  await browser.listeners.startup();
  await settle();
  const authorization = await ask({ type: "pastex-site-allowed" });
  const forged = await ask({ type: "pastex-open-panel", action: "close", capability: "not-the-token" });
  assert.equal(forged.ok, false);
  assert.equal(browser.closed.length, 0, "a stolen or stale token cannot close the panel");
  // A page that is not on the allowlist cannot close it either.
  const other = { id: "abc", tab: { id: 2, windowId: 8, url: "https://blocked.example/" }, frameId: 0, url: "https://blocked.example/" };
  const denied = await new Promise((resolve) => browser.listeners.message(
    { type: "pastex-open-panel", action: "close", capability: authorization.capability }, other, resolve));
  assert.equal(denied.ok, false);
});

test("the open panel tells the page to switch the launcher to close", async () => {
  const { browser, sender } = panelScenario();
  const panel = { id: "abc", url: "chrome-extension://abc/sidepanel.html" };
  const sent = [];
  browser.context.chrome.tabs.sendMessage = async (tabId, message) => { sent.push({ tabId, message }); };
  const response = await new Promise((resolve) => browser.listeners.message({ type: "pastex-panel-state", open: true }, panel, resolve));
  await settle();
  assert.equal(response.ok, true);
  assert.equal(sent[0].tabId, 1);
  assert.equal(sent[0].message.type, "pastex-panel-state");
  assert.equal(sent[0].message.open, true);
  // A content script cannot claim the panel state.
  const spoof = await new Promise((resolve) => browser.listeners.message({ type: "pastex-panel-state", open: true }, sender, resolve));
  assert.equal(spoof.ok, false);
});

// --- the toolbar toggles the panel ----------------------------------------

test("the toolbar icon toggles the side panel through the panel behavior", async () => {
  const browser = harness(os.tmpdir(), true);
  await settle();
  assert.equal(browser.behaviors.length, 1, "the worker sets the panel behavior once");
  assert.equal(browser.behaviors[0].openPanelOnActionClick, true,
    "Chrome opens the panel on click and closes it on the next click");
  // No onClicked listener may compete with that behavior.
  assert.equal(browser.listeners.action, undefined);
});

// --- edits wait for the site to be left -----------------------------------

function editScenario() {
  const browser = harness(os.tmpdir(), true);
  // The site configuration must stay readable, or the first read would try to
  // re-register content scripts and never answer the panel.
  browser.context.PastexConfig.readSites = async () => ({ allowedSites: ["https://allowed.example"], source: "linked" });
  // One stable configuration object: the cache keys a snapshot by it, and a
  // fresh object on every read would look like a different library.
  const configuration = { drive: { folderId: "root" }, serviceAccount: { client_email: "x", private_key: "k" } };
  browser.context.PastexConfig.readConfig = async () => configuration;
  // The Drive side: a listing of the library, kept in step with the writes, so
  // the sync after a flush sees what the flush just wrote.
  const library = [
    { id: "item1", kind: "text", name: "Synced", driveName: "Synced.txt", text: "from Drive" },
  ];
  browser.context.PastexLibrary.load = async () => ({
    tree: structuredClone(library), session: { rootId: "root" }, warnings: [], bodies: {},
    metadata: {}, signatures: {}, index: { order: {}, collapsed: {} },
  });
  const writes = [];
  browser.context.PastexLibrary.connect = async () => ({ token: "t", rootId: "root", index: { order: {}, collapsed: {} }, properties: {} });
  const drive = browser.context.PastexDrive;
  // A create checks for a duplicate name first, which is a listing of the
  // destination folder; it must be stubbed like every other Drive call.
  drive.listChildren = async () => [];
  drive.createFolder = async (t, name, parent) => {
    writes.push(["folder", name]);
    library.push({ id: "made-folder", kind: "folder", name: name, driveName: name, children: [] });
    return "made-folder";
  };
  drive.uploadNew = async (t, parent, name, text) => {
    writes.push(["text", name, text]);
    library.push({ id: "made-text", kind: "text", name: name, driveName: name + ".txt", text: text });
    return { id: "made-text" };
  };
  drive.updateText = async (t, id, text) => {
    writes.push(["update", id, text]);
    const node = library.find((row) => row.id === id);
    if (node) node.text = text;
  };
  drive.renameFile = async (t, id, name) => {
    writes.push(["rename", id, name]);
    const node = library.find((row) => row.id === id);
    if (node) node.name = name;
  };
  drive.trashFile = async (t, id) => {
    writes.push(["trash", id]);
    const at = library.findIndex((node) => node.id === id);
    if (at !== -1) library.splice(at, 1);
  };
  drive.writeIndexFile = async (t, root, index) => { writes.push(["index", index]); };
  const panel = { id: "abc", url: "chrome-extension://abc/sidepanel.html" };
  const send = (message) => new Promise((resolve) => browser.listeners.message(message, panel, resolve));
  return { browser, writes, send };
}

test("a create on a site is cached at once and not written to Drive", async () => {
  const { browser, writes, send } = editScenario();
  await send({ type: "pastex-panel-library" });
  const response = await send({ type: "pastex-panel-local-edit", command: {
    kind: "create", name: "Draft", text: "written here", parentId: "root",
    item: { kind: "text", name: "Draft", parentId: "root", localId: "" },
  } });
  await settle();
  assert.equal(response.ok, true);
  assert.equal(response.result.queued, true);
  assert.equal(writes.length, 0, "nothing reaches Drive while the user is on the site");
  // The cached read already shows the row.
  const library = await send({ type: "pastex-panel-library" });
  assert.ok(library.result.tree.some((node) => node.name === "Draft"));
  const status = await send({ type: "pastex-panel-status" });
  assert.equal(status.result.pending, 1);
});

test("leaving the site sends the queue and gives the row its Drive id", async () => {
  const { browser, writes, send } = editScenario();
  await send({ type: "pastex-panel-library" });
  await send({ type: "pastex-panel-local-edit", command: {
    kind: "create", name: "Draft", text: "written here", parentId: "root",
    item: { kind: "text", name: "Draft", parentId: "root", localId: "" },
  } });
  // A content script on an approved site reports that its tab was left.
  const site = { id: "abc", tab: { id: 1 }, frameId: 0, url: "https://allowed.example/path" };
  const flushed = await new Promise((resolve) => browser.listeners.message({ type: "pastex-flush-now" }, site, resolve));
  await settle();
  assert.equal(flushed.ok, true);
  assert.deepEqual(writes[0], ["text", "Draft", "written here"]);
  const status = await send({ type: "pastex-panel-status" });
  assert.equal(status.result.pending, 0, "the queue is empty after the flush");
  // The cached row now carries the Drive id the upload returned.
  const library = await send({ type: "pastex-panel-library" });
  const row = library.result.tree.find((node) => node.name === "Draft");
  assert.equal(row.id, "made-text");
});

test("an edit never schedules its own Drive write while the site is still in use", async () => {
  const { browser, writes, send } = editScenario();
  await send({ type: "pastex-panel-library" });
  await send({ type: "pastex-panel-local-edit", command: {
    kind: "create", name: "Draft", text: "here", parentId: "root",
    item: { kind: "text", parentId: "root", localId: "" },
  } });
  // Give any scheduled or immediate write a generous window to happen.
  await new Promise((resolve) => setTimeout(resolve, 4500));
  assert.equal(writes.length, 0, "no timer and no immediate write may reach Drive");
  const status = await send({ type: "pastex-panel-status" });
  assert.equal(status.result.pending, 1, "the edit is still waiting in the queue");
});

test("leaving the site hides it from the worker and sends the queue", async () => {
  const { browser, writes } = editScenario();
  const site = { id: "abc", tab: { id: 1 }, frameId: 0, url: "https://allowed.example/path" };
  await new Promise((resolve) => browser.listeners.message({ type: "pastex-site-visible", visible: true }, site, resolve));
  await new Promise((resolve) => browser.listeners.message({ type: "pastex-site-visible", visible: false }, site, resolve));
  await new Promise((resolve) => browser.listeners.message({ type: "pastex-flush-now" }, site, resolve));
  await settle();
  assert.equal(writes.length, 0, "an empty queue sends nothing");
});

test("the background check refuses to run while a site is visible", async () => {
  const browser = harness(os.tmpdir(), true);
  browser.context.PastexConfig.readSites = async () => ({ allowedSites: ["https://allowed.example"], source: "linked" });
  browser.context.PastexConfig.readConfig = async () => ({
    drive: { folderId: "root" }, serviceAccount: { client_email: "x", private_key: "k" },
  });
  let loads = 0;
  browser.context.PastexLibrary.load = async () => {
    loads++;
    return { tree: [{ id: "item", kind: "text", name: "Item", text: "text" }],
      session: { rootId: "root" }, warnings: [], bodies: {}, metadata: {}, signatures: {}, index: { order: {}, collapsed: {} } };
  };
  const site = { id: "abc", tab: { id: 1 }, frameId: 0, url: "https://allowed.example/path" };
  await new Promise((resolve) => browser.listeners.message({ type: "pastex-site-visible", visible: true }, site, resolve));
  browser.idle = "idle";
  browser.listenersForApis.alarm({ name: "pastex-idle-sync" });
  await settle();
  assert.equal(loads, 0, "a visible site blocks the background check");
  await new Promise((resolve) => browser.listeners.message({ type: "pastex-site-visible", visible: false }, site, resolve));
  browser.listenersForApis.alarm({ name: "pastex-idle-sync" });
  await settle();
  assert.equal(loads, 1, "once the site is left, the check runs");
});

test("Sync sends the queue and then reads Drive", async () => {
  const { browser, send, leave } = editScenario();
  const site = { id: "abc", tab: { id: 2 }, frameId: 0, url: "https://allowed.example/other" };
  // Another allowed site is open, so the worker must wait rather than write.
  await new Promise((resolve) => browser.listeners.message({ type: "pastex-site-visible", visible: true }, site, resolve));
  await send({ type: "pastex-panel-library" });
  await send({ type: "pastex-panel-local-edit", command: {
    kind: "create", name: "Draft", text: "here", parentId: "root",
    item: { kind: "text", parentId: "root", localId: "" },
  } });
  const panel = { id: "abc", url: "chrome-extension://abc/sidepanel.html" };
  const held = await new Promise((resolve) => browser.listeners.message({ type: "pastex-sync-now" }, panel, resolve));
  assert.equal(held.ok, true);
  assert.equal(held.result.deferred, true, "a visible site keeps the queue waiting");
  assert.equal(held.result.pending, 1);
  // Once that site is left, Sync is what sends it.
  await new Promise((resolve) => browser.listeners.message({ type: "pastex-site-visible", visible: false }, site, resolve));
  const done = await new Promise((resolve) => browser.listeners.message({ type: "pastex-sync-now" }, panel, resolve));
  await settle();
  assert.equal(done.result.deferred, false);
  assert.equal(done.result.pending, 0, "the queue is empty after Sync");
});

test("Sync sends the queue without reading the library back", async () => {
  const { browser, writes } = editScenario();
  const panel = { id: "abc", url: "chrome-extension://abc/sidepanel.html" };
  const send = (message) => new Promise((resolve) => browser.listeners.message(message, panel, resolve));
  await send({ type: "pastex-panel-library" });
  await send({ type: "pastex-panel-local-edit", command: {
    kind: "create", name: "Draft", text: "here", parentId: "root",
    item: { kind: "text", parentId: "root", localId: "" },
  } });
  // Count every read the worker makes after this point.
  let loads = 0;
  const realLoad = browser.context.PastexLibrary.load;
  browser.context.PastexLibrary.load = async (...args) => { loads++; return realLoad(...args); };
  const answer = await send({ type: "pastex-sync-now" });
  await settle();
  assert.equal(answer.result.flushed.sent, 1, "the queued write was sent");
  assert.deepEqual(writes[0], ["text", "Draft", "here"]);
  assert.equal(loads, 0, "Sync must not read the library back from Drive");
  assert.equal(answer.result.pending, 0);
});

test("only the panel may ask for a sync", async () => {
  const { browser } = editScenario();
  const page = { id: "abc", tab: { id: 1 }, frameId: 0, url: "https://allowed.example/" };
  const answer = await new Promise((resolve) => browser.listeners.message({ type: "pastex-sync-now" }, page, resolve));
  assert.equal(answer.ok, false);
});

test("a leftover queue is sent when the browser starts again", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "pastex-retry-"));
  try {
    const browser = harness(home, true);
    browser.context.PastexConfig.readSites = async () => ({ allowedSites: ["https://allowed.example"], source: "linked" });
    const configuration = { drive: { folderId: "root" }, serviceAccount: { client_email: "x", private_key: "k" } };
    browser.context.PastexConfig.readConfig = async () => configuration;
    browser.context.PastexLibrary.connect = async () => ({ token: "t", rootId: "root", index: { order: {}, collapsed: {} }, properties: {} });
    browser.context.PastexLibrary.load = async () => ({
      tree: [], session: { rootId: "root" }, warnings: [], bodies: {}, metadata: {}, signatures: {}, index: { order: {}, collapsed: {} },
    });
    const drive = browser.context.PastexDrive;
    drive.fetchAccessToken = async () => "token";
    drive.getFolder = async () => ({ mimeType: drive.FOLDER_MIME, properties: { pastex_index: "" } });
    drive.listChildren = async () => [];
    const writes = [];
    drive.uploadNew = async (t, parent, name, text) => { writes.push([name, text]); return { id: "made" }; };
    drive.readIndexFile = async () => ({ id: "", index: null });
    drive.writeIndexFile = async () => {};
    // A queue that never left before the browser was quit.
    const queue = browser.context.PastexQueue.create(browser.context.chrome.storage.local);
    await queue.push([{ id: "q1", kind: "create", name: "Recovered", text: "still here",
      item: { id: "local-1", localId: "local-1", kind: "text", parentId: "root", isNew: true } }]);
    browser.idle = "idle";
    browser.listeners.startup();
    await settle(60);
    assert.deepEqual(writes[0], ["Recovered", "still here"], "the leftover edit reaches Drive on the next start");
    // The worker's queue is the one that matters; a second reader only proves
    // the stored blob, which is the same storage the worker just cleared.
    assert.equal((await browser.context.deferredQueue.all()).length, 0, "the queue is empty afterwards");
  } finally { fs.rmSync(home, { recursive: true, force: true }); }
});

test("a page that is not an approved site cannot trigger a drive write", async () => {
  const { browser } = editScenario();
  const stranger = { id: "someone-else", tab: { id: 1 }, frameId: 0, url: "https://example.test/" };
  const refused = await new Promise((resolve) => browser.listeners.message({ type: "pastex-flush-now" }, stranger, resolve));
  assert.equal(refused.ok, false);
  const injection = { id: "abc", tab: { id: 1 }, frameId: 1, url: "https://allowed.example/path" };
  const subframe = await new Promise((resolve) => browser.listeners.message({ type: "pastex-flush-now" }, injection, resolve));
  assert.equal(subframe.ok, false, "only the top frame of an extension page may ask");
});

test("an edit is refused for a sender that is not the panel", async () => {
  const { browser } = editScenario();
  const page = { id: "abc", tab: { id: 1 }, frameId: 0, url: "https://allowed.example/path" };
  const refused = await new Promise((resolve) => browser.listeners.message(
    { type: "pastex-panel-local-edit", command: { kind: "delete", ids: ["item1"] } }, page, resolve));
  assert.equal(refused.ok, false, "a content script cannot edit the library");
});


// --- the round trip a user actually complains about ------------------------
//
// "Add text, collapse a folder and reorder a row work in the panel, but after
// restarting the browser they are gone, while creating a folder survived."
// These tests follow the writes all the way to what Drive reports afterwards.

function roundTripScenario() {
  const { browser, writes } = editScenario();
  const site = { id: "abc", tab: { id: 1 }, frameId: 0, url: "https://allowed.example/path" };
  // The Drive folder as the loader will read it on the next start.
  const driveState = { index: null, children: [] };
  const drive = browser.context.PastexDrive;
  drive.getFolder = async () => ({ id: "root", mimeType: drive.FOLDER_MIME });
  drive.listChildren = async () => structuredClone(driveState.children);
  // The index is a file in the library, so it lives with the children.
  drive.readIndexFile = async () => ({ id: driveState.index ? "index-file" : "",
    index: structuredClone(driveState.index) });
  drive.writeIndexFile = async (t, root, index) => {
    writes.push(["index", structuredClone(index)]);
    driveState.index = structuredClone(index);
  };
  drive.uploadNew = async (t, parent, name, text) => {
    writes.push(["text", name, text]);
    const id = "drive-" + name;
    driveState.children.push({ id: id, name: name + ".txt", mimeType: drive.TEXT_MIME });
    return { id: id };
  };
  drive.createFolder = async (t, name) => {
    writes.push(["folder", name]);
    const id = "drive-" + name;
    driveState.children.push({ id: id, name: name, mimeType: drive.FOLDER_MIME });
    return id;
  };
  // The next read of the library starts from what Drive now holds.
  drive.downloadText = async () => "";
  return { browser, writes, driveState, site };
}

test("add text, collapse and reorder survive a browser restart", async () => {
  const { browser, driveState, site } = roundTripScenario();
  const panel = { id: "abc", url: "chrome-extension://abc/sidepanel.html" };
  const send = (message) => new Promise((resolve) => browser.listeners.message(message, panel, resolve));
  await send({ type: "pastex-panel-library" });
  // A folder already on Drive, so there is something to fold and to order.
  driveState.children.push({ id: "folder1", name: "Folder", mimeType: browser.context.PastexDrive.FOLDER_MIME });
  await send({ type: "pastex-panel-library" });

  // 1. add a snippet, 2. fold the folder, 3. move the snippet to the top.
  await send({ type: "pastex-panel-local-edit", command: {
    kind: "create", name: "New one", text: "body", parentId: "root",
    item: { kind: "text", name: "New one", parentId: "root", localId: "" },
  } });
  await send({ type: "pastex-panel-local-edit", command: {
    kind: "collapse", ids: ["folder1"], collapsed: true,
  } });
  await send({ type: "pastex-panel-local-edit", command: {
    kind: "reorder", groups: [["drive-New one", "folder1"]], parents: [""],
  } });

  // Leaving the site is what sends the queue.
  await new Promise((resolve) => browser.listeners.message({ type: "pastex-flush-now" }, site, resolve));
  await settle(120);

  assert.ok(driveState.children.some((file) => file.name === "New one.txt"),
    "the added text reached Drive as a real file");
  const index = driveState.index;
  assert.deepEqual(index.collapsed, { folder1: "1" }, "the fold reached Drive");
  assert.deepEqual(index.order.root, ["drive-New one", "folder1"],
    "the order reached Drive under the root folder's own id");
});

test("a move is queued locally and reaches Drive as one move per row", async () => {
  const { browser, writes, send } = editScenario();
  const site = { id: "abc", tab: { id: 1 }, frameId: 0, url: "https://allowed.example/path" };
  await send({ type: "pastex-panel-library" });
  const drive = browser.context.PastexDrive;
  const moves = [];
  drive.moveFile = async (t, id, from, to) => { moves.push([id, to]); };
  const queued = await send({ type: "pastex-panel-local-edit", command: {
    kind: "move", ids: ["item1"], parentId: "folder-dest",
  } });
  assert.equal(queued.ok, true);
  assert.equal(moves.length, 0, "nothing reaches Drive while the site is open");
  const status = await send({ type: "pastex-panel-status" });
  assert.equal(status.result.pending, 1);
  await new Promise((resolve) => browser.listeners.message({ type: "pastex-flush-now" }, site, resolve));
  await settle();
  assert.deepEqual(moves, [["item1", "folder-dest"]], "the queued move is applied once");
  assert.equal(writes.filter((w) => w[0] === "index").length, 0, "a move is not an index write");
});

test("a write Drive refuses for good is reported instead of vanishing", async () => {
  const { browser, send } = editScenario();
  const site = { id: "abc", tab: { id: 1 }, frameId: 0, url: "https://allowed.example/path" };
  // An open panel listens on a port; this is how a refusal reaches it.
  const posted = [];
  // A panel port: the worker accepts it only from an extension page in a tab.
  browser.listeners.connect({
    name: "pastex-library-progress",
    sender: { id: browser.context.chrome.runtime.id, url: browser.context.chrome.runtime.getURL("sidepanel.html") },
    onDisconnect: { addListener() {} }, onMessage: { addListener() {} },
    postMessage: (message) => { posted.push(message); },
  });
  await send({ type: "pastex-panel-library" });
  browser.context.PastexDrive.uploadNew = async () => { throw new Error("Drive API POST /files failed (HTTP 400): Invalid name"); };
  await send({ type: "pastex-panel-local-edit", command: {
    kind: "create", name: "Bad", text: "x", parentId: "root",
    item: { kind: "text", name: "Bad", parentId: "root", localId: "" },
  } });
  await new Promise((resolve) => browser.listeners.message({ type: "pastex-flush-now" }, site, resolve));
  await settle();
  const dropped = posted.find((m) => m.dropped);
  assert.ok(dropped, "the panel is told that the write was dropped");
  assert.match(dropped.reason, /Invalid name/);
});

test("a snippet kept out by the storage quota is not thrown away", async () => {
  const { browser, send } = editScenario();
  const site = { id: "abc", tab: { id: 1 }, frameId: 0, url: "https://allowed.example/path" };
  const posted = [];
  browser.listeners.connect({
    name: "pastex-library-progress",
    sender: { id: browser.context.chrome.runtime.id, url: browser.context.chrome.runtime.getURL("sidepanel.html") },
    onDisconnect: { addListener() {} }, onMessage: { addListener() {} },
    postMessage: (message) => { posted.push(message); },
  });
  await send({ type: "pastex-panel-library" });
  // Exactly what Drive answers for a service account: the folder can hold
  // files, but this account may not create one.
  browser.context.PastexDrive.uploadNew = async () => {
    throw new Error("Drive API POST /files failed (HTTP 403): Service Accounts do not have storage quota.");
  };
  await send({ type: "pastex-panel-local-edit", command: {
    kind: "create", name: "Keep me", text: "precious", parentId: "root",
    item: { kind: "text", name: "Keep me", parentId: "root", localId: "" },
  } });
  await new Promise((resolve) => browser.listeners.message({ type: "pastex-flush-now" }, site, resolve));
  await settle();
  // The user wrote this text. A quota problem is fixable, so the entry waits.
  const status = await send({ type: "pastex-panel-status" });
  assert.equal(status.result.pending, 1, "the write is kept for the next attempt");
  const blocked = posted.find((m) => m.blocked);
  assert.ok(blocked, "the panel is told why, without pressing Sync");
  assert.match(blocked.reason, /storage quota/i);
  // The cached row is still there in the worker: the flush read the library
  // back, and the quota refusal did not remove the local row the way a real
  // rejection does.
  const snapshot = await browser.context.libraryCache.current();
  assert.ok(snapshot.tree.some((node) => node.name === "Keep me"),
    "the row stays visible, because the write has not been given up on");
});

test("a flush that reads the library back keeps the edits still waiting", async () => {
  const { browser, send } = editScenario();
  const site = { id: "abc", tab: { id: 1 }, frameId: 0, url: "https://allowed.example/path" };
  await send({ type: "pastex-panel-library" });
  // Offline: the write is retried later, not dropped, so the read that follows
  // cannot contain it.
  browser.context.PastexDrive.uploadNew = async () => { throw new Error("network down"); };
  await send({ type: "pastex-panel-local-edit", command: {
    kind: "create", name: "Pending", text: "not on Drive yet", parentId: "root",
    item: { kind: "text", name: "Pending", parentId: "root", localId: "" },
  } });
  await new Promise((resolve) => browser.listeners.message({ type: "pastex-flush-now" }, site, resolve));
  await settle();
  const status = await send({ type: "pastex-panel-status" });
  assert.equal(status.result.pending, 1, "the entry is still queued");
  const snapshot = await browser.context.libraryCache.current();
  assert.ok(snapshot.tree.some((node) => node.name === "Pending"),
    "the row the user typed is still in the list");
});

test("a row that never reached Drive is not moved in Drive", async () => {
  const { browser, send } = editScenario();
  const site = { id: "abc", tab: { id: 1 }, frameId: 0, url: "https://allowed.example/path" };
  await send({ type: "pastex-panel-library" });
  const drive = browser.context.PastexDrive;
  const moves = [];
  drive.moveFile = async (t, id, from, to) => { moves.push([id, to]); };
  await send({ type: "pastex-panel-local-edit", command: {
    kind: "move", ids: ["local-abc"], parentId: "folder-dest",
  } });
  await new Promise((resolve) => browser.listeners.message({ type: "pastex-flush-now" }, site, resolve));
  await settle();
  assert.deepEqual(moves, [], "a create places the row in its folder by itself");
});

test("the stored index is the one the loader applies, not a shape it ignores", async () => {
  const { browser, driveState, site } = roundTripScenario();
  const panel = { id: "abc", url: "chrome-extension://abc/sidepanel.html" };
  const send = (message) => new Promise((resolve) => browser.listeners.message(message, panel, resolve));
  const FOLDER = browser.context.PastexDrive.FOLDER_MIME;
  const TEXT = browser.context.PastexDrive.TEXT_MIME;
  driveState.children.push({ id: "a", name: "A.txt", mimeType: TEXT });
  driveState.children.push({ id: "b", name: "B.txt", mimeType: TEXT });
  await send({ type: "pastex-panel-library" });
  await send({ type: "pastex-panel-local-edit", command: {
    kind: "reorder", groups: [["b", "a"]], parents: [""],
  } });
  await new Promise((resolve) => browser.listeners.message({ type: "pastex-flush-now" }, site, resolve));
  await settle(120);

  // Read the library again exactly as a fresh start would. The real loader is
  // used here on purpose: editScenario replaces Library.load with a fixed list
  // so the rest of these tests can ignore Drive, and that stub would hide the
  // very thing this test is about.
  const RealLibrary = require("../library.js");
  // The scenarios use a placeholder key; the real loader has to be handed one
  // it can parse, and a token that needs no signature check.
  const realConfig = { readConfig: async () => ({
    drive: { folderId: "root" },
    serviceAccount: { client_email: "x", private_key: "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----" },
  }) };
  browser.context.PastexDrive.fetchAccessToken = async () => "token";
  browser.context.PastexDrive.downloadText = async () => "";
  const again = await RealLibrary.load(
    realConfig, browser.context.PastexDrive,
    undefined, { bodies: {}, texts: {}, index: null });
  assert.deepEqual(again.tree.map((node) => node.id), ["b", "a"],
    "the order written to Drive is the order the next read shows");
  assert.ok(FOLDER && TEXT);
});
