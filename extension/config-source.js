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
  var DRIVE = "pastex-google-drive.json", SITES = "pastex-sites.json", ACCOUNT = "service-account.json";
  var FOLDER = "folder ", CACHE = "pastex-config-cache";
  // The cached configuration shape is versioned. A record with another version
  // is treated as absent, which asks the user to pick the folder again instead
  // of decoding an unknown shape. See DEVELOPMENT.md.
  var CACHE_VERSION = 2;
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

    /* Identify the three files. The Drive and sites files must carry their
     * canonical names: a rename is not supported, it is a configuration the
     * user fixes once (DEVELOPMENT.md: no compatibility layer for names).
     * The service-account key is the exception, because Google downloads it
     * under a project-specific name, so it is recognised by its content. */
    function identify(raw) {
      var files = {}, sources = {};
      if (Object.prototype.hasOwnProperty.call(raw, DRIVE)) {
        files[DRIVE] = raw[DRIVE];
        sources[DRIVE] = DRIVE;
      }
      if (Object.prototype.hasOwnProperty.call(raw, SITES)) {
        files[SITES] = raw[SITES];
        sources[SITES] = SITES;
      }
      if (Object.prototype.hasOwnProperty.call(raw, ACCOUNT)) {
        files[ACCOUNT] = raw[ACCOUNT];
        sources[ACCOUNT] = ACCOUNT;
      } else {
        var keys = Object.keys(raw).filter(function (name) {
          return name !== DRIVE && name !== SITES && /\.json$/i.test(name);
        });
        for (var i = 0; i < keys.length; i++) {
          var value = raw[keys[i]];
          if (value && value.type === "service_account") {
            files[ACCOUNT] = value;
            sources[ACCOUNT] = keys[i];
            break;
          }
        }
      }
      return { files: files, sources: sources };
    }

    /* Check every file and report all the problems at once. A user who has to
     * pick the folder again after each single error is being told one third of
     * what is wrong, so missing and malformed files are collected, not thrown
     * one by one. */
    function problems(found) {
      var files = found.files || {};
      var drive = files[DRIVE], sa = files[ACCOUNT], sitesFile = files[SITES];
      var list = [];

      var id = drive && typeof drive.folder_id === "string" ? drive.folder_id.trim() : "";
      if (!id && drive && typeof drive.folder_url === "string") {
        var match = /\/folders\/([A-Za-z0-9_-]+)/.exec(drive.folder_url);
        id = match ? match[1] : "";
      }
      var validId = /^[A-Za-z0-9_-]+$/.test(id);
      if (!drive) list.push(DRIVE + " is missing");
      else if (!validId) list.push(DRIVE + " has no usable folder_id or folder_url");

      var validSa = sa && sa.type === "service_account" &&
        typeof sa.client_email === "string" && sa.client_email.trim() &&
        typeof sa.private_key === "string" &&
        sa.private_key.indexOf("-----BEGIN PRIVATE KEY-----") !== -1 &&
        sa.private_key.indexOf("-----END PRIVATE KEY-----") !== -1;
      if (!sa) list.push(ACCOUNT + " is missing");
      else if (!validSa) list.push(ACCOUNT + " is not a usable service-account key");

      if (!sitesFile) list.push(SITES + " is missing");
      else if (!Array.isArray(sitesFile.allowed_sites)) list.push(SITES + " needs an allowed_sites array");
      else {
        try { sites.parse(sitesFile.allowed_sites); }
        catch (err) { list.push(SITES + ": " + (err.message || err)); }
      }

      return {
        list: list,
        value: validId && validSa && sitesFile && Array.isArray(sitesFile.allowed_sites) ?
          { folderId: id, client_email: sa.client_email, private_key: sa.private_key, allowedSites: sitesFile.allowed_sites } : null,
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

    return {
      chooseFolder: chooseFolder,
      readConfig: readConfigFrom,
      readSites: readSitesFrom,
    };
  }
  return { create: create };
});
