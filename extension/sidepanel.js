/* Pastex Side Panel: snippet library, editor, Drive sync.
 *
 * One click on a text snippet inserts it into the active tab (desktop
 * parity); when no editable field is focused the text is copied to the
 * clipboard instead, so the click never loses anything.
 */

(function () {
  "use strict";

  var Store = window.PastexStore;
  var Config = window.PastexConfigSource.create(
    window.PastexHandleStore, window.PastexConfigImport, window.PastexSites,
    window.PastexManifest, typeof fetch === "undefined" ? null : fetch
  );

  var tree = [];
  var index = Store.createIndex([]);
  var renderPending = false;
  var settings = { collapsed: [] };
  var rootId = "";
  var ready = false;
  var selectedId = "";
  var query = "";
  var searchTimer = 0;
  var busy = false;
  var loading = false;
  var refreshRequested = false;
  var progressTimer = 0;
  var fromCache = false;
  var backgroundRefresh = false;
  var configVersion = 0;
  var loadConfigVersion = 0;
  var selecting = false;
  var selectedItems = new Set();
  var lastCheckedAt = 0;
  // Edits made here are applied to the cache at once; their Drive write waits
  // in the worker's queue until the tab is left, so nothing is fetched while
  // the user works.
  var pendingWrites = 0;
  var savingLocal = false;

  function $(id) {
    return document.getElementById(id);
  }

  function rebuildIndex() {
    index = Store.createIndex(tree);
  }

  // Coalesce progress updates into one paint per frame.
  function scheduleRender() {
    if (renderPending) return;
    renderPending = true;
    var frame = window.requestAnimationFrame || function (fn) { return window.setTimeout(fn, 16); };
    frame(function () { renderPending = false; render(); });
  }

  // The header status is the only place that reports what just happened.
  // Callers pass a tone ("ok" or "error") so the stylesheet can colour the
  // line; the message itself always says what happened, so colour is never
  // the only signal.
  function setStatus(text, tone) {
    var node = $("status");
    node.textContent = text;
    if (tone) node.setAttribute("data-state", tone);
    else node.removeAttribute("data-state");
  }

  // The panel shows when the library was last confirmed with Drive, so the
  // age of the list is visible even when nothing changed.
  function relativeTime(timestamp) {
    if (!timestamp) return "never";
    var seconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
    if (seconds < 45) return "just now";
    var minutes = Math.round(seconds / 60);
    if (minutes < 60) return minutes + " min ago";
    var hours = Math.round(minutes / 60);
    if (hours < 24) return hours + " h ago";
    return Math.round(hours / 24) + " d ago";
  }

  function renderLastChecked() {
    var node = $("last-checked");
    if (!node) return;
    node.textContent = "Checked: " + relativeTime(lastCheckedAt);
    node.title = lastCheckedAt
      ? "Google Drive was last checked at " + new Date(lastCheckedAt).toLocaleTimeString()
      : "The library has not been checked yet.";
  }

  function noteChecked(timestamp) {
    if (!timestamp) return;
    lastCheckedAt = timestamp;
    renderLastChecked();
  }

  function esc(text) {
    return String(text === undefined || text === null ? "" : text);
  }

  // The in-page launcher is the same switch for the panel, so an open panel
  // tells the page it is open and says goodbye when it closes.
  function announcePanelState(open) {
    try {
      chrome.runtime.sendMessage({ type: "pastex-panel-state", open: !!open }).catch(function () {});
    } catch (ignored) { /* older Chrome contexts reject the send */ }
  }
  window.addEventListener("pagehide", function () { announcePanelState(false); });
  window.addEventListener("unload", function () { announcePanelState(false); });

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

  // Folder collapse state is stored in Drive properties, so a reload or a
  // different computer sees the same structure as the ordering metadata.
  function applyCollapsedFromTree() {
    settings.collapsed = index.folders.filter(function (node) { return node.collapsed; })
      .map(function (node) { return node.id; });
  }

  async function persistCollapsed(ids, collapsed) {
    var before = settings.collapsed.slice();
    ids.forEach(function (id) { setCollapsed(id, collapsed); });
    render();
    var result = await runMutation({ action: "collapse", ids: ids, collapsed: collapsed });
    if (!result) { settings.collapsed = before; render(); }
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
    if (busy) button.disabled = true;
    button.addEventListener("click", function (event) {
      event.stopPropagation();
      onClick();
    });
    return button;
  }

  function appendRowActions(row, node) {
    if (selecting) return;
    row.appendChild(miniButton(node.kind === "folder" ? "Rename folder" : "Rename", "✎", function () {
      openEditor(node.kind === "folder" ? "rename-folder" : "rename-text", node);
    }));
    if (node.kind === "text") {
      var edit = miniButton("Edit", "📝", function () { openEditor("edit-text", node); });
      if (node.loadError) { edit.disabled = true; edit.title = "Cannot edit: " + node.loadError; }
      row.appendChild(edit);
    }
    var remove = miniButton("Delete", "🗑", function () { deleteNodes([node]); });
    remove.setAttribute("data-kind", "danger");
    row.appendChild(remove);
  }

  function renderRow(node, depth, host) {
    var row = document.createElement("div");
    row.className = "row" + ((selecting ? selectedItems.has(node.id) : node.id === selectedId) ? " selected" : "");
    row.setAttribute("data-kind", node.kind === "folder" ? "folder" : "text");
    row.setAttribute("role", "treeitem");
    if (selecting) row.setAttribute("aria-selected", String(selectedItems.has(node.id)));
    row.style.paddingLeft = 4 + depth * 2 + "px";

    var toggle = document.createElement("span");
    toggle.className = "toggle";
    toggle.textContent = node.kind === "folder" ? (isCollapsed(node.id) ? "▸" : "▾") : "📄";
    row.appendChild(toggle);

    var label = document.createElement("span");
    label.className = "label";
    label.textContent = (selecting ? (selectedItems.has(node.id) ? "☑ " : "☐ ") : "") + (esc(node.name) || "(untitled)") + (node.loadError ? " ⚠" : "");
    label.title = node.loadError || esc(node.name);
    row.appendChild(label);

    appendRowActions(row, node);
    if (selecting && node.kind === "folder") {
      toggle.title = "Expand or collapse folder";
      toggle.setAttribute("role", "button");
      toggle.setAttribute("aria-label", toggle.title);
      toggle.tabIndex = 0;
      function toggleFolder(event) {
        event.stopPropagation();
        persistCollapsed([node.id], !isCollapsed(node.id));
      }
      toggle.addEventListener("click", toggleFolder);
      toggle.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggleFolder(event); }
      });
    }

    function activateRow() {
      if (selecting) {
        toggleSelection(node.id);
      } else if (node.kind === "folder") {
        persistCollapsed([node.id], !isCollapsed(node.id));
      } else {
        selectNode(node.id);
        insertNode(node);
      }
    }
    row.tabIndex = 0;
    row.addEventListener("click", activateRow);
    row.addEventListener("keydown", function (event) {
      if (event.target === row && (event.key === "Enter" || event.key === " ")) {
        event.preventDefault(); activateRow();
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
    var host = document.createDocumentFragment();
    matches.forEach(function (node) {
      var row = document.createElement("div");
      row.className = "row" + ((selecting ? selectedItems.has(node.id) : node.id === selectedId) ? " selected" : "");
      row.setAttribute("data-kind", "text");
      var icon = document.createElement("span");
      icon.className = "toggle";
      icon.textContent = "📄";
      row.appendChild(icon);
      var label = document.createElement("span");
      label.className = "label";
      label.textContent = (selecting ? (selectedItems.has(node.id) ? "☑ " : "☐ ") : "") + (esc(node.name) || "(untitled)") + (node.loadError ? " ⚠" : "");
      label.title = node.loadError || esc(node.name);
      row.appendChild(label);
      appendRowActions(row, node);
      function activateResult() {
        if (selecting) toggleSelection(node.id);
        else { selectNode(node.id); insertNode(node); }
      }
      row.setAttribute("role", "treeitem");
      if (selecting) row.setAttribute("aria-selected", String(selectedItems.has(node.id)));
      row.tabIndex = 0;
      row.addEventListener("click", activateResult);
      row.addEventListener("keydown", function (event) {
        if (event.target === row && (event.key === "Enter" || event.key === " ")) {
          event.preventDefault(); activateResult();
        }
      });
      host.appendChild(row);
    });
    $("tree").appendChild(host);
    $("empty").hidden = matches.length > 0;
    if (!matches.length) {
      $("empty").querySelector("p").innerHTML = "No snippets match.<br />Clear the search to browse everything.";
    }
  }

  function render() {
    if (query) {
      renderSearchResults(index.search(query));
    } else {
      clearTree();
      var host = document.createDocumentFragment();
      tree.forEach(function (node) {
        renderRow(node, 0, host);
      });
      $("tree").appendChild(host);
      var hasNodes = tree.length > 0;
      $("empty").hidden = hasNodes;
      if (!hasNodes) {
        $("empty").querySelector("p").textContent = ready ? "No snippets in the Google Drive folder yet." : "Connect to Google Drive to show your snippets.";
      }
    }
    renderSelectionActions();
  }

  function toggleSelection(id) {
    if (selectedItems.has(id)) selectedItems.delete(id);
    else selectedItems.add(id);
    render();
  }

  function renderSelectionActions() {
    $("selection-actions").hidden = !selecting;
    $("btn-select").setAttribute("aria-pressed", String(selecting));
    $("btn-select").textContent = selecting ? "Done" : "Select";
    $("selection-count").textContent = selectedItems.size + " selected";
    $("selection-count").className = selectedItems.size ? "has-items" : "";
    $("btn-up").disabled = busy || !ready || !!query || !Store.reorderGroups(tree, selectedItems, -1).length;
    $("btn-down").disabled = busy || !ready || !!query || !Store.reorderGroups(tree, selectedItems, 1).length;
    $("btn-delete-selected").disabled = busy || !ready || !selectedItems.size;
  }

  function selectNode(id) {
    selectedId = id;
    render();
  }

  function selectedNode() {
    return selectedId ? index.byId.get(selectedId) || null : null;
  }

  function renderDriveBar() {
    var folders = index.folders.length;
    var state = busy ? "busy" : (ready ? "ready" : "error");
    var label = busy ? "Drive: loading…" :
      (ready ? (pendingWrites ? "Saved here • " + pendingWrites + " change(s) go to Drive when you leave this site" :
        (fromCache ? "Google Drive • session cache · " + index.texts.length + " snippets" : "Google Drive connected")) : "Google Drive unavailable");
    $("drive-status").textContent = label;
    $("drive-status").setAttribute("data-state", state);
    $("btn-sync").disabled = busy;
    $("btn-collapse-all").disabled = busy || !ready || !folders;
    $("btn-folder").disabled = busy || !ready;
    $("btn-text").disabled = busy || !ready;
    $("btn-empty-text").disabled = busy || !ready;
    // While Drive is unusable, Refresh is the action that helps; the create
    // buttons keep their primary look only when a click can succeed.
    $("btn-sync").className = ready ? "" : "primary";
  }

  function onProgress(partial, id) {
    if (!loading || loadConfigVersion !== configVersion) return;
    // A listing painted from the session snapshot is already complete: a
    // Drive round trip after it would only re-download what is on screen.
    if (fromCache) return;
    tree = partial;
    rootId = id;
    ready = true;
    rebuildIndex();
    if (!progressTimer) progressTimer = window.setTimeout(function () {
      progressTimer = 0;
      scheduleRender();
      setStatus("Loading Google Drive… " + index.texts.length + " snippets found.");
    }, 100);
  }

  try {
    var progressPort = chrome.runtime.connect({ name: "pastex-library-progress" });
    progressPort.onMessage.addListener(function (message) {
      if (!message) return;
      // The background worker refreshed the cache while the user was away.
      if (message.changed) { noteChecked(message.lastCheckedAt); applyBackgroundChange(); return; }
      if (message.filled) { applyBackgroundChange(); return; }
      if (message.local) { if (typeof message.pending === "number") pendingWrites = message.pending; applyBackgroundChange(); return; }
      onProgress(message.tree, message.rootId);
    });
  } catch (ignored) { /* Loading still works without live progress. */ }

  // The stored snapshot can be missing a few bodies (quota). They are filled
  // in the worker without listing Drive, and the panel repaints once when the
  // snapshot is complete.
  // The fill needs the panel to be idle, so it is queued behind the current
  // load instead of running in the middle of it. A microtask is enough: the
  // busy flag is cleared in the load's finally block.
  var fillScheduled = false;
  function scheduleFill() {
    if (fillScheduled) return;
    fillScheduled = true;
    Promise.resolve().then(function () {
      fillScheduled = false;
      fillMissingBodies();
    });
  }

  async function fillMissingBodies() {
    if (!ready || busy || loading) return;
    try {
      var response = await chrome.runtime.sendMessage({ type: "pastex-panel-fill" });
      if (!response || !response.ok || !response.result || !response.result.filled) return;
      await applyBackgroundChange();
      setStatus("Loaded the rest of the library from the cache.", "ok");
    } catch (ignored) { /* a later open tries again */ }
  }

  // A background sync already rewrote the cache, so this only reads the
  // snapshot back: no Drive request, no loading state and no status spam.
  async function applyBackgroundChange() {
    if (busy || loading || backgroundRefresh) return;
    if ($("editor-overlay") && !$("editor-overlay").hidden) return;
    backgroundRefresh = true;
    try {
      var response = await chrome.runtime.sendMessage({ type: "pastex-panel-library" });
      if (!response || !response.ok) return;
      var result = response.result;
      if (!result || !result.tree) return;
      tree = result.tree;
      rootId = result.rootId;
      fromCache = result.cached;
      ready = true;
      rebuildIndex();
      applyCollapsedFromTree();
      if (selectedId && !index.byId.has(selectedId)) selectedId = "";
      selectedItems.forEach(function (id) { if (!index.byId.has(id)) selectedItems.delete(id); });
      render();
      renderDriveBar();
      noteChecked(result.lastCheckedAt);
      if (result.pending) fillMissingBodies();
    } catch (ignored) {
      // A failed background update keeps the current list; the next manual
      // refresh reloads it from Drive.
    } finally { backgroundRefresh = false; }
  }

  async function refresh(force, quiet) {
    if (busy) { refreshRequested = true; return false; }
    var previous = { tree: tree, rootId: rootId, ready: ready, cached: fromCache };
    busy = true;
    loading = true;
    loadConfigVersion = configVersion;
    renderDriveBar();
    render();
    if (!quiet) setStatus(force ? "Refreshing snippets from Google Drive…" : "Loading snippets…");
    try {
      var response = await chrome.runtime.sendMessage({ type: "pastex-panel-library", force: !!force });
      if (loadConfigVersion !== configVersion) return false;
      if (!response || !response.ok) throw new Error((response && response.error) || "Drive is unavailable");
      var result = response.result;
      tree = result.tree;
      rootId = result.rootId;
      fromCache = result.cached;
      ready = true;
      rebuildIndex();
      applyCollapsedFromTree();
      if (selectedId && !index.byId.has(selectedId)) selectedId = "";
      selectedItems.forEach(function (id) { if (!index.byId.has(id)) selectedItems.delete(id); });
      render();
      noteChecked(result.lastCheckedAt);
      // The cached open is instant; the rest of the library (if the snapshot
      // is missing bodies) is filled from the worker without a Drive listing.
      if (result.cached && result.pending) scheduleFill();
      var warnings = result.warnings || [];
      if (!quiet) {
        setStatus(warnings.length ? "Loaded with " + warnings.length + " unreadable item(s): " + warnings[0] :
          (fromCache ? "Ready: " + index.texts.length + " snippets from the cache." :
            "Google Drive synced: " + index.texts.length + " snippets."), warnings.length ? "error" : "ok");
      }
      return true;
    } catch (err) {
      if (loadConfigVersion !== configVersion) return false;
      if (/HTTP (401|403|404)\b|invalid_grant|service-account\.json|pastex-google-drive\.json/i.test(err.message || "")) {
        tree = []; rootId = ""; ready = false; fromCache = false;
      } else {
        tree = previous.tree; rootId = previous.rootId; ready = previous.ready; fromCache = previous.cached;
      }
      render();
      if (!quiet) setStatus("Could not read the library: " + (err.message || err) + (ready ? " (showing previous list)" : ""), "error");
      return false;
    } finally {
      loading = false;
      if (progressTimer) { window.clearTimeout(progressTimer); progressTimer = 0; }
      busy = false;
      renderDriveBar();
      render();
      if (refreshRequested) { refreshRequested = false; refresh(true); }
    }
  }

  // --- insert into the page (or copy as the fallback) ---

  async function insertNode(node) {
    if (node.loadError) { setStatus("Cannot read this Drive item: " + node.loadError, "error"); return; }
    setStatus("Inserting…");
    var response = null;
    try {
      response = await chrome.runtime.sendMessage({
        type: "pastex-sidepanel-insert",
        id: node.id,
        text: typeof node.text === "string" ? node.text : "",
      });
    } catch (err) {
      response = { ok: false };
    }
    if (response && response.ok) {
      setStatus("Inserted into the page.", "ok");
      return;
    }
    if (response && response.reason === "site-not-allowed") {
      setStatus("Site not allowed. Check pastex-sites.json in the selected configuration manifest.", "error");
      return;
    }
    if (!response || response.reason !== "no-editable") {
      // A transport or content-script failure is not a request to copy.
      setStatus("Insert failed: " + ((response && response.reason) || "the page did not accept the snippet."), "error");
      return;
    }
    try {
      await navigator.clipboard.writeText(node.text || "");
      setStatus("No editable field focused — copied. Press Ctrl+V.", "ok");
    } catch (err) {
      setStatus("Could not insert or copy.", "error");
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
      setStatus("Cannot edit an unreadable Drive item: " + node.loadError, "error");
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

  // The queue speaks one small vocabulary ("create", "edit", "rename", "move",
  // "delete", "collapse", "reorder"); the panel still uses its editor actions.
  // The row itself travels along, because the write needs its kind, the name it
  // had and whether it already reached Drive.
  function identifyCommand(command) {
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
      command.item = rowIdentity(live);
      return command;
    }
    if (command.action === "delete") { command.kind = "delete"; return command; }
    if (command.action === "collapse") { command.kind = "collapse"; return command; }
    if (command.action === "reorder") { command.kind = "reorder"; return command; }
    return command.kind ? command : null;
  }

  function rowIdentity(node) {
    return {
      id: node.id, localId: node.id.indexOf("local-") === 0 ? node.id : "",
      kind: node.kind, name: node.name, driveName: node.driveName,
      parentId: parentOf(tree, node.id, rootId) || "",
      isNew: node.id.indexOf("local-") === 0,
    };
  }

  // An edit never talks to Drive: the worker applies it to the cache and
  // queues the write, and the panel repaints from the cache.
  async function runMutation(command) {
    if (busy || !ready) return null;
    busy = true;
    savingLocal = true;
    renderDriveBar();
    render();
    setStatus("Saving to the cache…");
    var result = null, failure = "";
    try {
      command.rootId = rootId;
      var queued = identifyCommand(command);
      if (!queued) throw new Error("That item or folder is no longer available.");
      var response = await chrome.runtime.sendMessage({ type: "pastex-panel-local-edit", command: queued });
      if (!response || !response.ok) throw new Error((response && response.error) || "The change could not be saved.");
      result = response.result || {};
      if (typeof result.pending === "number") pendingWrites = result.pending;
    } catch (err) {
      failure = "Could not save: " + (err.message || err);
    } finally { busy = false; savingLocal = false; }
    // Apply the cached list locally: no Drive request, no loading state.
    await applyBackgroundChange();
    renderDriveBar();
    if (failure) setStatus(failure, "error");
    else setStatus(pendingWrites ?
      "Saved to the cache; " + pendingWrites + " change(s) go to Drive when you leave this site." : "Saved.", "ok");
    return result;
  }

  async function saveEditor() {
    var name = $("editor-name").value.trim();
    if (!name || name.indexOf("/") !== -1) { setStatus("Enter a name without /.", "error"); return; }
    if (busy) return;
    var mode = editorMode;
    var node = index.byId.get(editorNodeId) || null;
    var parentId = mode === "new-folder" || mode === "new-text" ? ($("editor-parent").value || rootId) : parentOf(tree, editorNodeId, rootId);
    if (!parentId || ((mode !== "new-folder" && mode !== "new-text") && !node)) {
      setStatus("That item or folder is no longer available; refresh first.", "error"); return;
    }
    var result = await runMutation({ action: "save", mode: mode, name: name, node: node,
      parentId: parentId, text: $("editor-text").value });
    if (result) { selectedId = result.id || ""; closeEditor(); render(); }
  }

  async function deleteNodes(nodes) {
    if (!nodes.length || busy) return;
    var hasFolder = nodes.some(function (node) { return node.kind === "folder"; });
    if (!window.confirm("Move " + nodes.length + " selected item(s)" +
        (hasFolder ? " (including all contents of selected folders)" : "") + " to Google Drive trash?")) return;
    var result = await runMutation({ action: "delete", ids: nodes.map(function (node) { return node.id; }) });
    if (result) { selectedId = ""; selectedItems.clear(); render(); }
  }

  async function moveSelected(direction) {
    if (busy || query) return;
    var groups = Store.reorderGroups(tree, selectedItems, direction);
    if (!groups.length) return;
    // The worker stores the new order in the centralized index of the root
    // folder, so every group is sent with the folder it belongs to.
    var parents = groups.map(function (group) {
      return parentOf(tree, group[0], rootId) || rootId;
    });
    await runMutation({ action: "reorder", groups: groups, parents: parents });
  }

  // --- configuration: one JSON manifest with links ---

  async function applyConfiguration(load) {
    var button = $("btn-config");
    button.disabled = true;
    try {
      var applied = await load();
      var found = applied.found;
      var issues = [];
      try {
        var response = await chrome.runtime.sendMessage({ type: "pastex-reload-sites" });
        if (!response || !response.ok) throw new Error((response && response.error) || "Site registration failed.");
      } catch (err) { issues.push("Site registration failed; restart the browser: " + (err.message || err)); }
      configVersion++;
      tree = []; rootId = ""; ready = false; fromCache = false;
      selectedId = ""; selectedItems.clear();
      rebuildIndex(); render(); renderDriveBar();
      // Let an in-flight panel load finish so its status cannot overwrite
      // the configuration result; then force one clean Drive reload.
      refreshRequested = false;
      while (busy) await new Promise(function (resolve) { window.setTimeout(resolve, 10); });
      await refresh(true);
      setStatus(applied.label + ": " + found.drive + ", " + found.account + ", " + found.sites + ". Reload open pages." +
        (issues.length ? " " + issues.join(" ") : ""), issues.length ? "error" : "ok");
    } catch (err) {
      setStatus(err.name === "AbortError" ?
        "Selection canceled; configuration unchanged." :
        "Could not use configuration: " + (err.message || err), err.name === "AbortError" ? "" : "error");
    } finally {
      button.disabled = false;
    }
  }

  $("btn-config").addEventListener("click", function () {
    if (typeof window.showOpenFilePicker !== "function") {
      setStatus("This browser does not support selecting a manifest file.");
      return;
    }
    // The picker must be called directly from the user gesture.
    return applyConfiguration(async function () {
      var handles = await window.showOpenFilePicker({
        multiple: false,
        types: [{ description: "JSON manifest", accept: { "application/json": [".json"] } }],
      });
      var file = await handles[0].getFile();
      var found = await Config.chooseManifestText(await file.text());
      return { found: found, label: "Using manifest " + file.name };
    });
  });

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

  $("btn-sync").addEventListener("click", function () { refresh(true); });

  // The queue depth is shown in the drive bar, so it is read once on open and
  // refreshed whenever a local change lands.
  async function readPending() {
    try {
      var response = await chrome.runtime.sendMessage({ type: "pastex-panel-status" });
      if (response && response.ok && response.result && typeof response.result.pending === "number") {
        pendingWrites = response.result.pending;
        renderDriveBar();
      }
    } catch (ignored) { /* the bar falls back to the cache label */ }
  }
  $("btn-collapse-all").addEventListener("click", async function () {
    if (busy || !ready) return;
    var ids = index.folders.map(function (node) { return node.id; });
    if (!ids.length) return;
    if (query) { query = ""; $("search").value = ""; }
    await persistCollapsed(ids, true);
  });
  $("btn-select").addEventListener("click", function () {
    selecting = !selecting;
    selectedItems.clear();
    if (selecting && query) setStatus("Clear search to reorder items.");
    render();
  });
  $("btn-up").addEventListener("click", function () { moveSelected(-1); });
  $("btn-down").addEventListener("click", function () { moveSelected(1); });
  $("btn-delete-selected").addEventListener("click", function () {
    deleteNodes(Store.selectedRoots(tree, selectedItems));
  });

  $("editor-save").addEventListener("click", saveEditor);
  $("editor-cancel").addEventListener("click", closeEditor);
  $("editor-overlay").addEventListener("click", function (event) {
    if (event.target === $("editor-overlay")) {
      closeEditor();
    }
  });
  // Use the shared session cache on open; explicit refresh reloads Drive.
  renderDriveBar();
  renderLastChecked();
  // The age of the list is refreshed while the panel stays open.
  if (typeof window.setInterval === "function") window.setInterval(renderLastChecked, 30000);
  announcePanelState(true);
  refresh(false);
  readPending();
})();
