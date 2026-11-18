/* Pastex background service worker (Manifest V3).
 *
 *  - Toolbar click opens the Side Panel.
 *  - Ctrl+Shift+V (command "toggle-picker") opens the in-page picker.
 *  - The "Paste snippet" context menu lists recent snippets.
 *  - Content scripts are injected on demand for tabs that were already
 *    open when the extension was installed or updated.
 */

importScripts("store.js");

var MENU_ROOT = "pastex-insert-root";
var MENU_BROWSE = "pastex-browse";
var MENU_RECENT_PREFIX = "pastex-recent-";
var MAX_MENU_RECENTS = 8;

function activeTab() {
  return chrome.tabs.query({ active: true, currentWindow: true }).then(function (tabs) {
    return tabs && tabs[0] ? tabs[0] : null;
  });
}

// Send a message to the tab's content script, injecting it first when the
// tab predates the installation (chrome:// pages cannot be injected).
async function messageTab(tabId, message) {
  try {
    return await chrome.tabs.sendMessage(tabId, message);
  } catch (firstErr) {
    await chrome.scripting.executeScript({ target: { tabId: tabId }, files: ["content.js"] });
    await chrome.scripting.insertCSS({ target: { tabId: tabId }, files: ["content.css"] });
    return chrome.tabs.sendMessage(tabId, message);
  }
}

async function togglePickerInActiveTab() {
  var tab = await activeTab();
  if (!tab) {
    return;
  }
  try {
    await messageTab(tab.id, { type: "pastex-toggle-picker" });
  } catch (err) {
    // Restricted pages (chrome://, Web Store): fall back to the Side Panel.
    if (tab.windowId !== undefined) {
      await chrome.sidePanel.open({ windowId: tab.windowId }).catch(function () {});
    }
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

// --- context menu: recent snippets + browse-all ---

function shortTitle(path, maxLen) {
  maxLen = maxLen || 40;
  if (path.length <= maxLen) {
    return path;
  }
  return "…" + path.slice(path.length - maxLen + 1);
}

async function rebuildContextMenu() {
  try {
    await chrome.contextMenus.removeAll();
  } catch (ignored) {
    /* nothing to remove */
  }
  var tree = [];
  var recent = [];
  try {
    tree = await PastexStore.loadTree();
    recent = await PastexStore.loadRecent();
  } catch (ignored) {
    return;
  }
  chrome.contextMenus.create({
    id: MENU_ROOT,
    title: "Paste snippet",
    contexts: ["editable"],
  });
  var shown = 0;
  for (var i = 0; i < recent.length && shown < MAX_MENU_RECENTS; i++) {
    var node = PastexStore.findById(tree, recent[i]);
    if (!node || node.kind !== "text") {
      continue;
    }
    chrome.contextMenus.create({
      id: MENU_RECENT_PREFIX + node.id,
      parentId: MENU_ROOT,
      title: shortTitle(node.name),
      contexts: ["editable"],
    });
    shown++;
  }
  chrome.contextMenus.create({
    id: MENU_BROWSE,
    parentId: MENU_ROOT,
    title: shown ? "Browse all…" : "Browse snippets…",
    contexts: ["editable"],
  });
}

var rebuildTimer = 0;
function scheduleMenuRebuild() {
  if (rebuildTimer) {
    return;
  }
  rebuildTimer = setTimeout(function () {
    rebuildTimer = 0;
    rebuildContextMenu();
  }, 500);
}

// --- events ---

chrome.runtime.onInstalled.addListener(function () {
  rebuildContextMenu();
});

chrome.runtime.onStartup.addListener(function () {
  rebuildContextMenu();
});

chrome.storage.onChanged.addListener(function (changes, area) {
  if (area !== "local") {
    return;
  }
  if (changes[PastexStore.TREE_KEY] || changes[PastexStore.RECENT_KEY]) {
    scheduleMenuRebuild();
  }
});

chrome.contextMenus.onClicked.addListener(async function (info, tab) {
  if (info.menuItemId === MENU_BROWSE) {
    if (tab && tab.windowId !== undefined) {
      await chrome.sidePanel.open({ windowId: tab.windowId }).catch(function () {});
    }
    return;
  }
  if (String(info.menuItemId).indexOf(MENU_RECENT_PREFIX) === 0) {
    var id = String(info.menuItemId).slice(MENU_RECENT_PREFIX.length);
    var tree = await PastexStore.loadTree();
    var node = PastexStore.findById(tree, id);
    if (node && node.kind === "text" && tab) {
      try {
        await messageTab(tab.id, { type: "pastex-insert", id: node.id, text: node.text || "" });
      } catch (err) {
        /* restricted page: nothing to insert into */
      }
    }
  }
});

chrome.commands.onCommand.addListener(function (command) {
  if (command === "toggle-picker") {
    togglePickerInActiveTab();
  }
});

chrome.action.onClicked.addListener(async function (tab) {
  if (tab && tab.windowId !== undefined) {
    await chrome.sidePanel.open({ windowId: tab.windowId });
  }
});

// Side panel and options page ask the worker to insert/copy.
chrome.runtime.onMessage.addListener(function (message, sender, sendResponse) {
  if (!message || message.type !== "pastex-sidepanel-insert") {
    return false;
  }
  insertIntoActiveTab(message.id, message.text || "").then(function (response) {
    sendResponse(response || { ok: false });
  });
  return true;
});
