/* Read configuration from a folder the user picked once, discovering the
 * three JSON files by content rather than by a manifest of Drive links.
 * Only the directory handle is persisted (IndexedDB); the downloaded JSON and
 * the service-account key stay in memory.
 * Files are memoized per folder so repeated opens, mutations and allowlist
 * checks do not re-read the same three JSON files. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.PastexConfigSource = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  var DRIVE = "pastex-google-drive.json", SITES = "pastex-sites.json", ACCOUNT = "service-account.json";
  var FOLDER = "folder ";

  function create(store, discovery, sites, manifest) {
    var cache = { config: null, configKey: "", sites: null, sitesKey: "" };
    var inFlight = new Map();

    function requireFolder(source) {
      if (!source || source.kind !== "folder" || !source.handle || typeof source.handle.values !== "function")
        throw new Error("Choose a configuration folder in Config first.");
      return source;
    }

    function keyOf(source) {
      return JSON.stringify([source.kind, source.name || "", source.signature || ""]);
    }

    async function memo(kind, key, force, load) {
      if (!force && cache[kind] && cache[kind + "Key"] === key) return cache[kind];
      var slot = kind + ":" + key;
      if (!force && inFlight.has(slot)) return inFlight.get(slot);
      var promise = (async function () {
        var value = await load();
        cache[kind] = value;
        cache[kind + "Key"] = key;
        return value;
      })();
      if (!force) inFlight.set(slot, promise);
      try { return await promise; }
      finally { if (inFlight.get(slot) === promise) inFlight.delete(slot); }
    }

    /* Read the folder once and hand the JSON files to the discovery step,
     * which identifies them by content: a service-account key carries
     * "type": "service_account", the others carry folder_id/folder_url and
     * allowed_sites. Nested folders and non-JSON files are ignored. */
    async function folderFiles(source) {
      var handle = source.handle;
      var top = [];
      for await (var entry of handle.values()) {
        if (entry.kind !== "file") continue;
        if (!/\.json$/i.test(entry.name || "")) continue;
        // `let` keeps one binding per iteration: `text()` must close over this
        // file, not over whichever file the loop ended on.
        let file;
        try { file = await entry.getFile(); } catch (err) { continue; }
        top.push({
          name: file.name || entry.name,
          size: file.size,
          webkitRelativePath: (source.name || "configuration") + "/" + (file.name || entry.name),
          text: function () { return file.text(); },
        });
      }
      if (!top.length) throw new Error("No .json files found in the selected folder.");
      var found = await discovery.discover(top);
      return found;
    }

    async function readConfigFrom(source, force) {
      var active = requireFolder(source);
      return memo("config", keyOf(active), !!force, async function () {
        var found = await folderFiles(active);
        var drive = found.files[DRIVE], sa = found.files[ACCOUNT];
        var id = drive && typeof drive.folder_id === "string" ? drive.folder_id.trim() : "";
        if (!id && drive && typeof drive.folder_url === "string") {
          var match = /\/folders\/([A-Za-z0-9_-]+)/.exec(drive.folder_url);
          id = match ? match[1] : "";
        }
        var validId = /^[A-Za-z0-9_-]+$/.test(id);
        var validSa = sa && sa.type === "service_account" && typeof sa.client_email === "string" && sa.client_email.trim() &&
          typeof sa.private_key === "string" && sa.private_key.indexOf("-----BEGIN PRIVATE KEY-----") !== -1 && sa.private_key.indexOf("-----END PRIVATE KEY-----") !== -1;
        return {
          drive: validId ? { folderId: id } : null,
          driveError: validId ? "" : "The selected folder needs a " + DRIVE + " with a valid folder_id.",
          serviceAccount: validSa ? { client_email: sa.client_email, private_key: sa.private_key } : null,
          serviceAccountError: validSa ? "" : "The selected folder needs a valid " + ACCOUNT + " key.",
          sources: found.sources,
        };
      });
    }

    async function readSitesFrom(source, force) {
      var active = requireFolder(source);
      return memo("sites", keyOf(active), !!force, async function () {
        var found = await folderFiles(active);
        var data = found.files[SITES];

        if (!data || !Array.isArray(data.allowed_sites))
          throw new Error(SITES + ": allowed_sites must be an array.");
        sites.parse(data.allowed_sites); // Invalid lists fail closed everywhere.
        return { allowedSites: data.allowed_sites, source: FOLDER + SITES };
      });
    }

    /* Accept a folder handle chosen by the user, verify it, and only then
     * persist it. A broken folder never replaces a working configuration. */
    async function chooseFolder(handle) {
      if (!handle || typeof handle.values !== "function")
        throw new Error("Choose a configuration folder in Config first.");
      var descriptor = { kind: "folder", handle: handle, name: handle.name || "" };
      var results = await Promise.all([
        readConfigFrom(descriptor, true),
        readSitesFrom(descriptor, true),
      ]);
      var drive = results[0], allowed = results[1];
      if (!drive.drive) throw new Error(drive.driveError);
      if (!drive.serviceAccount) throw new Error(drive.serviceAccountError);
      // Persist only the handle, never the file contents or the key.
      await store.set(descriptor);
      return { drive: FOLDER + DRIVE, account: FOLDER + ACCOUNT, sites: allowed.source };
    }

    function invalidate() {
      cache.config = null; cache.configKey = "";
      cache.sites = null; cache.sitesKey = "";
      inFlight.clear();
    }

    return {
      chooseFolder: chooseFolder,
      invalidate: invalidate,
      readConfig: async function (force) { return readConfigFrom(await store.get(), force); },
      readSites: async function (force) { return readSitesFrom(await store.get(), force); },
    };
  }
  return { create: create };
});
