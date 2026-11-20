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
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.PastexCache = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  var KEY = "pastex.drive.session.v1";
  var LKEY = "pastex.drive.local.v1";
  var FRESH_MS = 60000;
  var MAX_LOCAL = 5;

  function create(native, drive, library, storage, cryptoApi, persistent) {
    var memory = null;
    var bodies = {};
    var identityCache = { config: null, value: "" };
    var revision = 0;
    var inflight = null;
    var mutations = Promise.resolve();
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

    async function localAll() {
      if (!persistent) return {};
      try { return (await persistent.get(LKEY))[LKEY] || {}; } catch (ignored) { return {}; }
    }

    async function clearSnapshot() {
      memory = null;
      try { if (storage) await storage.remove(KEY); } catch (ignored) { /* memory-only fallback */ }
    }

    async function clearLocal(key) {
      if (!persistent) return;
      try {
        var all = await localAll();
        if (key) delete all[key]; else all = {};
        await persistent.set({ [LKEY]: all });
      } catch (ignored) { /* local cache is best-effort */ }
    }

    async function discardAll() {
      await clearSnapshot();
      await clearLocal();
      bodies = {};
      identityCache = { config: null, value: "" };
    }

    async function invalidate() {
      revision++;
      // Keep verified bodies in memory; drop every derived snapshot because a
      // write may have changed the tree or its Drive properties.
      await clearSnapshot();
      await clearLocal();
    }

    async function readStored() {
      if (!memory && storage) {
        try { memory = (await storage.get(KEY))[KEY] || null; } catch (ignored) {}
      }
      bodies = (memory && memory.bodies) || {};
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
      if (!persistent) return;
      try {
        var all = await localAll();
        all[key] = snap;
        var keys = Object.keys(all).sort(function (a, b) {
          return (all[b].savedAt || 0) - (all[a].savedAt || 0);
        });
        keys.slice(MAX_LOCAL).forEach(function (old) { delete all[old]; });
        await persistent.set({ [LKEY]: all });
      } catch (ignored) { /* a full local cache must never block Drive */ }
    }

    function scheduleStaleRefresh(snap, key, onUpdate) {
      if (forceRefreshPending(snap) && !inflight && !silentTimer) {
        silentTimer = setTimeout(function () {
          silentTimer = 0;
          if (inflight) return;
          get(true, onUpdate).catch(function () {});
        }, 250);
      }
    }

    function forceRefreshPending(snap) {
      return !!snap && Date.now() - (snap.savedAt || 0) >= FRESH_MS;
    }

    function mergeBodies(next) {
      Object.keys(next || {}).forEach(function (id) { bodies[id] = next[id]; });
    }

    async function get(force, onUpdate) {
      await mutations;
      var currentRevision = revision;
      var key;
      try { key = await identity(); }
      catch (err) { await discardAll(); throw err; }
      var saved = await readStored();
      if (currentRevision !== revision) return get(force, onUpdate);
      if (saved && saved.identity !== key) { await discardAll(); saved = null; }
      if (!force && saved) {
        scheduleStaleRefresh(saved, key, onUpdate);
        return { tree: saved.tree, rootId: saved.rootId, warnings: [], cached: true };
      }
      if (!force) {
        var local = (await localAll())[key];
        if (local && local.identity === key) {
          memory = local;
          bodies = local.bodies || {};
          try { if (storage) await storage.set({ [KEY]: local }); } catch (ignored) {}
          scheduleStaleRefresh(local, key, onUpdate);
          return { tree: local.tree, rootId: local.rootId, warnings: [], cached: true };
        }
        if (local) await clearLocal(key);
      }
      if (inflight) {
        if (!force && inflight.identity === key) return inflight.promise;
        try { await inflight.promise; } catch (ignored) {}
        return get(force, onUpdate);
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
          await writeSnapshot(key, response.rootId, response.tree, result.metadata);
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

    function mutate(fn) {
      var operation = mutations.then(async function () {
        await invalidate();
        try { return await fn(); }
        finally { await invalidate(); }
      });
      mutations = operation.catch(function () {});
      return operation;
    }
    return { get: get, invalidate: invalidate, mutate: mutate };
  }
  return { create: create, KEY: KEY, LKEY: LKEY, FRESH_MS: FRESH_MS, MAX_LOCAL: MAX_LOCAL };
});
