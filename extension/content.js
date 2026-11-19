/* Pastex in-page layer: insert snippets at the cursor.
 *
 *  - Tracks the last focused editable (input, textarea, contenteditable).
 *  - Shows a small floating button next to the focused field that opens
 *    the picker at the cursor, on configured sites only.
 *  - The picker is a Shadow DOM popup: page styles cannot leak in or out.
 *  - Responds to background messages: toggle-picker, insert text.
 */

(function () {
  "use strict";

  if (window.__pastexContentLoaded) {
    return;
  }
  window.__pastexContentLoaded = true;

  var MAX_ROWS = 12;
  var BUTTON_HIDE_DELAY_MS = 180;

  var lastEditable = null;
  var buttonHost = null;
  var buttonHideTimer = 0;
  var pickerHost = null;
  var pickerState = null;

  async function siteAllowed() {
    try {
      var response = await chrome.runtime.sendMessage({ type: "pastex-site-allowed" });
      return !!(response && response.allowed);
    } catch (err) {
      return false;
    }
  }

  function isEditable(el) {
    if (!el || el === document.body || el === document.documentElement) {
      return false;
    }
    if (el.isContentEditable) {
      return true;
    }
    var tag = (el.tagName || "").toLowerCase();
    if (tag === "textarea") {
      return !el.disabled && !el.readOnly;
    }
    if (tag === "input") {
      var type = (el.type || "text").toLowerCase();
      if (el.disabled || el.readOnly) {
        return false;
      }
      return ["text", "search", "url", "tel", "email", "password", "number"].indexOf(type) !== -1;
    }
    return false;
  }

  // Request the current Drive library from the background worker; snippet
  // contents are never written to browser storage.
  async function loadTree() {
    var response = await chrome.runtime.sendMessage({ type: "pastex-get-library" });
    if (!response || !response.ok) throw new Error((response && response.error) || "Drive is unavailable");
    return response;
  }

  function flattenWithPaths(nodes, trail, out) {
    trail = trail || [];
    out = out || [];
    (nodes || []).forEach(function (node) {
      if (node.kind === "text" && !node.loadError) {
        out.push({ path: trail.concat(node.name).join("/"), node: node });
      } else if (node.kind === "folder") {
        flattenWithPaths(node.children || [], trail.concat(node.name), out);
      }
    });
    return out;
  }

  function searchEntries(entries, query) {
    var q = (query || "").trim().toLowerCase();
    if (!q) {
      return entries;
    }
    var nameHits = [];
    var bodyHits = [];
    entries.forEach(function (entry) {
      if ((entry.node.name || "").toLowerCase().indexOf(q) !== -1) {
        nameHits.push(entry);
      } else if ((entry.node.text || "").toLowerCase().indexOf(q) !== -1) {
        bodyHits.push(entry);
      }
    });
    return nameHits.concat(bodyHits);
  }

  // --- text insertion ---

  function insertText(target, text) {
    if (!target || !document.contains(target)) {
      return false;
    }
    target.focus({ preventScroll: true });
    // execCommand first: it preserves undo and notifies frameworks (React).
    try {
      if (document.execCommand("insertText", false, text)) {
        return true;
      }
    } catch (ignored) {
      /* fall through to the manual path */
    }
    var tag = (target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea") {
      try {
        var start = target.selectionStart === null ? target.value.length : target.selectionStart;
        var end = target.selectionEnd === null ? target.value.length : target.selectionEnd;
        target.setRangeText(text, start, end, "end");
        target.dispatchEvent(new Event("input", { bubbles: true }));
        return true;
      } catch (ignored) {
        return false;
      }
    }
    if (target.isContentEditable) {
      try {
        var selection = window.getSelection();
        if (selection && selection.rangeCount > 0) {
          var range = selection.getRangeAt(0);
          range.deleteContents();
          range.insertNode(document.createTextNode(text));
          range.collapse(false);
          target.dispatchEvent(new InputEvent("input", { bubbles: true }));
          return true;
        }
      } catch (ignored) {
        return false;
      }
    }
    return false;
  }

  // --- floating button ---

  function ensureButtonHost() {
    if (buttonHost) {
      return buttonHost;
    }
    buttonHost = document.createElement("div");
    buttonHost.id = "pastex-float-host";
    var shadow = buttonHost.attachShadow({ mode: "closed" });
    var style = document.createElement("style");
    style.textContent =
      ".pastex-float{position:fixed;z-index:2147483647;width:30px;height:30px;border-radius:50%;" +
      "border:1px solid #9ec1e8;background:#e8f1fd;color:#174a7c;font-size:15px;line-height:1;" +
      "cursor:pointer;box-shadow:0 2px 6px rgba(0,0,0,.25);padding:0}" +
      ".pastex-float:hover{background:#d3e5fb}";
    var button = document.createElement("button");
    button.className = "pastex-float";
    button.title = "Paste a Pastex snippet";
    button.textContent = "⚡";
    button.addEventListener("mousedown", function (event) {
      // Mousedown (not click): grabbing focus first would blur the field
      // and move the button away before the click lands.
      event.preventDefault();
      openPicker(lastEditable);
    });
    shadow.appendChild(style);
    shadow.appendChild(button);
    document.documentElement.appendChild(buttonHost);
    // Closed shadow root: keep our own reference to the button.
    buttonHost.__pastexButton = button;
    return buttonHost;
  }

  function positionButton(target) {
    var host = ensureButtonHost();
    var rect = target.getBoundingClientRect();
    var button = host.__pastexButton;
    if (!button) {
      return;
    }
    var x = Math.min(window.innerWidth - 38, Math.max(4, rect.right - 34));
    var y = Math.min(window.innerHeight - 38, Math.max(4, rect.bottom - 38));
    button.style.left = x + "px";
    button.style.top = y + "px";
    button.style.display = "block";
  }

  function hideButton() {
    if (buttonHost && buttonHost.__pastexButton) {
      buttonHost.__pastexButton.style.display = "none";
    }
  }

  function scheduleHideButton() {
    window.clearTimeout(buttonHideTimer);
    buttonHideTimer = window.setTimeout(hideButton, BUTTON_HIDE_DELAY_MS);
  }

  function cancelHideButton() {
    window.clearTimeout(buttonHideTimer);
  }

  // --- picker popup ---

  function closePicker() {
    if (pickerHost) {
      pickerHost.remove();
      pickerHost = null;
    }
    pickerState = null;
  }

  async function openPicker(target) {
    if (!(await siteAllowed())) { hideButton(); closePicker(); return; }
    closePicker();
    cancelHideButton();
    hideButton();
    var state = { target: target && document.contains(target) ? target : lastEditable,
      entries: [], matches: [], index: 0, error: "Loading Google Drive…" };
    pickerState = state;
    renderPicker("");
    try {
      var result = await loadTree();
      if (pickerState !== state) return; // closed or replaced while loading
      state.entries = flattenWithPaths(result.tree);
      state.error = result.warnings && result.warnings.length ? "Unreadable Drive item: " + result.warnings[0] : "";
      renderPicker(pickerHost.__pastexInput.value);
    } catch (err) {
      if (pickerState !== state) return;
      state.error = "Drive: " + (err.message || err);
      renderPicker(pickerHost.__pastexInput.value);
    }
  }

  function togglePicker() {
    if (pickerHost) {
      closePicker();
      return;
    }
    openPicker(lastEditable);
  }

  function renderPicker(query) {
    pickerState.matches = searchEntries(pickerState.entries, query);
    pickerState.index = 0;
    if (!pickerHost) {
      pickerHost = document.createElement("div");
      pickerHost.id = "pastex-picker-host";
      var shadow = pickerHost.attachShadow({ mode: "closed" });
      var style = document.createElement("style");
      style.textContent =
        ".pastex-picker{position:fixed;z-index:2147483647;width:380px;max-width:calc(100vw - 16px);" +
        "background:#fff;border:1px solid #9ec1e8;border-radius:8px;box-shadow:0 8px 28px rgba(0,0,0,.3);" +
        "font:13px system-ui,sans-serif;color:#222;overflow:hidden}" +
        ".pastex-picker input{box-sizing:border-box;width:calc(100% - 12px);margin:6px;padding:6px 8px;" +
        "border:1px solid #c6d8ec;border-radius:5px;font:inherit;outline:none}" +
        ".pastex-picker input:focus{border-color:#4a90d9}" +
        ".pastex-picker ul{list-style:none;margin:0;padding:0 6px 6px;max-height:264px;overflow:auto}" +
        ".pastex-picker li{padding:6px 8px;border-radius:5px;cursor:pointer;white-space:nowrap;" +
        "overflow:hidden;text-overflow:ellipsis}" +
        ".pastex-picker li.active{background:#e8f1fd}" +
        ".pastex-picker .empty{padding:10px;color:#777;text-align:center}";
      var box = document.createElement("div");
      box.className = "pastex-picker";
      var input = document.createElement("input");
      input.type = "text";
      input.placeholder = "Type to filter snippets…";
      input.setAttribute("aria-label", "Filter snippets");
      var list = document.createElement("ul");
      var warning = document.createElement("div");
      warning.setAttribute("role", "status");
      warning.style.cssText = "padding:0 8px;color:#9a4310;font-size:11px";
      box.appendChild(input);
      box.appendChild(warning);
      box.appendChild(list);
      shadow.appendChild(style);
      shadow.appendChild(box);
      document.documentElement.appendChild(pickerHost);
      pickerHost.__pastexBox = box;
      pickerHost.__pastexInput = input;
      pickerHost.__pastexList = list;
      pickerHost.__pastexWarning = warning;

      input.addEventListener("input", function () {
        renderPicker(input.value);
      });
      input.addEventListener("keydown", function (event) {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          movePicker(event.key === "ArrowDown" ? 1 : -1);
        } else if (event.key === "Enter") {
          event.preventDefault();
          choosePicker();
        } else if (event.key === "Escape") {
          event.preventDefault();
          closePicker();
        }
      });
      input.addEventListener("blur", function () {
        // Let a list click land before closing.
        window.setTimeout(function () {
          if (pickerHost && !pickerHost.__pastexHover) {
            closePicker();
          }
        }, 150);
      });
      list.addEventListener("mousedown", function (event) {
        var item = event.target && event.target.closest ? event.target.closest("li[data-index]") : null;
        if (item) {
          event.preventDefault();
          pickerState.index = parseInt(item.getAttribute("data-index"), 10) || 0;
          choosePicker();
        }
      });
      list.addEventListener("mouseover", function () {
        pickerHost.__pastexHover = true;
      });
      list.addEventListener("mouseout", function () {
        pickerHost.__pastexHover = false;
      });
      document.addEventListener("keydown", escClosesPicker, true);
      placePicker(box);
      input.focus();
    }
    pickerHost.__pastexWarning.textContent = pickerState.entries.length ? (pickerState.error || "") : "";
    paintPickerList();
  }

  function escClosesPicker(event) {
    if (event.key === "Escape" && pickerHost) {
      closePicker();
      document.removeEventListener("keydown", escClosesPicker, true);
    }
  }

  function placePicker(box) {
    // At the focused field when there is one, else near the pointer-fed
    // position is impossible without tracking, so use the viewport corner
    // offset: the common case (button/shortcut while typing) has a field.
    var x = window.innerWidth - 400;
    var y = 60;
    var target = pickerState && pickerState.target;
    if (target && document.contains(target)) {
      var rect = target.getBoundingClientRect();
      x = Math.min(window.innerWidth - 396, Math.max(8, rect.left));
      y = rect.bottom + 6;
      if (y + 320 > window.innerHeight) {
        y = Math.max(8, rect.top - 326);
      }
    }
    box.style.left = Math.max(8, x) + "px";
    box.style.top = Math.max(8, y) + "px";
  }

  function paintPickerList() {
    if (!pickerHost) {
      return;
    }
    var list = pickerHost.__pastexList;
    list.textContent = "";
    var matches = pickerState.matches.slice(0, MAX_ROWS);
    if (!matches.length) {
      var empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = pickerState.error || (pickerState.entries.length ? "No snippets match." : "No snippets in Google Drive.");
      list.appendChild(empty);
      return;
    }
    matches.forEach(function (entry, i) {
      var item = document.createElement("li");
      item.textContent = entry.path;
      item.title = entry.path;
      item.setAttribute("data-index", String(i));
      if (i === pickerState.index) {
        item.classList.add("active");
      }
      item.addEventListener("mousemove", function () {
        pickerState.index = i;
        paintPickerList();
      });
      list.appendChild(item);
    });
  }

  function movePicker(delta) {
    if (!pickerState || !pickerState.matches.length) {
      return;
    }
    var count = Math.min(pickerState.matches.length, MAX_ROWS);
    pickerState.index = (pickerState.index + delta + count) % count;
    paintPickerList();
  }

  async function choosePicker() {
    if (!(await siteAllowed())) { hideButton(); closePicker(); return; }
    if (!pickerState || !pickerState.matches.length) {
      return;
    }
    var entry = pickerState.matches[Math.min(pickerState.index, pickerState.matches.length - 1)];
    var target = pickerState.target;
    closePicker();
    if (entry) insertText(target, entry.node.text || "");
  }

  // --- focus tracking + button visibility ---

  document.addEventListener(
    "focusin",
    function (event) {
      var el = event.target;
      if (isEditable(el)) {
        lastEditable = el;
        siteAllowed().then(function (allowed) {
          if (allowed && document.hasFocus() && document.activeElement === el) {
            cancelHideButton();
            positionButton(el);
          } else if (!allowed) {
            hideButton();
            closePicker();
          }
        });
      }
    },
    true
  );

  document.addEventListener(
    "focusout",
    function () {
      scheduleHideButton();
    },
    true
  );

  window.addEventListener(
    "scroll",
    function () {
      if (buttonHost && buttonHost.__pastexButton && buttonHost.__pastexButton.style.display === "block" && lastEditable && document.contains(lastEditable)) {
        positionButton(lastEditable);
      }
    },
    true
  );

  // --- messages from the background worker ---

  chrome.runtime.onMessage.addListener(function (message, sender, sendResponse) {
    if (!message || typeof message.type !== "string" || message.type.indexOf("pastex-") !== 0) {
      return false;
    }
    if (message.type === "pastex-ping") {
      sendResponse({ ok: true });
      return true;
    }
    if (message.type === "pastex-toggle-picker") {
      togglePicker();
      sendResponse({ ok: true });
      return true;
    }
    if (message.type === "pastex-insert") {
      var target = lastEditable && document.contains(lastEditable) ? lastEditable : null;
      if (!target) {
        // No field focused: show the picker instead of failing silently.
        openPicker(null);
        sendResponse({ ok: false, reason: "no-editable" });
        return true;
      }
      var done = insertText(target, message.text || "");
      sendResponse({ ok: done });
      return true;
    }
    return false;
  });
})();
