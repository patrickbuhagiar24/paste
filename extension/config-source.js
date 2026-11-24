/* Read configuration from a folder the user picked once, then keep the parsed
 * JSON in persistent storage so later reads never touch the folder again.
 *
 * Why: only the side panel has a user gesture and a folder handle. A File
 * System Access handle cannot be used from an extension document other than
 * the page that holds it ("The request is not allowed by the user agent or the
 * platform in the current context"), so the panel scans the folder once,
 * stores the result, and everything afterwards reads that cache. The folder is
 * a source of configuration, never its storage. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.PastexConfigSource = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  // One file, three links. A link is what Drive's own Share button gives you, so
  // the configuration can be written without knowing which id belongs to what.
  var STORAGE = "pastex-storage.json";
  var LINK_FOLDER = "pastex-folder", LINK_SITES = "pastex-sites", LINK_INDEX = "pastex-index";
  var CLIENT = "client_id";
  var FOLDER = "folder ", CACHE = "pastex-config-cache";
  // The cached configuration shape is versioned. A record with another version
  // is treated as absent, which asks the user to pick the folder again instead
  // of decoding an unknown shape. See DEVELOPMENT.md.
  var CACHE_VERSION = 4;
  var MAX_BYTES = 256 * 1024;

  function create(sites, cacheStore) {
    var memoized = { config: null, configKey: "", sites: null, sitesKey: "" };
    var inFlight = new Map();

    function storage() {
      if (!cacheStore || typeof cacheStore.get !== "function" || typeof cacheStore.set !== "function")
        throw new Error("Configuration storage is unavailable.");
      return cacheStore;
    }

    async function readCache() {
      // chrome.storage.get(key) resolves to an OBJECT keyed by the key, so the
      // record has to be unwrapped. Reading it as the record itself makes
      // `files` undefined on every later open.
      var result = await storage().get(CACHE);
      var record = result && typeof result === "object" ? result[CACHE] : null;
      if (!record || typeof record !== "object" || record.version !== CACHE_VERSION) return null;
      return record;
    }

    async function writeCache(files, name) {
      await storage().set({ [CACHE]: {
        version: CACHE_VERSION,
        savedAt: new Date().toISOString(),
        folder: name || "",
        files: files,
      } });
    }

    /* Scan only the surface of the chosen folder: entries that are files and
     * end in .json. Subfolders are skipped without being opened. */
    async function scanFolder(handle) {
      if (!handle || typeof handle.values !== "function")
        throw new Error("Choose a configuration folder in Config first.");
      var raw = {};
      for await (var entry of handle.values()) {
        if (entry.kind !== "file") continue;
        if (!/\.json$/i.test(entry.name || "")) continue;
        var file;
        try { file = await entry.getFile(); } catch (err) { continue; }
        var text;
        try { text = await file.text(); } catch (err) { continue; }
        if (text.length > MAX_BYTES) continue;
        var value;
        try { value = JSON.parse(text.replace(/^\uFEFF/, "")); } catch (err) { continue; }
        if (!value || typeof value !== "object" || Array.isArray(value)) continue;
        raw[file.name || entry.name] = value;
      }
      if (!Object.keys(raw).length) throw new Error("No usable .json files in the folder.");
      return raw;
    }

    /* Identify the one configuration file. It carries a canonical name: a
     * rename is not supported, it is a configuration the user fixes once
     * (DEVELOPMENT.md: no compatibility layer for names). Any other .json file
     * in the folder is ignored, so the folder may hold notes or backups. */
    function identify(raw) {
      var files = {}, sources = {};
      if (Object.prototype.hasOwnProperty.call(raw, STORAGE)) {
        files[STORAGE] = raw[STORAGE];
        sources[STORAGE] = STORAGE;
      }
      return { files: files, sources: sources };
    }

    /* A Drive link, reduced to the id the API needs. Only the shapes Drive's
     * Share button and address bar produce are accepted: a bare id, a
     * /folders/<id> link, a /file/d/<id>/view link, or an ?id=<id> link. Anything
     * else is a typo the user can see, not something to guess at. */
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

    /* Check the file and report all the problems at once. A user who has to
     * pick the folder again after each single error is being told one third of
     * what is wrong, so every problem is collected, not thrown one by one. */
    function problems(found) {
      var files = found.files || {};
      var storage = files[STORAGE];
      var list = [];

      if (!storage) {
        list.push(STORAGE + " is missing");
        return { list: list, value: null };
      }

      var folderId = idFromLink(storage[LINK_FOLDER]);
      var indexId = idFromLink(storage[LINK_INDEX]);
      if (!folderId) list.push(STORAGE + " needs a " + LINK_FOLDER + " link to the library folder");
      if (!indexId) list.push(STORAGE + " needs an " + LINK_INDEX + " link to the order/collapse file");

      // The OAuth client of this installation lives in the same file: one file
      // describes the whole setup, and none of it is a secret.
      var clientId = typeof storage[CLIENT] === "string" ? storage[CLIENT].trim() : "";
      var validClient = /^[0-9]+-[A-Za-z0-9_-]+\.apps\.googleusercontent\.com$/.test(clientId);
      if (!clientId) list.push(STORAGE + " needs the " + CLIENT + " of a Google OAuth client");
      else if (!validClient) list.push(STORAGE + " needs the " + CLIENT + " of a Google OAuth client (type Web application)");

      // sites may also be an inline list, so a single file can describe the whole
      // installation; a link to a shared list is treated exactly like the id.
      var sitesValue = storage[LINK_SITES];
      var sitesId = idFromLink(sitesValue);
      var inline = Array.isArray(sitesValue) ? sitesValue : null;
      if (!sitesId && !inline) {
        list.push(STORAGE + " needs a " + LINK_SITES + " link to the site list or an inline array");
      } else if (inline) {
        try { sites.parse(inline); }
        catch (err) { list.push(STORAGE + ": " + (err.message || err)); }
      }

      return {
        list: list,
        value: folderId && indexId && validClient && (sitesId || inline) ?
          { folderId: folderId, indexId: indexId, clientId: clientId,
            sitesId: sitesId, allowedSites: inline } : null,
      };
    }

    function validate(found) {
      var checked = problems(found);
      if (checked.list.length) throw new Error(checked.list.join("; ") + ".");
      // sites.parse() already ran inside problems(), so the list is known good.
      return checked.value;
    }

    /* Called by the panel after the user picks a folder. Scans the surface,
     * validates, and only then replaces the cache: a broken folder never
     * wipes a working configuration. */
    async function chooseFolder(handle) {
      var found = identify(await scanFolder(handle));
      validate(found);
      await writeCache(found.files, handle.name || "");
      memoized.config = null; memoized.configKey = "";
      memoized.sites = null; memoized.sitesKey = "";
      return { storage: FOLDER + STORAGE };
    }

    /* All later reads come from the cache; the folder is not used again. */
    async function readConfigFrom(force) {
      var record = await readCache();
      if (!record) throw new Error("Choose a configuration folder in Config first.");
      var key = record.savedAt + ":" + record.folder;
      return memo("config", key, !!force, async function () {
        var v = validate({ files: record.files, sources: {} });
        return {
          drive: { folderId: v.folderId, indexId: v.indexId },
          driveError: "",
          oauth: { client_id: v.clientId },
          oauthError: "",
          // The site list is either a link to fetch or an array already read
          // from the one configuration file.
          sites: { fileId: v.sitesId || "", allowedSites: v.allowedSites || null },
          sources: {},
        };
      });
    }

    async function readSitesFrom(force) {
      var record = await readCache();
      if (!record) throw new Error("Choose a configuration folder in Config first.");
      var key = record.savedAt + ":" + record.folder;
      return memo("sites", key, !!force, async function () {
        var v = validate({ files: record.files, sources: {} });
        if (v.allowedSites) return { allowedSites: v.allowedSites, fileId: "", source: FOLDER + STORAGE };
        return { allowedSites: null, fileId: v.sitesId, source: FOLDER + STORAGE };
      });
    }

    function memo(kind, key, force, load) {
      if (!force && memoized[kind] && memoized[kind + "Key"] === key) return memoized[kind];
      var slot = kind + ":" + key;
      if (!force && inFlight.has(slot)) return inFlight.get(slot);
      var promise = (async function () {
        var value = await load();
        memoized[kind] = value;
        memoized[kind + "Key"] = key;
        return value;
      })();
      if (!force) inFlight.set(slot, promise);
      try { return promise; }
      finally { inFlight.get(slot) === promise && inFlight.delete(slot); }
    }

    return {
      chooseFolder: chooseFolder,
      readConfig: readConfigFrom,
      readSites: readSitesFrom,
    };
  }
  return { create: create };
});
