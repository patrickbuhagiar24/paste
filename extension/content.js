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
  // Whether this page may also close the panel again (Chrome 141+) and
  // whether the launcher currently opens or closes it.
  var canToggle = false;
  var panelOpen = false;

  function setLauncherLabel(button) {
    if (!button) return;
    button.textContent = canToggle && panelOpen ? "×" : "⚡";
    button.title = canToggle && panelOpen ? "Close Pastex side panel" : "Open Pastex side panel";
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
    // The launcher must read at a glance on any page: a deep indigo disc
    // with a near-black rim, a white inner ring and a coloured glow. The
    // white ring keeps it visible on dark pages, the dark rim on light ones.
    style.textContent =
      ".pastex-float{position:fixed;z-index:2147483647;width:32px;height:32px;border-radius:50%;" +
      "border:2px solid #101a3d;background:linear-gradient(180deg,#3b5bdb 0%,#1c3585 100%);" +
      "color:#fff;font-size:17px;line-height:1;cursor:pointer;padding:0;" +
      "font-family:'Apple Color Emoji','Segoe UI Emoji','Noto Color Emoji',sans-serif;" +
      "box-shadow:0 0 0 2px #fff,0 0 0 4px #101a3d,0 0 18px 4px rgba(59,91,219,.62)," +
      "0 3px 10px rgba(0,0,0,.4);transition:transform .12s ease,background .12s ease}" +
      ".pastex-float:hover{background:linear-gradient(180deg,#4c6ef5 0%,#24409a 100%);" +
      "transform:scale(1.08)}" +
      ".pastex-float:active{transform:scale(.94)}" +
      ".pastex-float:focus-visible{outline:3px solid #facc15;outline-offset:3px}" +
      // A slow halo around the button marks it as the Pastex launcher.
      ".pastex-float::after{content:'';position:absolute;inset:-2px;border-radius:50%;" +
      "border:2px solid rgba(59,91,219,.85);pointer-events:none;" +
      "animation:pastex-halo 2.4s ease-out infinite}" +
      "@keyframes pastex-halo{0%{transform:scale(1);opacity:.85}" +
      "70%{transform:scale(2);opacity:0}100%{transform:scale(2);opacity:0}}" +
      "@media (prefers-reduced-motion: reduce){.pastex-float::after{animation:none;opacity:0}}" +
      "@media (prefers-contrast: more){.pastex-float{border-width:3px;" +
      "box-shadow:0 0 0 3px #fff,0 0 0 6px #000,0 0 22px 6px rgba(59,91,219,.82)}}";
    var button = document.createElement("button");
    button.className = "pastex-float";
    button.title = "Open Pastex side panel";
    button.textContent = "⚡";
    button.addEventListener("mousedown", function (event) {
      // Mousedown (not click): grabbing focus first would blur the field
      // and move the button away before the click lands.
      if (!event.isTrusted || !capability) return;
      event.preventDefault();
      var closing = canToggle && panelOpen;
      chrome.runtime.sendMessage({
        type: "pastex-open-panel", capability: capability, action: closing ? "close" : "open",
      }).then(function (response) {
        if (!response || !response.ok) {
          // The page lost its authorization (for example the allowlist) or the
          // browser refused; the button says what the next click will do.
          setLauncherLabel(button);
          button.title = closing ? "Could not close Pastex; use the panel's own close button." :
            "Could not open Pastex; use its toolbar icon.";
          return;
        }
        if (response.closed) panelOpen = false;
        else if (!closing) panelOpen = true;
        setLauncherLabel(button);
      }).catch(function () { button.title = "Could not reach Pastex; use its toolbar icon."; });
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
    // The button sits over the bottom right corner of the focused field.
    var x = Math.min(window.innerWidth - 40, Math.max(4, rect.right - 36));
    var y = Math.min(window.innerHeight - 40, Math.max(4, rect.bottom - 40));
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
      canToggle = !!response.toggle;
      cancelHideButton();
      if (buttonHost) setLauncherLabel(buttonHost.__pastexButton);
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

  // Edits made on a site wait in the cache. The queue is sent only when the
  // tab is closed, never while the page still exists in any state: hiding a
  // tab by switching away is not leaving the site. The worker decides what to
  // send, so this only reports the close and never touches Drive itself.
  function reportClosed() {
    try {
      chrome.runtime.sendMessage({ type: "pastex-flush-now" }).catch(function () {});
    } catch (ignored) { /* the page is going away */ }
  }
  // The worker also refuses to run a background check while a site is open, so
  // visibility is reported separately from the close.
  function reportVisible(visible) {
    try {
      chrome.runtime.sendMessage({ type: "pastex-site-visible", visible: !!visible }).catch(function () {}); 
    } catch (ignored) { /* the page is going away */ }
  }
  document.addEventListener("visibilitychange", function () {
    reportVisible(document.visibilityState !== "hidden");
  });
  // pagehide is the last reliable event before a tab really goes away, and it
  // also fires on a navigation away. Hiding a tab does not fire it.
  window.addEventListener("pagehide", function () { reportVisible(false); reportClosed(); });
  reportVisible(document.visibilityState !== "hidden");

  chrome.runtime.onMessage.addListener(function (message, sender, sendResponse) {
    if (!message || typeof message.type !== "string" || message.type.indexOf("pastex-") !== 0) {
      return false;
    }
    if (message.type === "pastex-panel-state") {
      if (!sender || sender.id !== chrome.runtime.id) {
        sendResponse({ ok: false, reason: "unauthorized" });
        return true;
      }
      panelOpen = !!message.open;
      if (buttonHost) setLauncherLabel(buttonHost.__pastexButton);
      sendResponse({ ok: true });
      return true;
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
