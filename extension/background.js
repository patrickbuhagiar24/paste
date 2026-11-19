/* Pastex background service worker (Manifest V3).
 *
 *  - Toolbar click opens the Side Panel.
 *  - The in-page ⚡ button opens the side panel on approved sites.
 *  - Content scripts are injected on demand for tabs that were already
 *    open when the extension was installed or updated.
 */

importScripts("sites.js", "config-client.js", "drive.js", "library.js", "cache.js");

var PastexConfig = PastexConfigClient;

var libraryCache = PastexCache.create(PastexConfig, PastexDrive, PastexLibrary, chrome.storage.session, crypto);
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
// A short-lived capability bridges async allowlist reads to Chrome's required
// synchronous user-gesture call to sidePanel.open. Insertion still rechecks
// the live allowlist; a stale capability can never authorize insertion.
var panelGrants = new Map();
var siteRevision = 0;
function grantKey(sender) { return sender.tab.id + ":" + (sender.frameId || 0); }
function invalidateGrants() { panelGrants.clear(); siteRevision++; }

async function allowedSitePatterns() {
  var data = await PastexConfig.readSites();
  return PastexSites.parse(data.allowedSites);
}

// Register only user-approved origins. Empty or invalid configuration and
// folder-access errors are fail-closed: no scripts are registered.
async function configureSites() {
  invalidateGrants();
  var patterns = [];
  var configError = null;
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
    var session = await PastexLibrary.connect(PastexConfig, PastexDrive);
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

// Content scripts must authenticate on an allowed site before opening a panel.
chrome.runtime.onMessage.addListener(function (message, sender, sendResponse) {
  if (message && message.type === "pastex-reload-sites") {
    if (!trustedPanel(sender)) { sendResponse({ ok: false, error: "Unauthorized request." }); return false; }
    configureSites().then(function () { sendResponse({ ok: true }); })
      .catch(function (err) { sendResponse({ ok: false, error: err.message || String(err) }); });
    return true;
  }
  if (message && message.type === "pastex-open-panel") {
    if (!sender || sender.id !== chrome.runtime.id || !sender.tab || sender.frameId !== 0) {
      sendResponse({ ok: false }); return false;
    }
    var grant = panelGrants.get(grantKey(sender));
    if (!grant || grant.token !== message.capability || grant.url !== sender.url || grant.tabUrl !== sender.tab.url || grant.expires < Date.now() ||
        grant.revision !== siteRevision || !Number.isInteger(sender.tab.windowId)) {
      sendResponse({ ok: false }); return false;
    }
    // Do not await anything before this call: Chrome requires the user's
    // gesture, which arrives with the content script's mousedown message.
    try {
      chrome.sidePanel.open({ windowId: sender.tab.windowId }).then(function () { sendResponse({ ok: true }); })
        .catch(function () { sendResponse({ ok: false }); });
    } catch (err) { sendResponse({ ok: false }); return false; }
    return true;
  }
  if (message && message.type === "pastex-site-allowed") {
    if (!sender || sender.id !== chrome.runtime.id || !sender.tab || sender.frameId !== 0 || typeof sender.url !== "string") {
      sendResponse({ allowed: false }); return false;
    }
    var revision = siteRevision, key = grantKey(sender);
    allowedSitePatterns().then(function (patterns) {
      var allowed = revision === siteRevision && PastexSites.allows(patterns, sender.url) &&
        PastexSites.allows(patterns, sender.tab.url);
      if (!allowed) { panelGrants.delete(key); sendResponse({ allowed: false }); return; }
      var token = crypto.randomUUID();
      panelGrants.set(key, { token: token, url: sender.url, tabUrl: sender.tab.url, expires: Date.now() + 30000, revision: revision });
      sendResponse({ allowed: true, capability: token });
    }).catch(function () { panelGrants.delete(key); sendResponse({ allowed: false }); });
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
