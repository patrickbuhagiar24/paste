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
    if (/configure a|choose a configuration folder|add a pastex-google-drive/i.test(message)) nextSyncAt = Date.now() + CONFIG_RETRY_MS;
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
    await PastexDrive.setIndex(token, session.rootId, order, undefined, session.properties);
    session.index = order;  // the next write in this flush builds on this one
    return {};
  }
  if (command.action === "order") {
    var merged = PastexLibrary.cloneIndex(session.index);
    var next = command.index || {};
    Object.keys(next.order || {}).forEach(function (parent) { merged.order[parent] = next.order[parent]; });
    Object.keys(next.collapsed || {}).forEach(function (id) { merged.collapsed[id] = next.collapsed[id]; });
    await PastexDrive.setIndex(token, session.rootId, merged, undefined, session.properties);
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
    await PastexDrive.setIndex(token, session.rootId, folded, undefined, session.properties);
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
    return { id: localId(), kind: "order", queuedAt: Date.now(), item: {},
      // The panel sends groups/parents; an older caller sends ids. Both are
      // one group of one parent here.
      groups: command.kind === "reorder"
        ? (command.groups && command.groups.length ? command.groups : [command.ids || []]).map(function (g) { return (g || []).slice(); })
        : undefined,
      parents: command.kind === "reorder" ? (command.parents && command.parents.length ? command.parents.slice() : [""]) : undefined,
      collapse: command.kind === "collapse" ? { ids: (command.ids || []).slice(), collapsed: !!command.collapsed } : undefined };
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
  if (["create", "edit", "rename", "delete", "collapse", "reorder"].indexOf(command.kind) === -1)
    throw new Error("Unknown Drive operation.");
  if (command.kind === "create" || command.kind === "edit" || command.kind === "rename") {
    if (typeof command.name !== "string" || !command.name.trim() || command.name.indexOf("/") !== -1)
      throw new Error("Invalid name or parent folder.");
  }
  if (command.kind === "create" && !command.item) throw new Error("Invalid edit request.");
  return command;
}

async function queueLocalEdit(command) {
  validateLocalEdit(command);
  var localId = "";
  var result = await libraryCache.mutateLocal(async function (snapshot) {
    var applied = PastexQueue.apply(snapshot, command);
    // A create's new row is named here, so the upload can rename it in the
    // cache to the Drive id it receives.
    if (command.kind === "create" && applied && applied.length) localId = applied[0];
    return applied;
  });
  var entry = queuedCommand(command, localId);
  if (!result.skipped && !entry) {
    // Accepted by the cache but with nothing to send is a bug, not a no-op:
    // the edit would look saved and never leave the machine.
    throw new Error("The edit could not be queued for Drive.");
  }
  if (!result.skipped && entry) await deferredQueue.push([entry]);
  // Nothing is scheduled here on purpose. The write waits until the tab this
  // panel belongs to is closed, which the content script reports as the page
  // goes away; an edit on a site never sends a request to Drive by itself.
  if (!result.skipped) notifyPanelsLocal(result.pending);
  return { applied: result.applied, pending: result.pending, queued: !!entry, local: true };
}

// --- flush: send the queue once the tab is closed -------------------------
//
// There is no timer behind this: the only triggers are a tab being hidden or
// closed (reported by the content script) and the browser starting after a
// quit, which is the retry path for a queue that never left.

// Run the queue serially. A transient failure (offline, a rate limit) keeps
// the entry for the next flush; a permanent one (the row is gone) drops it
// from the cache and from the queue so it cannot be retried forever.
// options.readBack: false sends the queue and stops there. A new row keeps
// its local id until the next read; the panel already shows it and the next
// open (or the next flush) assigns the Drive id.
async function flushSoon(options) {
  var readBack = !(options && options.readBack === false);
  if (flushRunning) return { sent: 0, failed: 0 };
  var entries = await deferredQueue.all();
  if (!entries.length) return { sent: 0, failed: 0 };
  flushRunning = true;
  try {
    // One session for the whole flush; the identity cannot change halfway
    // through because a configuration change clears the queue.
    var session = null;
    try { session = await PastexLibrary.connect(PastexConfig, PastexDrive); }
    catch (ignored) { /* each entry retries its own connect and reports why */ }
    for (var i = 0; i < entries.length; i++) await sendQueued(entries[i], session);
  } finally {
    flushRunning = false;
    // The writes are on Drive now; one full read turns every local id into the
    // id Drive gave the row, so the cache matches Drive again.
    if (readBack) { try { await libraryCache.sync(); } catch (ignored) { /* the next open syncs */ } }
    // A flush that cannot report what it did leaves a silent gap, so the
    // result says how many entries are still waiting.
    reportFlush(await pendingCount());
  }
  return { sent: entries.length };
}

function reportFlush(pending) {
  if (pending) console.warn("Pastex: " + pending + " queued write(s) still waiting.");
}

// One explicit sync, for the panel's button: send what the queue holds and
// nothing else. It never reads the library back from Drive.
async function syncNow() {
  if (siteBusy()) return { flushed: { sent: 0, failed: 0 }, deferred: true, pending: await pendingCount() };
  var flushed = await flushSoon({ readBack: false });
  return { flushed: flushed, deferred: false, pending: await pendingCount() };
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
  // used only to fill in what the entry left out.
  var cached = PastexQueue.indexOf(snapshot && snapshot.tree);
  var groups = entry.groups && entry.groups.length ? entry.groups : null;
  var parents = entry.parents && entry.parents.length ? entry.parents : null;
  if (groups) {
    groups.forEach(function (group, at) {
      var parent = (parents && parents[at]) || session.rootId;
      index.order[parent] = (group || []).filter(function (id) { return String(id).indexOf("local-") !== 0; });
    });
  } else {
    Object.keys(cached.order).forEach(function (parent) {
      index.order[parent === "" ? session.rootId : parent] = cached.order[parent];
    });
  }
  return { action: "order", index: index };
}

// A queued edit, in Drive terms, with the ids already resolved.
function driveCommandFor(entry, session, snapshot) {
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

  // A row that never reached Drive is uploaded whatever the last edit called
  // itself: editing a brand-new row must not become a rename of a file that
  // does not exist yet.
  if (entry.kind === "create" || item.isNew) {
    return { action: "save", mode: item.kind === "folder" ? "new-folder" : "new-text",
      name: entry.name, parentId: item.parentId || rootId, text: entry.text || "", rootId: rootId };
  }
  // An edit of a row that already exists on Drive. A text row always takes the
  // body-writing mode: the body may have changed and the same call also
  // applies a rename, while "rename-text" would silently drop the body.
  return { action: "save", mode: item.kind === "folder" ? "rename-folder" : "edit-text",
    name: entry.name, parentId: item.parentId || rootId, text: entry.text, rootId: rootId,
    node: { id: item.id, kind: item.kind, name: item.driveName || entry.name,
      driveName: item.driveName || entry.name } };
}

// session may be passed in: a flush connects once and reuses it, because a
// connect is an OAuth token plus a folder read and a queue of ten entries
// would otherwise pay for twenty requests.
async function sendQueued(entry, session) {
  var result;
  try {
    var active = session || await PastexLibrary.connect(PastexConfig, PastexDrive);
    // Read once per entry, before the write lock: mutate() invalidates the
    // snapshot before it runs, so a read taken inside would always be empty,
    // and a read taken once per flush would miss the previous entry's write.
    var snapshot = await libraryCache.current();
    result = await libraryCache.mutate(async function () {
      var command = driveCommandFor(entry, active, snapshot);
      if (!command) return { skipped: true };
      var written = await applyDriveCommand(active, command);
      // A created row now has its Drive id, and the cached snapshot follows at
      // once: this runs while the write still holds the library lock, before
      // the invalidation that drops the snapshot.
      return written;
    });
  } catch (err) {
    var failure = err && err.message ? err.message : String(err);
    if (isPermanentFailure(failure)) {
      await deferredQueue.remove([entry.id]);
      await libraryCache.dropLocal(entry);
      return { ok: false, retry: false, error: failure };
    }
    // Offline, a rate limit or a missing configuration: keep it for later.
    return { ok: false, retry: true, error: failure };
  }
  await deferredQueue.remove([entry.id]);
  return { ok: true, id: result && result.id };
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
// closes it again on the next click (Chrome 141+), with no action.onClicked
// listener competing for the click.
function enableToolbarToggle() {
  if (!chrome.sidePanel || typeof chrome.sidePanel.setPanelBehavior !== "function") return;
  try {
    var result = chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
    if (result && typeof result.catch === "function") result.catch(function () {});
  } catch (ignored) { /* older Chrome keeps the toolbar click as it is */ }
}
enableToolbarToggle();

// Content scripts must authenticate on an allowed site before opening or
// closing a panel. Closing exists only from Chrome 141, so the sender is told
// whether a toggle is possible instead of guessing.
function panelToggleSupported() {
  return !!(chrome.sidePanel && typeof chrome.sidePanel.close === "function");
}

// Content scripts must authenticate on an allowed site before opening a panel.
chrome.runtime.onMessage.addListener(function (message, sender, sendResponse) {
  if (message && message.type === "pastex-reload-sites") {
    if (!trustedPanel(sender)) { sendResponse({ ok: false, error: "Unauthorized request." }); return false; }
    // A new configuration means a new library: the next read is a full sync.
    // A queue that points at the old folder must not be replayed into the new
    // one. The cached configuration itself is KEPT: the panel has just written
    // it, and it is the only source the worker has.
    libraryCache.configurationChanged();
    deferredQueue.clear().catch(function () {});
    var refreshed = Promise.resolve();
    Promise.resolve(refreshed).catch(function () {}).then(function () { return configureSites(); }).then(function () { sendResponse({ ok: true }); })
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
      if (!panelToggleSupported()) { sendResponse({ ok: false, reason: "unsupported" }); return false; }
      chrome.sidePanel.close({ windowId: sender.tab.windowId }).then(function () { sendResponse({ ok: true, closed: true }); })
        .catch(function () { sendResponse({ ok: false }); });
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
      sendResponse({ allowed: true, capability: token, toggle: panelToggleSupported() });
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
    trackPanelOperation(libraryCache.get(!!message.force, broadcastProgress))
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
