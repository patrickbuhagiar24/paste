"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const Store = require("../store.js");
const Library = require("../library.js");

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
  const ids = ["search", "tree", "empty", "drive-status", "btn-sync", "btn-folder", "btn-text", "btn-empty-text", "preview-title", "preview", "btn-insert", "btn-edit", "editor-title", "editor-parent-label", "editor-parent", "editor-text-wrap", "editor-name", "editor-text", "editor-overlay", "editor-save", "editor-cancel", "status", "btn-config", "config-overlay", "config-status", "config-close", "import-drive", "import-account", "import-sites"];
  const elements = Object.fromEntries(ids.map((id) => [id, new Element("div")]));
  const records = [
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
  const native = {
    readConfig: async () => ({ drive: { folderId: "root" }, serviceAccount: { client_email: "x", private_key: "y" } }),
    resetCache() {},
    importConfig: async (name, data) => { imports.push({ name, data }); },
  };
  const document = { getElementById: (id) => elements[id], createElement: (tag) => new Element(tag), addEventListener() {} };
  const window = { PastexStore: Store, PastexDrive: drive, PastexNative: native, PastexLibrary: Library, confirm: () => options.confirm !== false, setTimeout, clearTimeout };
  const context = vm.createContext({ document, window, chrome: { runtime: { sendMessage: async (message) => { messages.push(message); return { ok: true }; } } }, navigator: { clipboard: { writeText: async () => {} } }, console });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../sidepanel.js"), "utf8"), context);
  return { elements, records, context, imports, messages };
}
function allText(el) { return [el.textContent, ...el.children.map(allText)].join(" "); }
async function settled() { await new Promise(setImmediate); await new Promise(setImmediate); }

test("side panel loads Drive items on open and displays them, without local storage", async () => {
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
  row.listeners.click();
  assert.match(elements.preview.textContent, /403 denied/);
  assert.equal(elements["btn-edit"].disabled, true);
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
  row.listeners.click();
  await settled();
  elements["btn-edit"].listeners.click();
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

test("configuration dialog imports selected JSON and rejects invalid files", async () => {
  const { elements, imports, messages } = panelHarness(false);
  await settled();
  elements["btn-config"].listeners.click();
  assert.equal(elements["config-overlay"].hidden, false);
  const input = elements["import-sites"];
  input.files = [{ size: 30, text: async () => '{"allowed_sites":["https://example.com"]}' }];
  await input.listeners.change({ target: input });
  assert.equal(imports[0].name, "sites.json");
  assert.equal(imports[0].data.allowed_sites[0], "https://example.com");
  assert.equal(messages.at(-1).type, "pastex-reload-sites");
  assert.match(elements["config-status"].textContent, /imported/);
  input.files = [{ size: 10, text: async () => 'bad json' }];
  await input.listeners.change({ target: input });
  assert.equal(imports.length, 1);
  assert.match(elements["config-status"].textContent, /not valid JSON/);
  elements["config-close"].listeners.click();
  assert.equal(elements["config-overlay"].hidden, true);
});

test("configuration import can be canceled without writes", async () => {
  const { elements, imports } = panelHarness(false, { confirm: false });
  await settled();
  const input = elements["import-account"];
  input.files = [{ size: 2, text: async () => '{}' }];
  await input.listeners.change({ target: input });
  assert.equal(imports.length, 0);
  assert.equal(input.value, "");
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
