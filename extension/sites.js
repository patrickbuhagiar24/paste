/* Validate and match the explicit per-user site allowlist. */
(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PastexSites = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // Accept scheme plus host only, with an optional leading subdomain wildcard.
  // Never accept Chrome's global wildcard, paths, ports or credentials.
  function parse(sites) {
    if (!Array.isArray(sites) || sites.length > 500) throw new Error("allowed_sites must be an array of at most 500 origins");
    return sites.map(function (raw) {
      if (typeof raw !== "string") throw new Error("Each allowed site must be a string");
      var origin = raw.trim().toLowerCase();
      var match = /^(https?):\/\/(\*\.)?([a-z0-9-]+(?:\.[a-z0-9-]+)*)\/?$/.exec(origin);
      if (!match || match[3].length > 253 || match[3].split(".").some(function (label) {
        return label.length > 63 || label.startsWith("-") || label.endsWith("-");
      })) throw new Error("Invalid site origin: " + raw);
      var pattern = match[1] + "://" + (match[2] || "") + match[3] + "/*";
      return pattern;
    }).filter(function (pattern, index, all) { return all.indexOf(pattern) === index; });
  }

  function allows(patterns, url) {
    var parsed;
    try { parsed = new URL(url); } catch (err) { return false; }
    return patterns.some(function (pattern) {
      var match = /^(https?):\/\/(\*\.)?([^/]+)\/\*$/.exec(pattern);
      if (!match || parsed.protocol !== match[1] + ":") return false;
      var hostname = parsed.hostname.toLowerCase();
      return hostname === match[3] || (!!match[2] && hostname.endsWith("." + match[3]));
    });
  }
  return { parse: parse, allows: allows };
});
