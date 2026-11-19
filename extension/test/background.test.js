// Simulate Chrome's extension lifecycle and native-host launch, including
// the extension-origin argument that Chrome passes to the host process.
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
const { spawnSync } = require("node:child_process");

const ext = path.resolve(__dirname, "..");
const hostPath = path.resolve(ext, "../native-host/pastex-native-host");

function harness(home, unavailable) {
  const listeners = {};
  const event = (name) => ({ addListener(fn) { listeners[name] = fn; } });
  let launches = 0;
  let registered = [];
  let injected = 0;
  const runtime = {
    id: "abc", getURL: (name) => "chrome-extension://abc/" + name,
    onInstalled: event("installed"), onStartup: event("startup"), onMessage: event("message"), onConnect: event("connect"),
    sendNativeMessage(host, message, callback) {
      launches++;
      assert.equal(host, "com.pastex.native");
      if (unavailable) {
        runtime.lastError = { message: "Host not found" };
        callback();
        delete runtime.lastError;
        return;
      }
      const body = Buffer.from(JSON.stringify(message));
      const header = Buffer.alloc(4);
      header.writeUInt32LE(body.length);
      const proc = spawnSync(hostPath, ["chrome-extension://" + "a".repeat(32) + "/"], {
        input: Buffer.concat([header, body]), env: { HOME: home, PATH: "/usr/bin:/bin" },
      });
      assert.equal(proc.status, 0, String(proc.stderr));
      const length = proc.stdout.readUInt32LE(0);
      callback(JSON.parse(proc.stdout.subarray(4, 4 + length).toString()));
    },
  };
  const context = vm.createContext({
    chrome: {
      runtime, storage: { session: { get: async () => ({}), set: async () => {}, remove: async () => {} } },
      scripting: {
        getRegisteredContentScripts: async () => registered,
        unregisterContentScripts: async () => { registered = []; },
        registerContentScripts: async (scripts) => { registered = scripts; },
        executeScript: async () => { injected++; }, insertCSS: async () => {},
      },
      tabs: { get: async (id) => ({ id, url: id === 1 ? "https://allowed.example/path" : "https://blocked.example/" }),
        sendMessage: async () => { throw new Error("no receiver"); } },
      action: { onClicked: event("action") },
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
  return { listeners, context, get launches() { return launches; },
    get registered() { return registered; }, get injected() { return injected; } };
}

test("extension installation and browser startup initialize config via the real native host", async () => {
  for (const eventName of ["installed", "startup"]) {
    const home = fs.mkdtempSync(path.join(os.tmpdir(), "pastex-browser-"));
    try {
      const browser = harness(home, false);
      browser.listeners[eventName]();
      await new Promise(setImmediate);
      assert.equal(browser.launches, 1);
      const dir = path.join(home, ".config", "pastex");
      assert.deepEqual(fs.readdirSync(dir).sort(), ["pastex-google-drive.json", "pastex-sites.json", "service-account.json"]);
      for (const name of ["pastex-google-drive.json", "service-account.json", "pastex-sites.json"]) {
        assert.ok(fs.existsSync(path.join(dir, name)));
        assert.equal(fs.statSync(path.join(dir, name)).mode & 0o777, 0o600);
      }
    } finally { fs.rmSync(home, { recursive: true, force: true }); }
  }
});

test("repeated lifecycle events preserve user config files", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "pastex-existing-"));
  try {
    const browser = harness(home, false);
    browser.listeners.installed();
    await new Promise(setImmediate);
    const file = path.join(home, ".config", "pastex", "pastex-google-drive.json");
    const existing = JSON.stringify({ folder_url: "https://drive.google.com/drive/folders/example" });
    fs.writeFileSync(file, existing);
    browser.listeners.startup();
    await new Promise(setImmediate);
    assert.equal(fs.readFileSync(file, "utf8"), existing);
  } finally { fs.rmSync(home, { recursive: true, force: true }); }
});

test("site registration and on-demand injection are limited to configured sites", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "pastex-allow-"));
  try {
    const browser = harness(home, false);
    browser.listeners.installed();
    await new Promise(setImmediate);
    assert.equal(browser.registered.length, 0);
    const file = path.join(home, ".config", "pastex", "pastex-sites.json");
    fs.writeFileSync(file, JSON.stringify({ allowed_sites: ["https://allowed.example"] }));
    browser.listeners.startup();
    await new Promise(setImmediate);
    assert.deepEqual(Array.from(browser.registered[0].matches), ["https://allowed.example/*"]);
    const allowedMessage = (url) => new Promise((resolve) => {
      assert.equal(browser.listeners.message({ type: "pastex-site-allowed" }, { tab: { id: 1 }, url }, resolve), true);
    });
    assert.equal((await allowedMessage("https://allowed.example/path")).allowed, true);
    assert.equal((await allowedMessage("https://blocked.example/")).allowed, false);
    const configDir = path.join(home, ".config", "pastex");
    fs.writeFileSync(path.join(configDir, "pastex-google-drive.json"), JSON.stringify({ folder_id: "root" }));
    fs.writeFileSync(path.join(configDir, "service-account.json"), JSON.stringify({ type: "service_account", client_email: "test@example.com", private_key: "key" }));
    let loads = 0;
    browser.context.PastexLibrary.load = async () => {
      loads++;
      return { tree: [{ id: "drive-id", kind: "text", name: "Drive snippet", text: "from Drive" }], session: { rootId: "root" }, warnings: [] };
    };
    const libraryMessage = (url) => new Promise((resolve) => {
      assert.equal(browser.listeners.message({ type: "pastex-get-library" }, { tab: { id: 1 }, url }, resolve), true);
    });
    assert.equal((await libraryMessage("https://allowed.example/")).tree[0].text, "from Drive");
    assert.equal((await libraryMessage("https://blocked.example/")).ok, false);
    assert.equal(loads, 1);
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

test("an unavailable native host does not prevent extension startup", async () => {
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
    const file = path.join(home, ".config", "pastex", "pastex-sites.json");
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

test("panel and allowed picker share cached data; writes require panel and invalidate it", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "pastex-cache-worker-"));
  try {
    const browser = harness(home, false);
    browser.listeners.installed(); await new Promise(setImmediate);
    fs.writeFileSync(path.join(home, ".config", "pastex", "pastex-sites.json"), JSON.stringify({ allowed_sites: ["https://allowed.example"] }));
    browser.context.PastexNative.readConfig = async () => ({ drive: { folderId: "root" }, serviceAccount: { client_email: "x", private_key: "k" } });
    let text = "old", loads = 0;
    browser.context.PastexLibrary.load = async () => ({ tree: [{ id: "item", kind: "text", name: "Item", text: text }], session: { rootId: "root" }, warnings: [], sequence: ++loads });
    browser.context.PastexLibrary.connect = async () => ({ token: "token", rootId: "root" });
    browser.context.PastexDrive.updateText = async (token, id, value) => { assert.equal(id, "item"); text = value; };
    const panel = { id: "abc", url: "chrome-extension://abc/sidepanel.html" };
    const page = { id: "abc", tab: { id: 1 }, url: "https://allowed.example/" };
    function send(message, sender) { return new Promise((resolve) => browser.listeners.message(message, sender, resolve)); }
    assert.equal((await send({ type: "pastex-panel-library" }, panel)).result.tree[0].text, "old");
    assert.equal((await send({ type: "pastex-get-library" }, page)).tree[0].text, "old");
    assert.equal(loads, 1);
    assert.equal((await send({ type: "pastex-panel-mutate", command: {} }, page)).ok, false);
    const command = { action: "save", mode: "edit-text", rootId: "root", parentId: "root", name: "Item", text: "new", node: { id: "item", name: "Item", driveName: "Item.txt", kind: "text", text: "old" } };
    assert.equal((await send({ type: "pastex-panel-mutate", command }, panel)).ok, true);
    assert.equal((await send({ type: "pastex-get-library" }, page)).tree[0].text, "new");
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
  const send = (command) => new Promise((resolve) => browser.listeners.message({ type: "pastex-panel-mutate", command }, panel, resolve));
  assert.equal((await send({ rootId: "root", action: "save", mode: "new-folder", name: "Folder", parentId: "root" })).result.id, "f");
  assert.equal((await send({ rootId: "root", action: "save", mode: "new-text", name: "Note", text: "text", parentId: "root" })).result.id, "n");
  assert.equal((await send({ rootId: "root", action: "save", mode: "rename-text", name: "Renamed", parentId: "root", node: { id: "n", kind: "text", name: "Note", driveName: "Note.txt" } })).ok, true);
  assert.equal((await send({ rootId: "root", action: "reorder", groups: [["n", "f"]] })).ok, true);
  assert.equal((await send({ rootId: "root", action: "delete", ids: ["n", "f"] })).ok, true);
  assert.equal((await send({ rootId: "other", action: "delete", ids: ["n"] })).ok, false);
  assert.deepEqual(writes, [["folder", "Folder", "root"], ["new", "root", "Note", "text"], ["rename", "n", "Renamed.txt"], ["order", "n", 0], ["order", "f", 1], ["trash", "n"], ["trash", "f"]]);
});
