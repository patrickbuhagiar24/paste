/* The offscreen document reads granted handles on behalf of the service worker. */
"use strict";
var source = PastexConfigSource.create(PastexHandleStore, PastexConfigImport, PastexSites, PastexManifest);
chrome.runtime.onMessage.addListener(function (message, sender, respond) {
  if (!message || message.target !== "pastex-config-reader") return false;
  if (!sender || sender.id !== chrome.runtime.id || sender.url !== chrome.runtime.getURL("background.js")) return false;
  var force = !!message.force;
  var operation = message.operation === "sites" ? source.readSites(force) :
    message.operation === "drive" ? source.readConfig(force) :
    message.operation === "invalidate" ? Promise.resolve(source.invalidate()) :
    Promise.reject(new Error("Unknown configuration request."));
  operation.then(function (data) { respond({ ok: true, data: data }); })
    .catch(function (err) { respond({ ok: false, error: err.message || String(err) }); });
  return true;
});
