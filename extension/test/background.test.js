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


function harness(home, unavailable) {
  const listeners = {};
  const event = (name) => ({ addListener(fn) { listeners[name] = fn; } });
  let launches = 0;
  let registered = [];
  let injected = 0;
  const opened = [];
  let idleState = "idle";
  const listenersForApis = {};
  const alarms = {
    create() {},
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
    sendMessage: async (message) => {
      launches++;
      if (unavailable) return { ok: false, error: "folder unavailable" };
      const dir = path.join(home, "chosen-folder");
      try {
        if (message.operation === "sites") return { ok: true, data: { allowedSites: JSON.parse(fs.readFileSync(path.join(dir, "pastex-sites.json"))).allowed_sites } };
        return { ok: true, data: { drive: { folderId: "root" }, serviceAccount: { client_email: "x", private_key: "k" } } };
      } catch (err) { return { ok: false, error: err.message }; }
    },
  };
  const sessionData = {};
  const localData = {};
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
        sendMessage: async () => { throw new Error("no receiver"); } },
      alarms, idle: idleApi,
      action: { onClicked: event("action") }, sidePanel: { open: (options) => { opened.push(options); return Promise.resolve(); } },
    },
    PastexStore: { loadTree: async () => [], loadRecent: async () => [], findById: () => null },
    crypto: require("node:crypto").webcrypto, TextEncoder, setTimeout, clearTimeout, URL, console: { warn() {} },
  });
  context.self = context;
  context.importScripts = (...names) => {
    for (const name of names) {
      if (name !== "store.js") vm.runInContext(fs.readFileSync(path.join(ext, name), "utf8"), context);
    }
  };
  vm.runInContext(fs.readFileSync(path.join(ext, "background.js"), "utf8"), context);
  return { listeners, context, listenersForApis, opened,
    set idle(value) { idleState = value; },
    get launches() { return launches; },
    get registered() { return registered; }, get injected() { return injected; } };
}

test("extension startup never creates a configuration directory", async () => {
  for (const eventName of ["installed", "startup"]) {
    const home = fs.mkdtempSync(path.join(os.tmpdir(), "pastex-browser-"));
    try {
      const browser = harness(home, false);
      browser.listeners[eventName]();
      await new Promise(setImmediate);
      assert.equal(browser.launches, 1);
      assert.deepEqual(fs.readdirSync(home), []);
      assert.equal(browser.registered.length, 0);
    } finally { fs.rmSync(home, { recursive: true, force: true }); }
  }
});

test("site registration and on-demand injection are limited to configured sites", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "pastex-allow-"));
  try {
    const browser = harness(home, false);
    browser.listeners.installed();
    await new Promise(setImmediate);
    assert.equal(browser.registered.length, 0);
    fs.mkdirSync(path.join(home, "chosen-folder"));
    const file = path.join(home, "chosen-folder", "pastex-sites.json");
    fs.writeFileSync(file, JSON.stringify({ allowed_sites: ["https://allowed.example"] }));
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
    fs.writeFileSync(file, "bad json");
    browser.listeners.startup();
    await new Promise(setImmediate);
    assert.equal(browser.registered.length, 0);
    assert.equal((await allowedMessage("https://allowed.example/")).allowed, false);
    await assert.rejects(browser.context.messageTab(1, { type: "pastex-insert" }), /site-not-allowed/);
  } finally { fs.rmSync(home, { recursive: true, force: true }); }
});

test("an unavailable configuration manifest does not prevent extension startup", async () => {
  const browser = harness(os.tmpdir(), true);
  browser.listeners.installed();
  await new Promise(setImmediate);
  assert.equal(browser.launches, 1);
});

test("only the extension panel can request a site registration reload", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "pastex-reload-"));
  try {
    const browser = harness(home, false);
    browser.listeners.installed();
    await new Promise(setImmediate);
    fs.mkdirSync(path.join(home, "chosen-folder"), { recursive: true });
    const file = path.join(home, "chosen-folder", "pastex-sites.json");
    fs.writeFileSync(file, JSON.stringify({ allowed_sites: ["https://allowed.example"] }));
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

test("panel loads are cached; writes require panel and invalidate cache", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "pastex-cache-worker-"));
  try {
    const browser = harness(home, false);
    browser.listeners.installed(); await new Promise(setImmediate);
    fs.mkdirSync(path.join(home, "chosen-folder"), { recursive: true });
    fs.writeFileSync(path.join(home, "chosen-folder", "pastex-sites.json"), JSON.stringify({ allowed_sites: ["https://allowed.example"] }));
    browser.context.PastexConfig.readConfig = async () => ({ drive: { folderId: "root" }, serviceAccount: { client_email: "x", private_key: "k" } });
    let text = "old", loads = 0;
    browser.context.PastexLibrary.load = async () => ({ tree: [{ id: "item", kind: "text", name: "Item", text: text }], session: { rootId: "root" }, warnings: [], sequence: ++loads });
    browser.context.PastexLibrary.connect = async () => ({ token: "token", rootId: "root" });
    browser.context.PastexDrive.updateText = async (token, id, value) => { assert.equal(id, "item"); text = value; };
    const panel = { id: "abc", url: "chrome-extension://abc/sidepanel.html" };
    const page = { id: "abc", tab: { id: 1 }, url: "https://allowed.example/" };
    function send(message, sender) { return new Promise((resolve) => browser.listeners.message(message, sender, resolve)); }
    assert.equal((await send({ type: "pastex-panel-library" }, panel)).result.tree[0].text, "old");
    assert.equal((await send({ type: "pastex-panel-library" }, panel)).result.tree[0].text, "old");
    assert.equal(loads, 1);
    assert.equal((await send({ type: "pastex-panel-mutate", command: {} }, page)).ok, false);
    const command = { action: "save", mode: "edit-text", rootId: "root", parentId: "root", name: "Item", text: "new", node: { id: "item", name: "Item", driveName: "Item.txt", kind: "text", text: "old" } };
    assert.equal((await send({ type: "pastex-panel-mutate", command }, panel)).ok, true);
    assert.equal((await send({ type: "pastex-panel-library" }, panel)).result.tree[0].text, "new");
    assert.equal(loads, 2);
  } finally { fs.rmSync(home, { recursive: true, force: true }); }
});

test("worker handles create, rename, order and bulk trash with Drive, denying changed roots", async () => {
  const browser = harness(os.tmpdir(), true);
  const panel = { id: "abc", url: "chrome-extension://abc/sidepanel.html" };
  browser.context.PastexLibrary.connect = async () => ({ token: "token", rootId: "root" });
  const drive = browser.context.PastexDrive;
  const writes = [];
  drive.listChildren = async () => [];
  drive.createFolder = async (t, name, parent) => { writes.push(["folder", name, parent]); return "f"; };
  drive.uploadNew = async (t, parent, name, text) => { writes.push(["new", parent, name, text]); return { id: "n" }; };
  drive.renameFile = async (t, id, name) => { writes.push(["rename", id, name]); };
  drive.trashFile = async (t, id) => { writes.push(["trash", id]); };
  drive.setOrder = async (t, id, order) => { writes.push(["order", id, order]); };
  drive.setCollapsed = async (t, id, collapsed) => { writes.push(["collapse", id, collapsed]); };
  const send = (command) => new Promise((resolve) => browser.listeners.message({ type: "pastex-panel-mutate", command }, panel, resolve));
  assert.equal((await send({ rootId: "root", action: "save", mode: "new-folder", name: "Folder", parentId: "root" })).result.id, "f");
  assert.equal((await send({ rootId: "root", action: "save", mode: "new-text", name: "Note", text: "text", parentId: "root" })).result.id, "n");
  assert.equal((await send({ rootId: "root", action: "save", mode: "rename-text", name: "Renamed", parentId: "root", node: { id: "n", kind: "text", name: "Note", driveName: "Note.txt" } })).ok, true);
  assert.equal((await send({ rootId: "root", action: "reorder", groups: [["n", "f"]] })).ok, true);
  assert.equal((await send({ rootId: "root", action: "collapse", ids: ["f"], collapsed: true })).ok, true);
  assert.equal((await send({ rootId: "root", action: "delete", ids: ["n", "f"] })).ok, true);
  assert.equal((await send({ rootId: "other", action: "delete", ids: ["n"] })).ok, false);
  // Bulk writes run with bounded parallelism, so only their grouping is deterministic.
  const tupleSort = (a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b));
  assert.deepEqual(writes.slice(0, 3), [["folder", "Folder", "root"], ["new", "root", "Note", "text"], ["rename", "n", "Renamed.txt"]]);
  assert.deepEqual(writes.slice(3, 5).sort(tupleSort), [["order", "n", 0], ["order", "f", 1]].sort(tupleSort));
  assert.deepEqual(writes.slice(5, 6), [["collapse", "f", true]]);
  assert.deepEqual(writes.slice(6, 8).sort(tupleSort), [["trash", "n"], ["trash", "f"]].sort(tupleSort));
});

test("repeated paste requests reuse the cached site allowlist", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "pastex-fast-paste-"));
  try {
    const browser = harness(home, false);
    fs.mkdirSync(path.join(home, "chosen-folder"), { recursive: true });
    fs.writeFileSync(path.join(home, "chosen-folder", "pastex-sites.json"), JSON.stringify({ allowed_sites: ["https://allowed.example"] }));
    browser.listeners.startup();
    await new Promise(setImmediate);
    const readsAfterStartup = browser.launches;
    await assert.rejects(browser.context.messageTab(1, { type: "pastex-insert" }), /no receiver/);
    await assert.rejects(browser.context.messageTab(1, { type: "pastex-insert" }), /no receiver/);
    assert.equal(browser.launches, readsAfterStartup, "paste must not re-read linked configuration");
  } finally { fs.rmSync(home, { recursive: true, force: true }); }
});

test("bulk Drive writes run with bounded parallelism and reuse loaded properties", async () => {
  const browser = harness(os.tmpdir(), true);
  const panel = { id: "abc", url: "chrome-extension://abc/sidepanel.html" };
  browser.context.PastexLibrary.connect = async () => ({ token: "token", rootId: "root" });
  const drive = browser.context.PastexDrive;
  let active = 0, max = 0;
  const calls = [];
  async function track(kind, id, extra, known) {
    active++; max = Math.max(max, active);
    await new Promise(setImmediate);
    active--;
    calls.push([kind, id, extra, known]);
  }
  drive.setOrder = (t, id, order, impl, known) => track("order", id, order, known);
  drive.setCollapsed = (t, id, collapsed, impl, known) => track("collapse", id, collapsed, known);
  const send = (command) => new Promise((resolve) => browser.listeners.message({ type: "pastex-panel-mutate", command }, panel, resolve));
  const ids = ["a", "b", "c", "d", "e", "f", "g"];
  const properties = { a: { pastex_order: "9", pastex_collapsed: "1" } };
  assert.equal((await send({ rootId: "root", action: "reorder", groups: [ids], properties })).ok, true);
  assert.equal(max, 6);
  assert.deepEqual(calls.find((c) => c[1] === "a")[3], properties.a);
  calls.length = 0; active = 0; max = 0;
  assert.equal((await send({ rootId: "root", action: "collapse", ids, collapsed: true, properties })).ok, true);
  assert.equal(max, 6);
  assert.deepEqual(calls.find((c) => c[1] === "a")[3], properties.a);
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

test("a running panel operation suppresses the background sync", async () => {
  const browser = harness(os.tmpdir(), true);
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
