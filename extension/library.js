/* Google Drive is the source of truth; session caching is handled separately. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.PastexLibrary = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  async function connect(native, drive) {
    var config = await native.readConfig();
    if (!config.serviceAccount) throw new Error(config.serviceAccountError || "Configure service-account.json in ~/.config/pastex.");
    if (!config.drive || !config.drive.folderId) throw new Error(config.driveError || "Configure google-drive.json in ~/.config/pastex.");
    var account = drive.parseServiceAccount(JSON.stringify(config.serviceAccount));
    var token = await drive.fetchAccessToken(account);
    var rootId = config.drive.folderId;
    var folder = await drive.getFolder(token, rootId);
    if (!folder || folder.mimeType !== drive.FOLDER_MIME) throw new Error("The configured Drive ID is not a folder.");
    return { token: token, rootId: rootId };
  }

  // Limit network requests, not recursion: folders can be traversed in
  // parallel without deadlocking when all workers encounter subfolders.
  async function loadTree(drive, token, rootId, onUpdate) {
    var active = 0, waiting = [];
    function request(fn) {
      return new Promise(function (resolve, reject) {
        function run() {
          active++;
          Promise.resolve().then(fn).then(resolve, reject).finally(function () {
            active--;
            if (waiting.length) waiting.shift()();
          });
        }
        if (active < 6) run(); else waiting.push(run);
      });
    }
    var visited = new Set();
    var tree = [];
    function update() { if (onUpdate) onUpdate(tree); }
    async function visit(folderId, nodes) {
      if (visited.has(folderId)) throw new Error("Drive folder cycle detected.");
      visited.add(folderId);
      var files = await request(function () { return drive.listChildren(token, folderId); });
      // Unranked/new files retain Drive's natural order after ranked entries.
      function rank(file) {
        var raw = file.properties && file.properties.pastex_order;
        if (typeof raw !== "string" || !/^(0|[1-9][0-9]*)$/.test(raw)) return Number.MAX_SAFE_INTEGER;
        var value = Number(raw);
        return Number.isSafeInteger(value) ? value : Number.MAX_SAFE_INTEGER;
      }
      files = files.map(function (file, index) { return { file: file, index: index }; });
      files.sort(function (a, b) { return rank(a.file) - rank(b.file) || a.index - b.index; });
      files = files.map(function (item) { return item.file; });
      var jobs = [];
      files.forEach(function (file) {
        if (file.mimeType === drive.FOLDER_MIME) {
          var folder = { id: file.id, kind: "folder", name: file.name, children: [] };
          nodes.push(folder);
          jobs.push(visit(file.id, folder.children).catch(function (err) {
            folder.loadError = err.message || String(err);
          }).finally(update));
        } else if ((file.mimeType || "").indexOf("application/vnd.google-apps.") !== 0 &&
                   (file.mimeType === drive.TEXT_MIME || /\.txt$/i.test(file.name || ""))) {
          var node = { id: file.id, kind: "text", name: (file.name || "").replace(/\.txt$/i, ""),
            driveName: file.name, text: "", loadError: "Loading from Google Drive…" };
          nodes.push(node);
          jobs.push(request(function () { return drive.downloadText(token, file.id); }).then(function (text) {
            node.text = text;
            delete node.loadError;
          }).catch(function (err) { node.loadError = err.message || String(err); }).finally(update));
        }
      });
      update();
      await Promise.all(jobs);
    }
    await visit(rootId, tree);
    return tree;
  }

  async function load(native, drive, onUpdate) {
    var session = await connect(native, drive);
    var tree = await loadTree(drive, session.token, session.rootId, function (partial) {
      if (onUpdate) onUpdate(partial, session);
    });
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
