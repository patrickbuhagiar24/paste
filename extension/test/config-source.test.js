"use strict";
/* The folder is a SOURCE of configuration, never its storage: it is scanned
 * once when the user picks it, the parsed JSON is cached, and every later read
 * comes from the cache. Only the surface of the folder is scanned. */
const assert = require("node:assert/strict");
const Discovery = require("../config-import.js");
const Sites = require("../sites.js");
const { create } = require("../config-source.js");

const account = {
  type: "service_account",
  client_email: "a@example.com",
  private_key: "-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----\n",
};

function fileEntry(name, contents) {
  return { kind: "file", name: name, getFile: async () => ({ name: name, size: contents.length, text: async () => contents }) };
}
function dirHandle(name, files, folders) {
  const entries = Object.keys(files).map((n) => fileEntry(n, files[n])).concat(folders || []);
  return { kind: "directory", name: name, values: async function* () { for (const e of entries) yield e; } };
}
function goodFiles() {
  return {
    "pastex-google-drive.json": JSON.stringify({ folder_url: "https://drive.google.com/drive/folders/rootfolder" }),
    "pastex-sites.json": JSON.stringify({ allowed_sites: ["https://example.org"] }),
    "service-account.json": JSON.stringify(account),
  };
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
  return { source: create(memoryStore(), Discovery, Sites, store), store: store, handle: dirHandle("config", files, folders) };
}

test("choosing a folder scans it once and stores the parsed files, not the handle", async () => {
  const store = memoryStore();
  const source = create(memoryStore(), Discovery, Sites, store);
  const info = await source.chooseFolder(dirHandle("config", goodFiles()));
  assert.deepEqual(info, {
    drive: "folder pastex-google-drive.json",
    account: "folder service-account.json",
    sites: "folder pastex-sites.json",
  });
  const saved = store.peek()["pastex-config-cache"];
  assert.equal(saved.folder, "config");
  assert.deepEqual(Object.keys(saved.files).sort(), ["pastex-google-drive.json", "pastex-sites.json", "service-account.json"]);
  assert.equal(saved.files["pastex-google-drive.json"].folder_url, "https://drive.google.com/drive/folders/rootfolder");
  assert.ok(!JSON.stringify(saved).includes("getFile"), "a handle must never be stored");
});

test("every later read comes from the cache and never touches the folder again", async () => {
  const store = memoryStore();
  const source = create(memoryStore(), Discovery, Sites, store);
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
  const first = create(memoryStore(), Discovery, Sites, store);
  await first.chooseFolder(dirHandle("config", goodFiles()));

  // A fresh source object stands in for the next open: no handle anywhere.
  const next = create(memoryStore(), Discovery, Sites, store);
  const config = await next.readConfig();
  assert.equal(config.drive.folderId, "rootfolder");
  assert.equal(config.serviceAccount.client_email, "a@example.com");
  assert.deepEqual((await next.readSites()).allowedSites, ["https://example.org"]);
});

test("only the surface is scanned: subfolders are skipped, not opened", async () => {
  let nestedOpened = 0;
  const nested = dirHandle("nested", goodFiles(), []);
  const values = nested.values.bind(nested);
  nested.values = async function* () { nestedOpened++; yield* values(); };

  const store = memoryStore();
  const source = create(memoryStore(), Discovery, Sites, store);
  const files = goodFiles();
  files["notes.txt"] = "ignored, not .json";
  files["broken.json"] = "{ not json";
  await source.chooseFolder(dirHandle("config", files, [nested]));

  assert.equal(nestedOpened, 0, "a subfolder must not be entered");
  const saved = store.peek()["pastex-config-cache"];
  assert.ok(!("notes.txt" in saved.files), "non-JSON files are ignored");
  assert.ok(!("broken.json" in saved.files), "unparseable JSON is ignored");
  assert.equal((await source.readConfig()).drive.folderId, "rootfolder");
});

test("the three files are identified by content, so names do not matter", async () => {
  const files = {
    "my-drive.json": JSON.stringify({ folder_id: "fromid" }),
    "my-sites.json": JSON.stringify({ allowed_sites: ["https://a.example"] }),
    "project-1a2b3c.json": JSON.stringify(account),
  };
  const store = memoryStore();
  const source = create(memoryStore(), Discovery, Sites, store);
  await source.chooseFolder(dirHandle("config", files));
  assert.equal((await source.readConfig()).drive.folderId, "fromid");
  assert.deepEqual((await source.readSites()).allowedSites, ["https://a.example"]);
});

test("a broken folder never replaces a working configuration", async () => {
  const store = memoryStore();
  const source = create(memoryStore(), Discovery, Sites, store);
  await source.chooseFolder(dirHandle("config", goodFiles()));
  const before = JSON.stringify(store.peek());

  const broken = goodFiles();
  broken["pastex-sites.json"] = JSON.stringify({ allowed_sites: ["<all_urls>"] });
  await assert.rejects(source.chooseFolder(dirHandle("broken", broken)), /Invalid site origin/);
  assert.equal(JSON.stringify(store.peek()), before, "the previous cache must survive");

  await assert.rejects(source.chooseFolder(dirHandle("empty", {})), /No usable \.json files/);
  assert.equal(JSON.stringify(store.peek()), before);
});

test("missing pieces are named in the error", async () => {
  const source = create(memoryStore(), Discovery, Sites, memoryStore());
  const noDrive = { "pastex-sites.json": JSON.stringify({ allowed_sites: [] }), "service-account.json": JSON.stringify(account) };
  await assert.rejects(source.chooseFolder(dirHandle("config", noDrive)), /pastex-google-drive\.json is missing/);

  const noKey = { "pastex-google-drive.json": JSON.stringify({ folder_id: "root" }), "pastex-sites.json": JSON.stringify({ allowed_sites: [] }) };
  await assert.rejects(source.chooseFolder(dirHandle("config", noKey)), /service-account\.json is missing/);

  const noSites = { "pastex-google-drive.json": JSON.stringify({ folder_id: "root" }), "service-account.json": JSON.stringify(account) };
  await assert.rejects(source.chooseFolder(dirHandle("config", noSites)), /pastex-sites\.json is missing/);
});

test("every missing file is reported at once, not one per attempt", async () => {
  const source = create(memoryStore(), Discovery, Sites, memoryStore());
  const onlyKey = { "service-account.json": JSON.stringify(account) };
  await assert.rejects(source.chooseFolder(dirHandle("config", onlyKey)), (err) => {
    assert.match(err.message, /pastex-google-drive\.json is missing/);
    assert.match(err.message, /pastex-sites\.json is missing/);
    return true;
  });
});

test("a missing file is never reported as a validation error", async () => {
  // It used to say "allowed_sites must be an array" for a file that was absent.
  const source = create(memoryStore(), Discovery, Sites, memoryStore());
  const noSites = { "pastex-google-drive.json": JSON.stringify({ folder_id: "root" }), "service-account.json": JSON.stringify(account) };
  await assert.rejects(source.chooseFolder(dirHandle("config", noSites)), (err) => {
    assert.doesNotMatch(err.message, /allowed_sites/);
    assert.match(err.message, /is missing/);
    return true;
  });
});

test("a malformed file IS reported separately from a missing one", async () => {
  const source = create(memoryStore(), Discovery, Sites, memoryStore());
  const malformed = {
    "pastex-google-drive.json": JSON.stringify({ folder_url: "https://drive.google.com/drive/folders/" }),
    "service-account.json": JSON.stringify({ type: "service_account" }),
    "pastex-sites.json": JSON.stringify({ allowed_sites: ["<all_urls>"] }),
  };
  await assert.rejects(source.chooseFolder(dirHandle("config", malformed)), (err) => {
    assert.match(err.message, /no usable folder_id or folder_url/);
    assert.match(err.message, /not a usable service-account key/);
    assert.match(err.message, /Invalid site origin/);
    return true;
  });
});

test("reading before choosing a folder explains what to do", async () => {
  const source = create(memoryStore(), Discovery, Sites, memoryStore());
  await assert.rejects(source.readConfig(), /Choose a configuration folder in Config first/);
  await assert.rejects(source.readSites(), /Choose a configuration folder in Config first/);
});

test("describe reports where the configuration came from and what is missing", async () => {
  const store = memoryStore();
  const source = create(memoryStore(), Discovery, Sites, store);
  assert.deepEqual(await source.describe(), { configured: false, folder: "", savedAt: "" });
  await source.chooseFolder(dirHandle("my-config", goodFiles()));
  const described = await source.describe();
  assert.equal(described.configured, true);
  assert.equal(described.folder, "my-config");
  assert.deepEqual(described.missing, []);
});

test("clearing the cache returns to the unconfigured state", async () => {
  const store = memoryStore();
  const source = create(memoryStore(), Discovery, Sites, store);
  await source.chooseFolder(dirHandle("config", goodFiles()));
  await source.clearCache();
  await assert.rejects(source.readConfig(), /Choose a configuration folder/);
  assert.equal((await source.describe()).configured, false);
});
