/* Pastex shared snippet model: folders + texts, search, and storage I/O.
 *
 * A classic script with no DOM access, so every context can load it:
 * side panel and options via <script src>, the background worker via
 * importScripts(). Pure helpers are also unit-tested under node
 * (see extension/test/) through the module.exports branch below.
 *
 * Storage schema (chrome.storage.local):
 *   pastex.tree.v1      [{id, kind, name, text?, children?, updatedAt}]
 *   pastex.recent.v1    [nodeId, ...] (most recent first, max 50)
 *   pastex.settings.v1  {floatingButton, collapsed}
 */

(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module && module.exports) {
    module.exports = api;
  } else {
    root.PastexStore = api;
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var TREE_KEY = "pastex.tree.v1";
  var RECENT_KEY = "pastex.recent.v1";
  var SETTINGS_KEY = "pastex.settings.v1";
  var DRIVE_KEY = "pastex.drive.v1";
  var SA_KEY = "pastex.sa.v1";
  var MAX_RECENT = 50;

  function uuid() {
    if (typeof crypto !== "undefined" && crypto.randomUUID) {
      return crypto.randomUUID();
    }
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0;
      var v = c === "x" ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }

  function nowIso() {
    return new Date().toISOString();
  }

  function makeFolder(name) {
    return { id: uuid(), kind: "folder", name: name, children: [], updatedAt: nowIso() };
  }

  function makeText(name, body) {
    return { id: uuid(), kind: "text", name: name, text: body || "", updatedAt: nowIso() };
  }

  function touch(node) {
    node.updatedAt = nowIso();
    return node;
  }

  // Walk every node depth-first; fn(node, trailOfFolderNames).
  function walk(nodes, fn, trail) {
    trail = trail || [];
    (nodes || []).forEach(function (node) {
      fn(node, trail);
      if (node.kind === "folder") {
        walk(node.children || [], fn, trail.concat(node.name));
      }
    });
  }

  // Every text node, in tree order.
  function flatten(nodes) {
    var out = [];
    walk(nodes, function (node) {
      if (node.kind === "text") {
        out.push(node);
      }
    });
    return out;
  }

  // [{path: "Email/Reply", node}] for Drive sync and picker labels.
  function flattenWithPaths(nodes) {
    var out = [];
    walk(nodes, function (node, trail) {
      if (node.kind === "text") {
        out.push({ path: trail.concat(node.name).join("/"), node: node });
      }
    });
    return out;
  }

  function findById(nodes, id) {
    var hit = null;
    walk(nodes, function (node) {
      if (node.id === id) {
        hit = node;
      }
    });
    return hit;
  }

  // Match snippet names first, then contents (desktop parity).
  function search(nodes, query) {
    var q = (query || "").trim().toLowerCase();
    var texts = flatten(nodes);
    if (!q) {
      return texts;
    }
    var nameHits = [];
    var bodyHits = [];
    texts.forEach(function (node) {
      if ((node.name || "").toLowerCase().indexOf(q) !== -1) {
        nameHits.push(node);
      } else if ((node.text || "").toLowerCase().indexOf(q) !== -1) {
        bodyHits.push(node);
      }
    });
    return nameHits.concat(bodyHits);
  }

  function removeById(nodes, id) {
    for (var i = 0; i < nodes.length; i++) {
      var node = nodes[i];
      if (node.id === id) {
        nodes.splice(i, 1);
        return true;
      }
      if (node.kind === "folder" && removeById(node.children || [], id)) {
        return true;
      }
    }
    return false;
  }

  // Move a node within its own level; delta is -1 or +1.
  function moveNode(nodes, id, delta) {
    function inLevel(list) {
      var at = -1;
      for (var i = 0; i < list.length; i++) {
        if (list[i].id === id) {
          at = i;
          break;
        }
      }
      if (at !== -1) {
        var to = at + delta;
        if (to < 0 || to >= list.length) {
          return true;
        }
        var moved = list.splice(at, 1)[0];
        list.splice(to, 0, moved);
        return true;
      }
      for (var j = 0; j < list.length; j++) {
        if (list[j].kind === "folder" && inLevel(list[j].children || [])) {
          return true;
        }
      }
      return false;
    }
    return inLevel(nodes || []);
  }

  // Insert text nodes downloaded from Drive, creating folders as needed.
  // entries: [{segments: ["Email", "Reply"], text, updatedAt}]
  function mergeDownloaded(tree, entries) {
    (entries || []).forEach(function (entry) {
      var level = tree;
      for (var i = 0; i < entry.segments.length - 1; i++) {
        var name = entry.segments[i];
        var folder = null;
        for (var j = 0; j < level.length; j++) {
          if (level[j].kind === "folder" && level[j].name === name) {
            folder = level[j];
            break;
          }
        }
        if (!folder) {
          folder = makeFolder(name);
          level.push(folder);
        }
        level = folder.children;
      }
      var leaf = entry.segments[entry.segments.length - 1];
      var existing = null;
      for (var k = 0; k < level.length; k++) {
        if (level[k].kind === "text" && level[k].name === leaf) {
          existing = level[k];
          break;
        }
      }
      if (existing) {
        existing.text = entry.text;
        existing.updatedAt = entry.updatedAt || nowIso();
      } else {
        var node = makeText(leaf, entry.text);
        if (entry.updatedAt) {
          node.updatedAt = entry.updatedAt;
        }
        level.push(node);
      }
    });
    return tree;
  }

  // --- chrome.storage.local I/O (unavailable under node) ---

  function storageArea() {
    if (typeof chrome !== "undefined" && chrome.storage && chrome.storage.local) {
      return chrome.storage.local;
    }
    throw new Error("chrome.storage.local is unavailable");
  }

  function defaultSettings() {
    return { floatingButton: true, collapsed: [] };
  }

  function defaultDrive() {
    return { folderId: "", folderName: "", lastSyncAt: "", lastSummary: "" };
  }

  async function loadTree() {
    var area = storageArea();
    var obj = await area.get(TREE_KEY);
    return Array.isArray(obj[TREE_KEY]) ? obj[TREE_KEY] : [];
  }

  async function saveTree(tree) {
    var obj = {};
    obj[TREE_KEY] = tree;
    await storageArea().set(obj);
  }

  async function loadRecent() {
    var obj = await storageArea().get(RECENT_KEY);
    return Array.isArray(obj[RECENT_KEY]) ? obj[RECENT_KEY] : [];
  }

  async function pushRecent(id) {
    var recent = await loadRecent();
    recent = [id].concat(
      recent.filter(function (other) {
        return other !== id;
      })
    );
    var obj = {};
    obj[RECENT_KEY] = recent.slice(0, MAX_RECENT);
    await storageArea().set(obj);
    return obj[RECENT_KEY];
  }

  async function loadSettings() {
    var obj = await storageArea().get(SETTINGS_KEY);
    var saved = obj[SETTINGS_KEY] || {};
    var base = defaultSettings();
    Object.keys(base).forEach(function (key) {
      if (saved[key] === undefined) {
        saved[key] = base[key];
      }
    });
    return saved;
  }

  async function saveSettings(settings) {
    var obj = {};
    obj[SETTINGS_KEY] = settings;
    await storageArea().set(obj);
  }

  async function loadDrive() {
    var obj = await storageArea().get(DRIVE_KEY);
    var saved = obj[DRIVE_KEY] || {};
    var base = defaultDrive();
    Object.keys(base).forEach(function (key) {
      if (saved[key] === undefined) {
        saved[key] = base[key];
      }
    });
    return saved;
  }

  async function saveDrive(drive) {
    var obj = {};
    obj[DRIVE_KEY] = drive;
    await storageArea().set(obj);
  }

  async function loadServiceAccount() {
    var obj = await storageArea().get(SA_KEY);
    return obj[SA_KEY] || null;
  }

  async function saveServiceAccount(sa) {
    var obj = {};
    // Only the two fields the auth flow needs are kept.
    obj[SA_KEY] = { client_email: sa.client_email, private_key: sa.private_key };
    await storageArea().set(obj);
  }

  async function clearServiceAccount() {
    await storageArea().remove(SA_KEY);
  }

  return {
    TREE_KEY: TREE_KEY,
    RECENT_KEY: RECENT_KEY,
    SETTINGS_KEY: SETTINGS_KEY,
    DRIVE_KEY: DRIVE_KEY,
    SA_KEY: SA_KEY,
    MAX_RECENT: MAX_RECENT,
    uuid: uuid,
    nowIso: nowIso,
    makeFolder: makeFolder,
    makeText: makeText,
    touch: touch,
    walk: walk,
    flatten: flatten,
    flattenWithPaths: flattenWithPaths,
    findById: findById,
    search: search,
    removeById: removeById,
    moveNode: moveNode,
    mergeDownloaded: mergeDownloaded,
    defaultSettings: defaultSettings,
    defaultDrive: defaultDrive,
    loadTree: loadTree,
    saveTree: saveTree,
    loadRecent: loadRecent,
    pushRecent: pushRecent,
    loadSettings: loadSettings,
    saveSettings: saveSettings,
    loadDrive: loadDrive,
    saveDrive: saveDrive,
    loadServiceAccount: loadServiceAccount,
    saveServiceAccount: saveServiceAccount,
    clearServiceAccount: clearServiceAccount,
  };
});
