/* Google Drive is the source of truth. No snippet data is persisted locally. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.PastexLibrary = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  async function connect(native, drive) {
    var config = await native.readConfig();
    if (!config.serviceAccount) throw new Error("Configure service-account.json in ~/.config/pastex.");
    if (!config.drive || !config.drive.folderId) throw new Error("Configure google-drive.json in ~/.config/pastex.");
    var account = drive.parseServiceAccount(JSON.stringify(config.serviceAccount));
    var token = await drive.fetchAccessToken(account);
    var rootId = config.drive.folderId;
    var folder = await drive.getFolder(token, rootId);
    if (!folder || folder.mimeType !== drive.FOLDER_MIME) throw new Error("The configured Drive ID is not a folder.");
    return { token: token, rootId: rootId };
  }

  async function loadTree(drive, token, rootId) {
    var visited = new Set();
    async function visit(folderId) {
      if (visited.has(folderId)) throw new Error("Drive folder cycle detected.");
      visited.add(folderId);
      var files = await drive.listChildren(token, folderId);
      var nodes = [];
      for (var i = 0; i < files.length; i++) {
        var file = files[i];
        if (file.mimeType === drive.FOLDER_MIME) {
          var folder = { id: file.id, kind: "folder", name: file.name, children: [] };
          try { folder.children = await visit(file.id); }
          catch (err) { folder.loadError = err.message || String(err); }
          nodes.push(folder);
        } else if ((file.mimeType || "").indexOf("application/vnd.google-apps.") !== 0 &&
                   (file.mimeType === drive.TEXT_MIME || /\.txt$/i.test(file.name || ""))) {
          var node = { id: file.id, kind: "text", name: (file.name || "").replace(/\.txt$/i, ""),
            driveName: file.name, text: "" };
          try { node.text = await drive.downloadText(token, file.id); }
          catch (err) { node.loadError = err.message || String(err); }
          nodes.push(node);
        }
      }
      return nodes;
    }
    return visit(rootId);
  }

  async function load(native, drive) {
    var session = await connect(native, drive);
    var tree = await loadTree(drive, session.token, session.rootId);
    var warnings = [];
    function collect(nodes) {
      nodes.forEach(function (node) {
        if (node.loadError) warnings.push(node.name + ": " + node.loadError);
        if (node.children) collect(node.children);
      });
    }
    collect(tree);
    return { tree: tree, session: session, warnings: warnings };
  }

  return { connect: connect, loadTree: loadTree, load: load };
});
