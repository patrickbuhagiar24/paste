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

  // Build once per tree update: search and id lookups then avoid repeated
  // recursive walks and repeated lowercasing of every snippet body.
  function createIndex(nodes) {
    var byId = new Map(), texts = [], folders = [], lowered = [];
    walk(nodes, function (node) {
      byId.set(node.id, node);
      if (node.kind === "folder") {
        folders.push(node);
      } else {
        texts.push(node);
        lowered.push({
          node: node,
          name: String(node.name || "").toLowerCase(),
          text: String(node.text || "").toLowerCase(),
        });
      }
    });
    return {
      byId: byId,
      texts: texts,
      folders: folders,
      search: function (query) {
        var q = String(query || "").trim().toLowerCase();
        if (!q) return texts;
        var names = [], contents = [];
        lowered.forEach(function (entry) {
          if (entry.name.indexOf(q) !== -1) names.push(entry.node);
          else if (entry.text.indexOf(q) !== -1) contents.push(entry.node);
        });
        return names.concat(contents);
      },
    };
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
  return { walk: walk, createIndex: createIndex, reorderGroups: reorderGroups,
    selectedRoots: selectedRoots };
});
