"use strict";
/* The folder is a SOURCE of configuration, never its storage: it is scanned
 * once when the user picks it, the parsed JSON is cached, and every later read
 * comes from the cache. Only the surface of the folder is scanned. */
const assert = require("node:assert/strict");
const Sites = require("../sites.js");
const { create } = require("../config-source.js");

const CLIENT = "123456789012-abcdefghijklmnop.apps.googleusercontent.com";
const FOLDER_LINK = "https://drive.google.com/drive/folders/rootfolder";
const INDEX_LINK = "https://drive.google.com/file/d/indexfileid/view?usp=sharing";

function storageFile(overrides) {
  return Object.assign({
    "pastex-folder": FOLDER_LINK,
    "pastex-sites": ["https://example.org"],
    "pastex-index": INDEX_LINK,
    client_id: CLIENT,
  }, overrides || {});
}
function fileEntry(name, contents) {
  return { kind: "file", name: name, getFile: async () => ({ name: name, size: contents.length, text: async () => contents }) };
}
function dirHandle(name, files, folders) {
  const entries = Object.keys(files).map((n) => fileEntry(n, files[n])).concat(folders || []);
  return { kind: "directory", name: name, values: async function* () { for (const e of entries) yield e; } };
}
function goodFiles() {
  return { "pastex-storage.json": JSON.stringify(storageFile()) };
}
function memoryStore(initial) {
  let data = initial || {};
  return {
    get: async (key) => (key in data ? { [key]: data[key] } : {}),
    set: async (values) => { data = Object.assign({}, data, values); },
    peek: () => data,
  };
}
function harness(files, folders) {
  const store = memoryStore();
  // The cache writer and the reader must be the SAME store.
  return { source: create(Sites, store), store: store, handle: dirHandle("config", files, folders) };
}

test("choosing a folder scans it once and stores the parsed file, not the handle", async () => {
  const store = memoryStore();
  const source = create(Sites, store);
  const info = await source.chooseFolder(dirHandle("config", goodFiles()));
  assert.deepEqual(info, { storage: "folder pastex-storage.json" });
  const saved = store.peek()["pastex-config-cache"];
  assert.equal(saved.folder, "config");
  assert.deepEqual(Object.keys(saved.files), ["pastex-storage.json"]);
  // The links are stored as written; they are reduced to ids on every read, so
  // the stored shape stays exactly what the user typed.
  assert.equal(saved.files["pastex-storage.json"]["pastex-folder"], FOLDER_LINK);
  assert.ok(!JSON.stringify(saved).includes("getFile"), "a handle must never be stored");
});

test("the three links are reduced to ids, and one file describes everything", async () => {
  const store = memoryStore();
  const source = create(Sites, store);
  await source.chooseFolder(dirHandle("config", goodFiles()));
  const config = await source.readConfig();
  assert.deepEqual(config.drive, { folderId: "rootfolder", indexId: "indexfileid" });
  assert.equal(config.oauth.client_id, CLIENT);
  assert.deepEqual(config.sites, { fileId: "", allowedSites: ["https://example.org"] });
  assert.deepEqual((await source.readSites()).allowedSites, ["https://example.org"]);
});

test("every later read comes from the cache and never touches the folder again", async () => {
  const store = memoryStore();
  const source = create(Sites, store);
  let opens = 0;
  const handle = dirHandle("config", goodFiles());
  // Build a NEW handle that counts iterations. Reassigning `.values` on the
  // original loses sibling properties such as `kind` and silently makes the
  // handle invalid, which would make this test vacuous.
  const counting = { kind: "directory", name: handle.name };
  counting.values = async function* () {
    opens++;
    for await (const entry of handle.values()) yield entry;
  };

  await source.chooseFolder(counting);
  assert.equal(opens, 1);

  const config = await source.readConfig();
  const sites = await source.readSites();
  await source.readConfig(true);
  await source.readSites(true);
  assert.equal(opens, 1, "the folder must not be reopened after the first scan");
  assert.equal(config.drive.folderId, "rootfolder");
  assert.deepEqual(sites.allowedSites, ["https://example.org"]);
});

test("a new browser session reads the cache without a folder handle at all", async () => {
  const store = memoryStore();
  const first = create(Sites, store);
  await first.chooseFolder(dirHandle("config", goodFiles()));

  // A fresh source object stands in for the next open: no handle anywhere.
  const next = create(Sites, store);
  const config = await next.readConfig();
  assert.equal(config.drive.folderId, "rootfolder");
  assert.equal(config.drive.indexId, "indexfileid");
  assert.equal(config.oauth.client_id, CLIENT);
  assert.deepEqual((await next.readSites()).allowedSites, ["https://example.org"]);
});

test("only the surface is scanned: subfolders are skipped, not opened", async () => {
  let nestedOpened = 0;
  const nested = dirHandle("nested", goodFiles(), []);
  const values = nested.values.bind(nested);
  nested.values = async function* () { nestedOpened++; yield* values(); };

  const store = memoryStore();
  const source = create(Sites, store);
  const files = goodFiles();
  files["notes.txt"] = "ignored, not .json";
  files["broken.json"] = "{ not json";
  files["pastex-index.json"] = JSON.stringify({ v: 2, order: {}, collapsed: {} });
  await source.chooseFolder(dirHandle("config", files, [nested]));

  assert.equal(nestedOpened, 0, "a subfolder must not be entered");
  const saved = store.peek()["pastex-config-cache"];
  assert.ok(!("notes.txt" in saved.files), "non-JSON files are ignored");
  assert.ok(!("broken.json" in saved.files), "unparseable JSON is ignored");
  // A stray file that looks like an old configuration is ignored: one canonical
  // name is the whole contract.
  assert.deepEqual(Object.keys(saved.files), ["pastex-storage.json"]);
  assert.equal((await source.readConfig()).drive.folderId, "rootfolder");
});

test("the configuration file is required under its canonical name", async () => {
  // No file is recognised by its content: a renamed file is a configuration the
  // user fixes once, not an alias the code guesses at.
  const files = { "my-storage.json": JSON.stringify(storageFile()) };
  const store = memoryStore();
  const source = create(Sites, store);
  await assert.rejects(source.chooseFolder(dirHandle("config", files)),
    /pastex-storage\.json is missing/);
});

test("every link shape Drive produces is accepted, and a bad one is reported", async () => {
  const open = (link) => storageFile({ "pastex-folder": link });
  const ids = [];
  for (const link of [
    "1AbCdEfGhIjKlMnOpQrStUv",
    "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUv",
    "https://drive.google.com/drive/u/0/folders/1AbCdEfGhIjKlMnOpQrStUv?usp=sharing",
    "https://drive.google.com/open?id=1AbCdEfGhIjKlMnOpQrStUv",
  ]) {
    const store = memoryStore();
    const source = create(Sites, store);
    await source.chooseFolder(dirHandle("config", { "pastex-storage.json": JSON.stringify(open(link)) }));
    ids.push((await source.readConfig()).drive.folderId);
  }
  assert.deepEqual(new Set(ids), new Set(["1AbCdEfGhIjKlMnOpQrStUv"]),
    "every shape reduces to the same id");

  const store = memoryStore();
  const source = create(Sites, store);
  await assert.rejects(
    source.chooseFolder(dirHandle("config", {
      "pastex-storage.json": JSON.stringify(open("https://example.com/not-a-drive-link")),
    })),
    /needs a pastex-folder link to the library folder/);
});

test("a missing link is reported by key, all at once", async () => {
  const store = memoryStore();
  const source = create(Sites, store);
  const bare = storageFile();
  delete bare["pastex-index"];
  delete bare["pastex-sites"];
  await assert.rejects(source.chooseFolder(dirHandle("config", {
    "pastex-storage.json": JSON.stringify(bare),
  })), (err) => {
    assert.match(err.message, /needs an pastex-index link|needs a pastex-index link/);
    assert.match(err.message, /pastex-sites link/);
    return true;
  });
});

test("the client id lives in the same file and is checked there", async () => {
  const store = memoryStore();
  const source = create(Sites, store);
  await assert.rejects(source.chooseFolder(dirHandle("config", {
    "pastex-storage.json": JSON.stringify(storageFile({ client_id: "not-a-client" })),
  })), /needs the client_id of a Google OAuth client/);
});

test("a site link is kept for the library to fetch instead of being misread", async () => {
  const store = memoryStore();
  const source = create(Sites, store);
  await source.chooseFolder(dirHandle("config", {
    "pastex-storage.json": JSON.stringify(storageFile({
      "pastex-sites": "https://drive.google.com/file/d/sitesfileid/view",
    })),
  }));
  const config = await source.readConfig();
  assert.deepEqual(config.sites, { fileId: "sitesfileid", allowedSites: null });
  assert.deepEqual(await source.readSites(), { allowedSites: null, fileId: "sitesfileid", source: "folder pastex-storage.json" });
});

test("a broken folder never replaces a working configuration", async () => {
  const store = memoryStore();
  const source = create(Sites, store);
  await source.chooseFolder(dirHandle("config", goodFiles()));
  const before = JSON.stringify(store.peek());

  const broken = goodFiles();
  broken["pastex-storage.json"] = JSON.stringify(storageFile({ "pastex-sites": ["<all_urls>"] }));
  await assert.rejects(source.chooseFolder(dirHandle("broken", broken)), /Invalid site origin/);
  assert.equal(JSON.stringify(store.peek()), before, "the previous cache must survive");

  await assert.rejects(source.chooseFolder(dirHandle("empty", {})), /No usable \.json files/);
  assert.equal(JSON.stringify(store.peek()), before);
});

test("a missing file is never reported as a validation error", async () => {
  const source = create(Sites, memoryStore());
  await assert.rejects(source.chooseFolder(dirHandle("config", {
    "pastex-sites.json": "{}",
  })), (err) => {
    assert.doesNotMatch(err.message, /allowed_sites/);
    assert.match(err.message, /is missing/);
    return true;
  });
});

test("a malformed file IS reported separately from a missing one", async () => {
  const source = create(Sites, memoryStore());
  await assert.rejects(source.chooseFolder(dirHandle("config", {
    "pastex-storage.json": JSON.stringify(storageFile({
      "pastex-folder": "",
      "pastex-index": "",
      "pastex-sites": ["<all_urls>"],
      client_id: "",
    })),
  })), (err) => {
    assert.match(err.message, /needs a pastex-folder link/);
    assert.match(err.message, /needs an pastex-index link|needs a pastex-index link/);
    assert.match(err.message, /Invalid site origin/);
    assert.match(err.message, /needs the client_id/);
    return true;
  });
});

test("reading before choosing a folder explains what to do", async () => {
  const source = create(Sites, memoryStore());
  await assert.rejects(source.readConfig(), /Choose a configuration folder in Config first/);
  await assert.rejects(source.readSites(), /Choose a configuration folder in Config first/);
});
