/* Cache of a Drive library shared by the worker and panel.
 *
 * Credentials are never stored. The session snapshot is memory-only; a second,
 * derived snapshot in storage.local makes the first panel open after a browser
 * restart instant. Drive is still the source of truth: a local snapshot is
 * keyed by a SHA-256 identity and silently refreshed when it is stale, while
 * mutations invalidate both copies before and after the write.
 *
 * Verified text bodies are keyed by Drive's modifiedTime and survive tree
 * invalidations, so an explicit refresh does not re-download unchanged files.
 *
 * The background (idle) sync loads Drive once and compares the result with the
 * stored snapshot. The snapshot is only rewritten when Drive really reports a
 * difference; an unchanged library only moves a small freshness stamp.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.PastexCache = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  var KEY = "pastex.drive.session.v1";
  var LKEY = "pastex.drive.local.v1";
  var FKEY = "pastex.drive.fresh.v1";
  var FRESH_MS = 60000;
  var MAX_LOCAL = 5;

  function create(native, drive, library, storage, cryptoApi, persistent) {
    var memory = null;
    var bodies = {};
    var identityCache = { config: null, value: "" };
    var revision = 0;
    var inflight = null;
    var mutations = Promise.resolve();
    var mutating = 0;
    var silentTimer = 0;

    async function identity() {
      var config = await native.readConfig();
      if (!config.serviceAccount || !config.serviceAccount.client_email || !config.serviceAccount.private_key)
        throw new Error(config.serviceAccountError || "Configure a service-account.json link in the configuration manifest.");
      if (!config.drive || !config.drive.folderId)
        throw new Error(config.driveError || "Configure a pastex-google-drive.json link in the configuration manifest.");
      if (identityCache.config === config) return identityCache.value;
      var value = JSON.stringify([config.drive.folderId, config.serviceAccount.client_email, config.serviceAccount.private_key]);
      var digest = await cryptoApi.subtle.digest("SHA-256", new TextEncoder().encode(value));
      identityCache.value = Array.from(new Uint8Array(digest), function (b) { return b.toString(16).padStart(2, "0"); }).join("");
      identityCache.config = config;
      return identityCache.value;
    }

    // The derived snapshots live in storage.local so they survive a browser
    // restart; the session copy is only a fast path inside one session.
    function localStore() { return persistent || storage; }

    async function localAll() {
      var store = localStore();
      if (!store) return {};
      try { return (await store.get(LKEY))[LKEY] || {}; } catch (ignored) { return {}; }
    }

    async function clearSnapshot() {
      memory = null;
      try { if (storage) await storage.remove(KEY); } catch (ignored) { /* memory-only fallback */ }
    }

    async function clearLocal(key) {
      var store = localStore();
      if (!store) return;
      try {
        var all = await localAll();
        if (key) delete all[key]; else all = {};
        await store.set({ [LKEY]: all });
      } catch (ignored) { /* local cache is best-effort */ }
    }

    // A tiny map of identity -> last confirmed Drive check. It is kept apart
    // from the snapshot so an unchanged library never rewrites a big blob.
    async function freshAll() {
      var store = localStore();
      if (!store) return {};
      try { return (await store.get(FKEY))[FKEY] || {}; } catch (ignored) { return {}; }
    }

    async function clearFresh(key) {
      var store = localStore();
      if (!store) return;
      try {
        var all = await freshAll();
        if (key) delete all[key]; else all = {};
        await store.set({ [FKEY]: all });
      } catch (ignored) { /* the stamp is optional */ }
    }

    // The confirmation stamp lives in its own tiny map, so an unchanged
    // library never rewrites the (much larger) snapshot blob. A snapshot
    // written by an older version falls back to its own savedAt value.
    async function freshStamp(key, snapshot) {
      var all = await freshAll();
      if (all[key]) return all[key].savedAt || 0;
      return (snapshot && snapshot.savedAt) || 0;
    }

    async function markFresh(key) {
      var store = localStore();
      if (!store) return;
      try {
        var all = await freshAll();
        all[key] = { savedAt: Date.now() };
        var keys = Object.keys(all).sort(function (a, b) { return (all[b].savedAt || 0) - (all[a].savedAt || 0); });
        keys.slice(MAX_LOCAL).forEach(function (old) { delete all[old]; });
        await store.set({ [FKEY]: all });
      } catch (ignored) { /* the stamp is optional */ }
    }

    async function discardAll() {
      await clearSnapshot();
      await clearLocal();
      await clearFresh();
      bodies = {};
      identityCache = { config: null, value: "" };
    }

    // A write only invalidates the identity that was written to; snapshots of
    // other accounts stay usable.
    async function invalidate() {
      revision++;
      // Keep verified bodies in memory; drop every derived snapshot because a
      // write may have changed the tree or its Drive properties.
      await clearSnapshot();
      var key = identityCache.value;
      await clearLocal(key || undefined);
      await clearFresh(key || undefined);
    }

    async function readStored() {
      if (!memory && storage) {
        try { memory = (await storage.get(KEY))[KEY] || null; } catch (ignored) {}
      }
      if (memory && memory.bodies) bodies = memory.bodies;
      return memory;
    }

    function snapshot(key, rootId, tree, metadata) {
      return { identity: key, rootId: rootId, tree: tree, bodies: bodies,
        metadata: metadata, savedAt: Date.now() };
    }

    async function writeSnapshot(key, rootId, tree, metadata) {
      var snap = snapshot(key, rootId, tree, metadata);
      memory = snap;
      try { if (storage) await storage.set({ [KEY]: snap }); }
      catch (ignored) { try { if (storage) await storage.remove(KEY); } catch (ignoredAgain) {} }
      var store = localStore();
      if (!store) return;
      try {
        var all = await localAll();
        all[key] = snap;
        var keys = Object.keys(all).sort(function (a, b) {
          return (all[b].savedAt || 0) - (all[a].savedAt || 0);
        });
        keys.slice(MAX_LOCAL).forEach(function (old) { delete all[old]; });
        await store.set({ [LKEY]: all });
      } catch (ignored) { /* a full local cache must never block Drive */ }
    }

    function forceRefreshPending(stamp) {
      return Date.now() - (stamp || 0) >= FRESH_MS;
    }

    function scheduleStaleRefresh(stamp, onUpdate) {
      if (forceRefreshPending(stamp) && !inflight && !silentTimer) {
        silentTimer = setTimeout(function () {
          silentTimer = 0;
          if (inflight) return;
          get(true, onUpdate).catch(function () {});
        }, 250);
      }
    }

    function mergeBodies(next) {
      Object.keys(next || {}).forEach(function (id) { bodies[id] = next[id]; });
    }

    // A signature of the fields the user actually sees. Drive returns a few
    // volatile system properties, so comparing raw metadata would report a
    // change on every single check.
    function nodeSignature(node) {
      if (!node || typeof node !== "object") return String(node);
      var props = node.properties || {};
      if (node.kind === "folder") {
        return JSON.stringify([node.id, node.kind, node.name || "", !!node.collapsed,
          props.pastex_order || "", props.pastex_collapsed || "",
          (node.children || []).map(nodeSignature)]);
      }
      return JSON.stringify([node.id, node.kind, node.name || "", node.driveName || "",
        node.modifiedTime || "", node.text || "", !!node.deferred, props.pastex_order || ""]);
    }

    function treeSignature(tree) {
      return JSON.stringify((tree || []).map(nodeSignature));
    }

    function metadataSignature(metadata) {
      var keys = Object.keys(metadata || {}).sort();
      return JSON.stringify(keys.map(function (key) { return key + ":" + metadata[key]; }));
    }

    // Two snapshots describe the same library when their tree and their
    // folder metadata are identical. Bodies are re-verified by modifiedTime
    // during the load, so they are not part of the comparison.
    function sameLibrary(previous, tree, metadata) {
      if (!previous) return false;
      return treeSignature(previous.tree) === treeSignature(tree) &&
        metadataSignature(previous.metadata) === metadataSignature(metadata);
    }

    function sameTree(previous, tree) {
      if (!previous) return false;
      return treeSignature(previous.tree) === treeSignature(tree);
    }

    async function get(force, onUpdate, options) {
      options = options || {};
      await mutations;
      var currentRevision = revision;
      var key;
      try { key = await identity(); }
      catch (err) {
        // The configuration could not be read or is invalid, so the session
        // snapshot cannot be attributed to an identity any more. The keyed
        // local snapshots are kept: they are only served for their own
        // identity, and a transient read failure must not throw them away.
        await clearSnapshot();
        throw err;
      }
      var saved = await readStored();
      if (currentRevision !== revision) return get(force, onUpdate, options);
      if (saved && saved.identity !== key) {
        // Another account or folder: the old snapshot no longer describes
        // anything the user is allowed to see, so it is dropped. Snapshots of
        // other identities stay in the local cache, keyed and unusable for
        // this configuration.
        await clearSnapshot();
        await clearLocal(saved.identity);
        await clearFresh(saved.identity);
        saved = null;
      }
      var stamp = await freshStamp(key, saved);
      if (!force && saved) {
        scheduleStaleRefresh(stamp, onUpdate);
        return { tree: saved.tree, rootId: saved.rootId, warnings: [], cached: true };
      }
      var local = (await localAll())[key];
      if (local && local.identity !== key) { await clearLocal(key); local = null; }
      if (local && (!memory || !memory.tree)) {
        memory = local;
        bodies = local.bodies || {};
      }
      if (!force && local) {
        try { if (storage) await storage.set({ [KEY]: local }); } catch (ignored) {}
        scheduleStaleRefresh(await freshStamp(key, local), onUpdate);
        return { tree: local.tree, rootId: local.rootId, warnings: [], cached: true };
      }
      if (inflight) {
        if (!force && inflight.identity === key) return inflight.promise;
        try { await inflight.promise; } catch (ignored) {}
        return get(force, onUpdate, options);
      }
      var started = revision;
      var flight = { identity: key };
      flight.promise = (async function () {
        var result = await library.load(native, drive, function (tree, session) {
          if (started === revision && onUpdate) onUpdate(tree, session.rootId);
        }, { bodies: bodies, deferCollapsed: true,
          metadata: memory && memory.metadata || {}, tree: memory && memory.tree || [] });
        mergeBodies(result.bodies);
        if (started !== revision || await identity() !== key) throw new Error("Drive configuration or library changed while loading; refresh again.");
        var response = { tree: result.tree, rootId: result.session.rootId, warnings: result.warnings || [], cached: false };
        // Never replace a complete snapshot with a partial/error listing.
        // Explicit revocation of an item also invalidates the older snapshot.
        if (response.warnings.some(function (warning) { return /HTTP (401|403|404)\b|invalid_grant/i.test(warning); })) await discardAll();
        if (!response.warnings.length) {
          var previous = memory && memory.identity === key ? memory : null;
          var treeSame = sameTree(previous, response.tree);
          if (options.onlyIfChanged && sameLibrary(previous, response.tree, result.metadata)) {
            // Nothing changed on Drive: keep the stored snapshot and only move
            // the freshness stamp.
            await markFresh(key);
            response.unchanged = true;
          } else {
            // The tree may be identical while a folder timestamp moved; the
            // snapshot is still refreshed, but the user is not interrupted.
            await writeSnapshot(key, response.rootId, response.tree, result.metadata);
            await markFresh(key);
            response.unchanged = treeSame;
          }
          if (started !== revision) { await clearSnapshot(); throw new Error("Drive library changed while loading; refresh again."); }
        }
        return response;
      })();
      inflight = flight;
      try { return await flight.promise; }
      catch (err) {
        // If Drive explicitly denies access, do not serve the old snapshot on
        // the next opening. A mere offline/timeout failure may keep it.
        if (/HTTP (401|403|404)\b|invalid_grant/i.test(err.message || "")) await discardAll();
        throw err;
      } finally { if (inflight === flight) inflight = null; }
    }

    // Called by the background idle alarm. It never reports progress to the
    // panel and it only rewrites the cache when Drive reports a difference.
    async function sync() {
      if (inflight || silentTimer || mutating > 0) return { changed: false, skipped: true, tree: null, rootId: "" };
      var response = await get(true, null, { onlyIfChanged: true });
      if ((response.warnings || []).length) return { changed: false, skipped: true, tree: null, rootId: "" };
      return { changed: response.unchanged === false, skipped: false,
        tree: response.tree, rootId: response.rootId };
    }

    function mutate(fn) {
      var operation = mutations.then(async function () {
        mutating++;
        try {
          await invalidate();
          try { return await fn(); }
          finally { await invalidate(); }
        } finally { mutating--; }
      });
      mutations = operation.catch(function () {});
      return operation;
    }
    return { get: get, sync: sync, invalidate: invalidate, mutate: mutate };
  }
  return { create: create, KEY: KEY, LKEY: LKEY, FKEY: FKEY, FRESH_MS: FRESH_MS, MAX_LOCAL: MAX_LOCAL };
});
