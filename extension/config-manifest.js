/* Parse a single configuration manifest whose values are Google Drive file
 * links, then download the linked JSON files on demand. Only links are
 * remembered; no configuration contents or keys are persisted. */
(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PastexManifest = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var NAMES = ["pastex-google-drive.json", "pastex-sites.json", "service-account.json"];
  var MAX_BYTES = 256 * 1024;
  var DOWNLOAD_TIMEOUT_MS = 20000;

  function clean(value) {
    // The sharing links users paste are often Markdown with escaped "_".
    return String(value === undefined || value === null ? "" : value)
      .replace(/\\_/g, "_")
      .replace(/\\-/g, "-")
      .trim();
  }

  function unwrap(value) {
    var text = clean(value);
    var markdown = /^\[[^\]]*\]\(([^)]+)\)$/.exec(text);
    return markdown ? clean(markdown[1]) : text;
  }

  function fileId(value) {
    var raw = unwrap(value);
    if (!raw || /[\s<>]/.test(raw)) return "";
    var url;
    try { url = new URL(raw); } catch (err) { return ""; }
    if (url.protocol !== "https:" || url.hostname.toLowerCase() !== "drive.google.com" ||
        url.username || url.password) return "";
    var match = /^\/file\/d\/([A-Za-z0-9_-]+)(?:\/(?:view|preview|edit))?\/?$/.exec(url.pathname);
    if (match) return match[1];
    if (url.pathname === "/open") {
      var id = url.searchParams.get("id") || "";
      return /^[A-Za-z0-9_-]+$/.test(id) ? id : "";
    }
    return "";
  }

  function downloadUrl(id) {
    return "https://drive.google.com/uc?export=download&id=" + encodeURIComponent(id);
  }

  function parse(text) {
    var data;
    try { data = JSON.parse(String(text === undefined || text === null ? "" : text).replace(/^\uFEFF/, "")); }
    catch (err) { throw new Error("The configuration manifest is not valid JSON."); }
    if (!data || typeof data !== "object" || Array.isArray(data))
      throw new Error("The configuration manifest must be a JSON object.");

    var files = {};
    NAMES.forEach(function (name) {
      if (!Object.prototype.hasOwnProperty.call(data, name))
        throw new Error("The manifest is missing " + name + ".");
      var id = fileId(data[name]);
      if (!id)
        throw new Error(name + " must be an https://drive.google.com/file/d/... link.");
      files[name] = downloadUrl(id);
    });
    return { kind: "manifest", files: files };
  }

  async function fetchJson(url, fetchImpl, timeoutMs) {
    var fetcher = fetchImpl || (typeof fetch !== "undefined" ? fetch : null);
    if (!fetcher) throw new Error("Fetch is unavailable in this context.");

    var controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    var timer;
    var request = fetcher(url, {
      cache: "no-store",
      redirect: "follow",
      signal: controller ? controller.signal : undefined,
      headers: { Accept: "application/json,text/plain;q=0.9,*/*;q=0.8" },
    });
    if (controller && timeoutMs) {
      timer = setTimeout(function () { controller.abort(); }, timeoutMs);
    }
    var response;
    try { response = await request; }
    finally { if (timer) clearTimeout(timer); }

    if (!response.ok)
      throw new Error("Could not download a linked configuration file (HTTP " + response.status + ").");
    var declared = response.headers && typeof response.headers.get === "function" ?
      Number(response.headers.get("content-length")) || 0 : 0;
    if (declared > MAX_BYTES)
      throw new Error("A linked configuration file is too large (maximum 256 KB).");

    var text = await response.text();
    var bytes = typeof TextEncoder !== "undefined" ? new TextEncoder().encode(text).length : text.length;
    if (bytes > MAX_BYTES)
      throw new Error("A linked configuration file is too large (maximum 256 KB).");
    text = text.replace(/^\uFEFF/, "");

    try { return { text: text, json: JSON.parse(text) }; }
    catch (err) {
      throw new Error("A linked configuration file is not valid JSON. Check that each Drive link is downloadable without signing in.");
    }
  }

  return {
    NAMES: NAMES,
    MAX_BYTES: MAX_BYTES,
    DOWNLOAD_TIMEOUT_MS: DOWNLOAD_TIMEOUT_MS,
    clean: clean,
    unwrap: unwrap,
    fileId: fileId,
    downloadUrl: downloadUrl,
    parse: parse,
    fetchJson: fetchJson,
  };
});
