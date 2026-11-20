/* Pastex background service worker (Manifest V3).
 *
 *  - Toolbar click opens the Side Panel.
 *  - The in-page ⚡ button opens the side panel on approved sites.
 *  - Content scripts are injected on demand for tabs that were already
 *    open when the extension was installed or updated.
 */

importScripts("sites.js", "config-client.js", "drive.js", "library.js", "cache.js");

var PastexConfig = PastexConfigClient;

var libraryCache = PastexCache.create(PastexConfig, PastexDrive, PastexLibrary, chrome.storage.session, crypto, chrome.storage.local);
var progressPorts = new Set();
var progressTimer = 0;
var pendingProgress = null;
var panelOperations = 0;
var idleSyncRunning = false;
var idleSyncTimer = 0;
var IDLE_SECONDS = 60;
var IDLE_PERIOD_MINUTES = 2;
var IDLE_SYNC_ALARM = "pastex-idle-sync";
var CONFIG_RETRY_MS = 10 * 60 * 1000;
var nextSyncAt = 0;

// Bounded parallelism for independent Drive writes. A bulk collapse or
// reorder no longer waits for one HTTP round trip per item.
function mapLimit(items, limit, fn) {
  var results = new Array(items.length);
  var next = 0, active = 0, settled = false;
  return new Promise(function (resolve, reject) {
    function finish(error, index, value) {
      if (settled) return;
      if (error) { settled = true; reject(error); return; }
      results[index] = value;
      active--;
      pump();
    }
    function pump() {
      while (!settled && active < limit && next < items.length) {
        var index = next++;
        active++;
        (function (i) {
          Promise.resolve().then(function () { return fn(items[i], i); })
            .then(function (value) { finish(null, i, value); }, function (error) { finish(error); });
        })(index);
      }
      if (!settled && next >= items.length && active === 0) { settled = true; resolve(results); }
    }
    if (!items.length) { settled = true; resolve(results); } else pump();
  });
}

// --- background (idle) synchronization ---
//
// The panel must open instantly, but Drive stays the source of truth. While
// the user works, nothing is fetched in the background. When the machine goes
// idle (no keyboard or mouse input) or the screen is locked, the worker asks
// Drive once, compares the result with the cached snapshot and rewrites the
// cache only when something really changed.

function panelBusy() {
  return panelOperations > 0;
}

function idleState() {
  return new Promise(function (resolve) {
    if (!chrome.idle || typeof chrome.idle.queryState !== "function") { resolve("unknown"); return; }
    try { chrome.idle.queryState(IDLE_SECONDS, function (state) { resolve(state || "unknown"); }); }
    catch (ignored) { resolve("unknown"); }
  });
}

// Notify an open panel that the background sync found new content, so it can
// repaint from the (already fresh) cache without another Drive round trip.
function notifyPanelsChanged() {
  progressPorts.forEach(function (port) {
    try { port.postMessage({ changed: true }); } catch (ignored) { progressPorts.delete(port); }
  });
}

// The single entry point for every background Drive check. The idle gate is
// never bypassed: a check only runs while the machine is idle or locked, so
// the user is never charged for a request while working.
async function runIdleSync(assumeIdle) {
  if (idleSyncRunning) return;
  if (panelBusy()) return;
  // A broken configuration is not worth retrying every minute.
  if (Date.now() < nextSyncAt) return;
  var state = await idleState();
  // "active" means the keyboard was just used: the user is back, so nothing
  // is fetched. An unknown state means the idle API cannot vouch for the
  // user being away; only an idle transition justifies trusting it.
  if (state === "active") return;
  if (!assumeIdle && state !== "idle" && state !== "locked") return;
  if (panelBusy()) return;
  idleSyncRunning = true;
  try {
    var result = await libraryCache.sync();
    nextSyncAt = 0;
    if (result && result.changed) notifyPanelsChanged();
  } catch (err) {
    var message = err && err.message ? err.message : String(err);
    // A missing or broken configuration is not worth retrying every minute.
    if (/configure a|choose a configuration manifest/i.test(message)) nextSyncAt = Date.now() + CONFIG_RETRY_MS;
    else console.warn("Pastex background sync skipped:", message);
  } finally { idleSyncRunning = false; }
}

function scheduleIdleSync(delay, assumeIdle) {
  if (idleSyncTimer) clearTimeout(idleSyncTimer);
  idleSyncTimer = setTimeout(function () { idleSyncTimer = 0; runIdleSync(assumeIdle); }, delay || 0);
}

function ensureIdleAlarm() {
  if (!chrome.alarms || typeof chrome.alarms.create !== "function") return;
  function create() { chrome.alarms.create(IDLE_SYNC_ALARM, { delayInMinutes: 1, periodInMinutes: IDLE_PERIOD_MINUTES }); }
  if (typeof chrome.alarms.get !== "function") { create(); return; }
  chrome.alarms.get(IDLE_SYNC_ALARM, function (alarm) { if (!alarm) create(); });
}

function trackPanelOperation(promise) {
  panelOperations++;
  return Promise.resolve(promise).finally(function () { panelOperations--; });
}

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
var cachedPatterns = null;
var cachedPatternsAt = 0;
var PATTERN_TTL_MS = 30000;
function grantKey(sender) { return sender.tab.id + ":" + (sender.frameId || 0); }
function invalidateGrants() { panelGrants.clear(); siteRevision++; }
function invalidatePatterns() { cachedPatterns = null; cachedPatternsAt = 0; }

// Paste and launcher authorization reuse a short-lived in-memory copy of the
// allowlist. Re-reading the linked JSON on every click was the main insert
// delay; configureSites() and config reloads force a refresh immediately.
async function allowedSitePatterns(force) {
  if (!force && cachedPatterns && Date.now() - cachedPatternsAt < PATTERN_TTL_MS) return cachedPatterns;
  var data = await PastexConfig.readSites(!!force);
  var patterns = PastexSites.parse(data.allowedSites);
  cachedPatterns = patterns;
  cachedPatternsAt = Date.now();
  return patterns;
}

// Register only user-approved origins. Empty or invalid configuration and
// folder-access errors are fail-closed: no scripts are registered.
async function configureSites() {
  invalidateGrants();
  invalidatePatterns();
  var patterns = [];
  var configError = null;
  try {
    patterns = await allowedSitePatterns(true);
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
async function messageTab(tabOrId, message) {
  var tab = typeof tabOrId === "object" && tabOrId !== null ? tabOrId : await chrome.tabs.get(tabOrId);
  var patterns;
  try { patterns = await allowedSitePatterns(); } catch (err) { patterns = []; }
  if (!tab || !PastexSites.allows(patterns, tab.url)) {
    throw new Error("site-not-allowed");
  }
  try {
    return await chrome.tabs.sendMessage(tab.id, message);
  } catch (firstErr) {
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["content.js"] });
    await chrome.scripting.insertCSS({ target: { tabId: tab.id }, files: ["content.css"] });
    return chrome.tabs.sendMessage(tab.id, message);
  }
}

async function insertIntoActiveTab(id, text) {
  var tab = await activeTab();
  if (!tab) {
    return { ok: false };
  }
  try {
    var response = await messageTab(tab, { type: "pastex-insert", id: id, text: text });
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
    var known = command.properties || {};
    if (command.action === "delete") {
      var results = await mapLimit(command.ids || [], 6, function (id) {
        return PastexDrive.trashFile(token, id).then(function () { return ""; },
          function (err) { return err.message || String(err); });
      });
      var failures = results.filter(Boolean);
      if (failures.length) throw new Error(failures.length + " item(s) could not be deleted: " + failures[0]);
      return {};
    }
    if (command.action === "reorder") {
      for (var j = 0; j < command.groups.length; j++) {
        await mapLimit(command.groups[j], 6, function (id, index) {
          return PastexDrive.setOrder(token, id, index, undefined, known[id]);
        });
      }
      return {};
    }
    if (command.action === "collapse") {
      await mapLimit((command.ids || []).filter(Boolean), 6, function (id) {
        return PastexDrive.setCollapsed(token, id, !!command.collapsed, undefined, known[id]);
      });
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
  ensureIdleAlarm();
  // Warm the cache shortly after the browser starts, so the first panel open
  // is instant. This still honours the idle gate: an active user is not
  // charged for a request, and the idle transition triggers the check.
  scheduleIdleSync(3000);
}

chrome.runtime.onInstalled.addListener(initializeUserConfig);
chrome.runtime.onStartup.addListener(initializeUserConfig);

// MV3 service workers are terminated aggressively, so the alarm and the idle
// listeners must be registered at the top level to wake the worker again.
if (chrome.alarms && chrome.alarms.onAlarm) {
  chrome.alarms.onAlarm.addListener(function (alarm) {
    if (alarm && alarm.name === IDLE_SYNC_ALARM) scheduleIdleSync(0);
  });
}
if (chrome.idle) {
  if (chrome.idle.setDetectionInterval) chrome.idle.setDetectionInterval(IDLE_SECONDS);
  if (chrome.idle.onStateChanged) chrome.idle.onStateChanged.addListener(function (state) {
    // The transition itself proves the user stepped away.
    if (state === "idle" || state === "locked") scheduleIdleSync(0, true);
    // The user came back before the check ran: cancel it.
    else if (state === "active" && idleSyncTimer) { clearTimeout(idleSyncTimer); idleSyncTimer = 0; }
  });
}
ensureIdleAlarm();

chrome.action.onClicked.addListener(async function (tab) {
  if (tab && tab.windowId !== undefined) {
    await chrome.sidePanel.open({ windowId: tab.windowId });
  }
});

// Content scripts must authenticate on an allowed site before opening a panel.
chrome.runtime.onMessage.addListener(function (message, sender, sendResponse) {
  if (message && message.type === "pastex-reload-sites") {
    if (!trustedPanel(sender)) { sendResponse({ ok: false, error: "Unauthorized request." }); return false; }
    var refreshed = PastexConfig.invalidate ? PastexConfig.invalidate() : Promise.resolve();
    Promise.resolve(refreshed).catch(function () {}).then(function () { return configureSites(); }).then(function () { sendResponse({ ok: true }); })
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
    var operation = trackPanelOperation(message.type === "pastex-panel-library" ?
      libraryCache.get(!!message.force, broadcastProgress) : mutateDrive(message.command || {}));
    operation.then(function (result) { clearProgress(); sendResponse({ ok: true, result: result }); })
      .catch(function (err) { clearProgress(); sendResponse({ ok: false, error: err.message || String(err) }); });
    return true;
  }
  if (!message || message.type !== "pastex-sidepanel-insert") {
    return false;
  }
  if (!trustedPanel(sender)) { sendResponse({ ok: false, reason: "unauthorized" }); return false; }
  trackPanelOperation(insertIntoActiveTab(message.id, typeof message.text === "string" ? message.text : "")).then(function (response) {
    sendResponse(response || { ok: false });
  });
  return true;
});
