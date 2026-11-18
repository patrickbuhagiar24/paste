/* Pastex Side Panel: snippet library, preview, editor, Drive sync.
 *
 * One click on a text snippet inserts it into the active tab (desktop
 * parity); when no editable field is focused the text is copied to the
 * clipboard instead, so the click never loses anything.
 */

(function () {
  "use strict";

  var Store = window.PastexStore;
  var Drive = window.PastexDrive;
  var Native = window.PastexNative;

  var tree = [];
  var settings = Store.defaultSettings();
  var drive = Store.defaultDrive();
  // Credentials from ~/.config/pastex via the native host (apt installs).
  // When the host is absent, the browser-storage form is used instead.
  var fileFolderId = "";
  var fileSaEmail = "";
  var syncFolderId = "";
  var selectedId = "";
  var query = "";
  var searchTimer = 0;
  var syncing = false;

  function $(id) {
    return document.getElementById(id);
  }

  function setStatus(text) {
    $("status").textContent = text;
  }

  function esc(text) {
    return String(text === undefined || text === null ? "" : text);
  }

  // --- tree rendering ---

  function isCollapsed(id) {
    return settings.collapsed.indexOf(id) !== -1;
  }

  function setCollapsed(id, collapsed) {
    var at = settings.collapsed.indexOf(id);
    if (collapsed && at === -1) {
      settings.collapsed.push(id);
    } else if (!collapsed && at !== -1) {
      settings.collapsed.splice(at, 1);
    }
    Store.saveSettings(settings);
  }

  function clearTree() {
    var host = $("tree");
    while (host.firstChild) {
      host.removeChild(host.firstChild);
    }
  }

  function miniButton(title, text, onClick) {
    var button = document.createElement("button");
    button.className = "mini";
    button.title = title;
    button.textContent = text;
    button.addEventListener("click", function (event) {
      event.stopPropagation();
      onClick();
    });
    return button;
  }

  function renderRow(node, depth, host) {
    var row = document.createElement("div");
    row.className = "row" + (node.id === selectedId ? " selected" : "");
    row.setAttribute("role", "treeitem");
    row.style.paddingLeft = 4 + depth * 2 + "px";

    var toggle = document.createElement("span");
    toggle.className = "toggle";
    toggle.textContent = node.kind === "folder" ? (isCollapsed(node.id) ? "▸" : "▾") : "📄";
    row.appendChild(toggle);

    var label = document.createElement("span");
    label.className = "label";
    label.textContent = esc(node.name) || "(untitled)";
    label.title = esc(node.name);
    row.appendChild(label);

    if (node.kind === "text") {
      row.appendChild(
        miniButton("Insert into page", "⚡", function () {
          insertNode(node);
        })
      );
    }
    row.appendChild(
      miniButton("Move up", "↑", function () {
        moveSelected(node.id, -1);
      })
    );
    row.appendChild(
      miniButton("Move down", "↓", function () {
        moveSelected(node.id, 1);
      })
    );
    row.appendChild(
      miniButton(node.kind === "folder" ? "Rename folder" : "Rename", "✎", function () {
        openEditor(node.kind === "folder" ? "rename-folder" : "rename-text", node);
      })
    );
    row.appendChild(
      miniButton("Delete", "🗑", function () {
        deleteNode(node);
      })
    );

    row.addEventListener("click", function () {
      if (node.kind === "folder") {
        setCollapsed(node.id, !isCollapsed(node.id));
        render();
      } else {
        selectNode(node.id);
        insertNode(node);
      }
    });
    host.appendChild(row);

    if (node.kind === "folder" && !isCollapsed(node.id)) {
      var children = document.createElement("div");
      children.className = "children";
      children.setAttribute("role", "group");
      (node.children || []).forEach(function (child) {
        renderRow(child, depth + 1, children);
      });
      host.appendChild(children);
    }
  }

  function renderSearchResults(matches) {
    clearTree();
    var host = $("tree");
    matches.forEach(function (node) {
      var row = document.createElement("div");
      row.className = "row" + (node.id === selectedId ? " selected" : "");
      var icon = document.createElement("span");
      icon.className = "toggle";
      icon.textContent = "📄";
      row.appendChild(icon);
      var label = document.createElement("span");
      label.className = "label";
      label.textContent = esc(node.name) || "(untitled)";
      label.title = esc(node.name);
      row.appendChild(label);
      row.appendChild(
        miniButton("Insert into page", "⚡", function () {
          insertNode(node);
        })
      );
      row.addEventListener("click", function () {
        selectNode(node.id);
        insertNode(node);
      });
      host.appendChild(row);
    });
    $("empty").hidden = matches.length > 0;
    if (!matches.length) {
      $("empty").querySelector("p").innerHTML = "No snippets match.<br />Clear the search to browse everything.";
    }
  }

  function render() {
    if (query) {
      renderSearchResults(Store.search(tree, query));
    } else {
      clearTree();
      var host = $("tree");
      tree.forEach(function (node) {
        renderRow(node, 0, host);
      });
      var hasNodes = tree.length > 0;
      $("empty").hidden = hasNodes;
      if (!hasNodes) {
        $("empty").querySelector("p").innerHTML = "No snippets yet.<br />Create a folder or a text snippet to begin.";
      }
    }
    renderPreview();
  }

  function selectNode(id) {
    selectedId = id;
    render();
  }

  function selectedNode() {
    return selectedId ? Store.findById(tree, selectedId) : null;
  }

  function renderPreview() {
    var node = selectedNode();
    var isText = !!node && node.kind === "text";
    $("preview-title").textContent = node ? esc(node.name) : "Select a snippet.";
    $("preview").textContent = isText ? node.text || "" : "";
    $("btn-insert").disabled = !isText;
    $("btn-edit").disabled = !isText;
  }

  async function refresh() {
    tree = await Store.loadTree();
    if (selectedId && !Store.findById(tree, selectedId)) {
      selectedId = "";
    }
    render();
  }

  // --- insert into the page (or copy as the fallback) ---

  async function insertNode(node) {
    setStatus("Inserting…");
    var response = null;
    try {
      response = await chrome.runtime.sendMessage({
        type: "pastex-sidepanel-insert",
        id: node.id,
        text: node.text || "",
      });
    } catch (err) {
      response = { ok: false };
    }
    if (response && response.ok) {
      await Store.pushRecent(node.id);
      setStatus("Inserted into the page.");
      return;
    }
    try {
      await navigator.clipboard.writeText(node.text || "");
      setStatus("No editable field focused — copied. Press Ctrl+V.");
    } catch (err) {
      setStatus("Could not insert or copy.");
    }
  }

  // --- create / edit / delete / move ---

  var editorMode = "";
  var editorNodeId = "";

  function folderOptions() {
    var options = [{ id: "", name: "(top level)" }];
    Store.walk(tree, function (node) {
      if (node.kind === "folder") {
        options.push({ id: node.id, name: node.name });
      }
    });
    return options;
  }

  function openEditor(mode, node) {
    editorMode = mode;
    editorNodeId = node ? node.id : "";
    var titles = {
      "new-folder": "New folder",
      "new-text": "New snippet",
      "edit-text": "Edit snippet",
      "rename-text": "Rename snippet",
      "rename-folder": "Rename folder",
    };
    $("editor-title").textContent = titles[mode] || "Edit";
    var isNew = mode === "new-folder" || mode === "new-text";
    $("editor-parent-label").style.display = isNew ? "" : "none";
    $("editor-parent").style.display = isNew ? "" : "none";
    if (isNew) {
      var select = $("editor-parent");
      while (select.firstChild) {
        select.removeChild(select.firstChild);
      }
      folderOptions().forEach(function (option) {
        var el = document.createElement("option");
        el.value = option.id;
        el.textContent = option.name;
        select.appendChild(el);
      });
      // Default parent: the selected folder, when there is one.
      var selected = selectedNode();
      if (selected && selected.kind === "folder") {
        select.value = selected.id;
      }
    }
    var showText = mode === "new-text" || mode === "edit-text";
    $("editor-text-wrap").style.display = showText ? "" : "none";
    $("editor-name").value = node ? esc(node.name) : "";
    $("editor-text").value = node && node.kind === "text" ? node.text || "" : "";
    $("editor-overlay").hidden = false;
    $("editor-name").focus();
    $("editor-name").select();
  }

  function closeEditor() {
    $("editor-overlay").hidden = true;
  }

  async function saveEditor() {
    var name = $("editor-name").value.trim();
    if (!name) {
      setStatus("A name is required.");
      return;
    }
    if (editorMode === "new-folder" || editorMode === "new-text") {
      var parentId = $("editor-parent").value;
      var created = editorMode === "new-folder" ? Store.makeFolder(name) : Store.makeText(name, $("editor-text").value);
      if (!parentId) {
        tree.push(created);
      } else {
        var parent = Store.findById(tree, parentId);
        if (!parent || parent.kind !== "folder") {
          setStatus("That folder no longer exists.");
          return;
        }
        parent.children = parent.children || [];
        parent.children.push(created);
        Store.touch(parent);
      }
      selectedId = created.id;
    } else {
      var node = Store.findById(tree, editorNodeId);
      if (!node) {
        setStatus("That snippet no longer exists.");
        closeEditor();
        await refresh();
        return;
      }
      node.name = name;
      if (editorMode === "edit-text") {
        node.text = $("editor-text").value;
      }
      Store.touch(node);
    }
    await Store.saveTree(tree);
    closeEditor();
    await refresh();
    setStatus("Saved.");
  }

  async function deleteNode(node) {
    var what = node.kind === "folder" ? 'folder "' + node.name + '" and everything inside it' : 'snippet "' + node.name + '"';
    if (!window.confirm("Delete " + what + "?")) {
      return;
    }
    Store.removeById(tree, node.id);
    if (selectedId === node.id) {
      selectedId = "";
    }
    await Store.saveTree(tree);
    await refresh();
    setStatus("Deleted.");
  }

  async function moveSelected(id, delta) {
    Store.moveNode(tree, id, delta);
    await Store.saveTree(tree);
    await refresh();
  }

  // --- Google Drive sync ---

  function viaFiles() {
    return !!fileFolderId;
  }

  function effectiveFolderId() {
    return fileFolderId || drive.folderId;
  }

  async function refreshNative() {
    fileFolderId = "";
    fileSaEmail = "";
    try {
      var status = await Native.status();
      if (status && status.drive && status.drive.configured) {
        fileFolderId = status.drive.folderId || "";
      }
      if (status && status.serviceAccount && status.serviceAccount.configured) {
        fileSaEmail = status.serviceAccount.email || "";
      }
    } catch (err) {
      /* host absent: browser storage is used */
    }
    renderDriveBar();
  }

  // Service account: ~/.config/pastex first, browser storage as fallback.
  async function resolveServiceAccount() {
    try {
      var config = await Native.readConfig();
      if (config && config.serviceAccount && config.serviceAccount.client_email) {
        return { sa: config.serviceAccount, viaFiles: true };
      }
    } catch (err) {
      /* fall through to browser storage */
    }
    var stored = await Store.loadServiceAccount().catch(function () {
      return null;
    });
    return { sa: stored, viaFiles: false };
  }

  function driveStatusLine() {
    if (syncing) {
      return "Drive: syncing…";
    }
    if (!effectiveFolderId()) {
      return "Drive: not connected.";
    }
    var via = viaFiles() ? "files" : "browser";
    if (drive.lastSyncAt) {
      var when = drive.lastSyncAt.slice(0, 16).replace("T", " ");
      return "Drive (" + via + "): synced " + when + (drive.lastSummary ? " (" + drive.lastSummary + ")" : "");
    }
    return "Drive (" + via + "): connected, never synced.";
  }

  function renderDriveBar() {
    $("drive-status").textContent = driveStatusLine();
    $("drive-status").title = drive.folderName ? "Folder: " + drive.folderName : "";
    var busy = syncing;
    $("btn-sync").disabled = busy;
    $("btn-upload-all").disabled = busy;
    $("btn-download-all").disabled = busy;
  }

  async function runDriveSync(mode) {
    if (syncing) {
      return;
    }
    await refreshNative();
    var resolved = await resolveServiceAccount();
    if (!resolved.sa) {
      setStatus("Connect a service account in Settings first.");
      chrome.runtime.openOptionsPage();
      return;
    }
    var sa = resolved.sa;
    var folderId = effectiveFolderId();
    if (!folderId) {
      setStatus("Choose a Drive folder in Settings first.");
      chrome.runtime.openOptionsPage();
      return;
    }
    syncFolderId = folderId;
    if (mode === "upload" && !window.confirm("Upload ALL local snippets to Drive, overwriting newer remote copies?")) {
      return;
    }
    if (mode === "download" && !window.confirm("Replace ALL local snippets with the Drive copies?")) {
      return;
    }
    syncing = true;
    renderDriveBar();
    try {
      setStatus("Drive: signing in…");
      var token = await Drive.fetchAccessToken(sa);
      setStatus("Drive: listing folder…");
      var remote = await Drive.walkRemote(token, folderId);
      tree = await Store.loadTree();
      var localEntries = Store.flattenWithPaths(tree).map(function (entry) {
        return { path: entry.path, updatedAt: entry.node.updatedAt || "" };
      });
      var ops;
      if (mode === "upload") {
        ops = Drive.planUploadAll(localEntries, remote.texts);
      } else if (mode === "download") {
        tree = [];
        ops = Drive.planDownloadAll(remote.texts);
      } else {
        ops = Drive.planSync(localEntries, remote.texts);
      }
      var counts = { up: 0, down: 0, same: 0, errors: [] };
      var folderCache = {};
      var localByPath = {};
      Store.flattenWithPaths(tree).forEach(function (entry) {
        localByPath[entry.path] = entry.node;
      });
      var total = ops.length;
      for (var i = 0; i < ops.length; i++) {
        var op = ops[i];
        setStatus("Drive: " + (i + 1) + "/" + total + " " + op.path);
        try {
          await applyOp(token, op, localByPath, folderCache, counts);
        } catch (err) {
          counts.errors.push(op.path + ": " + (err && err.message ? err.message : err));
        }
      }
      await Store.saveTree(tree);
      var summary = counts.up + " up, " + counts.down + " down, " + counts.same + " same";
      if (counts.errors.length) {
        summary += ", " + counts.errors.length + " failed";
      }
      drive.lastSyncAt = new Date().toISOString();
      drive.lastSummary = summary;
      await Store.saveDrive(drive);
      await refresh();
      if (counts.errors.length) {
        setStatus("Drive sync finished with errors: " + counts.errors[0]);
      } else {
        setStatus("Drive sync finished: " + summary + ".");
      }
    } catch (err) {
      setStatus("Drive sync failed: " + (err && err.message ? err.message : err));
    } finally {
      syncing = false;
      renderDriveBar();
    }
  }

  async function applyOp(token, op, localByPath, folderCache, counts) {
    if (op.type === "noop") {
      counts.same++;
      return;
    }
    if (op.type === "upload-new" || op.type === "upload-update") {
      var node = localByPath[op.path];
      if (!node) {
        return;
      }
      var segments = op.path.split("/");
      if (op.type === "upload-new") {
        var parentId = await Drive.ensureFolderPath(token, syncFolderId, segments.slice(0, -1), undefined, folderCache);
        var created = await Drive.uploadNew(token, parentId, segments[segments.length - 1], node.text || "");
        node.updatedAt = (created && created.modifiedTime) || Store.nowIso();
      } else {
        var updated = await Drive.updateText(token, op.remoteId, node.text || "");
        node.updatedAt = (updated && updated.modifiedTime) || Store.nowIso();
      }
      counts.up++;
      return;
    }
    if (op.type === "download-new" || op.type === "download-update") {
      var text = await Drive.downloadText(token, op.remoteId);
      Store.mergeDownloaded(tree, [{ segments: op.path.split("/"), text: text, updatedAt: op.remoteModified || Store.nowIso() }]);
      var merged = Store.flattenWithPaths(tree);
      for (var i = 0; i < merged.length; i++) {
        if (merged[i].path === op.path) {
          localByPath[op.path] = merged[i].node;
        }
      }
      counts.down++;
      return;
    }
  }

  // --- events ---

  $("search").addEventListener("input", function (event) {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(function () {
      query = event.target.value;
      render();
    }, 150);
  });

  $("btn-folder").addEventListener("click", function () {
    openEditor("new-folder", null);
  });

  $("btn-text").addEventListener("click", function () {
    openEditor("new-text", null);
  });

  $("btn-empty-text").addEventListener("click", function () {
    openEditor("new-text", null);
  });

  $("btn-options").addEventListener("click", function () {
    chrome.runtime.openOptionsPage();
  });

  $("btn-sync").addEventListener("click", function () {
    runDriveSync("sync");
  });

  $("btn-upload-all").addEventListener("click", function () {
    runDriveSync("upload");
  });

  $("btn-download-all").addEventListener("click", function () {
    runDriveSync("download");
  });

  $("btn-insert").addEventListener("click", function () {
    var node = selectedNode();
    if (node && node.kind === "text") {
      insertNode(node);
    }
  });

  $("btn-edit").addEventListener("click", function () {
    var node = selectedNode();
    if (node && node.kind === "text") {
      openEditor("edit-text", node);
    }
  });

  $("editor-save").addEventListener("click", saveEditor);
  $("editor-cancel").addEventListener("click", closeEditor);
  $("editor-overlay").addEventListener("click", function (event) {
    if (event.target === $("editor-overlay")) {
      closeEditor();
    }
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !$("editor-overlay").hidden) {
      closeEditor();
    }
  });

  chrome.storage.onChanged.addListener(function (changes, area) {
    if (area !== "local") {
      return;
    }
    if (changes[Store.TREE_KEY]) {
      refresh();
    }
    if (changes[Store.DRIVE_KEY] || changes[Store.SA_KEY]) {
      Store.loadDrive().then(function (loaded) {
        drive = loaded;
        renderDriveBar();
      });
    }
  });

  // --- init ---

  // The Settings page may rewrite the config files while the panel sits
  // open; re-probe the native host whenever the panel regains focus.
  window.addEventListener("focus", function () {
    refreshNative();
  });

  (async function init() {
    try {
      settings = await Store.loadSettings();
      drive = await Store.loadDrive();
      await refresh();
      await refreshNative();
    } catch (err) {
      setStatus("Storage is unavailable: " + (err && err.message ? err.message : err));
    }
  })();
})();
