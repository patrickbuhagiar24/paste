/* Pastex Side Panel: snippet library, editor, Drive sync.
 *
 * One click on a text snippet inserts it into the active tab (desktop
 * parity); when no editable field is focused the text is copied to the
 * clipboard instead, so the click never loses anything.
 */

(function () {
  "use strict";

  var Store = window.PastexStore;
  // The panel scans the chosen folder once and stores the parsed files, so the
  // worker and later panel opens read the cache instead of the folder.
  var Config = window.PastexConfigSource.create(window.PastexSites, chrome.storage.local);

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
  var idleWaiters = [];
  function releaseBusy() {
    busy = false;
    var waiters = idleWaiters.splice(0);
    waiters.forEach(function (resolve) { resolve(); });
  }
  function waitUntilIdle() {
    return busy ? new Promise(function (resolve) { idleWaiters.push(resolve); }) : Promise.resolve();
  }
  var loading = false;
  var refreshRequested = false;
  var progressTimer = 0;
  var fromCache = false;
  var backgroundRefresh = false;
  var configVersion = 0;
  var loadConfigVersion = 0;
  var selecting = false;
  var selectedItems = new Set();
  // Edits made here are applied to the cache at once; their Drive write waits
  // in the worker's queue until the tab is left, so nothing is fetched while
  // the user works.
  var pendingWrites = 0;
  var savingLocal = false;
  // The row whose Edit menu is open, so its aria-expanded can be cleared when
  // the menu closes for any reason.
  var menuOpener = null;

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

  // --- trailing newline mode ---------------------------------------------
  // Off: a row click ends with a newline. On: remove trailing newlines.
  // The row action inserts without a trailing newline for that click only.
  var NEWLINE_KEY = "pastex-newline-on-insert";
  var newlineOnInsert = false;

  function reportNewline() {
    var toggle = $("btn-newline");
    if (!toggle) return;
    toggle.setAttribute("aria-pressed", String(newlineOnInsert));
    toggle.title = newlineOnInsert ?
      "No final newline — click to add one" :
      "Final newline on — click to remove it";
    toggle.setAttribute("aria-label", "No final newline");
  }

  function textForInsert(node, withoutNewline) {
    var text = typeof node.text === "string" ? node.text : "";
    if (withoutNewline || newlineOnInsert) return text.replace(/(?:\r\n|\r|\n)+$/, "");
    if (text === "" || /[\r\n]$/.test(text)) return text;
    return text + "\n";
  }

  async function setNewlineOnInsert(on) {
    newlineOnInsert = !!on;
    reportNewline();
    try { await chrome.storage.local.set({ [NEWLINE_KEY]: newlineOnInsert }); }
    catch (ignored) { /* the setting simply does not survive a restart */ }
  }

  // --- configuration state -----------------------------------------------
  //
  // The Config button says which case this installation is in: no
  // configuration yet (click to pick one) or configured (click to change it).
  // The state is written into data-configured, the title and the aria-label, so
  // the colour is a reminder and never the message.
  var configured = false;

  function reportConfiguration() {
    var button = $("btn-config");
    if (!button) return;
    button.setAttribute("data-configured", String(configured));
    var label = configured ?
      "Config: ready — change file" :
      "Config: choose a file";
    button.title = label;
    button.setAttribute("aria-label", label);
  }

  function markConfigured(value) {
    configured = !!value;
    reportConfiguration();
  }

  async function readNewlineOnInsert() {
    try {
      var saved = await chrome.storage.local.get(NEWLINE_KEY);
      if (saved && saved[NEWLINE_KEY] === true) newlineOnInsert = true;
    } catch (ignored) { /* keep the default */ }
    reportNewline();
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

  // Rename and Move are not separate buttons: both are edits of an existing
  // row, so they live in one menu behind a single Edit button.
  function appendRowActions(row, node) {
    if (selecting) return;
    // This action applies only to this insertion; the toolbar mode is unchanged.
    var newline = miniButton("Insert without final newline", "⏎", function () {
      insertNode(node, true);
    });
    newline.className += " no-newline";
    newline.setAttribute("data-kind", "newline");
    newline.setAttribute("aria-label", "Insert without final newline");
    row.appendChild(newline);
    var edit = miniButton("Edit", "✎", function () { openEditMenu(node, edit); });
    edit.setAttribute("aria-label", "Edit snippet or folder");
    edit.setAttribute("aria-haspopup", "menu");
    edit.setAttribute("aria-expanded", "false");
    row.appendChild(edit);
    var remove = miniButton("Delete", "🗑", function () { deleteNodes([node]); });
    remove.setAttribute("aria-label", "Delete");
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
      toggle.title = "Toggle folder";
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
    // Nothing to show is an empty box, not a sentence with a second button in
    // it: the status line already says what happened.
    $("empty").hidden = matches.length > 0;
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
      $("empty").hidden = tree.length > 0;
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
    // The button is an icon and stays one: writing "Select"/"Done" into it
    // replaced the ☑ glyph with a word, which is exactly the rule every other
    // button already follows. The pressed state and the tooltip carry it.
    var selectLabel = selecting ? "Exit selection" : "Select items";
    $("btn-select").setAttribute("title", selectLabel);
    $("btn-select").setAttribute("aria-label", selectLabel);
    // The number alone: the bar is already the selection bar, so the word said
    // nothing the toolbar does not.
    $("selection-count").textContent = String(selectedItems.size);
    $("selection-count").className = selectedItems.size ? "has-items" : "";
    $("btn-up").disabled = busy || !ready || !!query || !Store.reorderGroups(tree, selectedItems, -1).length;
    $("btn-down").disabled = busy || !ready || !!query || !Store.reorderGroups(tree, selectedItems, 1).length;
    $("btn-move-selected").disabled = busy || !ready || !selectedItems.size;
    $("btn-delete-selected").disabled = busy || !ready || !selectedItems.size;
  }

  function selectNode(id) {
    selectedId = id;
    render();
  }

  function selectedNode() {
    return selectedId ? index.byId.get(selectedId) || null : null;
  }

  // The panel has one status line, and it is for what just happened. There is no
  // permanent "connected" label and no "checked N min ago" second line: the
  // buttons already say whether Drive can be used, and a failure has to be the
  // thing that is readable.
  function renderButtons() {
    var folders = index.folders.length;
    $("btn-sync").disabled = busy;
    $("btn-collapse-all").disabled = busy || !ready || !folders;
    $("btn-folder").disabled = busy || !ready;
    $("btn-text").disabled = busy || !ready;
    // While Drive is unusable, Sync is the action that helps; the create
    // buttons keep their primary look only when a click can succeed.
    $("btn-sync").className = ready ? "icon" : "icon primary";
  }

  // Saving is local, so a queued change is not an error; it is reported once,
  // when a save leaves something waiting, instead of as a permanent label.
  function reportPending() {
    if (!pendingWrites) return;
    setStatus("Saved locally. " + pendingWrites + " change(s) sync when you leave this site.", "ok");
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
      setStatus("Loading… " + index.texts.length + " snippets found.");
    }, 100);
  }

  try {
    var progressPort = chrome.runtime.connect({ name: "pastex-library-progress" });
    progressPort.onMessage.addListener(function (message) {
      if (!message) return;
      // The background worker refreshed the cache while the user was away.
      if (message.changed) { applyBackgroundChange(); return; }
      if (message.filled) { applyBackgroundChange(); return; }
      if (message.local) { if (typeof message.pending === "number") pendingWrites = message.pending; applyBackgroundChange(); return; }
      // A queued write is waiting for a change only the user can make.
      if (message.blocked) {
        setStatus("Not synced: " + message.reason + " Saved locally; sync after fixing this.", "error");
        return;
      }
      // Drive refused a queued write for good; the row it described is gone.
      if (message.dropped) {
        setStatus("Drive refused a saved change" + (message.reason ? ": " + message.reason : "."), "error");
        applyBackgroundChange(true);
        return;
      }
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
      setStatus("Loaded the rest from the cache.", "ok");
    } catch (ignored) { /* a later open tries again */ }
  }

  // A background sync already rewrote the cache, so this only reads the
  // snapshot back: no Drive request, no loading state and no status spam.
  //
  // A background update never repaints under an open dialog, because it would
  // replace the list the user is working in. An edit the user just confirmed is
  // different: it already landed in the cache and the dialog is closing, so the
  // row must be repainted even though the dialog is still on screen for the
  // next few lines (a move would otherwise seem to do nothing).
  async function applyBackgroundChange(whileEditing) {
    if (busy || loading || backgroundRefresh) { return; }
    if (!whileEditing && $("editor-overlay") && !$("editor-overlay").hidden) return;
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
      renderButtons();
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
    renderButtons();
    render();
    if (!quiet) setStatus(force ? "Syncing…" : "Loading…");
    try {
      var response = await chrome.runtime.sendMessage({ type: "pastex-panel-library", force: !!force });
      if (loadConfigVersion !== configVersion) return false;
      if (!response || !response.ok) throw new Error((response && response.error) || "Drive is unavailable");
      var result = response.result;
      // The library could only be read because a configuration was picked, so
      // that is also the moment the Config button can say it is configured.
      markConfigured(true);
      tree = result.tree;
      rootId = result.rootId;
      fromCache = result.cached;
      ready = true;
      rebuildIndex();
      applyCollapsedFromTree();
      if (selectedId && !index.byId.has(selectedId)) selectedId = "";
      selectedItems.forEach(function (id) { if (!index.byId.has(id)) selectedItems.delete(id); });
      render();
      // The cached open is instant; the rest of the library (if the snapshot
      // is missing bodies) is filled from the worker without a Drive listing.
      if (result.cached && result.pending) scheduleFill();
      var warnings = result.warnings || [];
      if (!quiet) {
        setStatus(warnings.length ? warnings.length + " item(s) could not be read." :
          index.texts.length + " snippets" + (fromCache ? " (cache)" : "") + ".", warnings.length ? "error" : "ok");
      }
      return true;
    } catch (err) {
      if (loadConfigVersion !== configVersion) return false;
      if (/HTTP (401|403|404)\b|invalid_grant|Choose your configuration file/i.test(err.message || "")) {
        tree = []; rootId = ""; ready = false; fromCache = false;
      } else {
        tree = previous.tree; rootId = previous.rootId; ready = previous.ready; fromCache = previous.cached;
      }
      render();
      // "Choose your configuration file in Config first." is the one failure
      // that means nothing is configured yet; the Config button reflects it.
      if (/Choose your configuration file/i.test((err && err.message) || "")) markConfigured(false);
      if (!quiet) setStatus(err.message ? "Could not read the library. " + err.message : "Could not read the library.", "error");
      return false;
    } finally {
      loading = false;
      if (progressTimer) { window.clearTimeout(progressTimer); progressTimer = 0; }
      releaseBusy();
      renderButtons();
      render();
      if (refreshRequested) { refreshRequested = false; refresh(true); }
    }
  }

  // --- insert into the page (or copy as the fallback) ---

  async function insertNode(node, withoutNewline) {
    if (node.loadError) { setStatus("Cannot read this Drive item: " + node.loadError, "error"); return; }
    // A row inside a folded folder is listed but not downloaded. Inserting it
    // would insert an empty string, which looks exactly like a broken button.
    if (node.deferred && typeof node.text === "string" && node.text === "") {
      setStatus("Open the snippet’s folder first.", "error");
      return;
    }
    setStatus("Inserting…");
    var response = null;
    var text = textForInsert(node, withoutNewline);
    try {
      response = await chrome.runtime.sendMessage({
        type: "pastex-sidepanel-insert",
        id: node.id,
        text: text,
      });
    } catch (err) {
      response = { ok: false };
    }
    if (response && response.ok) {
      setStatus("Inserted.", "ok");
      return;
    }
    if (response && response.reason === "site-not-allowed") {
      setStatus("Site not allowed.", "error");
      return;
    }
    if (response && response.reason === "single-line") {
      // The field is a one-line input, so a newline has nowhere to go. Telling
      // the user is what stops the newline buttons looking broken.
      setStatus("Inserted. This field cannot hold a newline.", "error");
      return;
    }
    if (response && response.reason === "partial") {
      setStatus("Partly inserted. Check the field before retrying.", "error");
      return;
    }
    if (!response || response.reason !== "no-editable") {
      // A transport or content-script failure is not a request to copy.
      setStatus("Could not insert.", "error");
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      setStatus("Copied. Press Ctrl+V.", "ok");
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
    // A move that was abandoned must not leak its targets into this dialog.
    movingIds = [];
    var titles = {
      "new-folder": "New folder",
      "new-text": "New snippet",
      "edit-text": "Edit snippet",
      "rename-text": "Rename snippet",
      "rename-folder": "Rename folder",
    };
    $("editor-title").textContent = titles[mode] || "Edit";
    var isNew = mode === "new-folder" || mode === "new-text";
    showEditorFields(mode);
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
    $("editor-name").value = node ? esc(node.name) : "";
    $("editor-text").value = node && node.kind === "text" ? node.text || "" : "";
    $("editor-overlay").hidden = false;
    $("editor-name").focus();
    $("editor-name").select();
  }

  function closeEditor() {
    $("editor-overlay").hidden = true;
    closeEditMenu();
  }

  // One switch builds every editor's fields, so a dialog can never keep a field
  // a previous mode left visible. A new row picks a folder; a rename or an edit
  // names an existing row; a move only picks a folder.
  function showEditorFields(mode) {
    var isMove = mode === "move";
    var isNew = mode === "new-folder" || mode === "new-text";
    $("editor-parent-label").style.display = isNew || isMove ? "" : "none";
    $("editor-parent").style.display = isNew || isMove ? "" : "none";
    $("editor-name-label").style.display = isMove ? "none" : "";
    $("editor-name").style.display = isMove ? "none" : "";
    $("editor-text-wrap").style.display = mode === "new-text" || mode === "edit-text" ? "" : "none";
  }

  // --- the Edit menu -------------------------------------------------------
  //
  // Rename, Edit text and Move all change a row that already exists, so they
  // share one menu behind one button. The menu is rebuilt on every open because
  // a folder offers fewer actions than a snippet.

  function closeEditMenu() {
    var menu = $("row-menu");
    menu.hidden = true;
    if (menuOpener && menuOpener.setAttribute) menuOpener.setAttribute("aria-expanded", "false");
    menuOpener = null;
  }

  function openEditMenu(node, opener) {
    var menu = $("row-menu");
    while (menu.firstChild) menu.removeChild(menu.firstChild);
    function add(label, run, disabledReason) {
      var item = document.createElement("button");
      item.setAttribute("role", "menuitem");
      item.textContent = label;
      if (disabledReason) {
        item.disabled = true;
        item.title = disabledReason;
      }
      item.addEventListener("click", function (event) {
        event.stopPropagation();
        closeEditMenu();
        if (!item.disabled) run();
      });
      menu.appendChild(item);
    }
    add(node.kind === "folder" ? "Rename folder" : "Rename", function () {
      openEditor(node.kind === "folder" ? "rename-folder" : "rename-text", node);
    });
    if (node.kind === "text") {
      // A body that could not be read must not be replaced by an empty one.
      add("Edit text", function () { openEditor("edit-text", node); },
        node.loadError ? "Cannot edit: " + node.loadError : "");
    }
    add("Move to folder…", function () { openMoveEditor([node.id]); },
      index.folders.length ? "" : "Create a folder first.");
    menuOpener = opener || null;
    menu.hidden = false;
    if (menuOpener && menuOpener.setAttribute) menuOpener.setAttribute("aria-expanded", "true");
  }

  // --- move ----------------------------------------------------------------
  //
  // A move changes the folder a row lives in, never its name or its text. A
  // folder cannot be moved into itself or into its own content, so those
  // destinations are left out of the list rather than failing on Drive.

  var movingIds = [];

  function moveOptions(ids) {
    var blocked = new Set(ids);
    ids.forEach(function (id) {
      var node = index.byId.get(id);
      if (node && node.kind === "folder") {
        Store.walk(node.children || [], function (child) { blocked.add(child.id); });
      }
    });
    var current = ids.length === 1 ? (parentOf(tree, ids[0], rootId) || "") : "";
    var options = [{ id: "", name: "(top level)" }];
    Store.walk(tree, function (node) {
      if (node.kind !== "folder" || blocked.has(node.id)) return;
      // Moving a row where it already is would be a pointless Drive write.
      if (ids.length === 1 && node.id === current) return;
      options.push({ id: node.id, name: node.name });
    });
    return options;
  }

  function openMoveEditor(ids) {
    ids = (ids || []).filter(Boolean);
    if (!ids.length) return;
    var options = moveOptions(ids);
    if (options.length < 2) {
      setStatus("No other folder available.", "error");
      return;
    }
    movingIds = ids;
    editorMode = "move";
    editorNodeId = "";
    $("editor-title").textContent = ids.length === 1 ? "Move" : "Move " + ids.length + " items";
    showEditorFields("move");
    var select = $("editor-parent");
    while (select.firstChild) select.removeChild(select.firstChild);
    options.forEach(function (option) {
      var el = document.createElement("option");
      el.value = option.id;
      el.textContent = option.name;
      select.appendChild(el);
    });
    // When a single row is moved, the folder it already sits in is the one
    // place it cannot go, so the first other folder is preselected.
    select.value = options[0] ? options[0].id : "";
    $("editor-overlay").hidden = false;
    if (select.focus) select.focus();
  }

  async function saveMove() {
    var parentId = $("editor-parent").value || rootId;
    var ids = movingIds.slice();
    movingIds = [];
    if (busy) return;
    // The dialog is closed first: the repaint of the tree must not be skipped
    // because an editor happens to be on screen.
    closeEditor();
    var result = await runMutation({ action: "move", ids: ids, parentId: parentId });
    if (result) {
      selectedId = "";
      selectedItems.clear();
      render();
    }
  }

  var parentOf = window.PastexPanelCommand.parentOf;

  // An edit never talks to Drive: the worker applies it to the cache and
  // queues the write, and the panel repaints from the cache.
  async function runMutation(command, whileEditing) {
    if (busy || !ready) return null;
    busy = true;
    savingLocal = true;
    renderButtons();
    render();
    setStatus("Saving…");
    var result = null, failure = "";
    try {
      command.rootId = rootId;
      var queued = window.PastexPanelCommand.identify(command, index, tree, rootId);
      if (!queued) throw new Error("That item or folder is no longer available.");
      var response = await chrome.runtime.sendMessage({ type: "pastex-panel-local-edit", command: queued });
      if (!response || !response.ok) throw new Error((response && response.error) || "The change could not be saved.");
      result = response.result || {};
      if (typeof result.pending === "number") pendingWrites = result.pending;
    } catch (err) {
      failure = err.message ? "Could not save. " + err.message : "Could not save.";
    } finally { savingLocal = false; releaseBusy(); }
    // Apply the cached list locally: no Drive request, no loading state. The
    // caller says whether its own dialog is still open at this point: a save
    // closes the editor after the repaint, a move closes it before.
    await applyBackgroundChange(!!whileEditing);
    renderButtons();
    if (failure) setStatus(failure, "error");
    else if (pendingWrites) reportPending();
    else setStatus("Saved.", "ok");
    return result;
  }

  async function saveEditor() {
    if (editorMode === "move") { await saveMove(); return; }
    var name = $("editor-name").value.trim();
    if (!name || name.indexOf("/") !== -1) { setStatus("No / in the name.", "error"); return; }
    if (busy) return;
    var mode = editorMode;
    var node = index.byId.get(editorNodeId) || null;
    var parentId = mode === "new-folder" || mode === "new-text" ? ($("editor-parent").value || rootId) : parentOf(tree, editorNodeId, rootId);
    if (!parentId || ((mode !== "new-folder" && mode !== "new-text") && !node)) {
      setStatus("That item is gone. Refresh.", "error"); return;
    }
    var result = await runMutation({ action: "save", mode: mode, name: name, node: node,
      parentId: parentId, text: $("editor-text").value }, true);
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

  // --- configuration: one file of Drive links ---

  async function applyConfiguration(load, note) {
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
      markConfigured(true);
      tree = []; rootId = ""; ready = false; fromCache = false;
      selectedId = ""; selectedItems.clear();
      rebuildIndex(); render(); renderButtons();
      // Let an in-flight panel load finish so its status cannot overwrite
      // the configuration result; then force one clean Drive reload.
      refreshRequested = false;
      while (busy) await waitUntilIdle();
      await refresh(true);
      var extra = note ? note(applied) : "";
      setStatus(issues.length ? "Saved, but sites need a browser restart." : applied.label + ". " + extra,
        issues.length ? "error" : "ok");
    } catch (err) {
      setStatus(err.name === "AbortError" ?
        "Canceled. Config unchanged." :
        err.message ? "Config error: " + err.message : "Could not use that file.", err.name === "AbortError" ? "" : "error");
    } finally {
      button.disabled = false;
    }
  }

  $("btn-config").addEventListener("click", function () {
    if (pendingWrites) {
      setStatus("Sync pending changes before changing config.", "error");
      return;
    }
    if (typeof window.showOpenFilePicker !== "function") {
      setStatus("File selection is not supported.");
      return;
    }
    // The picker must be called directly from the user gesture. One pick is one
    // file: nothing is searched for and nothing else in its folder is read.
    return applyConfiguration(async function () {
      // The redirect URL is derived from the extension id, so the only place it
      // can be read is a running installation. Saying it here is what makes the
      // OAuth client possible to set up without a debugger.
      var redirect = "";
      try { redirect = chrome.identity.getRedirectURL(); } catch (ignored) { redirect = ""; }
      var picked = await window.showOpenFilePicker({
        multiple: false,
        id: "pastex-config",
        types: [{ description: "Pastex configuration", accept: { "application/json": [".json"] } }],
      });
      var status = await chrome.runtime.sendMessage({ type: "pastex-panel-status" });
      if (!status || !status.ok || !status.result || typeof status.result.pending !== "number")
        throw new Error("Cannot check pending changes. Try again.");
      if (status.result.pending) throw new Error("Sync pending changes before changing config.");
      var found = await Config.chooseFile(picked[0]);
      return { found: found, redirect: redirect, label: "Using " + (picked[0].name || "configuration") };
    }, function (applied) {
      // One status line, so the value is reported in the line that already
      // exists rather than in a second label nobody asked for.
      return applied.redirect ? "OAuth redirect URL: " + applied.redirect : "";
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

  // Sync sends what is waiting and stops there: it never reads the library
  // back from Drive, so the list on screen is not reloaded. The write is the
  // same queued write the leave would send; the worker says when it chose to
  // wait instead.
  $("btn-sync").addEventListener("click", async function () {
    if (busy) { refreshRequested = false; return; }
    setStatus("Sending…");
    var response = null;
    try {
      response = await chrome.runtime.sendMessage({ type: "pastex-sync-now" });
    } catch (ignored) { response = null; }
    if (!response || !response.ok || !response.result) {
      setStatus("Sync failed. Try again.", "error");
      return;
    }
    var result = response.result;
    pendingWrites = result.pending || 0;
    renderButtons();
    if (result.deferred) {
      setStatus("Saved. " + result.pending + " waiting to sync.", "ok");
      return;
    }
    // A refusal is permanent, so the write left the queue and a count alone
    // would suggest it was sent. A kept refusal is different: nothing was lost,
    // and the queue depth already says how much is waiting.
    var refused = result.refused || [];
    var kept = refused.filter(function (item) { return item.kept; });
    var lost = refused.filter(function (item) { return !item.kept; });
    if (kept.length) {
      setStatus("Could not send " + kept.length + " change(s): " + kept[0].error +
        " They are kept and will be sent once this is fixed.", "error");
      return;
    }
    if (lost.length) {
      setStatus("Drive refused " + lost.length + " change(s): " + lost[0].error, "error");
      return;
    }
    if (result.pending) {
      setStatus("Sent " + (result.flushed.sent || 0) + "; " + result.pending + " waiting.", "error");
      return;
    }
    setStatus(result.flushed.sent ? "Sent " + result.flushed.sent + "." : "Nothing to send.", "ok");
  });

  // The queue depth is shown in the drive bar, so it is read once on open and
  // refreshed whenever a local change lands.
  async function readPending() {
    try {
      var response = await chrome.runtime.sendMessage({ type: "pastex-panel-status" });
      if (response && response.ok && response.result && typeof response.result.pending === "number") {
        pendingWrites = response.result.pending;
      }
    } catch (ignored) { /* the next save reports the queue depth again */ }
  }
  $("btn-copy-pending").addEventListener("click", async function () {
    try {
      var response = await chrome.runtime.sendMessage({ type: "pastex-export-pending" });
      if (!response || !response.ok) throw new Error((response && response.error) || "Cannot read pending changes.");
      if (!response.changes.length) { setStatus("No pending changes."); return; }
      await navigator.clipboard.writeText(JSON.stringify({ changes: response.changes }, null, 2));
      setStatus("Pending changes copied. Keep them private.", "ok");
    } catch (err) { setStatus("Could not copy pending changes. " + (err.message || ""), "error"); }
  });
  $("btn-collapse-all").addEventListener("click", async function () {
    if (busy || !ready) return;
    var ids = index.folders.map(function (node) { return node.id; });
    if (!ids.length) return;
    if (query) { query = ""; $("search").value = ""; }
    await persistCollapsed(ids, true);
  });
  $("btn-newline").addEventListener("click", function () {
    setNewlineOnInsert(!newlineOnInsert);
  });
  $("btn-select").addEventListener("click", function () {
    selecting = !selecting;
    selectedItems.clear();
    if (selecting && query) setStatus("Clear the search first.");
    render();
  });
  $("btn-up").addEventListener("click", function () { moveSelected(-1); });
  $("btn-down").addEventListener("click", function () { moveSelected(1); });
  $("btn-move-selected").addEventListener("click", function () {
    openMoveEditor(Store.selectedRoots(tree, selectedItems).map(function (node) { return node.id; }));
  });
  $("btn-delete-selected").addEventListener("click", function () {
    deleteNodes(Store.selectedRoots(tree, selectedItems));
  });
  // A click anywhere outside the menu closes it; the menu's own buttons stop
  // the event, so choosing an action does not close it twice. Pastex has no
  // keyboard shortcut of its own, so a menu is dismissed by the same click that
  // opened it or by picking one of its actions.
  document.addEventListener("click", function () { if (!$("row-menu").hidden) closeEditMenu(); });

  $("editor-save").addEventListener("click", saveEditor);
  $("editor-cancel").addEventListener("click", closeEditor);
  $("editor-overlay").addEventListener("click", function (event) {
    if (event.target === $("editor-overlay")) {
      closeEditor();
    }
  });
  window.PastexConflictPanel.init({ get: $, send: function (message) { return chrome.runtime.sendMessage(message); },
    status: setStatus, clipboard: navigator.clipboard, confirm: function (text) { return window.confirm(text); },
    refresh: function () { return applyBackgroundChange(true); } });
  // Use the shared session cache on open; explicit refresh reloads Drive.
  renderButtons();
  reportNewline();
  reportConfiguration();
  readNewlineOnInsert();
  announcePanelState(true);
  refresh(false);
  readPending();
})();
