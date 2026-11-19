/* Remember only a browser-granted directory handle, never file contents. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.PastexHandleStore = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  var DB = "pastex-source-folder", STORE = "handles", KEY = "configuration";
  function open() {
    return new Promise(function (resolve, reject) {
      var request = indexedDB.open(DB, 1);
      request.onupgradeneeded = function () { request.result.createObjectStore(STORE); };
      request.onsuccess = function () { resolve(request.result); };
      request.onerror = function () { reject(request.error || new Error("Could not open folder storage.")); };
      request.onblocked = function () { reject(new Error("Folder storage is blocked.")); };
    });
  }
  async function transact(mode, action) {
    var db = await open();
    return new Promise(function (resolve, reject) {
      var transaction = db.transaction(STORE, mode);
      var request = action(transaction.objectStore(STORE));
      transaction.oncomplete = function () { db.close(); resolve(request.result); };
      transaction.onerror = function () { db.close(); reject(transaction.error || request.error || new Error("Could not access folder storage.")); };
      transaction.onabort = function () { db.close(); reject(transaction.error || new Error("Folder storage was interrupted.")); };
    });
  }
  function get() { return transact("readonly", function (store) { return store.get(KEY); }); }
  function set(handle) { return transact("readwrite", function (store) { return store.put(handle, KEY); }); }
  return { get: get, set: set };
});
