"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const Store = require("../store.js");
const Library = require("../library.js");
const ConfigImport = require("../config-import.js");

class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.style = {}; this.listeners = {}; this.value = ""; this.hidden = false; this.textContent = ""; }
  appendChild(child) { this.children.push(child); return child; }
  removeChild(child) { this.children.splice(this.children.indexOf(child), 1); }
  get firstChild() { return this.children[0]; }
  setAttribute() {}
  querySelector() { return this.paragraph || (this.paragraph = new Element("p")); }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  focus() {}
  select() {}
}

function panelHarness(fail, options = {}) {
  const ids = ["search", "tree", "empty", "drive-status", "btn-sync", "btn-folder", "btn-text", "btn-empty-text", "editor-title", "editor-parent-label", "editor-parent", "editor-text-wrap", "editor-name", "editor-text", "editor-overlay", "editor-save", "editor-cancel", "status", "btn-config", "config-overlay", "config-status", "config-close", "choose-manifest", "btn-select", "selection-actions", "selection-count", "btn-up", "btn-down", "btn-delete-selected"];
  const elements = Object.fromEntries(ids.map((id) => [id, new Element("div")]));
  const records = options.records || [
    { id: "item1", name: "Synced.txt", mimeType: "text/plain", text: "from Google Drive", parent: "root" },
    { id: "folder1", name: "Empty Folder", mimeType: "folder", parent: "root" },
  ];
  let seq = 0;
  const drive = {
    FOLDER_MIME: "folder", TEXT_MIME: "text/plain",
    parseServiceAccount: JSON.parse, fetchAccessToken: async () => "token",
    getFolder: async () => { if (fail === true) throw new Error("403 denied"); return { mimeType: "folder" }; },
    listChildren: async (token, parent) => records.filter((r) => r.parent === parent && !r.trashed),
    downloadText: async (token, id) => {
      if (options.beforeDownload) await options.beforeDownload(id);
      if (fail === "partial" && id === "item1") throw new Error("403 denied");
      return records.find((r) => r.id === id).text;
    },
    createFolder: async (token, name, parent) => { const id = "new" + ++seq; records.push({ id, name, parent, mimeType: "folder" }); return id; },
    uploadNew: async (token, parent, name, text) => { const id = "new" + ++seq; records.push({ id, name: name + ".txt", text, parent, mimeType: "text/plain" }); return { id }; },
    updateText: async (token, id, text) => { records.find((r) => r.id === id).text = text; },
    renameFile: async (token, id, name) => { records.find((r) => r.id === id).name = name; },
    trashFile: async (token, id) => { records.find((r) => r.id === id).trashed = true; },
  };
  const imports = [];
  const messages = [];
  const clipboardTexts = [];
  const native = {
    readConfig: async () => ({ drive: { folderId: "root" }, serviceAccount: { client_email: "x", private_key: "y" } }),
    resetCache() {},
    importConfigs: async (files) => { imports.push(files); return options.importResult || { imported: Object.keys(files), error: "" }; },
  };
  const document = { getElementById: (id) => elements[id], createElement: (tag) => new Element(tag), addEventListener() {} };
  const window = { PastexStore: Store, PastexDrive: drive, PastexNative: native, PastexLibrary: Library, PastexConfigImport: ConfigImport,
    PastexConfigSource: { create: () => ({
      chooseManifestText: async (text) => { imports.push(text); return { drive: "linked drive.json", account: "linked account.json", sites: "linked sites.json" }; },
    }) },
    PastexHandleStore: {}, PastexSites: {}, PastexManifest: {},
    showOpenFilePicker: options.manifestPicker || (async () => [{ getFile: async () => ({ name: "config.json", text: async () => "{}" }) }]),
    confirm: () => options.confirm !== false, setTimeout, clearTimeout };
  let progress = () => {};
  let cache;
  const runtime = {
    connect: () => ({ onMessage: { addListener(fn) { progress = fn; } } }),
    sendMessage: async (message) => {
      messages.push(message);
      if (message.type === "pastex-panel-library") {
        if (!message.force && cache) return { ok: true, result: { ...cache, cached: true } };
        try {
          const result = await Library.load(native, drive, (tree, session) => progress({ tree, rootId: session.rootId }));
          cache = { tree: result.tree, rootId: result.session.rootId, warnings: result.warnings };
          return { ok: true, result: { ...cache, cached: false } };
        } catch (err) { return { ok: false, error: err.message }; }
      }
      if (message.type === "pastex-panel-mutate") {
        const c = message.command;
        try {
          let result = {};
          if (c.action === "save") {
            if (c.mode === "new-folder") result.id = await drive.createFolder("token", c.name, c.parentId);
            else if (c.mode === "new-text") result.id = (await drive.uploadNew("token", c.parentId, c.name, c.text)).id;
            else {
              result.id = c.node.id;
              if (c.mode === "edit-text" && c.text !== c.node.text) await drive.updateText("token", c.node.id, c.text);
              if (c.name !== c.node.name) await drive.renameFile("token", c.node.id, c.name + (c.node.driveName.endsWith(".txt") ? ".txt" : ""));
            }
          } else if (c.action === "delete") {
            for (const id of c.ids) await drive.trashFile("token", id);
          } else if (c.action === "reorder") {
            for (const group of c.groups) for (let i = 0; i < group.length; i++) records.find((r) => r.id === group[i]).properties = { pastex_order: String(i) };
          }
          cache = null;
          return { ok: true, result };
        } catch (err) { cache = null; return { ok: false, error: err.message }; }
      }
      if (message.type === "pastex-sidepanel-insert" && options.insertResponse) return options.insertResponse;
      return { ok: true };
    },
  };
  const context = vm.createContext({ document, window, chrome: { runtime }, navigator: { clipboard: { writeText: async (text) => { clipboardTexts.push(text); } } }, console });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../sidepanel.js"), "utf8"), context);
  return { elements, records, context, imports, messages, clipboardTexts };
}
function allText(el) { return [el.textContent, ...el.children.map(allText)].join(" "); }
async function settled() { await new Promise(setImmediate); await new Promise(setImmediate); }

test("side panel loads Drive items on open and displays them", async () => {
  const { elements } = panelHarness(false);
  await settled();
  assert.match(allText(elements.tree), /Synced/);
  assert.match(allText(elements.tree), /Empty Folder/);
  assert.match(elements.status.textContent, /1 snippets/);
  assert.equal(elements.empty.hidden, true);
});

test("side panel shows Drive errors rather than claiming an empty library", async () => {
  const { elements } = panelHarness(true);
  await settled();
  assert.match(elements.status.textContent, /403 denied/);
  assert.match(elements.empty.querySelector("p").textContent, /Connect/);
  assert.equal(elements["btn-text"].disabled, true);
});

test("side panel keeps unreadable Drive items visible and reports the error", async () => {
  const { elements } = panelHarness("partial");
  await settled();
  assert.match(allText(elements.tree), /Synced.*⚠/);
  assert.match(allText(elements.tree), /Empty Folder/);
  assert.match(elements.status.textContent, /unreadable.*403 denied/);
  const row = elements.tree.children.find((r) => allText(r).includes("Synced"));
  const editButton = row.children.find((child) => (child.title || "").startsWith("Cannot edit:"));
  assert.ok(editButton.disabled);
  row.listeners.click();
  await settled();
  assert.match(elements.status.textContent, /403 denied/);
});

test("side panel creates, edits and trashes files directly in Drive", async () => {
  const { elements, records } = panelHarness(false);
  await settled();
  elements["btn-text"].listeners.click();
  elements["editor-name"].value = "New";
  elements["editor-text"].value = "written remotely";
  await elements["editor-save"].listeners.click();
  assert.equal(records.find((r) => r.name === "New.txt").text, "written remotely");
  assert.match(allText(elements.tree), /New/);
  const row = elements.tree.children.find((r) => allText(r).includes("Synced"));
  row.children.find((child) => child.title === "Edit").listeners.click({ stopPropagation() {} });
  elements["editor-text"].value = "updated remotely";
  await elements["editor-save"].listeners.click();
  assert.equal(records.find((r) => r.id === "item1").text, "updated remotely");
  const updatedRow = elements.tree.children.find((r) => allText(r).includes("Synced"));
  const deleteButton = updatedRow.children.find((child) => child.title === "Delete");
  await deleteButton.listeners.click({ stopPropagation() {} });
  await settled();
  assert.equal(records.find((r) => r.id === "item1").trashed, true);
  assert.doesNotMatch(allText(elements.tree), /Synced/);
});

test("configuration dialog opens before choosing a manifest", async () => {
  const { elements } = panelHarness(false);
  await settled();
  elements["btn-config"].listeners.click();
  assert.equal(elements["config-overlay"].hidden, false);
  elements["config-close"].listeners.click();
  assert.equal(elements["config-overlay"].hidden, true);
});

test("side panel shows discovered Drive items before downloads finish", async () => {
  let resume;
  const waiting = new Promise((resolve) => { resume = resolve; });
  const { elements } = panelHarness(false, { beforeDownload: () => waiting });
  await new Promise((resolve) => setTimeout(resolve, 140));
  assert.match(elements["drive-status"].textContent, /loading/);
  assert.match(allText(elements.tree), /Synced/);
  assert.match(allText(elements.tree), /Empty Folder/);
  assert.match(elements.status.textContent, /snippets found/);
  resume();
  await settled();
  assert.match(elements["drive-status"].textContent, /connected/);
});

test("select mode prevents paste, moves multiple selected items and deletes them", async () => {
  const records = ["A", "B", "C", "D"].map((name) => ({ id: name, name: name + ".txt", mimeType: "text/plain", text: name, parent: "root" }));
  const { elements, messages } = panelHarness(false, { records });
  await settled();
  const rowFor = (name) => elements.tree.children.find((row) => row.children.some((c) => c.className === "label" && c.textContent.endsWith(name)));
  rowFor("A").listeners.click();
  await settled();
  assert.equal(messages.filter((m) => m.type === "pastex-sidepanel-insert").length, 1);
  elements["btn-select"].listeners.click();
  assert.equal(elements["selection-actions"].hidden, false);
  rowFor("B").listeners.click();
  rowFor("C").listeners.click();
  assert.equal(elements["selection-count"].textContent, "2 selected");
  assert.equal(messages.filter((m) => m.type === "pastex-sidepanel-insert").length, 1);
  assert.equal(elements["btn-up"].disabled, false);
  elements["btn-up"].listeners.click();
  await settled();
  await settled();
  assert.deepEqual(elements.tree.children.map((row) => row.children.find((c) => c.className === "label").textContent.replace(/^[☑☐] /, "")), ["B", "C", "A", "D"]);
  assert.deepEqual(messages.find((m) => m.type === "pastex-panel-mutate" && m.command.action === "reorder").command.groups[0].join(","), "B,C,A,D");
  assert.ok(messages.some((m) => m.type === "pastex-panel-library" && m.force));
  elements["btn-delete-selected"].listeners.click();
  await settled(); await settled();
  assert.equal(records.find((r) => r.id === "B").trashed, true);
  assert.equal(records.find((r) => r.id === "C").trashed, true);
  assert.equal(records.find((r) => r.id === "A").trashed, undefined);
  assert.equal(elements["selection-count"].textContent, "0 selected");
  elements["btn-select"].listeners.click();
  assert.equal(elements["selection-actions"].hidden, true);
});

test("text rows expose rename, edit, delete in order without an insert action or preview", async () => {
  const { elements, messages } = panelHarness(false);
  await settled();
  let row = elements.tree.children.find((r) => allText(r).includes("Synced"));
  const buttons = row.children.filter((child) => child.className === "mini");
  assert.deepEqual(buttons.map((child) => child.title), ["Rename", "Edit", "Delete"]);
  buttons[1].listeners.click({ stopPropagation() {} });
  assert.equal(elements["editor-overlay"].hidden, false);
  assert.equal(elements["editor-text"].value, "from Google Drive");
  assert.equal(messages.filter((m) => m.type === "pastex-sidepanel-insert").length, 0);
  elements["editor-cancel"].listeners.click();
  elements.search.value = "Synced";
  elements.search.listeners.input({ target: elements.search });
  await new Promise((resolve) => setTimeout(resolve, 170));
  row = elements.tree.children[0];
  assert.deepEqual(row.children.filter((child) => child.className === "mini").map((child) => child.title), ["Rename", "Edit", "Delete"]);
  row.listeners.click();
  await settled();
  assert.equal(messages.filter((m) => m.type === "pastex-sidepanel-insert").length, 1);
});

test("a failed insertion does not silently copy the snippet", async () => {
  const { elements, clipboardTexts } = panelHarness(false, { insertResponse: { ok: false, reason: "page-rejected" } });
  await settled();
  const row = elements.tree.children.find((r) => allText(r).includes("Synced"));
  row.listeners.click();
  await settled();
  assert.deepEqual(clipboardTexts, []);
  assert.match(elements.status.textContent, /Insert failed/);
});

test("clipboard fallback runs only when no editable field is focused", async () => {
  const { elements, clipboardTexts } = panelHarness(false, { insertResponse: { ok: false, reason: "no-editable" } });
  await settled();
  const row = elements.tree.children.find((r) => allText(r).includes("Synced"));
  row.listeners.click();
  await settled();
  assert.deepEqual(clipboardTexts, ["from Google Drive"]);
  assert.match(elements.status.textContent, /copied/);
});

test("manifest picker loads one JSON with links and refreshes sites and Drive", async () => {
  const { elements, imports, messages } = panelHarness(false);
  await elements["choose-manifest"].listeners.click();
  await settled();
  assert.deepEqual(imports, ["{}"]);
  assert.ok(messages.some((m) => m.type === "pastex-reload-sites"));
  assert.ok(messages.some((m) => m.type === "pastex-panel-library" && m.force));
  assert.match(elements["config-status"].textContent, /Using manifest config\.json/);
  assert.match(elements["config-status"].textContent, /linked drive\.json/);
});

test("canceling the manifest picker leaves configuration unchanged", async () => {
  const { elements, imports } = panelHarness(false, { manifestPicker: async () => { const e = new Error("canceled"); e.name = "AbortError"; throw e; } });
  await elements["choose-manifest"].listeners.click();
  await settled();
  assert.equal(imports.length, 0);
  assert.match(elements["config-status"].textContent, /unchanged/);
});
