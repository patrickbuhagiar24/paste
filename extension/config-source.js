/* Read configuration from one file the user picked once, then keep the parsed
 * JSON in persistent storage so later reads never open a file again.
 *
 * Why: only the side panel has a user gesture and a File System Access handle.
 * A handle cannot be used from an extension document other than the page that
 * holds it ("The request is not allowed by the user agent or the platform in the
 * current context"), so the panel reads the file once, stores its contents, and
 * everything afterwards reads that cache. The file is a source of configuration,
 * never its storage.
 *
 * The user picks the file they mean in the file picker. There is no folder scan
 * and no search for "the files Pastex needs": one pick is one file. The name of
 * that file does not matter — any `.json` document will do — because what makes a
 * file a configuration is the keys it contains, all of which are checked here.
 * The one filename that does not work is an empty one: the panel reports the
 * picked name so the user can tell which file it is using. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.PastexConfigSource = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  // One file: three links, the client id and the client secret. A link is what
  // Drive's own Share button gives you, so the file can be written without
  // knowing which id belongs to what. The file name is the user's choice; these
  // keys are not: a file is a configuration because it carries them, whatever it
  // is called.
  var CONFIG = "The configuration file";
  var LINK_FOLDER = "pastex-folder", LINK_SITES = "pastex-sites", LINK_INDEX = "pastex-index";
  var CLIENT = "client_id";
  var SECRET = "client_secret";
  var FILE = "file ", CACHE = "pastex-config-cache";
  // The cached configuration shape is versioned. A record with another version
  // is treated as absent, which asks the user to pick the file again instead of
  // decoding an unknown shape. See README.md, Development policy.
  var CACHE_VERSION = 5;
  var MAX_BYTES = 256 * 1024;

  // Copying a configuration out of a chat window, or saving it from a phone
  // editor, can put characters into it that no one can see: a zero-width space
  // from a web page, a non-breaking space where an editor auto-indents. JSON
  // refuses them and the file looks perfect, which is why the message used to
  // say only "not valid JSON". They are removed rather than reported, because
  // none of them can ever be part of a key or a link.
  var INVISIBLE = /[\u200B-\u200D\u2060\uFEFF]/g;

  function cleaned(text) {
    return String(text || "")
      // A byte-order mark is legal before the object, but not inside it, and a
      // copy/paste can put one there.
      .replace(INVISIBLE, "")
      .replace(/\u00A0/g, " ");
  }

  // The first thing JSON.parse objects to, in the user's terms. Google's own
  // message ("Unexpected token ...") names a character the file cannot show.
  function explainJson(err, text) {
    var message = String((err && err.message) || "");
    var at = /position (\d+)/.exec(message);
    var where = at ? Number(at[1]) : -1;
    if (where >= 0) {
      var before = text.slice(0, where);
      var line = before.split("\n").length;
      var column = where - before.lastIndexOf("\n");
      return "line " + line + ", column " + column;
    }
    var unexpected = /Unexpected token '(.)'/i.exec(message);
    if (unexpected) return "unexpected " + JSON.stringify(unexpected[1]);
    return "";
  }

  function create(sites, cacheStore) {
    var memoized = { config: null, configKey: "", sites: null, sitesKey: "" };
    var inFlight = new Map();

    function storage() {
      if (!cacheStore || typeof cacheStore.get !== "function" || typeof cacheStore.set !== "function")
        throw new Error("Configuration storage is unavailable.");
      return cacheStore;
    }

    async function readCache() {
      // chrome.storage.get(key) resolves to an OBJECT keyed by the key, so the
      // record has to be unwrapped. Reading it as the record itself makes
      // `value` undefined on every later open.
      var result = await storage().get(CACHE);
      var record = result && typeof result === "object" ? result[CACHE] : null;
      if (!record || typeof record !== "object" || record.version !== CACHE_VERSION) return null;
      return record;
    }

    async function writeCache(value, name) {
      await storage().set({ [CACHE]: {
        version: CACHE_VERSION,
        savedAt: new Date().toISOString(),
        file: name || "",
        value: value,
      } });
    }

    /* Read the one file the user picked. There is no name check: the picker
     * filter already narrows the choice to JSON, and the keys are what decide
     * whether the file is a configuration. The name is only remembered so the
     * panel can say which file it is using. */
    async function readPickedFile(handle) {
      if (!handle || typeof handle.getFile !== "function")
        throw new Error("Choose your configuration file in Config first.");
      var name = handle.name || "";
      var file;
      try { file = await handle.getFile(); }
      catch (err) { throw new Error("That file could not be read. Choose it again."); }
      var text;
      try { text = await file.text(); }
      catch (err) { throw new Error("That file could not be read. Choose it again."); }
      name = file.name || name;
      if (text.length > MAX_BYTES) throw new Error(name + " is larger than " + (MAX_BYTES / 1024) + " KiB.");
      var clean = cleaned(text);
      var value;
      try { value = JSON.parse(clean); }
      catch (err) {
        var at = explainJson(err, clean);
        throw new Error(name + " is not valid JSON" + (at ? " (" + at + ")" : "") +
          ". Open it in a plain text editor: a trailing comma, a missing quote or a curly quote " +
          "from a word processor are the usual causes.");
      }
      if (!value || typeof value !== "object" || Array.isArray(value))
        throw new Error(name + " must contain a JSON object, starting with { and ending with }.");
      return { name: name, value: value };
    }

    /* A Drive link, reduced to the id the API needs. Only the shapes Drive's
     * Share button and address bar produce are accepted: a bare id, a
     * /folders/<id> link, a /file/d/<id>/view link, or an ?id=<id> link. Anything
     * else is a typo the user can see, not something to guess at. */
    function idFromLink(value) {
      var text = typeof value === "string" ? value.trim() : "";
      if (!text) return "";
      if (/^[A-Za-z0-9_-]{10,}$/.test(text)) return text;
      var folder = /\/folders\/([A-Za-z0-9_-]{10,})/.exec(text);
      if (folder) return folder[1];
      var file = /\/file\/d\/([A-Za-z0-9_-]{10,})/.exec(text);
      if (file) return file[1];
      var query = /[?&]id=([A-Za-z0-9_-]{10,})/.exec(text);
      if (query) return query[1];
      return "";
    }

    /* Check the keys and report all the problems at once. A user who has to
     * pick the file again after each single error is being told one third of
     * what is wrong, so every problem is collected, not thrown one by one. The
     * message names the key, never a filename, because any name is allowed. */
    function problems(storage) {
      var list = [];
      if (!storage || typeof storage !== "object") {
        list.push(CONFIG + " holds no keys");
        return { list: list, value: null };
      }

      var folderId = idFromLink(storage[LINK_FOLDER]);
      var indexId = idFromLink(storage[LINK_INDEX]);
      if (!folderId) list.push(CONFIG + " needs a " + LINK_FOLDER + " link to the library folder");
      if (!indexId) list.push(CONFIG + " needs a " + LINK_INDEX + " link to the order/collapse file");

      // The OAuth client of this installation lives in the same file: one file
      // describes the whole setup, and none of it is a secret.
      var clientId = typeof storage[CLIENT] === "string" ? storage[CLIENT].trim() : "";
      var validClient = /^[0-9]+-[A-Za-z0-9_-]+\.apps\.googleusercontent\.com$/.test(clientId);
      if (!clientId) list.push(CONFIG + " needs the " + CLIENT + " of a Google OAuth client");
      else if (!validClient) list.push(CONFIG + " needs the " + CLIENT + " of a Google OAuth client (type Web application)");

      // The secret is optional, because Google creates clients without one, but
      // a "Web application" client (what Personal mode onboarding creates) needs
      // it for the code exchange. A value that is clearly not a secret is
      // reported rather than sent to Google to be refused.
      var clientSecret = typeof storage[SECRET] === "string" ? storage[SECRET].trim() : "";
      var validSecret = !clientSecret || /^[A-Za-z0-9_-]{10,}$/.test(clientSecret);
      if (clientSecret && !validSecret) {
        list.push(CONFIG + ": the " + SECRET + " does not look like one (it is one long value from the console)");
      }

      // sites may also be an inline list, so a single file can describe the whole
      // installation; a link to a shared list is treated exactly like the id.
      var sitesValue = storage[LINK_SITES];
      var sitesId = idFromLink(sitesValue);
      var inline = Array.isArray(sitesValue) ? sitesValue : null;
      if (!sitesId && !inline) {
        list.push(CONFIG + " needs a " + LINK_SITES + " link to the site list or an inline array");
      } else if (inline) {
        try { sites.parse(inline); }
        catch (err) { list.push(CONFIG + ": " + (err.message || err)); }
      }

      return {
        list: list,
        value: folderId && indexId && validClient && validSecret && (sitesId || inline) ?
          { folderId: folderId, indexId: indexId, clientId: clientId, clientSecret: clientSecret,
            sitesId: sitesId, allowedSites: inline } : null,
      };
    }

    function validate(storage) {
      var checked = problems(storage);
      if (checked.list.length) throw new Error(checked.list.join("; ") + ".");
      // sites.parse() already ran inside problems(), so the list is known good.
      return checked.value;
    }

    /* Called by the panel after the user picks the file. Reads it, validates
     * it, and only then replaces the cache: a broken file never wipes a working
     * configuration. */
    async function chooseFile(handle) {
      var picked = await readPickedFile(handle);
      validate(picked.value);
      await writeCache(picked.value, picked.name);
      memoized.config = null; memoized.configKey = "";
      memoized.sites = null; memoized.sitesKey = "";
      return { storage: FILE + (picked.name || "configuration") };
    }

    /* All later reads come from the cache; the file is never opened again. */
    async function readConfigFrom(force) {
      var record = await readCache();
      if (!record) throw new Error("Choose your configuration file in Config first.");
      var key = record.savedAt + ":" + record.file;
      return memo("config", key, !!force, async function () {
        var v = validate(record.value);
        return {
          drive: { folderId: v.folderId, indexId: v.indexId },
          driveError: "",
          oauth: { client_id: v.clientId, client_secret: v.clientSecret },
          oauthError: "",
          // The site list is either a link to fetch or an array already read
          // from the one configuration file.
          sites: { fileId: v.sitesId || "", allowedSites: v.allowedSites || null },
          sources: {},
        };
      });
    }

    async function readSitesFrom(force) {
      var record = await readCache();
      if (!record) throw new Error("Choose your configuration file in Config first.");
      var key = record.savedAt + ":" + record.file;
      return memo("sites", key, !!force, async function () {
        var v = validate(record.value);
        if (v.allowedSites) return { allowedSites: v.allowedSites, fileId: "", source: FILE + (record.file || "configuration") };
        return { allowedSites: null, fileId: v.sitesId, source: FILE + (record.file || "configuration") };
      });
    }

    function memo(kind, key, force, load) {
      if (!force && memoized[kind] && memoized[kind + "Key"] === key) return memoized[kind];
      var slot = kind + ":" + key;
      if (!force && inFlight.has(slot)) return inFlight.get(slot);
      var promise = (async function () {
        var value = await load();
        memoized[kind] = value;
        memoized[kind + "Key"] = key;
        return value;
      })();
      if (!force) inFlight.set(slot, promise);
      try { return promise; }
      finally { inFlight.get(slot) === promise && inFlight.delete(slot); }
    }

    return {
      chooseFile: chooseFile,
      readConfig: readConfigFrom,
      readSites: readSitesFrom,
    };
  }
  return { create: create };
});
