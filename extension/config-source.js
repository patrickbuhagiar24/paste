/* Read configuration from a folder the user picked once, then keep the parsed
 * JSON in persistent storage so later reads never touch the folder again.
 *
 * Why: only the side panel has a user gesture and a folder handle. The
 * background worker reads configuration from an offscreen document, which is
 * not allowed to use File System Access handles ("The request is not allowed by
 * the user agent or the platform in the current context"). So the panel scans
 * the folder once, stores the result, and everything afterwards reads the cache.
 * The folder is a source of configuration, never its storage. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.PastexConfigSource = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  var DRIVE = "pastex-google-drive.json", SITES = "pastex-sites.json", ACCOUNT = "service-account.json";
  var FOLDER = "folder ", CACHE = "pastex-config-cache";
  var MAX_BYTES = 256 * 1024;

  function create(store, discovery, sites, cacheStore) {
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
      return record && typeof record === "object" ? record : null;
    }

    async function writeCache(files, name) {
      await storage().set({ [CACHE]: {
        version: 1,
        savedAt: new Date().toISOString(),
        folder: name || "",
        files: files,
      } });
    }

    async function clearCache() {
      await storage().set({ [CACHE]: null });
      memoized.config = null; memoized.configKey = "";
      memoized.sites = null; memoized.sitesKey = "";
      inFlight.clear();
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

    /* Identify the three files by their contents, so names do not matter and
     * a key downloaded under a project-specific name still works. */
    function identify(raw) {
      var files = {}, sources = {};
      var keys = Object.keys(raw);
      var named = function (name) { return raw[name]; };
      var viaContent = function (test) {
        for (var i = 0; i < keys.length; i++) if (test(raw[keys[i]])) return keys[i];
        return "";
      };
      var driveName = keys.indexOf(DRIVE) !== -1 ? DRIVE :
        keys.indexOf("google-drive.json") !== -1 ? "google-drive.json" :
        viaContent(function (v) { return typeof v.folder_id === "string" || typeof v.folder_url === "string"; });
      if (driveName) { files[DRIVE] = named(driveName); sources[DRIVE] = driveName; }

      var sitesName = keys.indexOf(SITES) !== -1 ? SITES :
        keys.indexOf("sites.json") !== -1 ? "sites.json" :
        viaContent(function (v) { return Array.isArray(v.allowed_sites); });
      if (sitesName) { files[SITES] = named(sitesName); sources[SITES] = sitesName; }

      var accountName = keys.indexOf(ACCOUNT) !== -1 ? ACCOUNT :
        viaContent(function (v) { return v.type === "service_account"; });
      if (accountName) { files[ACCOUNT] = named(accountName); sources[ACCOUNT] = accountName; }
      return { files: files, sources: sources };
    }

    function validate(found) {
      var drive = found.files[DRIVE], sa = found.files[ACCOUNT], sitesFile = found.files[SITES];
      var id = drive && typeof drive.folder_id === "string" ? drive.folder_id.trim() : "";
      if (!id && drive && typeof drive.folder_url === "string") {
        var match = /\/folders\/([A-Za-z0-9_-]+)/.exec(drive.folder_url);
        id = match ? match[1] : "";
      }
      var validId = /^[A-Za-z0-9_-]+$/.test(id);
      var validSa = sa && sa.type === "service_account" && typeof sa.client_email === "string" && sa.client_email.trim() &&
        typeof sa.private_key === "string" && sa.private_key.indexOf("-----BEGIN PRIVATE KEY-----") !== -1 && sa.private_key.indexOf("-----END PRIVATE KEY-----") !== -1;
      if (!validId) throw new Error("Add a pastex-google-drive.json with a valid folder_id to the folder.");
      if (!validSa) throw new Error("Add a valid service-account.json key to the folder.");
      if (!sitesFile || !Array.isArray(sitesFile.allowed_sites))
        throw new Error(SITES + ": allowed_sites must be an array.");
      sites.parse(sitesFile.allowed_sites); // Invalid lists fail closed everywhere.
      return { folderId: id, client_email: sa.client_email, private_key: sa.private_key, allowedSites: sitesFile.allowed_sites };
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
      return { drive: FOLDER + DRIVE, account: FOLDER + ACCOUNT, sites: FOLDER + SITES };
    }

    /* All later reads come from the cache; the folder is not used again. */
    async function readConfigFrom(force) {
      var record = await readCache();
      if (!record) throw new Error("Choose a configuration folder in Config first.");
      var key = record.savedAt + ":" + record.folder;
      return memo("config", key, !!force, async function () {
        var v = validate({ files: record.files, sources: {} });
        return {
          drive: { folderId: v.folderId },
          driveError: "",
          serviceAccount: { client_email: v.client_email, private_key: v.private_key },
          serviceAccountError: "",
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
        return { allowedSites: v.allowedSites, source: FOLDER + SITES };
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

    /* Status for the panel: tells the user which files are in use. */
    async function describe() {
      var record = await readCache();
      if (!record) return { configured: false, folder: "", savedAt: "" };
      var found = identify(record.files || {});
      return {
        configured: true,
        folder: record.folder || "",
        savedAt: record.savedAt || "",
        missing: [DRIVE, SITES, ACCOUNT].filter(function (name) { return !found.files[name]; }),
      };
    }

    return {
      chooseFolder: chooseFolder,
      clearCache: clearCache,
      describe: describe,
      invalidate: clearCache,
      readCached: async function (force) {
        var config = await readConfigFrom(!!force);
        var allowed = await readSitesFrom(!!force);
        return {
          drive: { folderId: config.drive.folderId },
          serviceAccount: config.serviceAccount,
          allowedSites: allowed.allowedSites,
        };
      },
      readConfig: readConfigFrom,
      readSites: readSitesFrom,
    };
  }
  return { create: create };
});
