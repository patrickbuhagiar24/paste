/* Deferred snippets: edits made while the user is on a site stay in the cache
 * and reach Drive only once the tab is left.
 *
 * Everything is applied to the cached snapshot immediately, so the panel and
 * the launcher always read the same, current library. A single Drive write is
 * queued per edit and is kept in storage.local, so a browser that is closed
 * before the flush can still send it on the next start. The worker is the only
 * writer: it reads this module, never the page.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.PastexQueue = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  var KEY = "pastex.deferred.v1";
  var MAX = 500;

  // --- the cached snapshot, edited in place ---------------------------------
  //
  // These helpers work on the shape stored by cache.js: the tree, the bodies
  // keyed by Drive id and the texts keyed by the SHA-256 of the content. A
  // local id starts with "local-" and is replaced by the Drive id once the
  // write lands, so an edit made before the flush still refers to the same row.

  function walk(nodes, fn) {
    (nodes || []).forEach(function (node) {
      fn(node);
      if (node.kind === "folder" && node.children) walk(node.children, fn);
    });
  }

  function find(nodes, id) {
    var hit = null;
    walk(nodes, function (node) { if (!hit && node.id === id) hit = node; });
    return hit;
  }

  function siblingsOf(nodes, id) {
    for (var i = 0; i < (nodes || []).length; i++) {
      if (nodes[i].id === id) return nodes;
      if (nodes[i].kind === "folder") {
        var found = siblingsOf(nodes[i].children, id);
        if (found) return found;
      }
    }
    return null;
  }

  function remove(nodes, id) {
    var siblings = siblingsOf(nodes, id);
    if (!siblings) return null;
    for (var i = 0; i < siblings.length; i++) {
      if (siblings[i].id === id) return siblings.splice(i, 1)[0];
    }
    return null;
  }

  // The tree returned by the library is the content of the root folder, so a
  // row with no parent folder is a sibling of the top-level rows. A row is
  // only appended when its folder really exists, so a stale parent is a no-op
  // rather than a row that the next read would lose.
  function insertInto(nodes, parentId, node) {
    if (!parentId) { nodes.push(node); return nodes; }
    var parent = find(nodes, parentId);
    if (!parent || parent.kind !== "folder") return null;
    parent.children = parent.children || [];
    parent.children.push(node);
    return parent.children;
  }

  function textNode(id, name, text, parentId, driverId) {
    return {
      id: id, kind: "text", name: name, driveName: (/\.txt$/i.test(name) ? name : name + ".txt"),
      parentId: parentId || "", driverId: driverId || "",
      collapsed: false, deferred: false, loadError: "",
      // A row that has never been written to Drive has no hash yet; the worker
      // hashes the body when it uploads it.
      text: text, hash: "",
    };
  }

  function folderNode(id, name, parentId) {
    return {
      id: id, kind: "folder", name: name, driveName: name,
      parentId: parentId || "", driverId: "",
      collapsed: false, deferred: false, loadError: "", children: [],
    };
  }

  // Apply a queued command to a snapshot. Returns the ids that were touched,
  // so the caller knows which row a create added. A command that no longer has
  // a target (an item deleted in the meantime) is a no-op.
  function apply(snapshot, command, newId) {
    var tree = snapshot.tree || (snapshot.tree = []);
    var item = command.item || {};
    // The panel sends the destination folder with the command; the queue
    // stores it on the item because the write needs it later.
    if (item.parentId === undefined && command.parentId !== undefined) item.parentId = command.parentId;
    // A row created before it reached Drive has neither id; the caller names it.
    // An edit or a rename always names the row it changes.
    var id = item.localId || item.id || newId ||
      ("local-" + Date.now() + "-" + Math.random().toString(16).slice(2));
    if (command.kind === "delete") {
      (command.ids || []).forEach(function (target) {
        remove(tree, target);
        if (snapshot.bodies) delete snapshot.bodies[target];
      });
      return command.ids || [];
    }
    if (command.kind === "rename" || command.kind === "edit") {
      var target = find(tree, id);
      if (!target) return [];
      target.name = command.name;
      if (command.kind === "edit") {
        if (typeof command.text === "string") target.text = command.text;
        // An edited body is no longer the stored one.
        target.hash = "";
        if (snapshot.bodies) delete snapshot.bodies[target.id];
      }
      return [id];
    }
    if (command.kind === "create") {
      if (find(tree, id)) return [];
      var name = command.name || item.name || "Untitled";
      var created = item.kind === "folder"
        ? folderNode(id, name, item.parentId)
        : textNode(id, name, command.text || "", item.parentId);
      var rootId = snapshot.rootId || snapshot.root || "";
      var intoRoot = !item.parentId || item.parentId === rootId || !find(tree, item.parentId);
      var siblings = intoRoot ? (tree.push(created), tree) : insertInto(tree, item.parentId, created);
      return siblings ? [id] : [];
    }
    if (command.kind === "reorder") {
      var groups = command.groups || [];
      var parents = command.parents || [];
      groups.forEach(function (group, at) {
        var parent = parents[at] || snapshot.rootId || "";
        // The tree is the content of the root folder, so a group in the root
        // folder reorders the tree itself.
        var holder = parent && parent !== snapshot.rootId && parent !== snapshot.root ? find(tree, parent) : null;
        var siblings = holder ? holder.children : tree;
        if (!siblings) return;
        var wanted = (group || []).map(function (id) { return find(tree, id); }).filter(Boolean);
        if (!wanted.length) return;
        // Reorder only the given rows, leaving any row the group does not
        // mention (a stale list) where it was.
        var order = wanted.map(function (node) { return node.id; });
        var next = siblings.slice();
        var slots = [];
        next.forEach(function (node, i) { if (order.indexOf(node.id) !== -1) slots.push(i); });
        slots.forEach(function (slot, i) { next[slot] = wanted[i]; });
        siblings.length = 0;
        Array.prototype.push.apply(siblings, next);
      });
      return [];
    }
    if (command.kind === "move") {
      // A move is the same edit in the cache as it is in Drive: the row leaves
      // its old folder and joins the new one. Rows the caller named but that no
      // longer exist are skipped, so a stale selection cannot create anything.
      var moved = [];
      (command.ids || []).forEach(function (target) {
        var node = find(tree, target);
        if (!node) return;
        var toRoot = !command.parentId || command.parentId === snapshot.rootId || command.parentId === snapshot.root;
        var holder = toRoot ? null : find(tree, command.parentId);
        // A destination that is not a folder is a stale list, not a request to
        // put the row somewhere that does not exist.
        if (!toRoot && (!holder || holder.kind !== "folder")) return;
        // A folder cannot be moved inside itself or inside its own content.
        if (node.kind === "folder") {
          var inside = false;
          walk([node], function (child) { if (child.id === command.parentId) inside = true; });
          if (inside) return;
        }
        if (!remove(tree, target)) return;
        var siblings = holder ? (holder.children = holder.children || []) : tree;
        siblings.push(node);
        moved.push(target);
      });
      return moved;
    }
    if (command.kind === "collapse") {
      (command.ids || []).forEach(function (target) {
        var node = find(tree, target);
        if (node) node.collapsed = !!command.collapsed;
      });
      return command.ids || [];
    }
    return [];
  }

  // The ordering and collapse state of every folder, as the centralized index
  // stores it. A row that never reached Drive has no place in it yet.
  var INDEX_VERSION = 2;

  // Every folder's children are keyed by that folder's id, and the root folder
  // is a folder like any other: its children are keyed by rootId. This has to
  // match library.js applyIndex(), which looks the root up by rootId; keying the
  // root as "" wrote a reorder that the next read could never find, so the order
  // silently fell back to Drive's own order.
  function indexOf(tree, rootId) {
    var root = rootId || "";
    var index = { order: {}, collapsed: {}, v: INDEX_VERSION };
    (function walkFolders(nodes, parentId) {
      var ids = [];
      (nodes || []).forEach(function (node) {
        // A row that never reached Drive is skipped, but its folder is still
        // walked so nothing nested inside it is lost.
        if (String(node.id).indexOf("local-") !== 0) {
          ids.push(node.id);
          if (node.collapsed) index.collapsed[node.id] = "1";
        }
        if (node.kind === "folder") walkFolders(node.children, node.id);
      });
      index.order[parentId] = ids;
    })(tree, root);
    // A library with no root id cannot name its root folder; recording it under
    // "" would be the same unreachable key again, so it is left out and the
    // order simply is Drive's own until a real root id is known.
    if (!root) delete index.order[""];
    return index;
  }

  // --- the queue itself -----------------------------------------------------

  function create(storage) {
    var cached = null;
    var writes = Promise.resolve();

    function serialize(fn) {
      var operation = writes.then(fn);
      writes = operation.catch(function () {});
      return operation;
    }

    async function read() {
      if (cached) return cached;
      if (!storage) { cached = []; return cached; }
      var stored = (await storage.get(KEY))[KEY];
      cached = Array.isArray(stored) ? stored : [];
      return cached;
    }

    async function write(list) {
      if (!storage) throw new Error("Cannot save pending changes: storage unavailable.");
      try {
        if (list.length) await storage.set({ [KEY]: list });
        else await storage.remove(KEY);
      } catch (err) {
        throw new Error("Cannot save pending changes. Check browser storage and try again.");
      }
      cached = list;
    }

    async function push(commands) {
      return serialize(async function () {
      var list = (await read()).slice();
      var at = Date.now();
      (commands || []).forEach(function (command, order) {
        command.queuedAt = at;
        command.order = order;
        // A later edit of a row that has not reached Drive yet replaces the
        // queued write for it, so one new row costs one upload, not two. A
        // failure never strands the row: the entry keeps the newest state.
        var localId = command.item && (command.item.localId || (String(command.item.id || "").indexOf("local-") === 0 ? command.item.id : ""));
        var at2 = localId ? list.findIndex(function (existing) {
          var other = existing.item && (existing.item.localId || existing.item.id || "");
          return other === localId && existing.kind !== "delete" && existing.kind !== "order";
        }) : -1;
        if (at2 !== -1 && command.kind !== "delete") { list[at2] = command; return; }
        if (at2 !== -1 && command.kind === "delete") { list.splice(at2, 1); return; }
        list.push(command);
      });
      // Never discard unsynced edits. Refuse new work until the queue is sent.
      if (list.length > MAX) throw new Error("Pending changes are full. Sync before editing again.");
      await write(list);
      return list;
      });
    }

    async function all() { await writes; return (await read()).slice().sort(byOrder); }

    async function remove(ids) {
      return serialize(async function () {
      var drop = new Set(ids || []);
      var list = (await read()).filter(function (command) { return !drop.has(command.id); });
      await write(list);
      return list;
      });
    }

    // Only rebase the exact first edit the user reviewed. A newly queued or
    // modified entry invalidates the preview instead of overwriting it.
    async function rebaseFirst(id, expectedText, expectedBase, newBase, useDrive) {
      return serialize(async function () {
        var list = (await read()).slice();
        var first = list.slice().sort(byOrder)[0];
        if (!first || first.id !== id || first.kind !== "edit" ||
            first.text !== expectedText || (first.baseText === undefined ? null : first.baseText) !== expectedBase)
          throw new Error("Pending changes changed. Review them again.");
        if (useDrive && list.some(function (entry) {
          return entry.id !== id && entry.kind === "edit" && entry.item && first.item && entry.item.id === first.item.id;
        })) throw new Error("Later edits depend on this snippet. Copy the versions and review them first.");
        var at = list.findIndex(function (entry) { return entry.id === id; });
        var updated = Object.assign({}, first, { baseText: newBase });
        if (useDrive) {
          updated.text = newBase;
          if (updated.command) updated.command = Object.assign({}, updated.command, { text: newBase });
        }
        list[at] = updated;
        await write(list);
      });
    }

    async function clear() { return serialize(function () { return write([]); }); }

    function byOrder(a, b) {
      return (a.queuedAt || 0) - (b.queuedAt || 0) || (a.order || 0) - (b.order || 0);
    }

    return { all: all, push: push, remove: remove, clear: clear, rebaseFirst: rebaseFirst };
  }

  return {
    create: create, apply: apply, find: find, KEY: KEY, indexOf: indexOf,
  };
});
