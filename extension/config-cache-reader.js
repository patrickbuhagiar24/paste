/* Read configuration straight from persistent storage inside the background
 * worker.
 *
 * The worker used to ask an offscreen document for configuration, and that
 * document tried to use the user's folder handle. File System Access handles
 * are not usable there, which surfaced as:
 *   "The request is not allowed by the user agent or the platform in the
 *    current context."
 * Configuration is now cached when the panel scans the folder, so the worker
 * only needs chrome.storage — no document, no handle, no request to a folder. */
(function (root) {
  "use strict";
  var CACHE = "pastex-config-cache";

  function create(storage) {
    async function record() {
      // chrome.storage.get(name) resolves to an object keyed by `name`; the
      // saved configuration is the value under that key, not the wrapper.
      var result = await storage.get(CACHE);
      var value = result && typeof result === "object" ? result[CACHE] : null;
      return value && typeof value === "object" ? value : null;
    }
    async function readConfig() {
      var saved = await record();
      if (!saved) throw new Error("Choose a configuration folder in Config first.");
      var files = saved.files || {};
      var drive = files["pastex-google-drive.json"] || {};
      var oauth = files["pastex-oauth.json"] || {};
      var id = typeof drive.folder_id === "string" ? drive.folder_id.trim() : "";
      if (!id && typeof drive.folder_url === "string") {
        var match = /\/folders\/([A-Za-z0-9_-]+)/.exec(drive.folder_url);
        id = match ? match[1] : "";
      }
      var validId = /^[A-Za-z0-9_-]+$/.test(id);
      var clientId = typeof oauth.client_id === "string" ? oauth.client_id.trim() : "";
      var validClient = /^[0-9]+-[A-Za-z0-9_-]+\.apps\.googleusercontent\.com$/.test(clientId);
      return {
        drive: validId ? { folderId: id } : null,
        driveError: validId ? "" : "Add a pastex-google-drive.json with a valid folder_id to the configuration folder.",
        oauth: validClient ? { client_id: clientId } : null,
        oauthError: validClient ? "" : "Add pastex-oauth.json with the client_id of a Google OAuth client (type Web application).",
        sources: {},
      };
    }
    async function readSites() {
      var saved = await record();
      if (!saved) throw new Error("Choose a configuration folder in Config first.");
      var file = (saved.files || {})["pastex-sites.json"];
      if (!file || !Array.isArray(file.allowed_sites))
        throw new Error("pastex-sites.json: allowed_sites must be an array.");
      return { allowedSites: file.allowed_sites, source: "configuration folder" };
    }
    return {
      readConfig: readConfig,
      readSites: readSites,
      invalidate: async function () { await storage.set({ [CACHE]: null }); },
    };
  }
  root.PastexConfigCacheReader = { create: create };
})(self);
