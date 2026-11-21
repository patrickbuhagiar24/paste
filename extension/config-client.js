/* MV3 workers use an extension offscreen document for file-system access. */
(function (root) {
  "use strict";
  var pending;
  async function ensureDocument() {
    if (pending) return pending;
    pending = (async function () {
      if (!await chrome.offscreen.hasDocument()) {
        await chrome.offscreen.createDocument({
          url: "config-reader.html", reasons: ["BLOBS"],
          justification: "Read user-selected configuration files using File System Access handles.",
        });
      }
    })();
    try { await pending; } finally { pending = null; }
  }
  async function read(operation, force) {
    await ensureDocument();
    var response = await chrome.runtime.sendMessage({ target: "pastex-config-reader", operation: operation, force: !!force });
    if (!response || !response.ok) throw new Error((response && response.error) || "Configuration reader unavailable; open Config and choose the manifest again.");
    return response.data;
  }
  root.PastexConfigClient = {
    readConfig: function (force) { return read("drive", force); },
    readSites: function (force) { return read("sites", force); },
    invalidate: function () { return read("invalidate"); },
  };
})(self);
