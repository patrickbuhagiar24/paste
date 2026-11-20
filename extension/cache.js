/* Browser-session cache of a Drive library, shared by the worker and panel.
 * Credentials are never stored. Chrome's session storage is memory-only and
 * is not exposed to content scripts. Drive remains the source of truth.
 *
 * The tree snapshot is invalidated after every mutation, but verified text
 * bodies are keyed by Drive's modifiedTime and survive those invalidations.
 * That lets forced refreshes and folder expansion reuse unchanged snippets.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.PastexCache = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  var KEY = "pastex.drive.session.v1";

  function create(native, drive, library, storage, cryptoApi) {
    var memory = null;
    var bodies = {};
    var identityCache = { config: null, value: "" };
    var revision = 0;
    var inflight = null;
    var mutations = Promise.resolve();

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

    async function clearSnapshot() {
      memory = null;
      try { if (storage) await storage.remove(KEY); } catch (ignored) { /* memory-only fallback */ }
    }

    async function discardAll() {
      await clearSnapshot();
      bodies = {};
      identityCache = { config: null, value: "" };
    }

    async function invalidate() {
      revision++;
      // Mutation invalidation must not throw away verified text bodies.
      await clearSnapshot();
    }

    async function readStored() {
      if (!memory && storage) {
        try { memory = (await storage.get(KEY))[KEY] || null; } catch (ignored) {}
      }
      bodies = (memory && memory.bodies) || {};
      return memory;
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
      if (!force && saved) return { tree: saved.tree, rootId: saved.rootId, warnings: [], cached: true };
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
        }, { bodies: bodies, deferCollapsed: true });
        mergeBodies(result.bodies);
        if (started !== revision || await identity() !== key) throw new Error("Drive configuration or library changed while loading; refresh again.");
        var response = { tree: result.tree, rootId: result.session.rootId, warnings: result.warnings || [], cached: false };
        // Never replace a complete snapshot with a partial/error listing.
        // Explicit revocation of an item also invalidates the older snapshot.
        if (response.warnings.some(function (warning) { return /HTTP (401|403|404)\b|invalid_grant/i.test(warning); })) await discardAll();
        if (!response.warnings.length) {
          memory = { identity: key, rootId: response.rootId, tree: response.tree, bodies: bodies };
          try { if (storage) await storage.set({ [KEY]: memory }); }
          catch (ignored) {
            // A failed replacement must not leave an older snapshot in session
            // storage for the next worker lifetime.
            try { if (storage) await storage.remove(KEY); } catch (ignoredAgain) {}
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
  return { create: create, KEY: KEY };
});
