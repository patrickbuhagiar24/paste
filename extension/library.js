/* Google Drive is the source of truth; session caching is handled separately.
 *
 * Three ideas keep a large library cheap:
 *
 *  - Bodies are cached by content. Every body is stored under the SHA-256 of
 *    its text, so Drive's own content checksum decides whether a snippet is
 *    downloaded again, and unchanged bodies survive a tree rebuild.
 *  - Every node carries a signature built from the metadata Drive reports in
 *    a listing. A node with the same signature is reused as it is, so only
 *    the branch that really changed is listed and downloaded again.
 *  - The order of the siblings and the folded folders live in one label on
 *    the root folder, so a reorder or a collapse is a single Drive write and
 *    a single read.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.PastexLibrary = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var Hash = (typeof PastexHash !== "undefined" && PastexHash)
    ? PastexHash
    : (typeof require === "function" ? require("./hash.js") : null);

  // --- the centralized order and collapse index ---

  // The stored shape is versioned. An unknown version is not decoded field by
  // field: it is discarded, and the library falls back to Drive's own order
  // with every folder open. See DEVELOPMENT.md ("state carries a version").
  //
  // The version field is named "v" because that is what queue.js writes when it
  // builds the label. When the two names disagreed, every write was stored
  // under "version" and every read looked for "v", so each reorder and each
  // fold was thrown away on the next load: one bug, two symptoms.
  var INDEX_VERSION = 2;

  function emptyIndex() {
    return { order: {}, collapsed: {}, v: INDEX_VERSION };
  }

  // The index is a single label on the root folder. It is the only source of
  // order and collapse state: there is no per-item fallback any more.
  function parseIndex(properties) {
    var raw = properties && properties.pastex_index;
    if (typeof raw !== "string" || !raw) return emptyIndex();
    var data;
    try { data = JSON.parse(raw); } catch (ignored) { return emptyIndex(); }
    if (!data || typeof data !== "object" || data.v !== INDEX_VERSION) return emptyIndex();
    var index = emptyIndex();
    if (data.order && typeof data.order === "object") {
      Object.keys(data.order).forEach(function (parent) {
        var list = data.order[parent];
        if (Array.isArray(list)) {
          index.order[parent] = list.filter(function (id) { return typeof id === "string"; });
        }
      });
    }
    if (data.collapsed && typeof data.collapsed === "object") {
      Object.keys(data.collapsed).forEach(function (id) {
        index.collapsed[id] = (data.collapsed[id] === "1" || data.collapsed[id] === true) ? "1" : "0";
      });
    }
    return index;
  }

  // Apply the centralized order for one folder. Rows the index names come
  // first, in the stored order; rows it does not name (new files, files added
  // from Drive) keep Drive's own order after them.
  function orderByIndex(files, index, folderId) {
    var saved = (index && index.order && index.order[folderId]) || [];
    if (!saved.length) return files;
    var position = new Map();
    saved.forEach(function (id, at) { position.set(id, at); });
    var ranked = [], unranked = [];
    files.forEach(function (file) {
      if (position.has(file.id)) ranked.push({ file: file, at: position.get(file.id) });
      else unranked.push(file);
    });
    ranked.sort(function (a, b) { return a.at - b.at; });
    return ranked.map(function (item) { return item.file; }).concat(unranked);
  }

  function cloneIndex(index) {
    var copy = emptyIndex();
    copy.v = INDEX_VERSION;
    Object.keys((index && index.order) || {}).forEach(function (parent) {
      copy.order[parent] = (index.order[parent] || []).slice();
    });
    Object.keys((index && index.collapsed) || {}).forEach(function (id) {
      copy.collapsed[id] = index.collapsed[id];
    });
    return copy;
  }

  // The signature of a node describes everything a listing reports about it:
  // its name, its type, its timestamps, Drive's content checksum and the one
  // label Pastex writes on the root folder. Two listings with the same
  // signature describe the same node.
  function nodeSignature(file) {
    var props = (file && file.properties) || {};
    return JSON.stringify([(file && file.id) || "", (file && file.name) || "",
      (file && file.mimeType) || "", (file && file.modifiedTime) || "",
      (file && file.sha256Checksum) || "", props.pastex_index || ""]);
  }

  async function connect(native, drive) {
    var config = await native.readConfig();
    if (!config.serviceAccount) throw new Error(config.serviceAccountError || "Add a service-account.json to the configuration folder.");
    if (!config.drive || !config.drive.folderId) throw new Error(config.driveError || "Add a pastex-google-drive.json to the configuration folder.");
    var account = drive.parseServiceAccount(JSON.stringify(config.serviceAccount));
    var token = await drive.fetchAccessToken(account);
    var rootId = config.drive.folderId;
    var folder = await drive.getFolder(token, rootId);
    if (!folder || folder.mimeType !== drive.FOLDER_MIME) throw new Error("The configured Drive ID is not a folder.");
    var properties = folder.properties || {};
    return {
      token: token, rootId: rootId, modifiedTime: folder.modifiedTime || "",
      properties: properties, index: parseIndex(properties),
    };
  }

  // Limit network requests, not recursion: folders can be traversed in
  // parallel without deadlocking when all workers encounter subfolders.
  // Cached text bodies are keyed by the SHA-256 of their content; files
  // inside a collapsed folder are left deferred until the folder is opened.
  async function loadTree(drive, token, rootId, onUpdate, options, session) {
    options = options || {};
    session = session || {};
    var savedBodies = options.bodies || {};
    var savedTexts = options.texts || {};
    var savedMeta = options.metadata || {};
    var savedSigs = options.signatures || {};
    var index = options.index || emptyIndex();
    var bodies = {};
    var texts = {};
    var metadata = Object.assign({}, savedMeta);
    var signatures = Object.assign({}, savedSigs);
    var cursors = {};
    var seen = new Set();

    // Collapsed content is deferred by default, so opening the panel does not
    // pay for folders the user has not asked for. The cache passes
    // deferCollapsed: false because it stores the whole library, and the
    // signature walk (signaturesOnly) asks for signatures only, so it never
    // defers a folder it still has to walk.
    var deferCollapsed = options.deferCollapsed !== false;

    // The previous tree is indexed once, so an unchanged folder can be
    // reused without listing its children again.
    var cachedNodes = new Map();
    (function indexCached(nodes) {
      (nodes || []).forEach(function (node) {
        cachedNodes.set(node.id, node);
        if (node.children) indexCached(node.children);
      });
    })(options.tree);

    function has(object, key) {
      return Object.prototype.hasOwnProperty.call(object, key);
    }

    // The body of a node, addressed by its content hash. A snapshot written
    // by an older version keeps the body inline; it is hashed on the way.
    function resolveBody(entry) {
      if (!entry) return null;
      var hash = entry.hash || "";
      if (hash && has(savedTexts, hash)) return { hash: hash, text: savedTexts[hash] };
      if (typeof entry.text === "string") {
        return { hash: hash || Hash.sha256Hex(entry.text), text: entry.text };
      }
      if (hash && has(texts, hash)) return { hash: hash, text: texts[hash] };
      return null;
    }

    // A body is reused when Drive reports the same content checksum, or when
    // the timestamps still match. The checksum is the stronger signal: it
    // survives a rename or another metadata change. A body whose text is
    // missing is downloaded again instead of silently turning into nothing.
    function reusableBody(file) {
      var entry = savedBodies[file.id];
      if (!entry) return null;
      var checksum = file.sha256Checksum || "";
      var same = (checksum && entry.driveHash && entry.driveHash === checksum) ||
        (file.modifiedTime && entry.modifiedTime && file.modifiedTime === entry.modifiedTime);
      if (!same) return null;
      return resolveBody(entry);
    }

    function rememberBody(file, text) {
      var hash = Hash.sha256Hex(text);
      texts[hash] = text;
      var entry = { modifiedTime: file.modifiedTime || "", hash: hash, driveHash: file.sha256Checksum || "" };
      bodies[file.id] = entry;
      return entry;
    }

    // A reused subtree is copied, never shared: the copy can be reordered
    // and folded without touching the snapshot that is still stored.
    function cloneNode(node) {
      seen.add(node.id);
      var copy = {};
      Object.keys(node).forEach(function (key) { copy[key] = node[key]; });
      if (node.kind === "text") {
        var entry = savedBodies[node.id];
        var resolved = entry ? resolveBody(entry) : null;
        if (resolved) {
          bodies[node.id] = {
            modifiedTime: node.modifiedTime || "", hash: resolved.hash,
            driveHash: entry.driveHash || "",
          };
          texts[resolved.hash] = resolved.text;
          copy.hash = resolved.hash;
        } else if (node.hash) {
          texts[node.hash] = node.text || "";
          bodies[node.id] = { modifiedTime: node.modifiedTime || "", hash: node.hash, driveHash: "" };
        }
      }
      if (node.children) copy.children = node.children.map(cloneNode);
      return copy;
    }

    var active = 0, waiting = [];
    function request(fn) {
      return new Promise(function (resolve, reject) {
        function run() {
          active++;
          Promise.resolve().then(fn).then(resolve, reject).finally(function () {
            active--;
            if (waiting.length) waiting.shift()();
          });
        }
        if (active < 6) run(); else waiting.push(run);
      });
    }

    var visited = new Set();
    var tree = [];
    function update() { if (onUpdate) onUpdate(tree); }

    async function visit(folderId, nodes, deferred, reuse) {
      if (reuse) {
        nodes.push(reuse);
        return;
      }
      if (visited.has(folderId)) throw new Error("Drive folder cycle detected.");
      visited.add(folderId);
      var files = await request(function () {
        return drive.listChildren(token, folderId, undefined, {
          onCursor: function (cursor) { cursors[folderId] = cursor || ""; },
        });
      });
      files = orderByIndex(files, index, folderId);
      var jobs = [];
      files.forEach(function (file) {
        var signature = nodeSignature(file);
        var unchanged = savedSigs[file.id] !== undefined && savedSigs[file.id] === signature;
        seen.add(file.id);
        if (file.mimeType === drive.FOLDER_MIME) {
          var collapsed = index.collapsed[file.id] === "1";
          var folder = {
            id: file.id, kind: "folder", name: file.name, children: [], collapsed: collapsed,
            properties: file.properties || {},
          };
          nodes.push(folder);
          metadata[file.id] = file.modifiedTime || "";
          signatures[file.id] = signature;
          var cached = cachedNodes.get(file.id);
          if (unchanged && cached) {
            // The folder did not change: its whole subtree is reused.
            folder.children = (cached.children || []).map(cloneNode);
          } else {
            jobs.push(visit(file.id, folder.children,
              deferred || (deferCollapsed && collapsed) || !!options.signaturesOnly, null)
              .catch(function (err) { folder.loadError = err.message || String(err); })
              .finally(update));
          }
        } else if ((file.mimeType || "").indexOf("application/vnd.google-apps.") !== 0 &&
                   (file.mimeType === drive.TEXT_MIME || /\.txt$/i.test(file.name || ""))) {
          var body = reusableBody(file);
          var node = { id: file.id, kind: "text", name: (file.name || "").replace(/\.txt$/i, ""),
            driveName: file.name, text: body ? body.text : "", modifiedTime: file.modifiedTime || "",
            properties: file.properties || {} };
          if (body) node.hash = body.hash;
          nodes.push(node);
          metadata[file.id] = file.modifiedTime || "";
          signatures[file.id] = signature;
          if (body) {
            var known = savedBodies[file.id] || {};
            bodies[file.id] = {
              modifiedTime: file.modifiedTime || "", hash: body.hash,
              driveHash: file.sha256Checksum || known.driveHash || "",
            };
            texts[body.hash] = body.text;
          } else if (deferred) {
            // Collapsed content is loaded when the folder is opened, unless
            // the caller asks for a complete snapshot.
            node.deferred = true;
          } else {
            node.loadError = "Loading from Google Drive…";
            jobs.push(request(function () { return drive.downloadText(token, file.id); }).then(function (text) {
              node.text = text;
              delete node.loadError;
              var record = rememberBody(file, text);
              node.hash = record.hash;
            }).catch(function (err) { node.loadError = err.message || String(err); }).finally(update));
          }
        }
      });
      update();
      await Promise.all(jobs);
    }

    // The centralized index describes the order of the siblings and the
    // folded folders. It is applied to the finished tree, so a reused
    // subtree is ordered and folded exactly like a freshly listed one.
    function applyIndex(nodes, parentId) {
      var order = index.order || {};
      var collapsed = index.collapsed || {};
      var ranks = order[parentId];
      if (ranks && ranks.length) {
        var positions = {};
        ranks.forEach(function (id, position) { positions[id] = position; });
        nodes.sort(function (a, b) {
          var left = positions[a.id], right = positions[b.id];
          if (left === undefined && right === undefined) return 0;
          if (left === undefined) return 1;
          if (right === undefined) return -1;
          return left - right;
        });
      }
      nodes.forEach(function (node) {
        if (node.kind !== "folder") return;
        var state = collapsed[node.id];
        if (state !== undefined) node.collapsed = state === "1";
        applyIndex(node.children || [], node.id);
      });
    }

    // Always list the root: child metadata changes (order/collapse) may not
    // bump the parent's modifiedTime. Nested folders can still be reused.
    await visit(rootId, tree, false, null);
    applyIndex(tree, rootId);
    Object.keys(signatures).forEach(function (id) { if (!seen.has(id)) delete signatures[id]; });
    metadata[rootId] = session.modifiedTime || metadata[rootId] || "";
    update();
    return {
      tree: tree, bodies: bodies, texts: texts, metadata: metadata,
      signatures: signatures, index: index, cursors: cursors,
    };
  }

  // Fill the bodies a stored snapshot is missing, without listing the
  // library again: a text node without a hash keeps its row and gets its
  // body from Drive. The caller decides when to run it (idle) and stores the
  // result through the normal snapshot path.
  async function fillMissing(native, drive, tree, options) {
    options = options || {};
    var session = await connect(native, drive);
    var bodies = options.bodies || {};
    var texts = options.texts || {};
    // A row with a hash already points at a verified body. A row without one
    // keeps text that was never content-addressed, so it is hashed first: if
    // the snapshot already stores that text, nothing is downloaded.
    var missing = [];
    (function walk(rows) {
      (rows || []).forEach(function (node) {
        if (node.kind === "folder") { walk(node.children); return; }
        // A deferred row is only waiting for its folder to be opened; a row
        // with a loadError failed and is retried here, which is how a body
        // that could not be read comes back on a later fill or sync.
        if (node.deferred) return;
        // Only the "still loading" placeholder is treated as in flight.
        if (node.loadError && node.loadError === "Loading from Google Drive…") return;
        if (typeof node.hash === "string" && node.hash) return;
        var text = typeof node.text === "string" ? node.text : "";
        // An empty row is a row without a body, not an empty snippet: it is
        // downloaded rather than hashed into the empty string.
        var hash = text ? Hash.sha256Hex(text) : "";
        if (text && texts[hash] !== undefined) {
          node.hash = hash;
        } else {
          missing.push({ node: node, hash: hash, text: text });
        }
      });
    })(tree);
    if (!missing.length) return { tree: tree, bodies: bodies, texts: texts, filled: 0 };
    var filled = 0;
    await Promise.all(missing.map(function (item) {
      return drive.downloadText(session.token, item.node.id).then(function (text) {
        var hash = Hash.sha256Hex(text);
        item.node.text = text;
        item.node.hash = hash;
        // The row is readable again, so the warning it used to show is gone.
        delete item.node.loadError;
        texts[hash] = text;
        bodies[item.node.id] = { modifiedTime: item.node.modifiedTime || "", hash: hash, driveHash: "" };
        filled++;
      }).catch(function (err) { item.node.loadError = err.message || String(err); });
    }));
    return { tree: tree, bodies: bodies, texts: texts, filled: filled, session: session };
  }

  // The signature walk used for a cheap freshness check: it needs a session
  // (one token and one folder read) but lists only what changed and never
  // downloads a body.
  async function verifyTree(drive, session, options) {
    return loadTree(drive, session.token, session.rootId, null, Object.assign({}, options || {}, {
      signaturesOnly: true, index: session.index,
    }), session);
  }

  async function load(native, drive, onUpdate, options) {
    var session = await connect(native, drive);
    // The index of the root folder is the freshest one: it is read with the
    // folder itself, so no extra request is needed.
    var merged = Object.assign({}, options || {}, { index: session.index });
    var result = await loadTree(drive, session.token, session.rootId, function (partial) {
      if (onUpdate) onUpdate(partial, session.rootId);
    }, merged, session);
    var tree = result.tree;
    var warnings = [];
    function collect(nodes) {
      nodes.forEach(function (node) {
        if (node.loadError) warnings.push(node.name + ": " + node.loadError);
        if (node.children) collect(node.children);
      });
    }
    collect(tree);
    return { tree: tree, session: session, warnings: warnings,
      bodies: result.bodies, texts: result.texts, metadata: result.metadata,
      signatures: result.signatures, index: result.index, cursors: result.cursors };
  }

  return { connect: connect, load: load, verifyTree: verifyTree, fillMissing: fillMissing,
    cloneIndex: cloneIndex };
});
