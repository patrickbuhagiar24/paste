/* Pure helpers for the in-memory Google Drive library; no local storage. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.PastexStore = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  function walk(nodes, fn, trail) {
    trail = trail || [];
    (nodes || []).forEach(function (node) {
      fn(node, trail);
      if (node.kind === "folder") walk(node.children || [], fn, trail.concat(node.name));
    });
  }
  function flatten(nodes) {
    var out = [];
    walk(nodes, function (node) { if (node.kind === "text") out.push(node); });
    return out;
  }
  function findById(nodes, id) {
    var hit = null;
    walk(nodes, function (node) { if (node.id === id) hit = node; });
    return hit;
  }
  function search(nodes, query) {
    var q = (query || "").trim().toLowerCase();
    var texts = flatten(nodes);
    if (!q) return texts;
    var names = [], contents = [];
    texts.forEach(function (node) {
      if ((node.name || "").toLowerCase().indexOf(q) !== -1) names.push(node);
      else if ((node.text || "").toLowerCase().indexOf(q) !== -1) contents.push(node);
    });
    return names.concat(contents);
  }
  return { walk: walk, flatten: flatten, findById: findById, search: search };
});
