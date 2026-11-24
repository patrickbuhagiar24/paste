"use strict";
/* The file is a SOURCE of configuration, never its storage: it is read once when
 * the user picks it, the parsed JSON is cached, and every later read comes from
 * the cache. One pick is one file: nothing else on disk is looked at. */
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
// A File System Access handle for one file: the picker returns exactly this.
function fileHandle(name, contents) {
  const text = typeof contents === "string" ? contents : JSON.stringify(contents);
  return { kind: "file", name: name, getFile: async () => ({ name: name, size: text.length, text: async () => text }) };
}
function goodFile() {
  return fileHandle("pastex-storage.json", storageFile());
}
function memoryStore(initial) {
  let data = initial || {};
  return {
    get: async (key) => (key in data ? { [key]: data[key] } : {}),
    set: async (values) => { data = Object.assign({}, data, values); },
    peek: () => data,
  };
}

test("picking the file reads it once and stores the parsed JSON, not the handle", async () => {
  const store = memoryStore();
  const source = create(Sites, store);
  const info = await source.chooseFile(goodFile());
  assert.deepEqual(info, { storage: "file pastex-storage.json" });
  const saved = store.peek()["pastex-config-cache"];
  assert.equal(saved.file, "pastex-storage.json");
  // The links are stored as written; they are reduced to ids on every read, so
  // the stored shape stays exactly what the user typed.
  assert.equal(saved.value["pastex-folder"], FOLDER_LINK);
  assert.ok(!JSON.stringify(saved).includes("getFile"), "a handle must never be stored");
});

test("the three links are reduced to ids, and one file describes everything", async () => {
  const store = memoryStore();
  const source = create(Sites, store);
  await source.chooseFile(goodFile());
  const config = await source.readConfig();
  assert.deepEqual(config.drive, { folderId: "rootfolder", indexId: "indexfileid" });
  assert.equal(config.oauth.client_id, CLIENT);
  assert.deepEqual(config.sites, { fileId: "", allowedSites: ["https://example.org"] });
  assert.deepEqual((await source.readSites()).allowedSites, ["https://example.org"]);
});

test("every later read comes from the cache and never opens the file again", async () => {
  const store = memoryStore();
  const source = create(Sites, store);
  let opens = 0;
  const handle = goodFile();
  const counting = { kind: "file", name: handle.name, getFile: async () => { opens++; return handle.getFile(); } };

  await source.chooseFile(counting);
  assert.equal(opens, 1);

  const config = await source.readConfig();
  const sites = await source.readSites();
  await source.readConfig(true);
  await source.readSites(true);
  assert.equal(opens, 1, "the file must not be opened again after the first read");
  assert.equal(config.drive.folderId, "rootfolder");
  assert.deepEqual(sites.allowedSites, ["https://example.org"]);
});

test("a new browser session reads the cache without a file handle at all", async () => {
  const store = memoryStore();
  const first = create(Sites, store);
  await first.chooseFile(goodFile());

  // A fresh source object stands in for the next open: no handle anywhere.
  const next = create(Sites, store);
  const config = await next.readConfig();
  assert.equal(config.drive.folderId, "rootfolder");
  assert.equal(config.drive.indexId, "indexfileid");
  assert.equal(config.oauth.client_id, CLIENT);
  assert.deepEqual((await next.readSites()).allowedSites, ["https://example.org"]);
});

test("picking a different file is refused by name instead of searched for", async () => {
  // There is no folder scan and no content sniffing: the picker returns the one
  // file the user meant, so a mistake is visible immediately.
  const store = memoryStore();
  const source = create(Sites, store);
  await assert.rejects(source.chooseFile(fileHandle("my-storage.json", storageFile())),
    /Choose pastex-storage\.json, not my-storage\.json/);
  await assert.rejects(source.chooseFile(fileHandle("pastex-sites.json", { allowed_sites: [] })),
    /Choose pastex-storage\.json, not pastex-sites\.json/);
  assert.equal(store.peek()["pastex-config-cache"], undefined, "nothing was cached");
});

test("an unreadable or unparseable file is reported, not cached", async () => {
  const store = memoryStore();
  const source = create(Sites, store);
  const broken = { kind: "file", name: "pastex-storage.json", getFile: async () => { throw new Error("NotReadableError"); } };
  await assert.rejects(source.chooseFile(broken), /could not be read/);
  await assert.rejects(source.chooseFile(fileHandle("pastex-storage.json", "{ not json")), /not valid JSON/);
  await assert.rejects(source.chooseFile(fileHandle("pastex-storage.json", "[]")), /must contain a JSON object/);
  await assert.rejects(source.chooseFile(fileHandle("pastex-storage.json", {})), /needs a pastex-folder link/);
  assert.equal(store.peek()["pastex-config-cache"], undefined);
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
    await source.chooseFile(fileHandle("pastex-storage.json", open(link)));
    ids.push((await source.readConfig()).drive.folderId);
  }
  assert.deepEqual(new Set(ids), new Set(["1AbCdEfGhIjKlMnOpQrStUv"]),
    "every shape reduces to the same id");

  const store = memoryStore();
  const source = create(Sites, store);
  await assert.rejects(
    source.chooseFile(fileHandle("pastex-storage.json", open("https://example.com/not-a-drive-link"))),
    /needs a pastex-folder link to the library folder/);
});

test("a missing link is reported by key, all at once", async () => {
  const store = memoryStore();
  const source = create(Sites, store);
  const bare = storageFile();
  delete bare["pastex-index"];
  delete bare["pastex-sites"];
  await assert.rejects(source.chooseFile(fileHandle("pastex-storage.json", bare)), (err) => {
    assert.match(err.message, /needs an pastex-index link|needs a pastex-index link/);
    assert.match(err.message, /pastex-sites link/);
    return true;
  });
});

test("the client id lives in the same file and is checked there", async () => {
  const store = memoryStore();
  const source = create(Sites, store);
  await assert.rejects(source.chooseFile(fileHandle("pastex-storage.json", storageFile({ client_id: "not-a-client" }))),
    /needs the client_id of a Google OAuth client/);
});

test("a site link is kept for the library to fetch instead of being misread", async () => {
  const store = memoryStore();
  const source = create(Sites, store);
  await source.chooseFile(fileHandle("pastex-storage.json", storageFile({
    "pastex-sites": "https://drive.google.com/file/d/sitesfileid/view",
  })));
  const config = await source.readConfig();
  assert.deepEqual(config.sites, { fileId: "sitesfileid", allowedSites: null });
  assert.deepEqual(await source.readSites(), { allowedSites: null, fileId: "sitesfileid", source: "file pastex-storage.json" });
});

test("a broken file never replaces a working configuration", async () => {
  const store = memoryStore();
  const source = create(Sites, store);
  await source.chooseFile(goodFile());
  const before = JSON.stringify(store.peek());

  await assert.rejects(source.chooseFile(fileHandle("pastex-storage.json", storageFile({ "pastex-sites": ["<all_urls>"] }))),
    /Invalid site origin/);
  assert.equal(JSON.stringify(store.peek()), before, "the previous cache must survive");
});

test("reading before picking the file explains what to do", async () => {
  const source = create(Sites, memoryStore());
  await assert.rejects(source.readConfig(), /Choose pastex-storage\.json in Config first/);
  await assert.rejects(source.readSites(), /Choose pastex-storage\.json in Config first/);
});
