/* Pastex in-page layer: insert snippets at the cursor.
 *
 *  - Tracks the last focused editable (input, textarea, contenteditable).
 *  - Shows a small button next to the focused field to open the side panel,
 *    on configured sites only.
 *  - Responds to side-panel insertion requests from the background worker.
 */

(function () {
  "use strict";

  if (window.__pastexContentLoaded) {
    return;
  }
  window.__pastexContentLoaded = true;

  var BUTTON_HIDE_DELAY_MS = 180;

  var lastEditable = null;
  var buttonHost = null;
  var buttonHideTimer = 0;
  var capability = "";
  var authTimer = 0;
  var focusGeneration = 0;

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

  // --- text insertion ---

  function insertText(target, text) {
    if (!target || !document.contains(target)) {
      return false;
    }
    target.focus({ preventScroll: true });
    var tag = (target.tagName || "").toLowerCase();
    // Inputs and textareas must keep the snippet byte-for-byte, including
    // newlines and trailing spaces. execCommand can normalise or drop them.
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
    // Contenteditable: execCommand preserves undo and notifies frameworks.
    try {
      if (document.execCommand("insertText", false, text)) {
        return true;
      }
    } catch (ignored) {
      /* fall through to the manual path */
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
    button.title = "Open Pastex side panel";
    button.textContent = "⚡";
    button.addEventListener("mousedown", function (event) {
      // Mousedown (not click): grabbing focus first would blur the field
      // and move the button away before the click lands.
      if (!event.isTrusted || !capability) return;
      event.preventDefault();
      chrome.runtime.sendMessage({ type: "pastex-open-panel", capability: capability }).then(function (response) {
        if (!response || !response.ok) button.title = "Could not open Pastex; use its toolbar icon.";
      }).catch(function () { button.title = "Could not open Pastex; use its toolbar icon."; });
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
    buttonHideTimer = window.setTimeout(function () {
      hideButton();
    }, BUTTON_HIDE_DELAY_MS);
  }

  function cancelHideButton() {
    window.clearTimeout(buttonHideTimer);
  }

  // Refresh short-lived authorization while an editable field stays focused.
  // The click itself must be sent synchronously to preserve Chrome's gesture.
  function authorize(el, generation) {
    chrome.runtime.sendMessage({ type: "pastex-site-allowed" }).then(function (response) {
      if (generation !== focusGeneration) return;
      if (!response || !response.allowed || !response.capability || !document.hasFocus() || document.activeElement !== el) {
        capability = "";
        hideButton();
        return;
      }
      capability = response.capability;
      cancelHideButton();
      positionButton(el);
      authTimer = window.setTimeout(function () { authorize(el, generation); }, 10000);
    }).catch(function () { if (generation === focusGeneration) { capability = ""; hideButton(); } });
  }

  document.addEventListener("focusin", function (event) {
    var el = event.target;
    if (!isEditable(el)) return;
    lastEditable = el;
    capability = "";
    window.clearTimeout(authTimer);
    var generation = ++focusGeneration;
    authorize(el, generation);
  }, true);

  document.addEventListener("focusout", function () {
    focusGeneration++;
    capability = "";
    window.clearTimeout(authTimer);
    scheduleHideButton();
  }, true);

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
    if (message.type === "pastex-insert") {
      // Only our background worker may ask this page to insert text.
      if (!sender || sender.id !== chrome.runtime.id) {
        sendResponse({ ok: false, reason: "unauthorized" });
        return true;
      }
      var target = lastEditable && document.contains(lastEditable) ? lastEditable : null;
      if (!target) {
        // The side panel can offer its clipboard fallback instead.
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
