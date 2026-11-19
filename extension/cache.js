/* Browser-session cache of a Drive library, shared by the worker and panel.
 * Credentials are never stored. Chrome's session storage is memory-only and
 * is not exposed to content scripts. Drive remains the source of truth.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.PastexCache = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  var KEY = "pastex.drive.session.v1";

  function create(native, drive, library, storage, cryptoApi) {
    var memory = null;
    var revision = 0;
    var inflight = null;
    var mutations = Promise.resolve();

    async function identity() {
      var config = await native.readConfig();
      if (!config.serviceAccount || !config.serviceAccount.client_email || !config.serviceAccount.private_key)
        throw new Error(config.serviceAccountError || "Configure service-account.json in ~/.config/pastex.");
      if (!config.drive || !config.drive.folderId)
        throw new Error(config.driveError || "Configure pastex-google-drive.json in ~/.config/pastex.");
      var value = JSON.stringify([config.drive.folderId, config.serviceAccount.client_email, config.serviceAccount.private_key]);
      var digest = await cryptoApi.subtle.digest("SHA-256", new TextEncoder().encode(value));
      return Array.from(new Uint8Array(digest), function (b) { return b.toString(16).padStart(2, "0"); }).join("");
    }

    async function discard() {
      memory = null;
      try { if (storage) await storage.remove(KEY); } catch (ignored) { /* memory-only fallback */ }
    }

    async function invalidate() {
      revision++;
      await discard();
    }

    async function readStored() {
      if (memory) return memory;
      try { if (storage) memory = (await storage.get(KEY))[KEY] || null; } catch (ignored) {}
      return memory;
    }

    async function get(force, onUpdate) {
      await mutations;
      var currentRevision = revision;
      var key;
      try { key = await identity(); }
      catch (err) { await discard(); throw err; }
      var saved = await readStored();
      if (currentRevision !== revision) return get(force, onUpdate);
      if (saved && saved.identity !== key) { await discard(); saved = null; }
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
        });
        if (started !== revision || await identity() !== key) throw new Error("Drive configuration or library changed while loading; refresh again.");
        var response = { tree: result.tree, rootId: result.session.rootId, warnings: result.warnings || [], cached: false };
        // Never replace a complete snapshot with a partial/error listing.
        // Explicit revocation of an item also invalidates the older snapshot.
        if (response.warnings.some(function (warning) { return /HTTP (401|403|404)\b|invalid_grant/i.test(warning); })) await discard();
        if (!response.warnings.length) {
          memory = { identity: key, rootId: response.rootId, tree: response.tree };
          try { if (storage) await storage.set({ [KEY]: memory }); }
          catch (ignored) {
            // A failed replacement must not leave an older snapshot in session
            // storage for the next worker lifetime.
            try { if (storage) await storage.remove(KEY); } catch (ignoredAgain) {}
          }
          if (started !== revision) { await discard(); throw new Error("Drive library changed while loading; refresh again."); }
        }
        return response;
      })();
      inflight = flight;
      try { return await flight.promise; }
      catch (err) {
        // If Drive explicitly denies access, do not serve the old snapshot on
        // the next opening. A mere offline/timeout failure may keep it.
        if (/HTTP (401|403|404)\b|invalid_grant/i.test(err.message || "")) await discard();
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
