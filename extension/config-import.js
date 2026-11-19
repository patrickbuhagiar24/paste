/* Identify configuration files only within the selected folder itself. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.PastexConfigImport = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  var DRIVE = "pastex-google-drive.json", SITES = "pastex-sites.json", ACCOUNT = "service-account.json";
  var MAX_BYTES = 256 * 1024;
  async function parse(file) {
    if (file.size > MAX_BYTES) throw new Error(file.name + " is too large (maximum 256 KB).");
    try { return JSON.parse(await file.text()); }
    catch (err) { throw new Error(file.name + " is not valid JSON."); }
  }
  async function discover(fileList) {
    var top = Array.from(fileList || []).filter(function (file) {
      var parts = (file.webkitRelativePath || "").split("/");
      return parts.length === 2 && parts[0] && parts[1] === file.name;
    });
    var byName = new Map();
    top.forEach(function (file) {
      if (byName.has(file.name)) throw new Error("Duplicate filename: " + file.name);
      byName.set(file.name, file);
    });
    var files = {}, sources = {};
    async function add(name, source) {
      if (!source) return;
      files[name] = await parse(source);
      sources[name] = source.name;
    }
    await add(DRIVE, byName.get(DRIVE) || byName.get("google-drive.json"));
    await add(SITES, byName.get(SITES) || byName.get("sites.json"));
    var account = byName.get(ACCOUNT);
    if (account) await add(ACCOUNT, account);
    else {
      // Google downloads the key under a project-specific filename. Look at
      // bounded JSON files in the chosen folder, not nested folders or logs.
      var candidates = top.filter(function (file) {
        return /\.json$/i.test(file.name) && file.name !== DRIVE && file.name !== SITES &&
          file.name !== "google-drive.json" && file.name !== "sites.json" && file.size <= MAX_BYTES;
      });
      if (candidates.length > 200) throw new Error("Too many JSON files; put the service-account key in a smaller folder.");
      for (var i = 0; i < candidates.length; i++) {
        var value;
        try { value = await parse(candidates[i]); } catch (ignored) { continue; }
        if (value && value.type === "service_account") {
          if (files[ACCOUNT]) throw new Error("More than one service-account key found; choose a folder with only one.");
          files[ACCOUNT] = value;
          sources[ACCOUNT] = candidates[i].name;
        }
      }
    }
    var missing = [DRIVE, ACCOUNT, SITES].filter(function (name) { return !Object.prototype.hasOwnProperty.call(files, name); });
    return { files: files, sources: sources, missing: missing };
  }
  return { discover: discover };
});
