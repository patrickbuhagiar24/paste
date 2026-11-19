/* Pastex background service worker (Manifest V3).
 *
 *  - Toolbar click opens the Side Panel.
 *  - The in-page ⚡ button opens the Drive picker on approved sites.
 *  - Content scripts are injected on demand for tabs that were already
 *    open when the extension was installed or updated.
 */

importScripts("native.js", "sites.js", "drive.js", "library.js", "cache.js");

var libraryCache = PastexCache.create(PastexNative, PastexDrive, PastexLibrary, chrome.storage.session, crypto);
var progressPorts = new Set();
var progressTimer = 0;
var pendingProgress = null;

function trustedPanel(sender) {
  return sender && sender.id === chrome.runtime.id && sender.url === chrome.runtime.getURL("sidepanel.html");
}

chrome.runtime.onConnect.addListener(function (port) {
  if (port.name !== "pastex-library-progress" || !trustedPanel(port.sender)) { port.disconnect(); return; }
  progressPorts.add(port);
  port.onDisconnect.addListener(function () { progressPorts.delete(port); });
});

function broadcastProgress(tree, rootId) {
  if (!progressPorts.size) return;
  pendingProgress = { tree: tree, rootId: rootId };
  if (progressTimer) return;
  progressTimer = setTimeout(function () {
    progressTimer = 0;
    var progress = pendingProgress;
    pendingProgress = null;
    progressPorts.forEach(function (port) {
      try { port.postMessage(progress); } catch (ignored) { progressPorts.delete(port); }
    });
  }, 100);
}

function clearProgress() {
  if (progressTimer) clearTimeout(progressTimer);
  progressTimer = 0;
  pendingProgress = null;
}

var CONTENT_SCRIPT_ID = "pastex-allowed-sites";

async function allowedSitePatterns() {
  var data = await PastexNative.readSites();
  return PastexSites.parse(data.allowedSites);
}

// Register only user-approved origins. Empty or invalid configuration and
// native host errors are fail-closed: no scripts are registered.
async function configureSites() {
  var patterns = [];
  var configError = null;
  PastexNative.resetCache();
  try {
    patterns = await allowedSitePatterns();
  } catch (err) {
    console.warn("Pastex site configuration unavailable:", err.message);
    configError = err;
  }
  var existing = await chrome.scripting.getRegisteredContentScripts({ ids: [CONTENT_SCRIPT_ID] });
  if (existing.length) await chrome.scripting.unregisterContentScripts({ ids: [CONTENT_SCRIPT_ID] });
  if (configError) throw configError;
  if (patterns.length) {
    await chrome.scripting.registerContentScripts([{
      id: CONTENT_SCRIPT_ID, matches: patterns, js: ["content.js"], css: ["content.css"],
      runAt: "document_idle", persistAcrossSessions: false,
    }]);
  }
}

function activeTab() {
  return chrome.tabs.query({ active: true, currentWindow: true }).then(function (tabs) {
    return tabs && tabs[0] ? tabs[0] : null;
  });
}

// Send a message to the tab's content script, injecting it first when the
// tab predates the installation (chrome:// pages cannot be injected).
async function messageTab(tabId, message) {
  var tab = await chrome.tabs.get(tabId);
  var patterns;
  try { patterns = await allowedSitePatterns(); } catch (err) { patterns = []; }
  if (!tab || !PastexSites.allows(patterns, tab.url)) {
    throw new Error("site-not-allowed");
  }
  try {
    return await chrome.tabs.sendMessage(tabId, message);
  } catch (firstErr) {
    await chrome.scripting.executeScript({ target: { tabId: tabId }, files: ["content.js"] });
    await chrome.scripting.insertCSS({ target: { tabId: tabId }, files: ["content.css"] });
    return chrome.tabs.sendMessage(tabId, message);
  }
}

async function insertIntoActiveTab(id, text) {
  var tab = await activeTab();
  if (!tab) {
    return { ok: false };
  }
  try {
    var response = await messageTab(tab.id, { type: "pastex-insert", id: id, text: text });
    return response || { ok: false };
  } catch (err) {
    return { ok: false, reason: String((err && err.message) || err) };
  }
}

// The worker serializes Drive writes and invalidates the shared cache before
// and after every write, including partial failures. Only the panel can write.
async function mutateDrive(command) {
  return libraryCache.mutate(async function () {
    var session = await PastexLibrary.connect(PastexNative, PastexDrive);
    if (!command.rootId || session.rootId !== command.rootId) throw new Error("Drive folder changed; refresh the list first.");
    var token = session.token;
    if (command.action === "save") {
      var mode = command.mode, name = command.name, node = command.node;
      if (typeof name !== "string" || !name.trim() || name.indexOf("/") !== -1 || !command.parentId)
        throw new Error("Invalid name or parent folder.");
      var driveName = mode === "new-text" ? name.replace(/\.txt$/i, "") + ".txt" : name;
      if (node && node.kind === "text") driveName = name.replace(/\.txt$/i, "") + (/\.txt$/i.test(node.driveName || "") ? ".txt" : "");
      if (!node || (driveName !== node.driveName && driveName !== node.name)) {
        var children = await PastexDrive.listChildren(token, command.parentId);
        if (children.some(function (child) { return child.name === driveName && child.id !== (node && node.id); }))
          throw new Error("An item with that name already exists in this folder.");
      }
      if (mode === "new-folder") return { id: await PastexDrive.createFolder(token, name, command.parentId) };
      if (mode === "new-text") return { id: (await PastexDrive.uploadNew(token, command.parentId, name.replace(/\.txt$/i, ""), command.text)).id };
      if (!node || !node.id || ["edit-text", "rename-text", "rename-folder"].indexOf(mode) === -1) throw new Error("Invalid edit request.");
      if (mode === "edit-text" && command.text !== node.text) await PastexDrive.updateText(token, node.id, command.text);
      if (driveName !== (node.driveName || node.name)) await PastexDrive.renameFile(token, node.id, driveName);
      return { id: node.id };
    }
    if (command.action === "delete") {
      var failures = [];
      for (var i = 0; i < command.ids.length; i++) {
        try { await PastexDrive.trashFile(token, command.ids[i]); }
        catch (err) { failures.push(err.message || String(err)); }
      }
      if (failures.length) throw new Error(failures.length + " item(s) could not be deleted: " + failures[0]);
      return {};
    }
    if (command.action === "reorder") {
      for (var j = 0; j < command.groups.length; j++) {
        var ids = command.groups[j];
        for (var k = 0; k < ids.length; k++) await PastexDrive.setOrder(token, ids[k], k);
      }
      return {};
    }
    throw new Error("Unknown Drive operation.");
  });
}

// --- events ---

function initializeUserConfig() {
  configureSites().catch(function (err) {
    console.warn("Pastex site registration failed:", err.message);
  });
}

chrome.runtime.onInstalled.addListener(initializeUserConfig);
chrome.runtime.onStartup.addListener(initializeUserConfig);

chrome.action.onClicked.addListener(async function (tab) {
  if (tab && tab.windowId !== undefined) {
    await chrome.sidePanel.open({ windowId: tab.windowId });
  }
});

// Content scripts re-check the current allowlist before showing or using the
// picker. This also disables an already-open page when its site is removed.
chrome.runtime.onMessage.addListener(function (message, sender, sendResponse) {
  if (message && message.type === "pastex-reload-sites") {
    // Only the extension's own panel can request registration changes.
    if (!trustedPanel(sender)) {
      sendResponse({ ok: false, error: "Unauthorized request." });
      return false;
    }
    configureSites().then(function () { sendResponse({ ok: true }); })
      .catch(function (err) { sendResponse({ ok: false, error: err.message || String(err) }); });
    return true;
  }
  if (message && message.type === "pastex-site-allowed") {
    allowedSitePatterns().then(function (patterns) {
      sendResponse({ allowed: !!(sender.tab && PastexSites.allows(patterns, sender.url)) });
    }).catch(function () { sendResponse({ allowed: false }); });
    return true;
  }
  if (message && message.type === "pastex-get-library") {
    allowedSitePatterns().then(async function (patterns) {
      if (!sender.tab || !PastexSites.allows(patterns, sender.url)) throw new Error("Site not allowed.");
      var result = await libraryCache.get(false, broadcastProgress);
      clearProgress();
      // A site can be removed while a slow load is in flight.
      var currentPatterns = await allowedSitePatterns();
      if (!sender.tab || !PastexSites.allows(currentPatterns, sender.url)) throw new Error("Site not allowed.");
      sendResponse({ ok: true, tree: result.tree, warnings: result.warnings });
    }).catch(function (err) { clearProgress(); sendResponse({ ok: false, error: err.message || String(err) }); });
    return true;
  }
  if (message && (message.type === "pastex-panel-library" || message.type === "pastex-panel-mutate")) {
    if (!trustedPanel(sender)) { sendResponse({ ok: false, error: "Unauthorized request." }); return false; }
    clearProgress();
    var operation = message.type === "pastex-panel-library" ?
      libraryCache.get(!!message.force, broadcastProgress) : mutateDrive(message.command || {});
    operation.then(function (result) { clearProgress(); sendResponse({ ok: true, result: result }); })
      .catch(function (err) { clearProgress(); sendResponse({ ok: false, error: err.message || String(err) }); });
    return true;
  }
  if (!message || message.type !== "pastex-sidepanel-insert") {
    return false;
  }
  if (!trustedPanel(sender)) { sendResponse({ ok: false, reason: "unauthorized" }); return false; }
  insertIntoActiveTab(message.id, message.text || "").then(function (response) {
    sendResponse(response || { ok: false });
  });
  return true;
});
