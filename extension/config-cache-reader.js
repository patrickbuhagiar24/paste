/* Read configuration straight from persistent storage inside the background
 * worker.
 *
 * The configuration is one file the user picked, of any name, holding three
 * Drive links and the client id; the site list may also be inline in it, so one
 * file describes the whole installation. The panel read that file once and
 * stored the parsed object, which is all this reader uses.
 *
 * The worker used to ask an offscreen document for configuration, and that
 * document tried to use the user's folder handle. File System Access handles
 * are not usable there, which surfaced as:
 *   "The request is not allowed by the user agent or the platform in the
 *    current context."
 * Configuration is now cached when the panel reads the file, so the worker only
 * needs chrome.storage — no document, no handle, no request to a file. */
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
    /* One file, three links, exactly as the panel validated it. Errors name the
     * missing key, never a filename, because the file may be called anything. The links are
     * reduced to ids here again because this reader is a separate context and
     * must not trust a stored shape it cannot check. */
    function idFromLink(value) {
      var text = typeof value === "string" ? value.trim() : "";
      if (!text) return "";
      if (/^[A-Za-z0-9_-]{10,}$/.test(text)) return text;
      var folder = /\/folders\/([A-Za-z0-9_-]{10,})/.exec(text);
      if (folder) return folder[1];
      var file = /\/file\/d\/([A-Za-z0-9_-]{10,})/.exec(text);
      if (file) return file[1];
      var query = /[?&]id=([A-Za-z0-9_-]{10,})/.exec(text);
      if (query) return query[1];
      return "";
    }

    function storageFile(saved) {
      // The record holds the parsed configuration of the one file the panel
      // picked; there is no file map any more.
      return saved.value || null;
    }

    function missing() {
      return new Error("Choose your configuration file in Config first.");
    }

    async function readConfig() {
      var saved = await record();
      if (!saved) throw missing();
      var storage = storageFile(saved);
      var folderId = storage ? idFromLink(storage["pastex-folder"]) : "";
      var indexId = storage ? idFromLink(storage["pastex-index"]) : "";
      var sitesValue = storage ? storage["pastex-sites"] : null;
      var sitesId = idFromLink(sitesValue);
      var inline = Array.isArray(sitesValue) ? sitesValue : null;
      var validFolder = /^[A-Za-z0-9_-]+$/.test(folderId);
      var validIndex = /^[A-Za-z0-9_-]+$/.test(indexId);
      var clientId = storage && typeof storage.client_id === "string" ? storage.client_id.trim() : "";
      var validClient = /^[0-9]+-[A-Za-z0-9_-]+\.apps\.googleusercontent\.com$/.test(clientId);
      return {
        drive: validFolder && validIndex ? { folderId: folderId, indexId: indexId } : null,
        driveError: validFolder && validIndex ? "" :
          "The configuration file needs a pastex-folder link and a pastex-index link.",
        oauth: validClient ? { client_id: clientId } : null,
        oauthError: validClient ? "" : "The configuration file needs the client_id of a Google OAuth client.",
        sites: { fileId: sitesId, allowedSites: inline },
        sources: {},
      };
    }
    async function readSites() {
      var saved = await record();
      if (!saved) throw missing();
      var storage = storageFile(saved);
      var value = storage ? storage["pastex-sites"] : null;
      if (Array.isArray(value)) return { allowedSites: value, fileId: "", source: "configuration file" };
      var id = idFromLink(value);
      if (!id) throw new Error("The configuration file needs a pastex-sites link or an inline array.");
      return { allowedSites: null, fileId: id, source: "configuration file" };
    }
    return {
      readConfig: readConfig,
      readSites: readSites,
      invalidate: async function () { await storage.set({ [CACHE]: null }); },
    };
  }
  root.PastexConfigCacheReader = { create: create };
})(self);
