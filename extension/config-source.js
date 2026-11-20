/* Read configuration from a single manifest of Google Drive links.
 * Only the links are persisted; downloaded JSON and keys stay in memory. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.PastexConfigSource = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  var DRIVE = "pastex-google-drive.json", SITES = "pastex-sites.json", ACCOUNT = "service-account.json";
  var LINKED = "linked ";

  function create(store, discovery, sites, manifest, fetchImpl) {
    var fetcher = fetchImpl || (typeof fetch !== "undefined" ? fetch : null);

    function requireManifest(source) {
      if (!source || source.kind !== "manifest" || !source.files || typeof source.files !== "object")
        throw new Error("Choose a configuration manifest in Config first.");
      if (!manifest || typeof manifest.fetchJson !== "function" || !manifest.NAMES)
        throw new Error("Link-based configuration is unavailable in this context.");
      return source;
    }

    async function linkedFile(source, name) {
      var result = await manifest.fetchJson(source.files[name], fetcher, manifest.DOWNLOAD_TIMEOUT_MS);
      return {
        name: name,
        size: result.text.length,
        webkitRelativePath: "linked/" + name,
        json: result.json,
        text: async function () { return result.text; },
      };
    }

    async function linkedFiles(source, skipSites) {
      var files = [];
      for (var i = 0; i < manifest.NAMES.length; i++) {
        var name = manifest.NAMES[i];
        if (skipSites && name === SITES) continue;
        files.push(await linkedFile(source, name));
      }
      return files;
    }

    async function readConfigFrom(source) {
      var active = requireManifest(source);
      var found = await discovery.discover(await linkedFiles(active, true));
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
        driveError: validId ? "" : "Choose a manifest with a valid " + DRIVE + " link.",
        serviceAccount: validSa ? { client_email: sa.client_email, private_key: sa.private_key } : null,
        serviceAccountError: validSa ? "" : "Choose a manifest with a valid " + ACCOUNT + " link.",
        sources: found.sources,
      };
    }

    async function readSitesFrom(source) {
      var active = requireManifest(source);
      var linked = await linkedFile(active, SITES);
      var data = linked.json;
      if (!data || !Array.isArray(data.allowed_sites))
        throw new Error(SITES + ": allowed_sites must be an array.");
      sites.parse(data.allowed_sites); // Invalid lists fail closed everywhere.
      return { allowedSites: data.allowed_sites, source: LINKED + SITES };
    }

    async function chooseManifestText(text) {
      if (!manifest || typeof manifest.parse !== "function")
        throw new Error("Link-based configuration is unavailable in this context.");
      var descriptor = manifest.parse(text);
      var drive = await readConfigFrom(descriptor);
      if (!drive.drive) throw new Error(drive.driveError);
      if (!drive.serviceAccount) throw new Error(drive.serviceAccountError);
      var allowed = await readSitesFrom(descriptor);
      // Persist only the download URLs, never the downloaded JSON or key.
      await store.set(descriptor);
      return { drive: LINKED + DRIVE, account: LINKED + ACCOUNT, sites: allowed.source };
    }

    return {
      chooseManifestText: chooseManifestText,
      readConfig: async function () { return readConfigFrom(await store.get()); },
      readSites: async function () { return readSitesFrom(await store.get()); },
    };
  }
  return { create: create };
});
