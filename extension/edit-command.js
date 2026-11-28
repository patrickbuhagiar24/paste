/* Build and validate queue entries independently of the service worker. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.PastexEditCommand = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  function create(localId) {
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

    return { queuedCommand: queuedCommand, validate: validateLocalEdit };
  }
  return { create: create };
});
