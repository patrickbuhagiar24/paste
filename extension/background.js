/* Pastex background service worker (Manifest V3).
 *
 *  - Toolbar click opens the Side Panel.
 *  - The in-page ⚡ button opens the Drive picker on approved sites.
 *  - Content scripts are injected on demand for tabs that were already
 *    open when the extension was installed or updated.
 */

importScripts("native.js", "sites.js", "drive.js", "library.js");

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
    if (sender.id !== chrome.runtime.id || sender.url !== chrome.runtime.getURL("sidepanel.html")) {
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
      var result = await PastexLibrary.load(PastexNative, PastexDrive);
      sendResponse({ ok: true, tree: result.tree, warnings: result.warnings || [] });
    }).catch(function (err) { sendResponse({ ok: false, error: err.message || String(err) }); });
    return true;
  }
  if (!message || message.type !== "pastex-sidepanel-insert") {
    return false;
  }
  insertIntoActiveTab(message.id, message.text || "").then(function (response) {
    sendResponse(response || { ok: false });
  });
  return true;
});
