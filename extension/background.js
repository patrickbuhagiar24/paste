/* Pastex background service worker (Manifest V3).
 *
 *  - The toolbar icon is a toggle: Chrome opens the side panel on click and
 *    closes it on the next click (Chrome 141+), through the panel behavior.
 *  - The in-page ⚡ button opens the side panel on approved sites.
 *  - Content scripts are injected on demand for tabs that were already
 *    open when the extension was installed or updated.
 */

importScripts("sites.js", "config-cache-reader.js", "drive.js", "hash.js", "library.js", "queue.js", "cache.js");

// Configuration is read from the cache the panel wrote when it scanned the
// chosen folder. No offscreen document and no folder handle is involved.
var PastexConfig = PastexConfigCacheReader.create(chrome.storage.local);

// The refresh token lives in storage.local, so a worker that Chrome discarded
// comes back and renews the access token instead of asking for a sign-in.
PastexDrive.setTokenStore(chrome.storage.local);
var libraryCache = PastexCache.create(PastexConfig, PastexDrive, PastexLibrary, chrome.storage.session, crypto, chrome.storage.local);
var deferredQueue = PastexQueue.create(chrome.storage.local);
// Every edit that has not reached Drive yet lives in the queue; a flush sends
// them one at a time while the panel is not using the library.
var flushRunning = false;
var progressPorts = new Set();
var progressTimer = 0;
var pendingProgress = null;
var panelOperations = 0;
var idleSyncRunning = false;
var idleSyncTimer = 0;
var IDLE_SECONDS = 60;
var IDLE_PERIOD_MINUTES = 2;
var IDLE_MAX_PERIOD_MINUTES = 30;
var IDLE_NO_CHANGE_STEPS = 2;
var IDLE_SYNC_ALARM = "pastex-idle-sync";
var BACKOFF_KEY = "pastex.sync.backoff.v1";
var CONFIG_RETRY_MS = 10 * 60 * 1000;
var nextSyncAt = 0;
// The interval between two background checks grows while Drive keeps
// reporting the same library, and returns to the fast interval as soon as
// a change is seen.
var syncPeriodMinutes = IDLE_PERIOD_MINUTES;
var unchangedChecks = 0;

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

// An allowed site with a visible tab is "in use": no background check may run
// while the user is on one, even if the machine has gone idle. The content
// script reports the visibility of its own tab.
var siteInUse = new Set();

function markSiteVisibility(sender, visible) {
  if (!sender || !sender.tab || typeof sender.tab.id !== "number") return;
  if (visible) siteInUse.add(sender.tab.id);
  else siteInUse.delete(sender.tab.id);
}

function siteBusy() {
  return siteInUse.size > 0;
}

if (chrome.tabs && chrome.tabs.onRemoved) {
  chrome.tabs.onRemoved.addListener(function (tabId, info) {
    siteInUse.delete(tabId);
    if (info && info.isWindowClosing) return;
    // Only the last site tab closing counts as leaving, and the worker is the
    // saver of last resort when a page could not report it itself.
    if (siteInUse.size === 0) retryDeferredWrites();
  });
}



function idleState() {
  return new Promise(function (resolve) {
    if (!chrome.idle || typeof chrome.idle.queryState !== "function") { resolve("unknown"); return; }
    try { chrome.idle.queryState(IDLE_SECONDS, function (state) { resolve(state || "unknown"); }); }
    catch (ignored) { resolve("unknown"); }
  });
}

// The cached snapshot may miss a few bodies (quota, an older snapshot). Once
// they are filled in the worker, the open panel can repaint from the complete
// snapshot; nothing is posted when the check filled nothing.
function notifyPanelsFilled(filled) {
  if (!filled) return;
  progressPorts.forEach(function (port) {
    try { port.postMessage({ filled: true }); }
    catch (ignored) { progressPorts.delete(port); }
  });
}

// Notify an open panel that the background sync found new content, so it can
// repaint from the (already fresh) cache without another Drive round trip.
// A local edit changed the cached library; the open panel repaints from the
// cache, with no Drive request.
function notifyPanelsLocal(pending) {
  progressPorts.forEach(function (port) {
    try { port.postMessage({ local: true, pending: pending || 0 }); }
    catch (ignored) { progressPorts.delete(port); }
  });
}

// A queued write that Drive refused for good leaves the panel with a row it
// shows and Drive does not have. The panel is told, so it can say so instead of
// looking as if the edit were saved.
function notifyPanelsDropped(reason) {
  progressPorts.forEach(function (port) {
    try { port.postMessage({ dropped: true, reason: reason || "" }); }
    catch (ignored) { progressPorts.delete(port); }
  });
}

// A queued write is waiting for something only the user can change (Drive will
// not let this account create files). The panel says so; nothing is dropped.
function notifyPanelsBlocked(reason) {
  progressPorts.forEach(function (port) {
    try { port.postMessage({ blocked: true, reason: reason || "" }); }
    catch (ignored) { progressPorts.delete(port); }
  });
}

function notifyPanelsChanged(lastCheckedAt) {
  progressPorts.forEach(function (port) {
    try { port.postMessage({ changed: true, lastCheckedAt: lastCheckedAt || Date.now() }); }
    catch (ignored) { progressPorts.delete(port); }
  });
}

// --- the adaptive interval between two background checks ---
//
// A library that does not change is checked less and less often, so an idle
// machine does not keep asking Drive the same question. A change resets the
// interval to its fast value.

function backoffStore() {
  try { return chrome.storage && chrome.storage.local; } catch (ignored) { return null; }
}

async function readBackoff() {
  var store = backoffStore();
  if (!store) return;
  try {
    var data = (await store.get(BACKOFF_KEY))[BACKOFF_KEY];
    if (!data) return;
    if (typeof data.period === "number" && data.period >= IDLE_PERIOD_MINUTES && data.period <= IDLE_MAX_PERIOD_MINUTES)
      syncPeriodMinutes = data.period;
    if (typeof data.unchanged === "number" && data.unchanged >= 0) unchangedChecks = data.unchanged;
  } catch (ignored) { /* the backoff is only an optimization */ }
}

async function saveBackoff() {
  var store = backoffStore();
  if (!store) return;
  try {
    await store.set({ [BACKOFF_KEY]: { period: syncPeriodMinutes, unchanged: unchangedChecks } });
  } catch (ignored) { /* the backoff is only an optimization */ }
}

function adaptBackoff(changed) {
  if (changed) {
    unchangedChecks = 0;
    syncPeriodMinutes = IDLE_PERIOD_MINUTES;
  } else {
    unchangedChecks++;
    if (unchangedChecks >= IDLE_NO_CHANGE_STEPS && syncPeriodMinutes < IDLE_MAX_PERIOD_MINUTES)
      syncPeriodMinutes = Math.min(syncPeriodMinutes * 2, IDLE_MAX_PERIOD_MINUTES);
  }
  saveBackoff();
  // The alarm is replaced, so the next check follows the new interval.
  ensureIdleAlarm(true, syncPeriodMinutes);
}

// The single entry point for every background Drive check. The idle gate is
// never bypassed: a check only runs while the machine is idle or locked, so
// the user is never charged for a request while working.
async function runIdleSync(assumeIdle) {
  if (idleSyncRunning) return;
  if (panelBusy() || siteBusy()) return;
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
    // A metadata-only check confirms an unchanged library without downloading
    // a body; only a real difference falls back to the full listing.
    var result = await libraryCache.verify();
    if (result && result.skipped) result = await libraryCache.sync();
    // A row typed while the check was reading is not on Drive, so the listing
    // that just landed does not contain it. Patching it back keeps a background
    // sync from deleting a snippet the user had just written.
    await replayMissingRows().catch(function () {});
    nextSyncAt = 0;
    // A skipped check never touched Drive, so it does not move the interval.
    if (result && !result.skipped) {
      adaptBackoff(!!result.changed);
      if (result.changed) notifyPanelsChanged(result.lastCheckedAt);
    }
    // The panel can be told when the stored snapshot became complete again.
    if (result) notifyPanelsFilled(result.filled);
  } catch (err) {
    var message = err && err.message ? err.message : String(err);
    // A missing or broken configuration is not worth retrying every minute.
    if (/choose your configuration file|configure a|Google sign-in/i.test(message))
      nextSyncAt = Date.now() + CONFIG_RETRY_MS;
    else console.warn("Pastex background sync skipped:", message);
  } finally { idleSyncRunning = false; }
}

function scheduleIdleSync(delay, assumeIdle) {
  if (idleSyncTimer) clearTimeout(idleSyncTimer);
  idleSyncTimer = setTimeout(function () { idleSyncTimer = 0; runIdleSync(assumeIdle); }, delay || 0);
}

// The alarm carries the interval between two checks. Replacing it starts the
// next check after the given delay, so a longer interval really means a
// longer wait, while a fresh start still checks soon.
function ensureIdleAlarm(replace, delay) {
  if (!chrome.alarms || typeof chrome.alarms.create !== "function") return;
  var wait = delay === undefined ? 1 : delay;
  function create() { chrome.alarms.create(IDLE_SYNC_ALARM, { delayInMinutes: wait, periodInMinutes: syncPeriodMinutes }); }
  if (replace) { create(); return; }
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

// --- who is showing the panel right now ------------------------------------
//
// The in-page launcher reads ⚡ (open it) or × (close it). A panel closed from
// the toolbar icon or from Chrome's own panel switch used to leave the launcher
// on ×, because the panel itself is the only thing that knew it was closing and
// a hidden panel runs no code. The worker owns the fact instead: Chrome reports
// every open and every close of an extension panel through onOpened/onClosed,
// whichever switch was used, and the launcher asks for the answer every time it
// is authorised instead of trusting a message it may have missed.
var panelWindows = new Set();

function markPanelWindow(windowId, open) {
  if (!Number.isInteger(windowId)) return;
  if (open) panelWindows.add(windowId);
  else panelWindows.delete(windowId);
}

// Tell the launcher of the tab the user is looking at. Best effort: the panel
// can be open in a window whose tabs have no content script.
function announcePanelToWindow(windowId) {
  if (!Number.isInteger(windowId) || !trustedPanelWindow(windowId)) return Promise.resolve();
  return chrome.tabs.query({ active: true, windowId: windowId }).then(function (tabs) {
    if (!tabs || !tabs[0]) return;
    return messageToLauncher(tabs[0], { type: "pastex-panel-state", open: panelWindows.has(windowId) });
  }).catch(function () { /* no launcher in this window */ });
}

function trustedPanelWindow(windowId) {
  return Number.isInteger(windowId) && !!chrome.tabs;
}

if (chrome.sidePanel && chrome.sidePanel.onOpened && typeof chrome.sidePanel.onOpened.addListener === "function") {
  chrome.sidePanel.onOpened.addListener(function (info) {
    markPanelWindow(info && info.windowId, true);
    return announcePanelToWindow(info && info.windowId);
  });
}
if (chrome.sidePanel && chrome.sidePanel.onClosed && typeof chrome.sidePanel.onClosed.addListener === "function") {
  chrome.sidePanel.onClosed.addListener(function (info) {
    markPanelWindow(info && info.windowId, false);
    return announcePanelToWindow(info && info.windowId);
  });
}

// A window that is gone cannot show a panel any more.
if (chrome.windows && chrome.windows.onRemoved) {
  chrome.windows.onRemoved.addListener(function (windowId) { panelWindows.delete(windowId); });
}

// A content script on an approved site may report that its tab was left. It
// can only trigger the flush that is already scheduled, never a Drive write of
// its own, and it still needs a sender that belongs to this extension.
function trustedStoppedSite(sender) {
  return !!(sender && sender.id === chrome.runtime.id && sender.tab && sender.frameId === 0 && sender.url &&
    /^https?:/i.test(sender.url));
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
  // The list is either inline in the configuration file or in a file the link
  // names; a link is fetched with the same sign-in, so one configuration file
  // can point at a shared allowlist.
  var allowedSites = data.allowedSites;
  if (!allowedSites) {
    var session = await PastexLibrary.connect(PastexConfig, PastexDrive);
    allowedSites = await PastexLibrary.readSites(PastexConfig, PastexDrive, session.token);
  }
  var patterns = PastexSites.parse(allowedSites);
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

// Best effort: the page may have no launcher, and that is not an error.
async function messageToLauncher(tab, message) {
  if (!tab || typeof tab.id !== "number") return;
  try { await chrome.tabs.sendMessage(tab.id, message); }
  catch (ignored) { /* no content script in this tab */ }
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

// One Drive write, with the session already resolved. sendQueued is its only
// caller: a panel edit never reaches this, it only reaches the queue.
async function applyDriveCommand(session, command) {
  var token = session.token;
  if (command.action === "save") {
    var mode = command.mode, name = command.name, node = command.node;
    if (typeof name !== "string" || !name.trim() || name.indexOf("/") !== -1 || !command.parentId)
      throw new Error("Invalid name or parent folder.");
    var driveName = mode === "new-text" ? name.replace(/\.txt$/i, "") + ".txt" : name;
    if (node && node.kind === "text") driveName = name.replace(/\.txt$/i, "") + (/\.txt$/i.test(node.driveName || "") ? ".txt" : "");
    if (!node || (driveName !== node.driveName && driveName !== node.name)) {
      var children = await PastexDrive.listChildren(token, command.parentId);
      if (!node && command.operationId) {
        var recovered = children.find(function (child) {
          return child.properties && child.properties.pastex_operation === command.operationId;
        });
        if (recovered) {
          if (recovered.name !== driveName ||
              (mode === "new-text" && await PastexDrive.downloadText(token, recovered.id) !== command.text))
            throw new Error("Conflict: a previously created item changed in Drive. The local edit is kept; review both versions before syncing.");
          return { id: recovered.id };
        }
      }
      if (children.some(function (child) { return child.name === driveName && child.id !== (node && node.id); }))
        throw new Error("An item with that name already exists in this folder.");
    }
    if (mode === "new-folder") return { id: await PastexDrive.createFolder(token, name, command.parentId, undefined, command.operationId) };
    if (mode === "new-text") return { id: (await PastexDrive.uploadNew(token, command.parentId, name.replace(/\.txt$/i, ""), command.text, undefined, false, command.operationId)).id };
    if (!node || !node.id || ["edit-text", "rename-text", "rename-folder"].indexOf(mode) === -1) throw new Error("Invalid edit request.");
    if (mode === "edit-text" && command.text !== node.text) {
      if (typeof command.baseText === "string") {
        var currentText = await PastexDrive.downloadText(token, node.id);
        if (currentText !== command.baseText && currentText !== command.text)
          throw new Error("Conflict: this snippet changed in Drive. The local edit is kept; review both versions before syncing.");
        if (currentText !== command.text) await PastexDrive.updateText(token, node.id, command.text);
      } else {
        // Older queued edits have no baseline. Do not overwrite a file whose
        // previous contents cannot be verified.
        throw new Error("Conflict: this queued edit has no original text. The local edit is kept; review it before syncing.");
      }
    }
    if (driveName !== (node.driveName || node.name)) await PastexDrive.renameFile(token, node.id, driveName);
    return { id: node.id };
  }
  if (command.action === "move") {
    var moved = await mapLimit(command.ids || [], 6, function (id) {
      return PastexDrive.moveFile(token, id, undefined, command.parentId)
        .then(function () { return ""; }, function (err) { return err.message || String(err); });
    });
    var refused = moved.filter(Boolean);
    if (refused.length) throw new Error(refused.length + " item(s) could not be moved: " + refused[0]);
    return {};
  }
  if (command.action === "delete") {
    var results = await mapLimit(command.ids || [], 6, function (id) {
      return PastexDrive.trashFile(token, id).then(function () { return ""; },
        function (err) { return err.message || String(err); });
    });
    var failures = results.filter(Boolean);
    if (failures.length) throw new Error(failures.length + " item(s) could not be deleted: " + failures[0]);
    return {};
  }
  // The order and the collapse state of the library live in one label on
  // the root folder, so a reorder or a collapse is a single Drive write.
  if (command.action === "reorder") {
    var order = PastexLibrary.cloneIndex(session.index);
    var groups = command.groups || [];
    var parents = command.parents || [];
    for (var j = 0; j < groups.length; j++) {
      var parent = parents[j] || session.rootId;
      order.order[parent] = (groups[j] || []).slice();
    }
    await PastexDrive.writeIndexFile(token, session.indexFileId, order);
    session.index = order;  // the next write in this flush builds on this one
    return {};
  }
  if (command.action === "order") {
    var merged = PastexLibrary.cloneIndex(session.index);
    var next = command.index || {};
    Object.keys(next.order || {}).forEach(function (parent) { merged.order[parent] = next.order[parent]; });
    Object.keys(next.collapsed || {}).forEach(function (id) { merged.collapsed[id] = next.collapsed[id]; });
    await PastexDrive.writeIndexFile(token, session.indexFileId, merged);
    // The session follows the label it just wrote, so a later write in the
    // same flush builds on it instead of overwriting it.
    session.index = merged;
    return {};
  }
  if (command.action === "collapse") {
    var folded = PastexLibrary.cloneIndex(session.index);
    (command.ids || []).filter(Boolean).forEach(function (id) {
      folded.collapsed[id] = command.collapsed ? "1" : "0";
    });
    await PastexDrive.writeIndexFile(token, session.indexFileId, folded);
    session.index = folded;
    return {};
  }

  throw new Error("Unknown Drive operation.");
}

// --- edits made while the user is on a site -------------------------------
//
// Nothing reaches Drive while a site is being used: the edit is applied to the
// cached snapshot at once (so the panel and the launcher read the same list)
// and its Drive write is queued. The queue lives in storage.local, so it
// survives a browser restart, and flush() sends it when the tab is left.

function localId() {
  return "local-" + (crypto.randomUUID ? crypto.randomUUID() : Date.now() + "-" + Math.random().toString(16).slice(2));
}

// The Drive name of a row, including the .txt suffix the library uses.
function driveNameOf(command) {
  var name = command.name || "";
  var item = command.item || {};
  if (command.kind === "create") {
    if (item.kind === "folder") return name;
    return name.replace(/\.txt$/i, "") + ".txt";
  }
  // An edit or a rename keeps whatever suffix the row already had.
  return item.driveName || name;
}

// The queue entry for one edit, or null when it needs no Drive write.
function queuedCommand(command, createdId) {
  var item = command.item || {};
  // A create gets the local id of the row that was just added to the cache,
  // so the queued write can name the row it uploads.
  if (!item.localId && createdId) item.localId = createdId;
  if (command.kind === "collapse" || command.kind === "reorder") {
    // Both live in the same root label, so the entry says which one this is.
    // Neither carries an item, which is why the id check above comes later.
    // A reorder carries the panel's groups/parents shape verbatim; a collapse
    // carries the ids and the folded flag.
    //
    // A row that never reached Drive has a `local-` id, which is not a Drive id:
    // putting one in the order made the read-back list it as a file and ask
    // Drive for it. It is dropped here, and the row joins the order on the first
    // reorder after its upload, when its real id is known.
    var driveIds = function (ids) {
      return (ids || []).filter(function (id) { return id && String(id).indexOf("local-") !== 0; });
    };
    return { id: localId(), kind: "order", queuedAt: Date.now(), item: {},
      // The panel sends groups/parents; an older caller sends ids. Both are
      // one group of one parent here.
      groups: command.kind === "reorder"
        ? (command.groups && command.groups.length ? command.groups : [command.ids || []]).map(driveIds)
        : undefined,
      parents: command.kind === "reorder" ? (command.parents && command.parents.length ? command.parents.slice() : [""]) : undefined,
      collapse: command.kind === "collapse" ? { ids: driveIds(command.ids), collapsed: !!command.collapsed } : undefined };
  }
  // A move names its targets in ids and the folder they go to. Like a delete it
  // carries no item, so its id check comes below with the delete's.
  if (command.kind === "move") {
    // A `local-` id is a row Drive has not seen yet; its own create entry puts it
    // in the right folder, so there is nothing to move.
    var moving = (command.ids || []).filter(function (id) { return id && String(id).indexOf("local-") !== 0; });
    if (!moving.length) return null;
    return { id: localId(), kind: "move", queuedAt: Date.now(), item: {},
      ids: moving, parentId: command.parentId || "" };
  }
  // A delete names its targets in ids and carries no item, so the id check
  // must look there first or the whole delete would be dropped.
  var id = item.localId || item.id || command.id ||
    (command.kind === "delete" ? ((command.ids || []).filter(Boolean)[0] || "") : "") ||
    (command.kind === "create" ? localId() : "");
  if (!id) return null;
  // A row is new when it says so, when it only has a local id, or when it has
  // no id at all (a create the panel has not named yet).
  // A local id always means "not on Drive yet", whatever the caller believes.
  var isNew = String(item.id || item.localId || "").indexOf("local-") === 0 ||
    (item.isNew !== undefined ? !!item.isNew : !item.id);
  return {
    id: localId(),
    kind: command.kind,
    // A row that never reached Drive has nothing to delete there.
    ids: (command.ids || []).filter(function (target) { return String(target).indexOf("local-") !== 0; }),
    text: typeof command.text === "string" ? command.text : undefined,
    name: command.name,
    item: { id: item.id || id, localId: item.localId || "", kind: item.kind || "text",
      parentId: item.parentId, driveName: item.driveName || driveNameOf(command), isNew: isNew },
  };
}

// Validate an edit without reading Drive: the queue is applied to the cache
// and this only rejects what can be checked locally.
function validateLocalEdit(command) {
  if (!command || typeof command !== "object") throw new Error("Invalid edit request.");
  if (["create", "edit", "rename", "move", "delete", "collapse", "reorder"].indexOf(command.kind) === -1)
    throw new Error("Unknown Drive operation.");
  if (command.kind === "create" || command.kind === "edit" || command.kind === "rename") {
    if (typeof command.name !== "string" || !command.name.trim() || command.name.indexOf("/") !== -1)
      throw new Error("Invalid name or parent folder.");
  }
  if (command.kind === "create" && !command.item) throw new Error("Invalid edit request.");
  return command;
}

// A write invalidates the stored snapshot, so an edit that arrives while one is
// running has nothing to be applied to and would be dropped silently -- the row
// the user just typed would vanish from the panel while its write waited in the
// queue. The queue is only ever written while a site is being used, and a site
// being used is exactly when a flush is running (it started when the previous
// site was left), so the edit waits for the write to settle and then applies to
// the snapshot it left behind.
var FLUSH_SETTLE_MS = 50;
var FLUSH_SETTLE_LIMIT = 40;   // two seconds: a flush is a handful of requests

async function settleFlush() {
  for (var i = 0; i < FLUSH_SETTLE_LIMIT && flushRunning; i++) {
    await new Promise(function (resolve) { setTimeout(resolve, FLUSH_SETTLE_MS); });
  }
}

async function applyLocalEdit(command) {
  var entry = null;
  var result = await libraryCache.mutateLocal(async function (snapshot) {
    // Prepare on a copy. Persist the command before publishing the cached
    // change: storage failure cannot leave an edit that looks saved but has no
    // durable queue entry. The queue is the recovery record if the worker stops
    // between these two steps.
    var draft = JSON.parse(JSON.stringify(snapshot));
    var previous = command.kind === "edit" && command.item && command.item.id ?
      PastexQueue.find(draft.tree, command.item.id) : null;
    var baseText = previous && previous.kind === "text" && typeof previous.text === "string" &&
      String(previous.id).indexOf("local-") !== 0 ? previous.text : undefined;
    var applied = PastexQueue.apply(draft, command);
    var createdId = command.kind === "create" && applied.length ? applied[0] : "";
    entry = queuedCommand(command, createdId);
    if (!entry) throw new Error("The edit could not be queued for Drive.");
    if (baseText !== undefined) entry.baseText = baseText;
    if (createdId && command.item) command.item.localId = createdId;
    entry.command = command;
    await deferredQueue.push([entry]);
    Object.keys(snapshot).forEach(function (key) { delete snapshot[key]; });
    Object.assign(snapshot, draft);
    return applied;
  });
  return { result: result, entry: entry };
}

async function queueLocalEdit(command) {
  validateLocalEdit(command);
  var attempt = await applyLocalEdit(command);
  if (attempt.result.skipped && flushRunning) {
    await settleFlush();
    attempt = await applyLocalEdit(command);
  }
  var result = attempt.result;
  if (result.skipped) throw new Error("Cannot save changes while the library is unavailable. Try again.");
  notifyPanelsLocal(result.pending);
  return { applied: result.applied, pending: result.pending, queued: !!attempt.entry, local: true };
}

// --- flush: send the queue once the tab is closed -------------------------
//
// There is no timer behind this: the only triggers are a tab being hidden or
// closed (reported by the content script) and the browser starting after a
// quit, which is the retry path for a queue that never left.

// Run the queue serially. A transient failure (offline, a rate limit) keeps
// the entry for the next flush; a permanent one (the row is gone) drops it
// from the cache and from the queue so it cannot be retried forever.
// options.readBack: false sends the queue and stops there.
async function flushSoon(options) {
  var readBack = !(options && options.readBack === false);
  if (flushRunning) return { sent: 0, failed: 0 };
  var entries = await deferredQueue.all();
  if (!entries.length) return { sent: 0, failed: 0 };
  lastFlushRefusals = [];
  // What the user is looking at, taken before the first write: a write
  // invalidates the snapshot, and this is what puts it back afterwards with the
  // new ids swapped in. Without it, sending the queue would cost a full read of
  // the whole library just to learn the ids of the rows it just created.
  var before = await libraryCache.current().catch(function () { return null; });
  flushRunning = true;
  var renames = new Map();
  var sentCount = 0;
  try {
    // One session for the whole flush; the identity cannot change halfway
    // through because a configuration change clears the queue.
    var session = null;
    try { session = await PastexLibrary.connect(PastexConfig, PastexDrive); }
    catch (ignored) { /* each entry retries its own connect and reports why */ }
    // The map lives for this flush only: a later flush learns it from the rows
    // it created, which is where the real ids come from.
    var resolved = new Map();
    for (var i = 0; i < entries.length; i++) {
      var outcome = await sendQueued(entries[i], session, resolved);
      if (!outcome.ok) break;
      sentCount++;
    }
    renames = resolved;
  } finally {
    flushRunning = false;
    // The ids are the only thing the cache did not know: the rows, their order
    // and their folded state are exactly what the user already sees, so they
    // are put back as they were and only the `local-` ids are renamed.
    var adopted = 0;
    if (before && before.tree && renames.size) {
      adopted = swapLocalIds(before.tree, renames);
      dropLocalIds(before.index);
    }
    if (adopted || (before && before.tree)) {
      try { await libraryCache.restore(before); } catch (ignored) { /* the next open syncs */ }
    }
    // A row created while this flush was already sending is not in the copy
    // taken before it started, and its own entry arrived after the queue was
    // read, so nothing else will put it back. It goes on top of the snapshot
    // here; its write waits for the next flush like any other.
    await replayMissingRows().catch(function () {});
    if (readBack) {
      // A row whose id was not learned still points at `local-`, and only a
      // real read turns it into the id Drive gave it. The read is skipped when
      // every id was adopted, which is the ordinary case; the panel already
      // shows the rows either way.
      if (!adopted) {
        try { await libraryCache.sync(); } catch (ignored) { /* the next open syncs */ }
      }
      // Anything still queued is not on Drive yet, so it is put back on top of
      // what the snapshot holds.
      await replayPendingEdits();
    }
    // A flush that cannot report what it did leaves a silent gap, so the
    // result says how many entries are still waiting.
    reportFlush(await pendingCount());
  }
  return { sent: sentCount };
}

// A created row is named by a `local-` id until Drive answers with the real
// one. Renaming it is a rename of the row itself: the body is keyed by content
// hash, so the text and its place in the tree are untouched.
function swapLocalIds(tree, renames) {
  var swapped = 0;
  (function walk(nodes) {
    (nodes || []).forEach(function (node) {
      if (renames.has(node.id)) { node.id = renames.get(node.id); swapped++; }
      if (node.kind === "folder") walk(node.children);
    });
  })(tree || []);
  return swapped;
}

// The order and the folded map name rows by id, so a renamed row leaves a stale
// entry there. It is dropped rather than rewritten: the row rejoins the order on
// the next reorder, and neither map decides what exists.
function dropLocalIds(index) {
  if (!index) return;
  Object.keys(index.order || {}).forEach(function (parent) {
    index.order[parent] = (index.order[parent] || []).filter(function (id) {
      return String(id).indexOf("local-") !== 0;
    });
  });
  Object.keys(index.collapsed || {}).forEach(function (id) {
    if (String(id).indexOf("local-") === 0) delete index.collapsed[id];
  });
}

function reportFlush(pending) {
  if (pending) console.warn("Pastex: " + pending + " queued write(s) still waiting.");
}

// What the last flush could not send, and why. A refusal is permanent, so the
// entry is gone from the queue by the time Sync answers; without this the panel
// would report a smaller number and leave the user guessing.
var lastFlushRefusals = [];

// One explicit sync, for the panel's button: send what the queue holds and
// nothing else. It never reads the library back from Drive.
async function syncNow() {
  if (siteBusy()) return { flushed: { sent: 0, failed: 0 }, deferred: true, pending: await pendingCount() };
  var flushed = await flushSoon({ readBack: false });
  // "Sent 3." while three writes were refused is a lie the panel cannot see
  // through, so the refusals travel back with the count.
  return { flushed: flushed, deferred: false, pending: await pendingCount(),
    refused: lastFlushRefusals.slice() };
}

async function pendingCount() {
  return (await deferredQueue.all()).length;
}

// A queued reorder or collapse, as the one index write it becomes. The order
// comes from the cache the edit was applied to, so it is already what the
// panel shows.
// The label on the root folder is the source of truth for the order and the
// folded state, so these two writes start from it (session.index) rather than
// from the cache: the cache is cleared by every write in the same flush, so a
// cached tree would silently empty the order for the second write.
function queuedIndex(entry, session, snapshot) {
  var index = PastexLibrary.cloneIndex(session.index);
  if (entry.collapse) {
    // A fold is one flag per row, and it is added next to the order that is
    // already stored, so a fold can never erase a pending reorder.
    (entry.collapse.ids || []).forEach(function (id) {
      if (String(id).indexOf("local-") === 0) return;
      index.collapsed[id] = entry.collapse.collapsed ? "1" : "0";
    });
    return { action: "order", index: index };
  }
  // A reorder names the groups it changed. The tree in the cache is the best
  // description of a folder the entry does not name (a stale group), so it is
  // used only to fill in what the entry left out. The root folder's order must
  // be keyed by its Drive id, which is the only key the loader looks up.
  var cached = PastexQueue.indexOf(snapshot && snapshot.tree, session.rootId);
  var groups = entry.groups && entry.groups.length ? entry.groups : null;
  var parents = entry.parents && entry.parents.length ? entry.parents : null;
  if (groups) {
    groups.forEach(function (group, at) {
      var parent = (parents && parents[at]) || session.rootId;
      index.order[parent] = (group || []).filter(function (id) { return String(id).indexOf("local-") !== 0; });
    });
  } else {
    Object.keys(cached.order).forEach(function (parent) {
      index.order[parent] = cached.order[parent];
    });
  }
  return { action: "order", index: index };
}

// A row that never reached Drive is named by a `local-` id. A queued write that
// points at one can only be sent after the write that creates it, so the flush
// keeps the local -> Drive id map it learns and asks again next time. Anything
// still unknown is deferred, never sent with a placeholder: an empty or local
// parent is what Drive answers with "File not found: ." (one GET /files, one 404).
function resolveDriveId(id, resolved) {
  var value = id === undefined || id === null ? "" : String(id);
  if (value.indexOf("local-") !== 0) return value;
  return (resolved && resolved.get(value)) || "";
}

function unresolvedParent(parentId, resolved) {
  return String(parentId || "").indexOf("local-") === 0 && !resolveDriveId(parentId, resolved);
}

// A queued edit, in Drive terms, with the ids already resolved.
function driveCommandFor(entry, session, snapshot, resolved) {
  var rootId = session.rootId;
  var item = entry.item || {};
  if (entry.kind === "order") {
    // A reorder and a collapse share one label on the root folder, so both are
    // written as that one index. The queue entry carries which one it was.
    return queuedIndex(entry, session, snapshot);
  }
  if (entry.kind === "delete") {
    var ids = (entry.ids || []).filter(Boolean);
    return ids.length ? { action: "delete", ids: ids } : null;
  }
  if (entry.kind === "move") {
    // A row that never reached Drive has no parent there; it is uploaded in the
    // right folder by its own create entry, so there is nothing to move.
    var movable = (entry.ids || []).filter(function (id) { return String(id).indexOf("local-") !== 0; });
    if (unresolvedParent(entry.parentId, resolved)) return movable.length ? { defer: true } : null;
    return movable.length ? { action: "move", ids: movable,
      parentId: resolveDriveId(entry.parentId, resolved) || rootId } : null;
  }

  // A row that never reached Drive is uploaded whatever the last edit called
  // itself: editing a brand-new row must not become a rename of a file that
  // does not exist yet.
  if (entry.kind === "create" || item.isNew) {
    var createParent = item.parentId || rootId;
    // Its folder has not reached Drive yet: sending now would ask Drive for a
    // folder id that does not exist.
    if (unresolvedParent(createParent, resolved)) return { defer: true };
    return { action: "save", mode: item.kind === "folder" ? "new-folder" : "new-text",
      name: entry.name, parentId: resolveDriveId(createParent, resolved) || rootId,
      text: entry.text || "", rootId: rootId, operationId: item.localId || entry.id };
  }
  // An edit of a row that already exists on Drive. A text row always takes the
  // body-writing mode: the body may have changed and the same call also
  // applies a rename, while "rename-text" would silently drop the body.
  return { action: "save", mode: item.kind === "folder" ? "rename-folder" :
      (entry.kind === "rename" ? "rename-text" : "edit-text"),
    name: entry.name, parentId: resolveDriveId(item.parentId || rootId, resolved) || rootId,
    text: entry.text, baseText: entry.baseText, rootId: rootId,
    node: { id: item.id, kind: item.kind, name: item.driveName || entry.name,
      driveName: item.driveName || entry.name } };
}

// session may be passed in: a flush connects once and reuses it, because a
// connect is an OAuth token plus a folder read and a queue of ten entries
// would otherwise pay for twenty requests.
async function sendQueued(entry, session, resolved) {
  // A revoked OAuth token answers 401 once. Forgetting the token and signing in
  // again is enough, so a single retry is allowed here instead of leaving the
  // whole queue stuck until the browser restarts. The retry is its own small
  // loop around the body: a `continue` inside the catch would skip the
  // bookkeeping below only by accident of nesting.
  for (var attempt = 0; attempt < 2; attempt++) {
    var result;
    try {
      var active = session || (await PastexLibrary.connect(PastexConfig, PastexDrive));
      // Read once per entry, before the write lock: mutate() invalidates the
      // snapshot before it runs, so a read taken inside would always be empty,
      // and a read taken once per flush would miss the previous entry's write.
      var snapshot = await libraryCache.current();
      result = await libraryCache.mutate(async function () {
        var command = driveCommandFor(entry, active, snapshot, resolved);
        if (!command) return { skipped: true };
        // Nothing is dropped: the entry waits in the queue until the write it
        // depends on has produced an id.
        if (command.defer) return { defer: true };
        var written = await applyDriveCommand(active, command);
        // A created row now has its Drive id, and the cached snapshot follows at
        // once: this runs while the write still holds the library lock, before
        // the invalidation that drops the snapshot.
        return written;
      });
    } catch (err) {
      var failure = err && err.message ? err.message : String(err);
      if (/HTTP 401\b/.test(failure) && attempt === 0) {
        // The token Google no longer accepts: drop it and ask for a fresh one.
        if (session && session.token) await PastexDrive.removeCachedToken(session.token);
        session = null;
        continue;
      }
      if (/^Conflict:/.test(failure) || isStorageQuotaRefusal(failure)) {
        // Kept, not dropped: see isStorageQuotaRefusal. The panel is told at
        // once so the reason is visible without pressing Sync, and the row
        // stays in the cache because the write is still going to happen.
        lastFlushRefusals.push({ kind: entry.kind, name: entry.name || "", error: failure, kept: true });
        notifyPanelsBlocked(failure);
        return { ok: false, retry: true, error: failure };
      }
      if (isPermanentFailure(failure)) {
        // Keep refused operations until the user can resolve the cause. A
        // permission error or a temporary missing file must not erase work.
        lastFlushRefusals.push({ kind: entry.kind, name: entry.name || "", error: failure, kept: true });
        notifyPanelsBlocked(failure);
        return { ok: false, retry: true, error: failure };
      }
      // Offline, a rate limit or a missing configuration: keep it for later.
      return { ok: false, retry: true, error: failure };
    }
    if (result && result.defer) return { ok: false, retry: true, error: "its folder has not reached Drive yet" };
    // A create's Drive id is remembered for the rest of the flush, so the row
    // created inside a new folder is uploaded into that folder.
    if (resolved && result && result.id && entry.item && entry.item.localId)
      resolved.set(entry.item.localId, result.id);
    await deferredQueue.remove([entry.id]);
    return { ok: true, id: result && result.id };
  }
}

// Put back a row the fresh snapshot does not have. A load that started before a
// row was typed returns a library that has never heard of it, and the queue is
// the only record of that row. Replaying the whole command list works but is
// wrong for a command that edits a row Drive now describes differently; this
// only adds what is missing, so a fresh listing is never reverted.
async function replayMissingRows() {
  var entries;
  try { entries = await deferredQueue.all(); } catch (ignored) { return 0; }
  var creates = entries.filter(function (entry) {
    return entry && entry.command && entry.command.kind === "create" && entry.item && entry.item.localId;
  });
  if (!creates.length) return 0;
  var added = await libraryCache.mutateLocal(async function (snapshot) {
    var present = new Set();
    (function walk(nodes) {
      (nodes || []).forEach(function (node) { present.add(node.id); if (node.kind === "folder") walk(node.children); });
    })(snapshot.tree || []);
    var put = 0;
    creates.forEach(function (entry) {
      if (present.has(entry.item.localId)) return;
      try { if (PastexQueue.apply(snapshot, entry.command).length) put++; } catch (ignored) { /* keep the rest */ }
    });
    return put ? [put] : [];
  });
  if (added && added.applied && added.applied.length) notifyPanelsLocal(await pendingCount());
  return added && added.applied ? added.applied.length : 0;
}

// Replay the edits that are still waiting onto a freshly read snapshot, so the
// panel never shows a library that forgot a local change. Only entries that
// carry their command can be replayed; an older entry is left alone.
async function replayPendingEdits() {
  var entries;
  try { entries = await deferredQueue.all(); } catch (ignored) { return 0; }
  var replay = entries.filter(function (entry) { return entry && entry.command; });
  if (!replay.length) return 0;
  await libraryCache.mutateLocal(async function (snapshot) {
    replay.forEach(function (entry) {
      try { PastexQueue.apply(snapshot, entry.command); } catch (ignored) { /* keep the rest */ }
    });
    return [];
  });
  notifyPanelsLocal(await pendingCount());
  return replay.length;
}

// A full Drive answers with a 403 that no retry can fix. That is a problem the
// user can change (free space, or move the library into a Shared Drive), not a
// write that is wrong, so the entry is kept: the text in it is user content and
// dropping it would lose work.
function isStorageQuotaRefusal(message) {
  return /storage quota|storageQuotaExceeded/i.test(String(message || ""));
}

// A write that Drive refuses for good is not worth retrying; a network or
// quota failure is.
function isPermanentFailure(message) {
  if (/HTTP (400|403|404|410)\b/.test(message)) return true;
  if (/already exists|Invalid edit request|Invalid name|not found|Unknown Drive operation/i.test(message)) return true;
  return false;
}

// --- events ---

function initializeUserConfig() {
  configureSites().catch(function (err) {
    console.warn("Pastex site registration failed:", err.message);
  });
  // The learned interval is applied, but the first check still happens soon.
  readBackoff().then(function () { ensureIdleAlarm(true, 1); });
  ensureIdleAlarm();
  // A browser start touches Drive only when a queue is waiting: a start with
  // nothing to send reads nothing, because the cache already holds the whole
  // library and an ordinary open is served from it.
  retryDeferredWrites();
}

// The retry path for a queue that never left. It runs on both install and
// start, so a machine that was shut down with pending edits still lands them.
async function retryDeferredWrites() {
  try {
    if (!(await pendingCount())) return;
    // Never write while an allowed site is open: the queue waits for the tab
    // to be left, which is the only moment the user asked for.
    if (siteBusy()) return;
    await flushSoon();
    notifyPanelsChanged(Date.now());
  } catch (ignored) { /* the next start, or the next left tab, tries again */ }
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
readBackoff().then(function () { ensureIdleAlarm(true, 1); });
ensureIdleAlarm();

// The toolbar icon toggles the panel instead of only opening it. Chrome owns
// that behavior: with openPanelOnActionClick it opens the panel on click and
// closes it again on the next click, with no action.onClicked listener
// competing for the click. The manifest's minimum_chrome_version guarantees
// the API exists, so there is no feature test here.
chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(function () {});

// Content scripts must authenticate on an allowed site before opening a panel.
var recoveryFlight = null;
async function recoverQueuedCache(result) {
  if (!recoveryFlight) {
    recoveryFlight = (async function () {
      if (await pendingCount()) { await replayPendingEdits(); return true; }
      return false;
    })().catch(function (err) { recoveryFlight = null; throw err; });
  }
  return await recoveryFlight ? libraryCache.get(false) : result;
}

chrome.runtime.onMessage.addListener(function (message, sender, sendResponse) {
  if (message && message.type === "pastex-reload-sites") {
    if (!trustedPanel(sender)) { sendResponse({ ok: false, error: "Unauthorized request." }); return false; }
    // A new configuration means a new library: the next read is a full sync.
    // A queue that points at the old folder must not be replayed into the new
    // one. The cached configuration itself is KEPT: the panel has just written
    // it, and it is the only source the worker has.
    libraryCache.configurationChanged();
    configureSites().then(function () { sendResponse({ ok: true }); })
      .catch(function (err) { sendResponse({ ok: false, error: err.message || String(err) }); });
    return true;
  }
  if (message && message.type === "pastex-open-panel") {
    if (!sender || sender.id !== chrome.runtime.id || !sender.tab || sender.frameId !== 0) {
      sendResponse({ ok: false }); return false;
    }
    // The launcher asks the worker to close the panel when the request comes
    // from the same page that holds the open panel.
    if (message.action === "close") {
      var closing = panelGrants.get(grantKey(sender));
      if (!closing || closing.token !== message.capability || closing.url !== sender.url ||
          closing.expires < Date.now() || closing.revision !== siteRevision) {
        sendResponse({ ok: false }); return false;
      }
      chrome.sidePanel.close({ windowId: sender.tab.windowId }).then(function () {
        // The event does the same thing; setting it here too means a browser
        // that has not fired it yet still answers the next request correctly.
        markPanelWindow(sender.tab.windowId, false);
        sendResponse({ ok: true, closed: true, open: false });
      }).catch(function () { sendResponse({ ok: false }); });
      return true;
    }
    var grant = panelGrants.get(grantKey(sender));
    if (!grant || grant.token !== message.capability || grant.url !== sender.url || grant.tabUrl !== sender.tab.url || grant.expires < Date.now() ||
        grant.revision !== siteRevision || !Number.isInteger(sender.tab.windowId)) {
      sendResponse({ ok: false }); return false;
    }
    // Do not await anything before this call: Chrome requires the user's
    // gesture, which arrives with the content script's mousedown message.
    try {
      chrome.sidePanel.open({ windowId: sender.tab.windowId }).then(function () {
        markPanelWindow(sender.tab.windowId, true);
        // The state after the gesture, so the launcher never has to assume it.
        sendResponse({ ok: true, open: true });
      })
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
      // The launcher asks this on every focus, so the answer carries the panel
      // state: a close the page never heard about is corrected here.
      sendResponse({ allowed: true, capability: token,
        panelOpen: Number.isInteger(sender.tab.windowId) && panelWindows.has(sender.tab.windowId) });
    }).catch(function () { panelGrants.delete(key); sendResponse({ allowed: false }); });
    return true;
  }
  if (message && message.type === "pastex-panel-state") {
    // The open panel tells the active page whether the in-page launcher
    // should read ⚡ (open) or × (close). Only the page's own launcher uses
    // it, and the content script still verifies the sender.
    if (!trustedPanel(sender)) { sendResponse({ ok: false, error: "Unauthorized request." }); return false; }
    activeTab().then(function (tab) {
      if (!tab) { sendResponse({ ok: true }); return; }
      markPanelWindow(tab.windowId, !!message.open);
      return messageToLauncher(tab, { type: "pastex-panel-state", open: !!message.open });
    }).then(function () { sendResponse({ ok: true }); })
      .catch(function () { sendResponse({ ok: true }); });
    return true;
  }
  if (message && message.type === "pastex-panel-local-edit") {
    if (!trustedPanel(sender)) { sendResponse({ ok: false, error: "Unauthorized request." }); return false; }
    // The edit never touches Drive here: it is applied to the cache and its
    // write waits for the tab to be left.
    queueLocalEdit(message.command || {}).then(function (result) { sendResponse({ ok: true, result: result }); })
      .catch(function (err) { sendResponse({ ok: false, error: err.message || String(err) }); });
    return true;
  }
  if (message && message.type === "pastex-sync-now") {
    if (!trustedPanel(sender)) { sendResponse({ ok: false, error: "Unauthorized request." }); return false; }
    // The Sync button: send what the queue holds, then answer with a fresh
    // read. A site that is still open keeps its queue waiting, exactly as a
    // leave would; the answer says so instead of writing behind its back.
    syncNow().then(function (result) { sendResponse({ ok: true, result: result }); })
      .catch(function (err) { sendResponse({ ok: false, error: err.message || String(err) }); });
    return true;
  }
  if (message && message.type === "pastex-panel-status") {
    if (!trustedPanel(sender)) { sendResponse({ ok: false, error: "Unauthorized request." }); return false; }
    pendingCount().then(function (pending) { sendResponse({ ok: true, result: { pending: pending } }); })
      .catch(function () { sendResponse({ ok: true, result: { pending: 0 } }); });
    return true;
  }
  if (message && message.type === "pastex-site-visible") {
    // Only a content script of this extension may report a tab's visibility.
    if (!trustedStoppedSite(sender)) { sendResponse({ ok: false, error: "Unauthorized request." }); return false; }
    markSiteVisibility(sender, message.visible);
    sendResponse({ ok: true });
    return false;
  }
  if (message && message.type === "pastex-flush-now") {
    if (!trustedPanel(sender) && !trustedStoppedSite(sender)) {
      sendResponse({ ok: false, error: "Unauthorized request." }); return false;
    }
    // The page that reports the leave is leaving, so its tab stops counting as
    // in use even if the visibility message has not arrived yet. Another
    // allowed site that is still visible keeps the queue waiting.
    markSiteVisibility(sender, false);
    if (siteBusy()) { sendResponse({ ok: true, result: { sent: 0, deferred: true } }); return false; }
    flushSoon().then(function (result) { sendResponse({ ok: true, result: result }); })
      .catch(function (err) { sendResponse({ ok: false, error: err.message || String(err) }); });
    return true;
  }
  if (message && message.type === "pastex-panel-fill") {
    if (!trustedPanel(sender)) { sendResponse({ ok: false, error: "Unauthorized request." }); return false; }
    var filling = trackPanelOperation(libraryCache.fill());
    filling.then(function (result) { sendResponse({ ok: true, result: result }); })
      .catch(function (err) { sendResponse({ ok: false, error: err.message || String(err) }); });
    return true;
  }
  if (message && message.type === "pastex-panel-library") {
    if (!trustedPanel(sender)) { sendResponse({ ok: false, error: "Unauthorized request." }); return false; }
    clearProgress();
    trackPanelOperation(libraryCache.get(!!message.force, broadcastProgress).then(recoverQueuedCache))
      .then(function (result) { clearProgress(); sendResponse({ ok: true, result: result }); })
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
