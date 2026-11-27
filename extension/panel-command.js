/* Translate panel actions into queue commands. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.PastexPanelCommand = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  function parentOf(nodes, id, parentId) {
    for (var i = 0; i < nodes.length; i++) {
      if (nodes[i].id === id) return parentId;
      if (nodes[i].kind === "folder") {
        var found = parentOf(nodes[i].children || [], id, nodes[i].id);
        if (found) return found;
      }
    }
    return "";
  }

  // The queue speaks one small vocabulary ("create", "edit", "rename",
  // "delete", "collapse", "reorder"); the panel still uses its editor actions.
  // The row itself travels along, because the write needs its kind, the name it
  // had and whether it already reached Drive.
  function identifyCommand(command, index, tree, rootId) {
    if (command.action === "save") {
      var live = command.node && command.node.id ? index.byId.get(command.node.id) : null;
      if (command.mode === "new-folder" || command.mode === "new-text") {
        command.kind = "create";
        command.item = { kind: command.mode === "new-folder" ? "folder" : "text",
          name: command.name, parentId: command.parentId, localId: "" };
        return command;
      }
      if (!live) return null;
      command.kind = command.mode === "rename-folder" || (command.mode === "rename-text" && !command.text)
        ? "rename" : "edit";
      // A snippet whose name alone changes is a rename; its body is kept.
      if (command.mode === "rename-text") command.kind = "rename";
      command.item = rowIdentity(live, tree, rootId);
      return command;
    }
    if (command.action === "move") {
      command.kind = "move";
      command.ids = (command.ids || []).filter(Boolean);
      return command.ids.length ? command : null;
    }
    if (command.action === "delete") { command.kind = "delete"; return command; }
    if (command.action === "collapse") { command.kind = "collapse"; return command; }
    if (command.action === "reorder") { command.kind = "reorder"; return command; }
    return command.kind ? command : null;
  }

  function rowIdentity(node, tree, rootId) {
    return {
      id: node.id, localId: node.id.indexOf("local-") === 0 ? node.id : "",
      kind: node.kind, name: node.name, driveName: node.driveName,
      parentId: parentOf(tree, node.id, rootId) || "",
      isNew: node.id.indexOf("local-") === 0,
    };
  }

  return { parentOf: parentOf, identify: identifyCommand };
});
