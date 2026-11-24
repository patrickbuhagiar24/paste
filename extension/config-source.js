/* Read configuration from one file the user picked once, then keep the parsed
 * JSON in persistent storage so later reads never open a file again.
 *
 * Why: only the side panel has a user gesture and a File System Access handle.
 * A handle cannot be used from an extension document other than the page that
 * holds it ("The request is not allowed by the user agent or the platform in the
 * current context"), so the panel reads the file once, stores its contents, and
 * everything afterwards reads that cache. The file is a source of configuration,
 * never its storage.
 *
 * The user picks the file they mean, by name, in the file picker. There is no
 * folder scan and no search for "the files Pastex needs": one pick is one file,
 * which is why nothing here can pick the wrong one. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.PastexConfigSource = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  // One file, three links and the client id. A link is what Drive's own Share
  // button gives you, so the file can be written without knowing which id
  // belongs to what.
  var STORAGE = "pastex-storage.json";
  var LINK_FOLDER = "pastex-folder", LINK_SITES = "pastex-sites", LINK_INDEX = "pastex-index";
  var CLIENT = "client_id";
  var FILE = "file ", CACHE = "pastex-config-cache";
  // The cached configuration shape is versioned. A record with another version
  // is treated as absent, which asks the user to pick the file again instead of
  // decoding an unknown shape. See DEVELOPMENT.md.
  var CACHE_VERSION = 5;
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

    async function writeCache(file, name) {
      await storage().set({ [CACHE]: {
        version: CACHE_VERSION,
        savedAt: new Date().toISOString(),
        file: name || "",
        value: file,
      } });
    }

    /* Read the one file the user picked. The name is checked because the picker
     * cannot filter by name: picking something else is a mistake the user can
     * see and fix, not a different configuration to decode. */
    async function readPickedFile(handle) {
      if (!handle || typeof handle.getFile !== "function")
        throw new Error("Choose " + STORAGE + " in Config first.");
      var name = handle.name || "";
      if (name !== STORAGE) {
        throw new Error("Choose " + STORAGE + ", not " + (name || "another file") + ".");
      }
      var file;
      try { file = await handle.getFile(); }
      catch (err) { throw new Error("That file could not be read. Choose it again."); }
      var text;
      try { text = await file.text(); }
      catch (err) { throw new Error("That file could not be read. Choose it again."); }
      if (text.length > MAX_BYTES) throw new Error(STORAGE + " is larger than " + (MAX_BYTES / 1024) + " KiB.");
      var value;
      try { value = JSON.parse(text.replace(/^\uFEFF/, "")); }
      catch (err) { throw new Error(STORAGE + " is not valid JSON."); }
      if (!value || typeof value !== "object" || Array.isArray(value))
        throw new Error(STORAGE + " must contain a JSON object.");
      return { name: file.name || name, value: value };
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
     * pick the file again after each single error is being told one third of
     * what is wrong, so every problem is collected, not thrown one by one. */
    function problems(storage) {
      var list = [];
      if (!storage || typeof storage !== "object") {
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

    function validate(storage) {
      var checked = problems(storage);
      if (checked.list.length) throw new Error(checked.list.join("; ") + ".");
      // sites.parse() already ran inside problems(), so the list is known good.
      return checked.value;
    }

    /* Called by the panel after the user picks the file. Reads it, validates
     * it, and only then replaces the cache: a broken file never wipes a working
     * configuration. */
    async function chooseFile(handle) {
      var picked = await readPickedFile(handle);
      validate(picked.value);
      await writeCache(picked.value, picked.name);
      memoized.config = null; memoized.configKey = "";
      memoized.sites = null; memoized.sitesKey = "";
      return { storage: FILE + picked.name };
    }

    /* All later reads come from the cache; the file is never opened again. */
    async function readConfigFrom(force) {
      var record = await readCache();
      if (!record) throw new Error("Choose " + STORAGE + " in Config first.");
      var key = record.savedAt + ":" + record.file;
      return memo("config", key, !!force, async function () {
        var v = validate(record.value);
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
      if (!record) throw new Error("Choose " + STORAGE + " in Config first.");
      var key = record.savedAt + ":" + record.file;
      return memo("sites", key, !!force, async function () {
        var v = validate(record.value);
        if (v.allowedSites) return { allowedSites: v.allowedSites, fileId: "", source: FILE + STORAGE };
        return { allowedSites: null, fileId: v.sitesId, source: FILE + STORAGE };
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
      chooseFile: chooseFile,
      readConfig: readConfigFrom,
      readSites: readSitesFrom,
    };
  }
  return { create: create };
});
