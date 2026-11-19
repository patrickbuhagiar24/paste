/* Read configuration directly from a folder granted by the user. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.PastexConfigSource = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  var DRIVE = "pastex-google-drive.json", SITES = "pastex-sites.json", ACCOUNT = "service-account.json";
  var accessError = "Configuration folder access is unavailable. Open Config and choose the folder again.";

  function create(store, discovery, sites) {
    async function permission(folder) {
      if (!folder) throw new Error("Choose a configuration folder in Config first.");
      try {
        if (typeof folder.queryPermission === "function" && await folder.queryPermission({ mode: "read" }) !== "granted")
          throw new Error(accessError);
      } catch (err) {
        if (err.name === "NotAllowedError" || err.name === "SecurityError") throw new Error(accessError);
        throw err;
      }
      return folder;
    }
    async function withFolder(folder, fn) {
      try { return await fn(await permission(folder)); }
      catch (err) {
        if (err.name === "NotAllowedError" || err.name === "SecurityError") throw new Error(accessError);
        throw err;
      }
    }
    async function current() { return permission(await store.get()); }
    async function allFiles(folder, skipSites) {
      var files = [];
      for await (var entry of folder.values()) {
        if (entry.kind !== "file" || !/\.json$/i.test(entry.name)) continue;
        if (skipSites && (entry.name === SITES || entry.name === "sites.json")) continue;
        if (files.length >= 205) throw new Error("Too many JSON files; use a smaller configuration folder.");
        var file = await entry.getFile();
        files.push({ name: entry.name, size: file.size, webkitRelativePath: "selected/" + entry.name,
          text: function (f) { return function () { return f.text(); }; }(file) });
      }
      return files;
    }
    async function readConfigFrom(folder) {
      return withFolder(folder, async function (dir) {
        var found = await discovery.discover(await allFiles(dir, true));
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
          driveError: validId ? "" : "Choose a folder with a valid " + DRIVE + ".",
          serviceAccount: validSa ? { client_email: sa.client_email, private_key: sa.private_key } : null,
          serviceAccountError: validSa ? "" : "Choose a folder with a valid " + ACCOUNT + ".",
          sources: found.sources,
        };
      });
    }
    async function readSitesFrom(folder) {
      return withFolder(folder, async function (dir) {
        var fileHandle;
        try { fileHandle = await dir.getFileHandle(SITES); }
        catch (err) {
          if (err.name !== "NotFoundError") throw err;
          try { fileHandle = await dir.getFileHandle("sites.json"); }
          catch (missing) {
            if (missing.name !== "NotFoundError") throw missing;
            throw new Error("Choose a folder containing " + SITES + ".");
          }
        }
        var file = await fileHandle.getFile();
        if (file.size > 256 * 1024) throw new Error(file.name + " is too large (maximum 256 KB).");
        var data;
        try { data = JSON.parse(await file.text()); }
        catch (err) { throw new Error(file.name + " is not valid JSON."); }
        if (!data || !Array.isArray(data.allowed_sites)) throw new Error(file.name + ": allowed_sites must be an array.");
        sites.parse(data.allowed_sites); // Invalid lists fail closed everywhere.
        return { allowedSites: data.allowed_sites, source: file.name };
      });
    }
    async function choose(folder) {
      var drive = await readConfigFrom(folder);
      if (!drive.drive) throw new Error(drive.driveError);
      if (!drive.serviceAccount) throw new Error(drive.serviceAccountError);
      var allowed = await readSitesFrom(folder);
      await store.set(folder);
      // Return filenames only, never the private key or JSON contents.
      return { drive: drive.sources[DRIVE], account: drive.sources[ACCOUNT], sites: allowed.source };
    }
    return {
      choose: choose,
      readConfig: async function () { return readConfigFrom(await current()); },
      readSites: async function () { return readSitesFrom(await current()); },
    };
  }
  return { create: create };
});
