/* Cache of a Drive library shared by the worker and panel.
 *
 * Credentials are never stored. The session snapshot is memory-only; a second,
 * derived snapshot in storage.local makes the first panel open after a browser
 * restart instant. Drive is still the source of truth: a local snapshot is
 * keyed by a SHA-256 identity and silently refreshed when it is stale, while
 * mutations invalidate both copies before and after the write.
 *
 * Reading never touches Drive. The stored snapshot is the whole library
 * (folders and every body), so an open panel is served from memory or
 * storage.local and never waits for the network: the only full sync happens
 * when the configuration is first loaded, and later changes come from the
 * background checks while the user is away.
 *
 * storage.local is bounded, so the write degrades in stages instead of failing
 * silently: the complete snapshot of this library first, then the same
 * snapshot with each body kept once, then only the newest snapshot. The panel
 * counts the rows that still need a body so they can be filled while the
 * machine is idle. A library that does not fit at all falls back to the
 * worker's session memory, as before.
 *
 * Verified text bodies are addressed by the SHA-256 of their content and
 * survive tree invalidations, so an explicit refresh does not re-download
 * unchanged files. Every node also keeps the signature of the metadata Drive
 * reported for it, so a refresh only rebuilds the branch that changed, and a
 * cached snapshot can be confirmed with metadata alone (verify).
 *
 * The background (idle) check loads Drive once and compares the result with the
 * stored snapshot. The snapshot is only rewritten when Drive really reports a
 * difference; an unchanged library only moves a small freshness stamp.
 *
 * An edit made while the user is on an allowed site is applied to the cached
 * snapshot at once (mutateLocal); the worker keeps the matching Drive write in
 * the queue (extension/queue.js) and sends it when the tab is left. A row
 * created that way carries a local id until that write lands, and a second
 * edit on it is queued behind the first one.
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
    var texts = {};
    var signatures = {};
    var index = null;
    var cursors = {};
    var identityCache = { config: null, value: "" };
    var revision = 0;
    var inflight = null;
    var mutations = Promise.resolve();
    var mutating = 0;
    var silentTimer = 0;
    var fullSyncStale = true;

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
      var stamp = Date.now();
      if (!store) return stamp;
      try {
        var all = await freshAll();
        all[key] = { savedAt: stamp };
        pruneEntries(all);
        await store.set({ [FKEY]: all });
      } catch (ignored) { /* the stamp is optional */ }
      return stamp;
    }

    // Keep the newest MAX_LOCAL entries of a keyed store and drop the rest.
    // Both stores are bounded the same way, so the rule lives in one place.
    function pruneEntries(all) {
      Object.keys(all).sort(function (a, b) {
        return ((all[b] && all[b].savedAt) || 0) - ((all[a] && all[a].savedAt) || 0);
      }).slice(MAX_LOCAL).forEach(function (old) { delete all[old]; });
    }

    async function discardAll() {
      await clearSnapshot();
      await clearLocal();
      await clearFresh();
      bodies = {};
      texts = {};
      signatures = {};
      index = null;
      cursors = {};
      identityCache = { config: null, value: "" };
    }

    // A write only invalidates the identity that was written to; snapshots of
    // other accounts stay usable.
    async function invalidate() {
      revision++;
      // A write changed Drive, so the derived snapshot is no longer the
      // library: the next read syncs once and caches the result again.
      fullSyncStale = true;
      // Keep verified bodies in memory; drop every derived snapshot because a
      // write may have changed the tree or its Drive properties.
      await clearSnapshot();
      var key = identityCache.value;
      await clearLocal(key || undefined);
      await clearFresh(key || undefined);
    }

    // Write an already edited snapshot back. The caller (mutateLocal) owns the
    // edit; this only makes it durable in the same staged way as any write.
    async function persistTree(key, snap) {
      memory = snap;
      try { if (storage) await storage.set({ [KEY]: snap }); } catch (ignored) { /* memory keeps it */ }
      await writeLocal(key, snap);
    }

    async function readStored() {
      if (!memory && storage) {
        try { memory = (await storage.get(KEY))[KEY] || null; } catch (ignored) {}
      }
      if (!memory) {
        // The derived snapshot is the source of truth when the session copy was
        // dropped (a worker restart): editing it in a copy would be lost.
        var key;
        try { key = await identity(); } catch (ignored) { key = ""; }
        var stored = key ? (await localAll())[key] : null;
        if (stored && stored.tree) memory = stored;
      }
      if (memory && memory.bodies) bodies = memory.bodies;
      if (memory && memory.texts) texts = memory.texts;
      if (memory && memory.signatures) signatures = memory.signatures;
      if (memory && memory.index) index = memory.index;
      if (memory && memory.cursors) cursors = memory.cursors;
      return memory;
    }

    function snapshot(key, rootId, tree, metadata) {
      return { identity: key, rootId: rootId, tree: tree, bodies: bodies,
        texts: texts, signatures: signatures, index: index, cursors: cursors,
        metadata: metadata, savedAt: Date.now() };
    }

    // The local quota is shared by every snapshot and by whatever else the
    // extension stores, so a write may not fit. Three candidates are tried in
    // order: the complete snapshot, then the same snapshot with the bodies'
    // text kept only once, then only the newest snapshot. A complete snapshot
    // is never replaced by a degraded one, so a small library cannot be
    // knocked out of the cache by a large one.
    async function writeLocal(key, snap) {
      var store = localStore();
      if (!store) return;
      try {
        var all = await localAll();
        all[key] = snap;
        pruneEntries(all);
        await store.set({ [LKEY]: all });
      } catch (ignored) {
        // The candidate with the fewest bytes that still describes the library
        // the same way.
        try { await store.set({ [LKEY]: { [key]: compactSnapshot(snap) } }); }
        catch (ignoredAgain) { try { await store.set({ [LKEY]: { [key]: snap } }); } catch (ignoredThird) { /* session memory only */ } }
      }
    }

    // The inline text of a text node duplicates texts[hash]; the loader reads
    // a missing node text back from texts through the body entry. Nested
    // folders are stripped too, since most of the library lives there.
    function compactSnapshot(snap) {
      var copy = Object.assign({}, snap);
      copy.tree = (snap.tree || []).map(strip);
      function strip(node) {
        var clone = Object.assign({}, node);
        if (clone.kind === "text" && clone.hash) delete clone.text;
        if (clone.children) clone.children = clone.children.map(strip);
        return clone;
      }
      return copy;
    }

    async function writeSnapshot(key, rootId, tree, metadata) {
      // This is a full read of Drive: nothing else needs to be fetched until
      // a write or a new configuration makes it stale again.
      fullSyncStale = false;
      var snap = snapshot(key, rootId, tree, metadata);
      memory = snap;
      try { if (storage) await storage.set({ [KEY]: snap }); }
      catch (ignored) { try { if (storage) await storage.remove(KEY); } catch (ignoredAgain) {} }
      await writeLocal(key, snap);
    }

    // The rows of a tree, in order, for the helpers below.
    function eachNode(tree, visit) {
      (function walk(nodes) {
        (nodes || []).forEach(function (node) {
          visit(node);
          if (node.kind === "folder") walk(node.children);
        });
      })(tree || []);
    }

    // A row is served by the cache when it points at a body the snapshot
    // stores: a hash, or inline text that can be hashed on the spot.
    function resolvedText(node, storedTexts) {
      if (typeof node.text === "string" && node.text) return node.text;
      if (typeof node.hash === "string" && node.hash && storedTexts && storedTexts[node.hash] !== undefined) {
        return storedTexts[node.hash];
      }
      return "";
    }

    function needsBody(node, storedTexts) {
      if (!node || node.kind !== "text" || node.loadError || node.deferred) return false;
      return !resolvedText(node, storedTexts);
    }

    // Count the rows a snapshot does not serve yet, so the panel can say so.
    function pendingCount(tree, storedTexts) {
      var count = 0;
      eachNode(tree, function (node) { if (needsBody(node, storedTexts)) count++; });
      return count;
    }

    // Called while the machine is idle. It fills the rows a stored snapshot
    // could not download, without listing Drive again, and rewrites the
    // snapshot so the next open is complete. Only the panel's cache is
    // touched; the Drive library itself is never written here.
    async function fill() {
      var key;
      try { key = await identity(); } catch (ignored) { return { filled: 0, skipped: true, pending: 0 }; }
      var saved = await readStored();
      if (!saved || saved.identity !== key || !saved.tree) return { filled: 0, skipped: true, pending: 0 };
      var missing = [];
      eachNode(saved.tree, function (node) { if (needsBody(node, texts)) missing.push(node); });
      if (!missing.length) return { filled: 0, skipped: false, pending: 0 };
      var result = await library.fillMissing(native, drive, saved.tree, { bodies: bodies, texts: texts });
      bodies = result.bodies;
      texts = result.texts;
      if (result.filled > 0) {
        mergeResult({ bodies: bodies, texts: texts });
        await writeSnapshot(key, saved.rootId, saved.tree, saved.metadata);
      }
      return { filled: result.filled, skipped: false, pending: pendingCount(saved.tree, texts) };
    }

    // Reads no longer schedule a refresh: a stale snapshot is corrected by the
    // background check, which runs while the user is away, or by the explicit
    // Refresh button (force), which is a full sync.

    // The bodies, the node signatures and the centralized index of a load
    // replace the stored ones. A body is addressed by the SHA-256 of its
    // content, so the same text is stored once.
    function mergeResult(result) {
      Object.keys((result && result.bodies) || {}).forEach(function (id) { bodies[id] = result.bodies[id]; });
      Object.keys((result && result.texts) || {}).forEach(function (hash) { texts[hash] = result.texts[hash]; });
      if (result && result.signatures) signatures = Object.assign({}, signatures, result.signatures);
      if (result && result.index) index = result.index;
      if (result && result.cursors) cursors = result.cursors;
    }

    // A signature of the fields the user actually sees. Drive returns a few
    // volatile system properties, so comparing raw metadata would report a
    // change on every single check. A body is addressed by its content
    // hash, so a long snippet does not have to be read again to compare it.
    function nodeSignature(node) {
      if (!node || typeof node !== "object") return String(node);
      var props = node.properties || {};
      if (node.kind === "folder") {
        return JSON.stringify([node.id, node.kind, node.name || "", !!node.collapsed,
          props.pastex_order || "", props.pastex_collapsed || "",
          (node.children || []).map(nodeSignature)]);
      }
      return JSON.stringify([node.id, node.kind, node.name || "", node.driveName || "",
        node.modifiedTime || "", node.hash || node.text || "", !!node.deferred, props.pastex_order || ""]);
    }

    function treeSignature(tree) {
      return JSON.stringify((tree || []).map(nodeSignature));
    }

    function metadataSignature(metadata) {
      var keys = Object.keys(metadata || {}).sort();
      return JSON.stringify(keys.map(function (key) { return key + ":" + metadata[key]; }));
    }

    // The signature map of the nodes: one line per item, so a large library
    // is compared without touching the bodies again.
    function signaturesSignature(signatures) {
      var keys = Object.keys(signatures || {}).sort();
      return JSON.stringify(keys.map(function (key) { return key + ":" + signatures[key]; }));
    }

    function indexSignature(index) {
      if (!index) return "";
      var order = index.order || {}, collapsed = index.collapsed || {};
      var parents = Object.keys(order).sort();
      var folders = Object.keys(collapsed).sort();
      return JSON.stringify({
        order: parents.map(function (parent) { return parent + ":" + (order[parent] || []).join(","); }),
        collapsed: folders.map(function (folder) { return folder + ":" + collapsed[folder]; }),
      });
    }

    // Two snapshots describe the same library when their tree, their folder
    // metadata, their node signatures and their centralized index are
    // identical. Bodies are re-verified by content hash during the load, so
    // they are not part of the comparison.
    function sameLibrary(previous, tree, metadata, signatures, index) {
      if (!previous) return false;
      if (treeSignature(previous.tree) !== treeSignature(tree)) return false;
      if (metadataSignature(previous.metadata) !== metadataSignature(metadata)) return false;
      if (signatures !== undefined && signaturesSignature(previous.signatures) !== signaturesSignature(signatures)) return false;
      if (index !== undefined && indexSignature(previous.index) !== indexSignature(index)) return false;
      return true;
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
      // The Sync button asks for a real read. Without this, "force" would be
      // accepted and ignored, and the button would do nothing visible.
      // An explicit request reads Drive again, tree and bodies alike: the fill
      // pass retries rows that failed before, so one button fixes both.
      if (force) return loadFromDrive(key, onUpdate, options);
      if (saved) {
        var storedTexts = saved.texts || texts || {};
        return { tree: saved.tree, rootId: saved.rootId, warnings: [], cached: true,
          lastCheckedAt: stamp, pending: pendingCount(saved.tree, storedTexts) };
      }
      var local = (await localAll())[key];
      if (local && local.identity !== key) { await clearLocal(key); local = null; }
      if (local && (!memory || !memory.tree)) {
        memory = local;
        bodies = local.bodies || {};
        texts = local.texts || {};
        signatures = local.signatures || {};
        index = local.index || null;
      }
      if (local) {
        // The derived snapshot becomes the session copy, so the next read of
        // this worker is served without touching local storage again.
        try { if (storage) await storage.set({ [KEY]: local }); } catch (ignored) { /* the local copy is still served */ }
        var localStamp = await freshStamp(key, local);
        return { tree: local.tree, rootId: local.rootId, warnings: [], cached: true,
          lastCheckedAt: localStamp, pending: pendingCount(local.tree, texts) };
      }
      // A configuration was just read and nothing has been stored for it yet;
      // only then is a full sync of the library part of loading it.
      var firstLoad = fullSyncStale;
      var response = await loadFromDrive(key, onUpdate, options);
      if (firstLoad) fullSyncStale = false;
      return response;
    }

    // The one full read of Drive, used when the configuration is first loaded
    // and by the background checks. An ordinary open never reaches it.
    async function loadFromDrive(key, onUpdate, options) {
      options = options || {};
      if (inflight) {
        if (inflight.identity === key) return inflight.promise;
        try { await inflight.promise; } catch (ignored) {}
        return loadFromDrive(key, onUpdate, options);
      }
      var started = revision;
      var flight = { identity: key };
      flight.promise = (async function () {
        var previous = memory && memory.identity === key ? memory : null;
        var result = await library.load(native, drive, function (tree, session) {
          if (started === revision && onUpdate) onUpdate(tree, session.rootId);
        }, { bodies: bodies, texts: texts, deferCollapsed: false, index: index,
          signatures: signatures,
          metadata: memory && memory.metadata || {}, tree: memory && memory.tree || [] });
        mergeResult(result);
        if (started !== revision || await identity() !== key) throw new Error("Drive configuration or library changed while loading; refresh again.");
        var response = { tree: result.tree, rootId: result.session.rootId, warnings: result.warnings || [],
          cached: false, lastCheckedAt: Date.now(), pending: pendingCount(result.tree, texts) };
        // Never replace a complete snapshot with a partial/error listing.
        // Explicit revocation of an item also invalidates the older snapshot.
        if (response.warnings.some(function (warning) { return /HTTP (401|403|404)\b|invalid_grant/i.test(warning); })) await discardAll();
        if (!response.warnings.length) {
          var treeSame = sameTree(previous, response.tree);
          if (options.onlyIfChanged && sameLibrary(previous, response.tree, result.metadata, result.signatures, result.index)) {
            // Nothing changed on Drive: keep the stored snapshot and only move
            // the freshness stamp.
            response.lastCheckedAt = await markFresh(key);
            response.unchanged = true;
          } else {
            // The tree may be identical while a folder timestamp moved; the
            // snapshot is still refreshed, but the user is not interrupted.
            await writeSnapshot(key, response.rootId, response.tree, result.metadata);
            response.lastCheckedAt = await markFresh(key);
            response.unchanged = treeSame;
          }
          if (started !== revision) { await clearSnapshot(); throw new Error("Drive library changed while loading; refresh again."); }
        }
        // The panel shows when the library was last confirmed with Drive.
        response.lastCheckedAt = response.lastCheckedAt || Date.now();
        return response;
      })();
      inflight = flight;
      try { return await flight.promise; }
      catch (err) {
        // If Drive explicitly denies access, do not serve the old snapshot on
        // the next opening. A mere offline/timeout failure may keep it.
        if (/HTTP (401|403|404)\b|invalid_grant/i.test(err.message || "")) await discardAll();
        throw err;
      } finally {
        if (inflight === flight) inflight = null;
        // A failed first sync is retried on the next configuration read
        // rather than on every open.
        if (options.firstLoad) fullSyncStale = true;
      }
    }

    // Called by the background idle alarm. It never reports progress to the
    // panel and it only rewrites the cache when Drive reports a difference.
    async function sync() {
      // A write in flight wins: it is about to invalidate the snapshot, so a
      // read taken now would be stale before it landed. The flush calls this
      // after its last write finished, which is why it gets a real answer.
      if (inflight || silentTimer || mutating > 0) return { changed: false, skipped: true, tree: null, rootId: "", lastCheckedAt: 0 };
      var key;
      try { key = await identity(); } catch (err) { throw err; }
      var response = await loadFromDrive(key, null, { onlyIfChanged: true });
      if ((response.warnings || []).length) return { changed: false, skipped: true, tree: null, rootId: "", lastCheckedAt: 0 };
      return { changed: response.unchanged === false, skipped: false,
        tree: response.tree, rootId: response.rootId,
        lastCheckedAt: response.lastCheckedAt || Date.now(),
        pending: response.pending || 0 };
    }

    // A metadata-only check of a cached snapshot: it lists the folders and
    // compares signatures without downloading one body, reports how many rows
    // would change, and only then asks for the full listing. A library that
    // did not change therefore costs a fraction of a full load.
    async function verify() {
      var idle = { checked: false, changed: false, skipped: true, lastCheckedAt: 0, pending: 0 };
      if (inflight || silentTimer || mutating > 0) return idle;
      var key;
      try { key = await identity(); } catch (ignored) { return idle; }
      var saved = await readStored();
      if (!saved || saved.identity !== key || !saved.tree || !saved.signatures) {
        return { checked: false, changed: false, skipped: true, lastCheckedAt: 0,
          pending: pendingCount(saved && saved.tree, (saved && saved.texts) || texts) };
      }
      var started = revision;
      var session;
      try {
        session = await library.connect(native, drive);
        var listings = await library.verifyTree(drive, session, {
          signatures: saved.signatures, metadata: saved.metadata || {}, tree: saved.tree,
          bodies: saved.bodies || {}, texts: saved.texts || {}, index: saved.index,
        });
        if (started !== revision) return idle;
        var changed = signaturesSignature(listings.signatures) !== signaturesSignature(saved.signatures) ||
          indexSignature(listings.index) !== indexSignature(saved.index);
        if (changed) {
          // The panel should reload from Drive: same shape as sync().
          var full = await loadFromDrive(key, null, { onlyIfChanged: true });
          return { checked: true, changed: full.unchanged === false, skipped: false,
            lastCheckedAt: full.lastCheckedAt || Date.now(), pending: full.pending || 0 };
        }
      } catch (err) {
        // A folder read failure during the check is not a change; the next
        // idle check tries again.
        if (/HTTP (401|403|404)\b|invalid_grant/i.test(err.message || "")) throw err;
        return idle;
      }
      // Nothing changed: the freshness stamp moves and any body the snapshot
      // is still missing is filled now that the tree is known to be stable.
      var stamp = await markFresh(key);
      var filled = await fill();
      return { checked: true, changed: false, skipped: false, lastCheckedAt: stamp,
        filled: filled.filled || 0, pending: filled.pending || 0 };
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
    // The user picked a configuration manifest: the next read is a full sync.
    function configurationChanged() { fullSyncStale = true; }

    // A read-only view of the cached snapshot, for a queued index write.
    async function current() {
      // The in-memory copy is cleared by an invalidation, and a caller that
      // reads just before taking the write lock would see nothing. Read the
      // persisted snapshot directly, which is what the panel is showing.
      var saved = null;
      var key;
      try { key = await identity(); } catch (ignored) { key = ""; }
      if (key) { try { saved = (await localAll())[key] || null; } catch (ignored) { saved = null; } }
      if (!saved || !saved.tree) {
        if (memory && memory.tree) saved = memory;
        else { try { saved = (await storage.get(KEY))[KEY] || null; } catch (ignored) { saved = null; } }
      }
      // A copy, not the stored object: a write clears the snapshot in place,
      // so a caller holding the stored tree would see it emptied underneath.
      return saved && saved.tree ? { tree: JSON.parse(JSON.stringify(saved.tree)), rootId: saved.rootId } : null;
    }

    // --- edits made while the user is on a site ----------------------------
    //
    // The change is applied to the cached snapshot at once, so every read
    // serves it, and the Drive write waits in the queue. Nothing here talks to
    // Drive: that is flush()'s job, when the tab is left.
    async function mutateLocal(apply) {
      // The same lock as a Drive write: a full load that is still running
      // would otherwise overwrite the edit with the listing it read before it.
      var operation = mutations.then(async function () {
        mutating++;
        try {
          var key;
          try { key = await identity(); } catch (ignored) { key = ""; }
          var saved = await readStored();
          if (!saved || !saved.tree || (key && saved.identity !== key)) {
            return { applied: [], pending: 0, skipped: true };
          }
          var applied = await apply(saved);
          saved.savedAt = Date.now();
          if (key) await persistTree(key, saved);
          return { applied: applied || [], pending: pendingCount(saved.tree, texts), skipped: false };
        } finally { mutating--; }
      });
      mutations = operation.catch(function () {});
      return operation;
    }

    // Rename a just-created row to its Drive id, inside a write that already
    // holds the library lock. The snapshot is in memory there, so this does
    // not take the lock again (which would deadlock) and does not race the
    // invalidation that follows the write.
    // A queued write failed permanently (the row was deleted on another
    // machine, the folder moved): drop it from the cache so the next read does
    // not offer a row Drive does not have.
    async function dropLocal(command) {
      if (!command || !command.item || !command.item.localId) return 0;
      var saved = await readStored();
      if (!saved || !saved.tree) return 0;
      var localId = command.item.localId;
      var removed = 0;
      (function prune(nodes) {
        (nodes || []).forEach(function (node, at) {
          if (node.id === localId) { nodes.splice(at, 1); removed++; return; }
          if (node.kind === "folder") prune(node.children);
        });
      })(saved.tree);
      if (saved.bodies) delete saved.bodies[localId];
      if (removed) {
        saved.savedAt = Date.now();
        var key;
        try { key = await identity(); } catch (ignored) { key = ""; }
        if (key) await persistTree(key, saved);
      }
      return removed;
    }

    return { get: get, sync: sync, verify: verify, fill: fill,
      pending: pendingCount, invalidate: invalidate, mutate: mutate,
      configurationChanged: configurationChanged, mutateLocal: mutateLocal,
      dropLocal: dropLocal, current: current };
  }
  return { create: create, KEY: KEY, LKEY: LKEY, FKEY: FKEY };
});
