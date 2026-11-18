/* Talk to the Pastex native host (com.pastex.native), the bridge to
 * ~/.config/pastex/*.json. The apt package installs the host plus its
 * manifests; without apt (unpacked dev install, non-Linux) the host is
 * absent and every call below fails fast so callers fall back to the
 * manual Settings form backed by chrome.storage.
 *
 * Classic script, no DOM: loadable from pages, and unit-tested under node
 * through the module.exports branch below (with a stubbed chrome object).
 */

(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module && module.exports) {
    module.exports = api;
  } else {
    root.PastexNative = api;
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var HOST = "com.pastex.native";
  var TIMEOUT_MS = 8000;

  // Negative cache: when the host is missing, sendNativeMessage fails
  // immediately anyway, but a missing-host probe on every keystroke would
  // still spam the log — remember absence briefly.
  var unavailableUntil = 0;
  var UNAVAILABLE_TTL_MS = 30000;

  function messaging() {
    if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.sendNativeMessage) {
      return chrome.runtime;
    }
    return null;
  }

  function markUnavailable() {
    unavailableUntil = Date.now() + UNAVAILABLE_TTL_MS;
  }

  function send(message) {
    return new Promise(function (resolve, reject) {
      var runtime = messaging();
      if (!runtime) {
        reject(new Error("native messaging is unavailable"));
        return;
      }
      if (Date.now() < unavailableUntil) {
        reject(new Error("native host is unavailable"));
        return;
      }
      var settled = false;
      var timer = setTimeout(function () {
        if (!settled) {
          settled = true;
          markUnavailable();
          reject(new Error("native host timed out"));
        }
      }, TIMEOUT_MS);
      try {
        runtime.sendNativeMessage(HOST, message, function (response) {
          if (settled) {
            return;
          }
          settled = true;
          clearTimeout(timer);
          var lastError = runtime.lastError;
          if (lastError) {
            markUnavailable();
            reject(new Error(lastError.message || "native host error"));
            return;
          }
          if (!response || response.ok !== true) {
            reject(new Error((response && response.error) || "native host error"));
            return;
          }
          resolve(response);
        });
      } catch (err) {
        if (!settled) {
          settled = true;
          clearTimeout(timer);
          markUnavailable();
          reject(err instanceof Error ? err : new Error(String(err)));
        }
      }
    });
  }

  function status() {
    return send({ type: "pastex-status" });
  }

  function readConfig() {
    return send({ type: "pastex-read-config" });
  }

  function writeDriveConfig(folderId, folderUrl) {
    return send({ type: "pastex-write-drive-config", folderId: folderId || "", folderUrl: folderUrl || "" });
  }

  function writeServiceAccount(key) {
    return send({ type: "pastex-write-service-account", key: key });
  }

  function resetCache() {
    unavailableUntil = 0;
  }

  return {
    HOST: HOST,
    TIMEOUT_MS: TIMEOUT_MS,
    send: send,
    status: status,
    readConfig: readConfig,
    writeDriveConfig: writeDriveConfig,
    writeServiceAccount: writeServiceAccount,
    resetCache: resetCache,
  };
});
