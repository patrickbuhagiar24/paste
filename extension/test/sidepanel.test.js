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
  appendChild(child) {
    if (child.tagName === "fragment") {
      this.children.push(...child.children);
      child.children = [];
    } else {
      this.children.push(child);
    }
    return child;
  }
  removeChild(child) { this.children.splice(this.children.indexOf(child), 1); }
  get firstChild() { return this.children[0]; }
  setAttribute(name, value) { this[name] = value; }
  removeAttribute(name) { delete this[name]; }
  querySelector() { return this.paragraph || (this.paragraph = new Element("p")); }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  focus() {}
  select() {}
}

function panelHarness(fail, options = {}) {
  const ids = ["search", "tree", "empty", "drive-status", "last-checked", "btn-sync", "btn-collapse-all", "btn-folder", "btn-text", "btn-empty-text", "editor-title", "editor-parent-label", "editor-parent", "editor-text-wrap", "editor-name", "editor-text", "editor-overlay", "editor-save", "editor-cancel", "status", "btn-config", "btn-select", "selection-actions", "selection-count", "btn-up", "btn-down", "btn-delete-selected", "btn-close-panel"];
  const elements = Object.fromEntries(ids.map((id) => [id, new Element("div")]));
  // The real markup starts with the editor dialog closed.
  elements["editor-overlay"].hidden = true;
  elements["selection-actions"].hidden = true;
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
  let pickerCalls = 0;
  const clipboardTexts = [];
  const native = {
    readConfig: async () => ({ drive: { folderId: "root" }, serviceAccount: { client_email: "x", private_key: "y" } }),
    resetCache() {},
    importConfigs: async (files) => { imports.push(files); return options.importResult || { imported: Object.keys(files), error: "" }; },
  };
  const document = { getElementById: (id) => elements[id], createElement: (tag) => new Element(tag), createDocumentFragment: () => new Element("fragment"), addEventListener() {} };
  const window = { PastexStore: Store, PastexDrive: drive, PastexNative: native, PastexLibrary: Library, PastexConfigImport: ConfigImport,
    PastexConfigSource: { create: () => ({
      chooseManifestText: async (text) => { imports.push(text); return { drive: "linked drive.json", account: "linked account.json", sites: "linked sites.json" }; },
    }) },
    PastexHandleStore: {}, PastexSites: {}, PastexManifest: {},
    showOpenFilePicker: async (opts) => {
      pickerCalls++;
      return options.manifestPicker ? options.manifestPicker(opts) : [{ getFile: async () => ({ name: "config.json", text: async () => "{}" }) }];
    },
    confirm: () => options.confirm !== false, setTimeout, clearTimeout,
    addEventListener() {}};
  let progress = () => {};
  let cache;
  const runtime = {
    connect: () => ({ onMessage: { addListener(fn) { progress = fn; } } }),
    sendMessage: async (message) => {
      messages.push(message);
      if (message.type === "pastex-panel-library") {
        if (options.libraryResult) return options.libraryResult;
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
          } else if (c.action === "collapse") {
            for (const id of c.ids) {
              const folder = records.find((r) => r.id === id);
              if (folder) folder.properties = { ...(folder.properties || {}), pastex_collapsed: c.collapsed ? "1" : "0" };
            }
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
  return { elements, records, context, imports, messages, clipboardTexts, pickerCalls: () => pickerCalls,
    progress: (message) => progress(message) };
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

test("Config opens the JSON file picker immediately", async () => {
  const { elements, pickerCalls } = panelHarness(false);
  await settled();
  elements["btn-config"].listeners.click();
  assert.equal(pickerCalls(), 1);
  await settled();
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
  assert.equal(messages.find((m) => m.type === "pastex-panel-mutate" && m.command.action === "reorder").command.parents[0], "root");
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
  await elements["btn-config"].listeners.click();
  assert.deepEqual(imports, ["{}"]);
  assert.ok(messages.some((m) => m.type === "pastex-reload-sites"));
  assert.ok(messages.some((m) => m.type === "pastex-panel-library" && m.force));
  assert.match(elements.status.textContent, /Using manifest config\.json/);
  assert.match(elements.status.textContent, /linked drive\.json/);
});

test("canceling the manifest picker leaves configuration unchanged", async () => {
  const { elements, imports } = panelHarness(false, { manifestPicker: async () => { const e = new Error("canceled"); e.name = "AbortError"; throw e; } });
  await elements["btn-config"].listeners.click();
  assert.equal(imports.length, 0);
  assert.match(elements.status.textContent, /unchanged/);
});

test("collapse all folds every folder and clears an active search", async () => {
  const { elements } = panelHarness(false);
  await settled();
  assert.equal(elements["btn-collapse-all"].disabled, false);
  elements.search.value = "Synced";
  elements.search.listeners.input({ target: elements.search });
  await new Promise((resolve) => setTimeout(resolve, 170));
  await elements["btn-collapse-all"].listeners.click();
  await settled(); await settled();
  assert.equal(elements.search.value, "");
  assert.match(elements.status.textContent, /Google Drive loaded/);
  const folderRow = elements.tree.children.find((row) => allText(row).includes("Empty Folder"));
  assert.equal(folderRow.children[0].textContent, "▸");
});

test("collapsed folders loaded from Drive stay collapsed in a new panel", async () => {
  const records = [{ id: "f", name: "Saved Folder", mimeType: "folder", parent: "root", properties: { pastex_collapsed: "1" } }];
  const { elements } = panelHarness(false, { records });
  await settled();
  const row = elements.tree.children[0];
  assert.equal(row.children[0].textContent, "▸");
});

test("session cache count is shown in the drive bar, not the footer", async () => {
  const tree = [{ id: "a", kind: "text", name: "A", text: "A" }, { id: "b", kind: "text", name: "B", text: "B" }];
  const { elements } = panelHarness(false, { libraryResult: { ok: true, result: { tree, rootId: "root", cached: true, warnings: [] } } });
  await settled();
  assert.match(elements["drive-status"].textContent, /Google Drive • session cache · 2 snippets/);
  assert.doesNotMatch(elements.status.textContent, /session cache/i);
  assert.match(elements.status.textContent, /Ready\./);
});

test("a background sync notification repaints the panel from the cache", async () => {
  const tree = [{ id: "a", kind: "text", name: "Alpha", text: "A" }];
  const { elements, progress } = panelHarness(false, { libraryResult: { ok: true, result: { tree, rootId: "root", cached: true, warnings: [] } } });
  await settled();
  elements["drive-status"].textContent = "";
  elements.status.textContent = "";
  progress({ changed: true });
  await settled();
  await settled();
  assert.match(allText(elements.tree), /Alpha/);
  assert.match(elements["drive-status"].textContent, /Google Drive • session cache · 1 snippets/);
  assert.equal(elements.status.textContent, "", "a background refresh must not spam the status line");
  assert.equal(elements["btn-sync"].disabled, false, "a background refresh must not disable the toolbar");
});

test("the toolbar moves the create buttons to the end", () => {
  const html = fs.readFileSync(path.join(__dirname, "../sidepanel.html"), "utf8");
  const toolbar = html.split('<div class="toolbar"')[1].split("</div>")[0];
  const order = [...toolbar.matchAll(/<button id="(btn-[a-z-]+)"/g)].map((match) => match[1])
    .filter((id) => id !== "btn-close-panel");
  assert.deepEqual(order, ["btn-collapse-all", "btn-select", "btn-sync", "btn-config", "btn-text", "btn-folder"]);
});

test("the panel shows when the library was last checked", async () => {
  const tree = [{ id: "a", kind: "text", name: "Alpha", text: "A" }];
  const checked = Date.now() - 120000;
  const { elements } = panelHarness(false, {
    libraryResult: { ok: true, result: { tree, rootId: "root", cached: true, warnings: [], lastCheckedAt: checked } },
  });
  await settled();
  assert.match(elements["last-checked"].textContent, /Checked: 2 min ago/);
  assert.match(elements["last-checked"].title, /last checked at/);
});

test("the panel says when the library was never checked", async () => {
  const { elements } = panelHarness(false);
  await settled();
  assert.match(elements["last-checked"].textContent, /Checked: never/);
});

test("a background update reports the moment of the check", async () => {
  const { elements, progress } = panelHarness(false, { libraryResult: { ok: false, error: "offline" } });
  await settled();
  elements["last-checked"].textContent = "";
  progress({ changed: true, lastCheckedAt: Date.now() - 300000 });
  await settled();
  assert.match(elements["last-checked"].textContent, /Checked: 5 min ago/);
});

test("the manual refresh button is still the fallback for a stale library", async () => {
  const tree = [{ id: "a", kind: "text", name: "Alpha", text: "A" }];
  const { elements, messages } = panelHarness(false, {
    libraryResult: { ok: true, result: { tree, rootId: "root", cached: true, warnings: [], lastCheckedAt: Date.now() } },
  });
  await settled();
  elements["btn-sync"].listeners.click();
  await settled();
  assert.ok(messages.some((message) => message.type === "pastex-panel-library" && message.force === true));
});

test("a cached open with a row missing its body asks the worker to fill it", async () => {
  const tree = [{ id: "a", kind: "text", name: "Alpha", text: "" }];
  const { elements, messages } = panelHarness(false, {
    libraryResult: { ok: true, result: { tree, rootId: "root", cached: true, warnings: [], pending: 1 } },
  });
  await settled();
  await settled();
  assert.ok(messages.some((m) => m.type === "pastex-panel-fill"),
    "the panel must ask for the rows the snapshot is missing");
  assert.match(allText(elements.tree), /Alpha/, "the list is shown before the fill");
});

test("a complete cached open does not ask for a fill", async () => {
  const tree = [{ id: "a", kind: "text", name: "Alpha", text: "A" }];
  const { messages } = panelHarness(false, {
    libraryResult: { ok: true, result: { tree, rootId: "root", cached: true, warnings: [], pending: 0 } },
  });
  await settled();
  await settled();
  assert.equal(messages.filter((m) => m.type === "pastex-panel-fill").length, 0);
});

test("the panel offers Close only when the browser can close a side panel", async () => {
  const script = fs.readFileSync(path.join(__dirname, "../sidepanel.js"), "utf8");
  assert.match(script, /typeof chrome\.sidePanel\.close === "function"/,
    "closing is feature-detected, because sidePanel.close() is Chrome 141+");
  const html = fs.readFileSync(path.join(__dirname, "../sidepanel.html"), "utf8");
  assert.ok(/<button id="btn-close-panel"[^>]*hidden/.test(html),
    "the button starts hidden and is revealed only when usable");
});
