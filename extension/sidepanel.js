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
  var Library = window.PastexLibrary;

  var tree = [];
  var settings = { collapsed: [] };
  var rootId = "";
  var ready = false;
  var selectedId = "";
  var query = "";
  var searchTimer = 0;
  var busy = false;

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
    label.textContent = (esc(node.name) || "(untitled)") + (node.loadError ? " ⚠" : "");
    label.title = node.loadError || esc(node.name);
    row.appendChild(label);

    if (node.kind === "text") {
      row.appendChild(
        miniButton("Insert into page", "⚡", function () {
          insertNode(node);
        })
      );
    }
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
      label.textContent = (esc(node.name) || "(untitled)") + (node.loadError ? " ⚠" : "");
      label.title = node.loadError || esc(node.name);
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
        $("empty").querySelector("p").textContent = ready ? "No snippets in the Google Drive folder yet." : "Connect to Google Drive to show your snippets.";
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
    $("preview").textContent = node && node.loadError ? "Could not read from Drive: " + node.loadError : (isText ? node.text || "" : "");
    $("btn-insert").disabled = !isText || !!node.loadError;
    $("btn-edit").disabled = !isText || !!node.loadError;
  }

  function renderDriveBar() {
    $("drive-status").textContent = busy ? "Drive: loading…" : (ready ? "Google Drive connected" : "Google Drive unavailable");
    $("btn-sync").disabled = busy;
    $("btn-folder").disabled = busy || !ready;
    $("btn-text").disabled = busy || !ready;
    $("btn-empty-text").disabled = busy || !ready;
  }

  async function refresh() {
    if (busy) return false;
    busy = true;
    renderDriveBar();
    setStatus("Loading snippets from Google Drive…");
    try {
      var result = await Library.load(Native, Drive);
      tree = result.tree;
      rootId = result.session.rootId;
      ready = true;
      if (selectedId && !Store.findById(tree, selectedId)) selectedId = "";
      render();
      var warnings = result.warnings || [];
      setStatus(warnings.length ? "Drive loaded with " + warnings.length + " unreadable item(s): " + warnings[0] :
        "Google Drive loaded: " + Store.flatten(tree).length + " snippets.");
      return true;
    } catch (err) {
      tree = [];
      rootId = "";
      ready = false;
      selectedId = "";
      render();
      setStatus("Could not load Google Drive: " + (err.message || err));
      return false;
    } finally {
      busy = false;
      renderDriveBar();
    }
  }

  // --- insert into the page (or copy as the fallback) ---

  async function insertNode(node) {
    if (node.loadError) { setStatus("Cannot read this Drive item: " + node.loadError); return; }
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
      setStatus("Inserted into the page.");
      return;
    }
    if (response && response.reason === "site-not-allowed") {
      setStatus("Site not allowed. See ~/.config/pastex/sites.json and README.");
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
    if (node && node.loadError && mode === "edit-text") {
      setStatus("Cannot edit an unreadable Drive item: " + node.loadError);
      return;
    }
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

  async function currentSession() {
    if (!ready) throw new Error("Load Google Drive first.");
    var session = await Library.connect(Native, Drive);
    if (session.rootId !== rootId) throw new Error("Drive folder changed; refresh the list first.");
    return session;
  }

  async function ensureAvailable(session, parentId, name, exceptId) {
    var children = await Drive.listChildren(session.token, parentId);
    if (children.some(function (child) { return child.name === name && child.id !== exceptId; })) {
      throw new Error("An item with that name already exists in this folder.");
    }
  }

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

  async function saveEditor() {
    var name = $("editor-name").value.trim();
    if (!name || name.indexOf("/") !== -1) {
      setStatus("Enter a name without /.");
      return;
    }
    if (busy) return;
    var mode = editorMode;
    var node = Store.findById(tree, editorNodeId);
    var parentId = mode === "new-folder" || mode === "new-text" ? ($("editor-parent").value || rootId) : parentOf(tree, editorNodeId, rootId);
    if (!parentId || ((mode !== "new-folder" && mode !== "new-text") && !node)) {
      setStatus("That item or folder is no longer available; refresh first.");
      return;
    }
    busy = true;
    renderDriveBar();
    setStatus("Saving to Google Drive…");
    var failure = "";
    try {
      var session = await currentSession();
      var driveName = mode === "new-text" ? name.replace(/\.txt$/i, "") + ".txt" : name;
      if (node && node.kind === "text") driveName = name.replace(/\.txt$/i, "") + (/\.txt$/i.test(node.driveName || "") ? ".txt" : "");
      if (!node || driveName !== node.driveName && driveName !== node.name) {
        await ensureAvailable(session, parentId, driveName, node && node.id);
      }
      if (mode === "new-folder") selectedId = await Drive.createFolder(session.token, name, parentId);
      else if (mode === "new-text") {
        var created = await Drive.uploadNew(session.token, parentId, name.replace(/\.txt$/i, ""), $("editor-text").value);
        selectedId = created.id;
      } else {
        if (mode === "edit-text" && $("editor-text").value !== node.text) {
          await Drive.updateText(session.token, node.id, $("editor-text").value);
        }
        if (driveName !== (node.driveName || node.name)) await Drive.renameFile(session.token, node.id, driveName);
      }
      closeEditor();
    } catch (err) {
      failure = "Could not save to Drive (some changes may have succeeded): " + (err.message || err);
    } finally {
      busy = false;
    }
    await refresh();
    if (failure) setStatus(failure);
  }

  async function deleteNode(node) {
    var what = node.kind === "folder" ? 'folder "' + node.name + '" and everything inside it' : 'snippet "' + node.name + '"';
    if (!window.confirm("Move " + what + " to Google Drive trash?")) return;
    if (busy) return;
    busy = true;
    renderDriveBar();
    var failure = "";
    try {
      var session = await currentSession();
      await Drive.trashFile(session.token, node.id);
      if (selectedId === node.id) selectedId = "";
    } catch (err) { failure = "Could not delete from Drive: " + (err.message || err); }
    finally { busy = false; }
    await refresh();
    if (failure) setStatus(failure);
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

  $("btn-sync").addEventListener("click", function () {
    refresh();
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
  // Re-read Drive on opening the panel or using the refresh button.
  renderDriveBar();
  refresh();
})();
