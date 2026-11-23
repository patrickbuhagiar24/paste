"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const Store = require("../store.js");
const Library = require("../library.js");
const Queue = require("../queue.js");

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
  const ids = ["search", "tree", "empty", "drive-bar", "drive-dot", "drive-status", "last-checked", "btn-sync", "btn-collapse-all", "btn-folder", "btn-text", "btn-empty-text", "editor-title", "editor-parent-label", "editor-parent", "editor-text-wrap", "editor-name", "editor-text", "editor-overlay", "editor-save", "editor-cancel", "status", "btn-config", "btn-select", "selection-actions", "selection-count", "btn-up", "btn-down", "btn-move-selected", "btn-delete-selected", "row-menu", "editor-name-label"];
  const elements = Object.fromEntries(ids.map((id) => [id, new Element("div")]));
  // The real markup starts with the editor dialog closed.
  elements["editor-overlay"].hidden = true;
  elements["selection-actions"].hidden = true;
  elements["row-menu"].hidden = true;
  const records = options.records || [
    { id: "item1", name: "Synced.txt", mimeType: "text/plain", text: "from Google Drive", parent: "root" },
    { id: "folder1", name: "Empty Folder", mimeType: "folder", parent: "root" },
  ];
  let seq = 0;
  const drive = {
    FOLDER_MIME: "folder", TEXT_MIME: "text/plain",
    parseServiceAccount: JSON.parse, fetchAccessToken: async () => "token",
    getFolder: async () => {
      if (fail === true) throw new Error("403 denied");
      // A test can fold folders through the root label, exactly like Drive.
      const collapsed = {};
      (options.collapsed || []).forEach((id) => { collapsed[id] = "1"; });
      return { mimeType: "folder", properties: { pastex_index: JSON.stringify({ v: 2, order: {}, collapsed }) } };
    },
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
  const window = { PastexStore: Store, PastexDrive: drive, PastexNative: native, PastexLibrary: Library,
    PastexConfigSource: { create: () => ({
      chooseFolder: async (handle) => {
        imports.push(handle.name);
        return { drive: "folder drive.json", account: "folder account.json", sites: "folder sites.json" };
      },
    }) },
    PastexSites: {},
    showDirectoryPicker: async (opts) => {
      pickerCalls++;
      return options.folderPicker ? options.folderPicker(opts) : { name: "my-config" };
    },
    confirm: () => options.confirm !== false, setTimeout, clearTimeout,
    addEventListener() {}};
  let progress = () => {};
  let cache;
  const queue = [];
  // Apply an edit to the cached snapshot, the way the worker does: the real
  // queue module does the edit, and the test worker keeps the queue.
  function applyLocalEdit(c) {
    if (!cache) throw new Error("The library has not loaded yet.");
    const applied = Queue.apply(cache, c);
    queue.push({ id: "q" + queue.length, kind: c.kind, command: c });
    return { applied, pending: queue.length, queued: true, local: true };
  }
  const runtime = {
    connect: () => ({ onMessage: { addListener(fn) { progress = fn; } } }),
    sendMessage: async (message) => {
      messages.push(message);
      // The worker sends the queue and answers with what it did; it never
      // reads the library for this route.
      if (message.type === "pastex-sync-now") {
        if (options.syncResult) return options.syncResult;
        const sent = queue.length;
        queue.length = 0;
        return { ok: true, result: { flushed: { sent }, deferred: false, pending: 0 } };
      }
      if (message.type === "pastex-panel-library") {
        if (options.libraryResult) return options.libraryResult;
        // The cache is the snapshot with the queued edits already applied, so
        // a repaint after an edit shows the edit without a Drive read.
        if (!message.force && cache) return { ok: true, result: { ...cache, cached: true } };
        try {
          const result = await Library.load(native, drive, (tree, session) => progress({ tree, rootId: session.rootId }));
          cache = { tree: result.tree, rootId: result.session.rootId, warnings: result.warnings };
          return { ok: true, result: { ...cache, cached: false } };
        } catch (err) { return { ok: false, error: err.message }; }
      }
      // An edit is applied to the cached snapshot and queued; the test worker
      // keeps the queue itself, exactly like the real one.
      if (message.type === "pastex-panel-local-edit") {
        const c = message.command;
        // A local edit also drops the "read again" flag, so the repaint that
        // follows every edit reads the edited cache instead of Drive.
        if (cache) cache = { ...cache, cached: true };
        try {
          const result = applyLocalEdit(c);
          return { ok: true, result };
        } catch (err) { return { ok: false, error: err.message }; }
      }
      if (message.type === "pastex-panel-status") {
        return { ok: true, result: { pending: queue.length } };
      }
      if (message.type === "pastex-sidepanel-insert" && options.insertResponse) return options.insertResponse;
      return { ok: true };
    },
  };
  const context = vm.createContext({ document, window, chrome: { runtime, storage: { local: { get: async () => ({}), set: async () => {} } } }, navigator: { clipboard: { writeText: async (text) => { clipboardTexts.push(text); } } }, console });
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
  assert.match(elements.status.textContent, /1 item\(s\) could not be read/);
  const row = elements.tree.children.find((r) => allText(r).includes("Synced"));
  const editButton = row.children.find((child) => child.title === "Edit");
  editButton.listeners.click({ stopPropagation() {} });
  const menu = elements["row-menu"];
  assert.equal(menu.hidden, false, "Edit opens the row menu");
  const editText = menu.children.find((item) => item.textContent === "Edit text");
  assert.ok(editText.disabled, "an item whose text could not be read cannot be rewritten");
  assert.match(editText.title, /Cannot edit: 403 denied/);
  row.listeners.click();
  await settled();
  assert.match(elements.status.textContent, /403 denied/);
});

test("side panel edits the cache and queues the Drive write instead of calling Drive", async () => {
  const { elements, records, messages } = panelHarness(false);
  await settled();
  elements["btn-text"].listeners.click();
  elements["editor-name"].value = "New";
  elements["editor-text"].value = "written remotely";
  await elements["editor-save"].listeners.click();
  await settled();
  // The row is on screen at once, from the cache, and Drive was not touched.
  assert.match(allText(elements.tree), /New/);
  assert.equal(records.some((r) => r.name === "New.txt"), false, "creating must not write Drive while on a site");
  const row = elements.tree.children.find((r) => allText(r).includes("Synced"));
  row.children.find((child) => child.title === "Edit").listeners.click({ stopPropagation() {} });
  elements["row-menu"].children.find((item) => item.textContent === "Edit text").listeners.click({ stopPropagation() {} });
  elements["editor-text"].value = "updated remotely";
  await elements["editor-save"].listeners.click();
  await settled();
  assert.match(allText(elements.tree), /Synced/);
  assert.equal(records.find((r) => r.id === "item1").text, "from Google Drive", "an edit waits in the cache");
  const updatedRow = elements.tree.children.find((r) => allText(r).includes("Synced"));
  const deleteButton = updatedRow.children.find((child) => child.title === "Delete");
  await deleteButton.listeners.click({ stopPropagation() {} });
  await settled();
  assert.doesNotMatch(allText(elements.tree), /Synced/);
  assert.equal(records.find((r) => r.id === "item1").trashed, undefined, "a delete waits in the queue too");
  // The panel never asks the worker for a Drive write.
  assert.equal(messages.some((m) => m.type === "pastex-panel-mutate"), false);
  assert.ok(messages.some((m) => m.type === "pastex-panel-local-edit"));
});

test("Config opens the folder picker immediately", async () => {
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
  const labelOf = (row) => row.children.find((c) => c.className === "label").textContent.replace(/^[☑☐] /, "");
  assert.deepEqual(elements.tree.children.map(labelOf), ["B", "C", "A", "D"]);
  // The order is applied to the cache and queued; nothing is written to Drive
  // while the panel is open on a site.
  const reorder = messages.find((m) => m.type === "pastex-panel-local-edit" && m.command.kind === "reorder");
  assert.equal(reorder.command.groups[0].join(","), "B,C,A,D");
  assert.equal(reorder.command.parents[0], "root");
  assert.equal(records.map((r) => r.name).join(","), "A.txt,B.txt,C.txt,D.txt", "Drive keeps its own order until the flush");
  elements["btn-delete-selected"].listeners.click();
  await settled(); await settled();
  assert.equal(records.find((r) => r.id === "B").trashed, undefined, "a delete waits in the queue");
  assert.equal(records.find((r) => r.id === "C").trashed, undefined);
  assert.doesNotMatch(allText(elements.tree), /B C/, "the rows are gone from the cache at once");
  assert.equal(elements["selection-count"].textContent, "0 selected");
  elements["btn-select"].listeners.click();
  assert.equal(elements["selection-actions"].hidden, true);
});

test("rename and move live in the Edit menu, not in their own row buttons", async () => {
  const { elements, messages } = panelHarness(false);
  await settled();
  let row = elements.tree.children.find((r) => allText(r).includes("Synced"));
  const buttons = row.children.filter((child) => child.className === "mini");
  assert.deepEqual(buttons.map((child) => child.title), ["Edit", "Delete"],
    "the row has one Edit button, not a Rename button next to it");
  // A snippet offers rename, an edit of its text and a move.
  buttons[0].listeners.click({ stopPropagation() {} });
  assert.equal(elements["row-menu"].hidden, false);
  assert.deepEqual(elements["row-menu"].children.map((item) => item.textContent),
    ["Rename", "Edit text", "Move to folder…"]);
  elements["row-menu"].children[1].listeners.click({ stopPropagation() {} });
  assert.equal(elements["editor-overlay"].hidden, false);
  assert.equal(elements["editor-text"].value, "from Google Drive");
  assert.equal(messages.filter((m) => m.type === "pastex-sidepanel-insert").length, 0);
  elements["editor-cancel"].listeners.click();
  // A folder can be renamed and moved, but has no text to edit.
  const folderRow = elements.tree.children.find((r) => allText(r).includes("Empty Folder"));
  folderRow.children.filter((child) => child.className === "mini")[0].listeners.click({ stopPropagation() {} });
  assert.deepEqual(elements["row-menu"].children.map((item) => item.textContent),
    ["Rename folder", "Move to folder…"]);
  elements["row-menu"].children[0].listeners.click({ stopPropagation() {} });
  assert.equal(elements["editor-title"].textContent, "Rename folder");
  elements["editor-cancel"].listeners.click();
  elements.search.value = "Synced";
  elements.search.listeners.input({ target: elements.search });
  await new Promise((resolve) => setTimeout(resolve, 170));
  row = elements.tree.children[0];
  assert.deepEqual(row.children.filter((child) => child.className === "mini").map((child) => child.title), ["Edit", "Delete"]);
  row.listeners.click();
  await settled();
  assert.equal(messages.filter((m) => m.type === "pastex-sidepanel-insert").length, 1);
});

test("Move sends the picked folder, and the row changes folder in the cache at once", async () => {
  const records = [
    { id: "item1", name: "Synced.txt", mimeType: "text/plain", text: "from Google Drive", parent: "root" },
    { id: "folder1", name: "Empty Folder", mimeType: "folder", parent: "root" },
    { id: "folder2", name: "Other Folder", mimeType: "folder", parent: "root" },
  ];
  const { elements, messages, records: live } = panelHarness(false, { records });
  await settled();
  const row = elements.tree.children.find((r) => allText(r).includes("Synced"));
  row.children.find((child) => child.title === "Edit").listeners.click({ stopPropagation() {} });
  elements["row-menu"].children.find((item) => item.textContent === "Move to folder…").listeners.click({ stopPropagation() {} });
  assert.equal(elements["editor-overlay"].hidden, false);
  assert.equal(elements["editor-name"].style.display, "none", "a move never asks for a new name");
  assert.equal(elements["editor-title"].textContent, "Move");
  // The destination list leaves out the folder the row is already in, because
  // moving it there would be a Drive write that changes nothing.
  assert.equal(elements["editor-parent"].children.map((o) => o.textContent).join("|"), "(top level)|Empty Folder|Other Folder");
  elements["editor-parent"].value = "folder2";
  await elements["editor-save"].listeners.click();
  await settled();
  const move = messages.find((m) => m.type === "pastex-panel-local-edit" && m.command.kind === "move");
  assert.equal(move.command.ids.join(","), "item1");
  assert.equal(move.command.parentId, "folder2");
  assert.equal(live.find((r) => r.id === "item1").parent, "root", "Drive waits for the flush");
  // The cache already shows the row inside its new folder. The panel renders a
  // folder's content as a block after the folder's own row, so the top level
  // now holds two folders and no stray snippet row.
  const top = elements.tree.children.filter((child) => child.className === "row");
  assert.equal(top.length, 2, "the snippet is no longer a top-level row");
  const other = top.find((r) => allText(r).includes("Other Folder"));
  const after = elements.tree.children[elements.tree.children.indexOf(other) + 1];
  assert.equal(after.className, "children");
  assert.match(allText(after), /Synced/);
});

test("a folder cannot be moved into itself or into its own content", async () => {
  const records = [
    { id: "outer", name: "Outer", mimeType: "folder", parent: "root" },
    { id: "inner", name: "Inner", mimeType: "folder", parent: "outer" },
    { id: "last", name: "Last", mimeType: "folder", parent: "root" },
  ];
  const { elements } = panelHarness(false, { records });
  await settled();
  const outer = elements.tree.children.find((r) => allText(r).includes("Outer"));
  outer.children.filter((child) => child.className === "mini")[0].listeners.click({ stopPropagation() {} });
  elements["row-menu"].children.find((item) => item.textContent === "Move to folder…").listeners.click({ stopPropagation() {} });
  const offered = elements["editor-parent"].children.map((option) => option.textContent);
  // The folder itself and everything inside it are not destinations.
  assert.equal(offered.join("|"), "(top level)|Last");
});

test("a failed insertion does not silently copy the snippet", async () => {
  const { elements, clipboardTexts } = panelHarness(false, { insertResponse: { ok: false, reason: "page-rejected" } });
  await settled();
  const row = elements.tree.children.find((r) => allText(r).includes("Synced"));
  row.listeners.click();
  await settled();
  assert.deepEqual(clipboardTexts, []);
  assert.match(elements.status.textContent, /Could not insert/);
});

test("clipboard fallback runs only when no editable field is focused", async () => {
  const { elements, clipboardTexts } = panelHarness(false, { insertResponse: { ok: false, reason: "no-editable" } });
  await settled();
  const row = elements.tree.children.find((r) => allText(r).includes("Synced"));
  row.listeners.click();
  await settled();
  assert.deepEqual(clipboardTexts, ["from Google Drive"]);
  assert.match(elements.status.textContent, /Copied/);
});

test("folder picker scans one folder and refreshes sites and Drive", async () => {
  const { elements, imports, messages } = panelHarness(false);
  await elements["btn-config"].listeners.click();
  assert.deepEqual(imports, ["my-config"]);
  assert.ok(messages.some((m) => m.type === "pastex-reload-sites"));
  assert.ok(messages.some((m) => m.type === "pastex-panel-library" && m.force));
  assert.match(elements.status.textContent, /Using folder my-config/);
});

test("canceling the folder picker leaves configuration unchanged", async () => {
  const { elements, imports } = panelHarness(false, { folderPicker: async () => { const e = new Error("canceled"); e.name = "AbortError"; throw e; } });
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
  // The fold is applied to the cache and queued like any other edit.
  assert.match(elements.status.textContent, /Saved/);
  const folderRow = elements.tree.children.find((row) => allText(row).includes("Empty Folder"));
  assert.equal(folderRow.children[0].textContent, "▸");
});

test("collapsed folders recorded in the index stay collapsed in a new panel", async () => {
  // The fold lives in the root label, so the library folds the folder while
  // reading it; the panel just renders what it is given.
  const records = [{ id: "f", name: "Saved Folder", mimeType: "folder", parent: "root" }];
  const { elements } = panelHarness(false, { records, collapsed: ["f"] });
  await settled();
  const row = elements.tree.children[0];
  assert.equal(row.children[0].textContent, "▸");
});

test("session cache count is shown in the drive bar, not the footer", async () => {
  const tree = [{ id: "a", kind: "text", name: "A", text: "A" }, { id: "b", kind: "text", name: "B", text: "B" }];
  const { elements } = panelHarness(false, { libraryResult: { ok: true, result: { tree, rootId: "root", cached: true, warnings: [] } } });
  await settled();
  assert.match(elements["drive-status"].textContent, /Google Drive • session cache · 2 snippets/);
  assert.match(elements.status.textContent, /2 snippets \(cache\)/);
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
  const order = [...toolbar.matchAll(/<button id="(btn-[a-z-]+)"/g)].map((match) => match[1]);
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

test("Sync sends the queue and never reads the library back", async () => {
  const tree = [{ id: "a", kind: "text", name: "Alpha", text: "A" }];
  const { elements, messages } = panelHarness(false, {
    libraryResult: { ok: true, result: { tree, rootId: "root", cached: true, warnings: [], lastCheckedAt: Date.now() } },
    syncResult: { ok: true, result: { flushed: { sent: 2 }, deferred: false, pending: 0 } },
  });
  await settled();
  messages.length = 0;
  elements["btn-sync"].listeners.click();
  await settled();
  assert.ok(messages.some((message) => message.type === "pastex-sync-now"), "Sync asks the worker to send the queue");
  assert.equal(messages.some((message) => message.type === "pastex-panel-library"), false,
    "Sync must not ask for a library read");
  assert.equal(messages.some((message) => message.type === "pastex-panel-library" && message.force === true), false,
    "and it must not force a Drive read");
  assert.match(elements.status.textContent, /Sent 2\./);
});

test("Sync says how many changes are held back while a site is open", async () => {
  const tree = [{ id: "a", kind: "text", name: "Alpha", text: "A" }];
  const { elements, messages } = panelHarness(false, {
    libraryResult: { ok: true, result: { tree, rootId: "root", cached: true, warnings: [], lastCheckedAt: Date.now() } },
    syncResult: { ok: true, result: { flushed: { sent: 0 }, deferred: true, pending: 3 } },
  });
  await settled();
  messages.length = 0;
  elements["btn-sync"].listeners.click();
  await settled();
  assert.equal(messages.some((message) => message.type === "pastex-panel-library"), false);
  assert.match(elements.status.textContent, /3 waiting to sync/);
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

test("the toolbar icon toggles the panel and the panel no longer offers a Close button", async () => {
  const background = fs.readFileSync(path.join(__dirname, "../background.js"), "utf8");
  // Chrome owns the toolbar toggle: openPanelOnActionClick closes the panel on
  // the next click, and no action.onClicked listener competes for the click.
  assert.match(background, /setPanelBehavior\(\{ openPanelOnActionClick: true \}\)/,
    "the toolbar icon must use the panel behavior that toggles");
  assert.doesNotMatch(background, /chrome\.action\.onClicked\.addListener/,
    "an onClicked listener would take the click back from the toggle");
  const html = fs.readFileSync(path.join(__dirname, "../sidepanel.html"), "utf8");
  assert.doesNotMatch(html, /btn-close-panel/, "the panel's own Close button is gone");
  const script = fs.readFileSync(path.join(__dirname, "../sidepanel.js"), "utf8");
  assert.doesNotMatch(script, /sidePanel\.close/, "the panel does not close the side panel itself");
});

