/* Review the first pending text edit without discarding either version. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.PastexConflict = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  function create(queue, config, library, drive) {
    async function firstEdit() {
      var entries = await queue.all();
      var entry = entries[0];
      if (!entry || entry.kind !== "edit" || !entry.item || entry.item.kind !== "text" ||
          !entry.item.id || entry.item.isNew || typeof entry.text !== "string")
        throw new Error("The first pending change is not an existing snippet edit.");
      return entry;
    }
    async function current(entry) {
      var session = await library.connect(config, drive);
      return drive.downloadText(session.token, entry.item.id);
    }
    async function preview() {
      var entry = await firstEdit();
      return { id: entry.id, name: entry.name || "Snippet", local: entry.text,
        base: typeof entry.baseText === "string" ? entry.baseText : null,
        remote: await current(entry) };
    }
    async function resolve(review, useDrive) {
      if (!review || typeof review.id !== "string" || typeof review.local !== "string" ||
          typeof review.remote !== "string" || !(review.base === null || typeof review.base === "string"))
        throw new Error("Review the versions again.");
      var entry = await firstEdit();
      if (entry.id !== review.id || entry.text !== review.local ||
          (entry.baseText === undefined ? null : entry.baseText) !== review.base)
        throw new Error("Pending changes changed. Review them again.");
      if (await current(entry) !== review.remote)
        throw new Error("Drive changed again. Review the versions again.");
      await queue.rebaseFirst(review.id, review.local, review.base, review.remote, !!useDrive);
    }
    return { preview: preview,
      keepLocal: function (review) { return resolve(review, false); },
      keepDrive: function (review) { return resolve(review, true); } };
  }
  return { create: create };
});
