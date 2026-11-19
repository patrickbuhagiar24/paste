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
  // Move each selected block one position among its siblings, never across
  // folders. Return only groups that actually changed.
  function reorderGroups(nodes, selected, direction) {
    var groups = [];
    function visit(siblings) {
      var ordered = siblings.slice();
      if (direction < 0) {
        for (var i = 1; i < ordered.length; i++) {
          if (selected.has(ordered[i].id) && !selected.has(ordered[i - 1].id)) {
            var before = ordered[i - 1]; ordered[i - 1] = ordered[i]; ordered[i] = before;
          }
        }
      } else {
        for (var j = ordered.length - 2; j >= 0; j--) {
          if (selected.has(ordered[j].id) && !selected.has(ordered[j + 1].id)) {
            var after = ordered[j + 1]; ordered[j + 1] = ordered[j]; ordered[j] = after;
          }
        }
      }
      if (ordered.some(function (node, index) { return node !== siblings[index]; }))
        groups.push(ordered.map(function (node) { return node.id; }));
      siblings.forEach(function (node) { if (node.kind === "folder") visit(node.children || []); });
    }
    visit(nodes || []);
    return groups;
  }

  function selectedRoots(nodes, selected) {
    var result = [];
    function visit(siblings) {
      (siblings || []).forEach(function (node) {
        if (selected.has(node.id)) result.push(node);
        else if (node.kind === "folder") visit(node.children);
      });
    }
    visit(nodes);
    return result;
  }
  return { walk: walk, flatten: flatten, findById: findById, search: search,
    reorderGroups: reorderGroups, selectedRoots: selectedRoots };
});
